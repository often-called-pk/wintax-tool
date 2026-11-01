#!/usr/bin/env python3
"""Headless checks of wtxapp/session.py and wtxapp/gui.py. Run them without an ECU.

The download and beacon calls are stand-ins. The decode checks read the stored
test lap (TESTLAP) and decode it into a temporary folder. The window check builds
the main window on a display, or under xvfb-run. It prints SKIP when neither exists.

Run with: python3 -I tests/test_app.py
Prints PASS per check. Exits 1 on the first failure.
"""
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import tkinter
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # the wtxapp package
sys.path.insert(0, str(ROOT / "src"))  # the protocol and decode modules

import wtx_client  # noqa: E402
from wtxapp import gui  # noqa: E402
from wtxapp.session import RunSession  # noqa: E402
from wtxapp.gui import (DEFAULTS, TITLE, App, beacon_text, check_ip, check_output, load_config,  # noqa: E402
                        progress_text, run_if_confirmed, save_config)

TESTLAP = ("/home/pk/Downloads/pk-backup-d-drive/Motorsports/WinTAX ORECA BASIC APPLICATION-7/"
           "Data/047 - Arjun/F4_047/Run_713_R1_047/Lap_00002991_00927/cableData.ztx")
FAKE_N = 4096
BEACON = wtx_client.Beacon("192.168.111.254", "SRG_927", "F4_106", "(ABS=3012,TrigOut)", "255.255.255.0")
REQUIRED_KEYS = {"app_version", "os", "python", "frozen", "ecu_ip", "started", "finished",
                 "status", "phases", "download", "device"}


def fake_download(ip, out_path, *args, **kwargs):
    """Stand-in for wtx_client.download. Write FAKE_N bytes in two steps and return the count."""
    with open(out_path, "wb") as f:
        f.write(b"\x5a" * (FAKE_N // 2))
        f.flush()
        time.sleep(0.5)  # The progress poll sees the partial file.
        f.write(b"\xa5" * (FAKE_N - FAKE_N // 2))
    return FAKE_N


def failing_download(ip, out_path, *args, **kwargs):
    """Stand-in for wtx_client.download that fails the control handshake."""
    raise wtx_client.ProtocolError("bad ack for op 0x1")


def failing_callback(event):
    """A GUI callback that always raises. The session must not stop."""
    raise RuntimeError("GUI callback bug")


def listing(folder):
    """Return the name, size and mtime of each entry. Equal listings mean an unchanged folder."""
    return {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in Path(folder).iterdir()}


def check_session_log_and_metrics():
    events = []
    with tempfile.TemporaryDirectory() as tmp:
        with RunSession(tmp, on_event=events.append):
            pass
        logs = Path(tmp, "logs")
        (log_file,) = logs.glob("wtx_*.log")
        (run_file,) = logs.glob("run_*.json")
        header = log_file.read_text(encoding="utf-8").splitlines()[0]
        assert "frozen=" in header and "ecu_ip=" in header, header
        run = json.loads(run_file.read_text(encoding="utf-8"))
        assert REQUIRED_KEYS <= run.keys(), REQUIRED_KEYS - run.keys()
        assert run["status"] == "ok" and run["finished"], run
        assert any(e["type"] == "log" for e in events)


def check_download_ok():
    events = []
    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch.object(wtx_client, "download", fake_download):
            with RunSession(tmp, on_event=events.append) as s:
                blob = s.download()
        d = s.metrics["download"]
        assert blob and os.path.isfile(blob), blob
        assert d["bytes"] == FAKE_N, d
        assert len(d["sha256"]) == 64 and set(d["sha256"]) <= set("0123456789abcdef"), d
        assert s.metrics["status"] == "ok", s.metrics
        progress = [e for e in events if e["type"] == "progress"]
        assert progress and progress[-1]["bytes"] == FAKE_N and "rate_bps" in progress[-1], progress
        assert d["sha256"] == hashlib.sha256(Path(blob).read_bytes()).hexdigest(), d
        assert any(e["type"] == "done" for e in events)


def check_download_completeness():
    """The ECU sends no end-of-stream flag, so a finished download is never confirmed complete."""
    events = []
    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch.object(wtx_client, "download", fake_download):
            with RunSession(tmp, on_event=events.append) as s:
                assert s.download()
        assert s.metrics["status"] == "ok", s.metrics
        assert s.metrics["download"]["completeness"] == "unconfirmed", s.metrics
        run = json.loads(next(Path(tmp, "logs").glob("run_*.json")).read_text(encoding="utf-8"))
        assert run["download"]["completeness"] == "unconfirmed", run
        warnings = [e["msg"] for e in events if e["type"] == "log" and e["level"] == "WARNING"]
        assert any("partial" in m and "Confirm" in m for m in warnings), warnings
        log_text = next(Path(tmp, "logs").glob("wtx_*.log")).read_text(encoding="utf-8")
        assert "WARNING" in log_text and "partial" in log_text, log_text
    with tempfile.TemporaryDirectory() as tmp:  # A failed download has no completeness to report.
        with mock.patch.object(wtx_client, "download", failing_download):
            with RunSession(tmp) as s:
                assert s.download() is None
        assert "completeness" not in s.metrics["download"], s.metrics


def check_download_error():
    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch.object(wtx_client, "download", failing_download):
            with RunSession(tmp, on_event=failing_callback) as s:
                blob = s.download()
        err = s.metrics["error"]
        assert blob is None and s.metrics["status"] == "error", s.metrics
        assert err["type"] == "ProtocolError" and "Traceback" in err["traceback"], err
        run = json.loads(next(Path(tmp, "logs").glob("run_*.json")).read_text(encoding="utf-8"))
        assert run["status"] == "error" and run["error"]["type"] == "ProtocolError", run


def check_discover():
    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch.object(wtx_client, "listen_beacon", lambda timeout=5.0: BEACON):
            with RunSession(tmp) as s:
                assert s.discover() is BEACON
        assert s.metrics["device"]["name"] == "SRG_927", s.metrics
        assert s.metrics["device"]["alias"] == "F4_106", s.metrics
    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch.object(wtx_client, "listen_beacon", side_effect=socket.timeout()):
            with RunSession(tmp) as s:
                assert s.discover() is None
        assert s.metrics["status"] == "ok", s.metrics


def check_decode_real_lap():
    assert os.path.isfile(TESTLAP), "The stored test lap is missing."
    lap_dir = os.path.dirname(TESTLAP)
    before = listing(lap_dir)
    with tempfile.TemporaryDirectory() as tmp:
        with RunSession(tmp) as s:
            out = s.decode(TESTLAP)
            assert out and os.path.isfile(os.path.join(tmp, "decoded", "summary.csv")), out
        assert s.metrics["status"] == "ok", s.metrics
    assert listing(lap_dir) == before, "The input lap folder changed."


def check_decode_folder_best_effort():
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp, "in")
        Path(src, "A", "Lap").mkdir(parents=True)
        Path(src, "A", "Lap", "cableData.ztx").write_bytes(b"not a zip")  # This lap fails.
        Path(src, "B", "Lap").mkdir(parents=True)
        shutil.copy(TESTLAP, Path(src, "B", "Lap", "cableData.ztx"))  # This lap decodes.
        with RunSession(Path(tmp, "session")) as s:
            root = s.decode(src)
        assert root and Path(root).name == "decoded", root
        assert os.path.isfile(Path(root, "Lap_2", "summary.csv")), "The good lap is missing."
        assert not Path(root, "Lap").exists(), "The bad lap wrote files."
        assert s.metrics["status"] == "ok", s.metrics
        log_text = next(Path(tmp, "session", "logs").glob("wtx_*.log")).read_text(encoding="utf-8")
        assert "skipped lap" in log_text, "The log does not name the bad lap."


def check_decode_refuses_output_inside_input():
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp, "in")
        src.mkdir()
        with RunSession(src) as s:  # The output folder is <src>/decoded. It lies inside the input.
            assert s.decode(src) is None
        assert s.metrics["status"] == "error", s.metrics
        assert not Path(src, "decoded").exists(), "The decode wrote output inside the input."


def check_decode_empty_path():
    """An empty input path is refused. It must not mean the working folder."""
    here = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp, "work", "Lap")
        work.mkdir(parents=True)
        shutil.copy(TESTLAP, work / "cableData.ztx")  # A walk of the working folder would decode this lap.
        os.chdir(work.parent)
        try:
            with RunSession(Path(tmp, "session")) as s:
                assert s.decode("") is None
                assert s.decode("  ") is None
        finally:
            os.chdir(here)
        assert s.metrics["status"] == "error", s.metrics
        assert not Path(tmp, "session", "decoded").exists(), "The decode walked the working folder."


def check_package_results():
    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch.object(wtx_client, "download", fake_download):
            with RunSession(tmp) as s:
                blob = s.download()
                Path(tmp, "decoded", "lap").mkdir(parents=True)
                Path(tmp, "decoded", "lap", "summary.csv").write_text("name,rate_hz\n", encoding="utf-8")
                Path(tmp, "decoded", "summary.csv").write_text("name,rate_hz\n", encoding="utf-8")  # one lap
                zip_path = s.package_results()
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
        assert os.path.isfile(zip_path), zip_path
        assert any(n.startswith("logs/wtx_") and n.endswith(".log") for n in names), sorted(names)
        assert any(n.startswith("logs/run_") and n.endswith(".json") for n in names), sorted(names)
        assert "download/" + Path(blob).name in names, sorted(names)
        assert "decoded/lap/summary.csv" in names and "decoded/summary.csv" in names, sorted(names)
        assert Path(zip_path).name.startswith("wtx_results_192.168.111.254_"), zip_path


def check_package_zip_name():
    """The zip name holds the beacon alias when discover found one, else the ECU IP. It stays one safe name."""
    def zip_name(listen, ecu_ip=wtx_client.ECU_IP_DEFAULT):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(wtx_client, "listen_beacon", listen):
                with RunSession(tmp, ecu_ip=ecu_ip) as s:
                    if listen is not None:
                        s.discover()
                    zip_path = Path(s.package_results())
            assert zip_path.parent == Path(tmp).resolve() and zip_path.is_file(), zip_path
            return zip_path.name

    ip = wtx_client.ECU_IP_DEFAULT
    assert zip_name(lambda timeout=5.0: BEACON).startswith("wtx_results_F4_106_")
    hostile = BEACON._replace(alias="../x y:z")  # The beacon is not trusted. Keep the name one file name.
    assert zip_name(lambda timeout=5.0: hostile).startswith("wtx_results_.._x_y_z_")
    assert zip_name(mock.Mock(side_effect=socket.timeout())).startswith(f"wtx_results_{ip}_")  # no beacon
    assert zip_name(lambda timeout=5.0: BEACON._replace(alias="")).startswith(f"wtx_results_{ip}_")
    assert zip_name(None, ecu_ip="my ecu/1").startswith("wtx_results_my_ecu_1_")


def raises(exc, fn, *args):
    """Return True when fn(*args) raises exc."""
    try:
        fn(*args)
    except exc:
        return True
    return False


def check_gui_config():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp, "wtxapp.json")
        assert load_config(path) == DEFAULTS, "A missing file must give the defaults."
        cfg = {"ecu_ip": "10.0.0.7", "input": str(Path(tmp, "in")), "output": str(Path(tmp, "out"))}
        save_config(cfg, path)
        assert load_config(path) == cfg, load_config(path)
        path.write_text("{not json", encoding="utf-8")
        assert load_config(path) == DEFAULTS, "A broken file must give the defaults."
        path.write_text("[1, 2]", encoding="utf-8")
        assert load_config(path) == DEFAULTS, "A file of the wrong shape must give the defaults."
        save_config(cfg, Path(tmp, "no", "such", "dir.json"))  # An unwritable path must not raise.


def check_gui_progress_text():
    ev = {"type": "progress", "msg": "1536 bytes", "bytes": 1536, "rate_bps": 2048.0}
    assert progress_text(ev, 3.5) == "1,536 bytes, 2.0 kB/s, 3.5 s elapsed", progress_text(ev, 3.5)
    zero = {"type": "progress", "msg": "0 bytes", "bytes": 0, "rate_bps": 0.0}
    assert progress_text(zero, 0.0) == "0 bytes, 0.0 kB/s, 0.0 s elapsed", progress_text(zero, 0.0)


def check_gui_confirm_gate():
    """The arming step runs only after a confirm that returns True."""
    calls = []
    assert run_if_confirmed(lambda: False, lambda: calls.append("arm")) is False
    assert run_if_confirmed(lambda: "yes", lambda: calls.append("arm")) is False  # Only True counts.
    assert calls == [], "The arming step ran without a confirm."
    assert run_if_confirmed(lambda: True, lambda: calls.append("arm")) is True
    assert calls == ["arm"], calls


def check_gui_field_checks():
    """The ECU IP must be an IPv4 address. The output folder must not sit inside the input folder."""
    assert check_ip(" 192.168.111.254 ") == "192.168.111.254"
    for bad in ("", "ecu.local", "999.1.1.1", "10.0.0", "fe80::1"):
        assert raises(ValueError, check_ip, bad), bad
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp, "in")
        lap = Path(src, "Lap")
        lap.mkdir(parents=True)
        assert raises(ValueError, check_output, "")
        assert raises(ValueError, check_output, "   ")
        assert check_output(str(Path(tmp, "out")), str(src)) == Path(tmp, "out").resolve()
        assert check_output(str(Path(tmp, "in2")), str(src)) == Path(tmp, "in2").resolve()  # A sibling is outside.
        assert raises(ValueError, check_output, str(src), str(src))  # The same folder.
        assert raises(ValueError, check_output, str(Path(src, "sub")), str(src))  # Inside.
        assert raises(ValueError, check_output, str(lap), str(Path(lap, "cableData.ztx")))  # The lap's own folder.


def check_gui_beacon_text():
    assert beacon_text(None) == "No ECU found."
    assert beacon_text(BEACON) == ("The ECU is on the network. Its name is SRG_927, its alias is F4_106 "
                                   "and its state is (ABS=3012,TrigOut).")


class Field:
    """Stand-in for a Tk StringVar. The App handlers only call get()."""

    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


def make_app(ip, input_path, output):
    """Return an App without a window. The button handlers read three fields. _start records its job and IP."""
    app = App.__new__(App)
    app.ip_var, app.in_var, app.out_var = Field(ip), Field(input_path), Field(output)
    app.started = []
    app._start = lambda label, found, job: app.started.append((job, found[0]))
    return app


def check_gui_ip_scope():
    """Test connection and Download need a valid ECU IP. Decode, Package and Open results run offline.

    This check builds no window, so it runs on any machine.
    """
    shown, opened = [], []
    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp, "in"), Path(tmp, "out")
        src.mkdir()
        out.mkdir()
        with mock.patch.object(gui.messagebox, "showerror", lambda title, msg: shown.append(msg)), \
                mock.patch.object(gui.messagebox, "askyesno", lambda title, msg: True), \
                mock.patch.object(gui, "open_folder", opened.append):
            for ip in ("ecu.local", "999.1.1.1", ""):  # None of these is an IPv4 address.
                app = make_app(ip, str(src), str(out))
                app.on_decode()
                app.on_package()
                app.on_open()
                used = ip or wtx_client.ECU_IP_DEFAULT  # A blank field falls back to the default.
                assert app.started == [(gui._decode, used), (gui._package, used)], (ip, app.started)
                assert opened == [out.resolve()] and not shown, (ip, opened, shown)
                opened.clear()
                app = make_app(ip, str(src), str(out))
                app.on_test()
                app.on_download()  # The confirm returns True, so only the IP check can stop it.
                assert app.started == [], (ip, app.started)
                assert len(shown) == 2 and all("IPv4" in m for m in shown), (ip, shown)
                shown.clear()
            app = make_app("10.0.0.7", str(src), str(out))  # A valid IP reaches all four jobs.
            for press in (app.on_test, app.on_download, app.on_decode, app.on_package):
                press()
            jobs = [gui._test, gui._download, gui._decode, gui._package]
            assert app.started == [(job, "10.0.0.7") for job in jobs] and not shown, (app.started, shown)


def check_gui_window():
    """Build the main window, run one update and destroy it. Use xvfb-run when no display is set."""
    if sys.platform != "win32" and not os.environ.get("DISPLAY"):
        if not shutil.which("xvfb-run"):
            return "skip"
        code = (f"import sys; sys.path[:0] = [{str(ROOT)!r}, {str(ROOT / 'tests')!r}]; "
                "import test_app; test_app.check_gui_window()")
        subprocess.run(["xvfb-run", "-a", sys.executable, "-I", "-c", code], check=True, timeout=120)
        return None
    try:
        root = tkinter.Tk()
    except tkinter.TclError:  # DISPLAY is set, but no X server answers.
        return "skip"
    try:
        App(root)
        root.update()
        assert root.title() == TITLE, root.title()
    finally:
        root.destroy()
    return None


CHECKS = [
    check_session_log_and_metrics,
    check_download_ok,
    check_download_completeness,
    check_download_error,
    check_discover,
    check_decode_real_lap,
    check_decode_folder_best_effort,
    check_decode_refuses_output_inside_input,
    check_decode_empty_path,
    check_package_results,
    check_package_zip_name,
    check_gui_config,
    check_gui_progress_text,
    check_gui_confirm_gate,
    check_gui_field_checks,
    check_gui_beacon_text,
    check_gui_ip_scope,
    check_gui_window,
]

if __name__ == "__main__":
    for check in CHECKS:
        try:
            result = check()
        except Exception as e:  # any failure ends the run with exit 1
            print(f"FAIL {check.__name__}: {type(e).__name__}: {e}")
            sys.exit(1)
        print(f"{'SKIP' if result == 'skip' else 'PASS'} {check.__name__}")
