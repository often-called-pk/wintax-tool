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
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import tkinter
import zipfile
from collections import namedtuple
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # the wtxapp package
sys.path.insert(0, str(ROOT / "src"))  # the protocol and decode modules

import wtx_client  # noqa: E402
from wtxapp import gui, session  # noqa: E402
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


def idle_download(ip, out_path, *args, **kwargs):
    """Stand-in for wtx_client.download that ends on the idle timeout: all bytes at once, then silence."""
    with open(out_path, "wb") as f:
        f.write(b"\x5a" * FAKE_N)
    time.sleep(0.7)  # The progress poll sees no growth, so the last rates are 0.0.
    return FAKE_N


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
        done = [e for e in events if e["type"] == "done"]
        # The app cannot confirm that the data is complete, so the text must not claim more (I6).
        assert done and "Not yet verified" in done[-1]["msg"] and "4,096 bytes" in done[-1]["msg"], done
        assert "finished" not in done[-1]["msg"], done


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


def check_download_disk_precheck():
    """I2: a full or unwritable output folder stops Download before the arming SET goes out."""
    sent = []

    def arming_download(ip, out_path, *args, **kwargs):
        """Stand-in for wtx_client.download. The real call sends the SET before it opens the blob."""
        sent.append(ip)
        return fake_download(ip, out_path)

    usage = namedtuple("usage", "total used free")
    with tempfile.TemporaryDirectory() as tmp, mock.patch.object(wtx_client, "download", arming_download):
        events = []
        with mock.patch.object(shutil, "disk_usage", lambda path: usage(10**12, 10**12 - 5 * 2**20, 5 * 2**20)):
            with RunSession(Path(tmp, "full"), on_event=events.append) as s:
                assert s.download() is None
        assert sent == [], "The SET went out on a full disk."
        assert s.metrics["status"] == "error" and s.metrics["error"]["type"] == "OSError", s.metrics
        (err,) = [e for e in events if e["type"] == "error"]
        assert err["msg"] == "The download did not start." and "5 MB free" in err["error"], err
        assert "ECU was not changed" in err["hint"] and "disk space" in err["hint"], err
        assert not list(Path(tmp, "full", "download").iterdir()), "A file stayed in the download folder."
        assert "download" not in s.metrics["phases"], s.metrics["phases"]
        # A file stands where the download folder should be, so no new file can go there.
        events.clear()
        with RunSession(Path(tmp, "locked"), on_event=events.append) as s:
            shutil.rmtree(Path(tmp, "locked", "download"))
            Path(tmp, "locked", "download").write_text("not a folder", encoding="utf-8")
            assert s.download() is None
        assert sent == [], "The SET went out for an unwritable folder."
        (err,) = [e for e in events if e["type"] == "error"]
        assert s.metrics["status"] == "error" and err["msg"] == "The download did not start." and err["error"], err
        # A disk with room still arms the ECU.
        with RunSession(Path(tmp, "fine")) as s:
            assert s.download()
        assert sent == [wtx_client.ECU_IP_DEFAULT], sent


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


def check_decode_file_beside_output():
    """I1: a lap file and a new output folder beside it are fine. Only a folder input can nest the output."""
    with tempfile.TemporaryDirectory() as tmp:
        desk = Path(tmp, "Desktop")
        desk.mkdir()
        ztx = desk / "cableData.ztx"
        shutil.copy(TESTLAP, ztx)
        before = listing(desk)
        with RunSession(desk / "out") as s:
            out = s.decode(ztx)
            assert out and os.path.isfile(os.path.join(out, "summary.csv")), out
        assert s.metrics["status"] == "ok", s.metrics
        assert listing(desk)["cableData.ztx"] == before["cableData.ztx"], "The input file changed."
        with RunSession(desk / "out_two") as s:  # A path that does not exist gives the plain error.
            assert s.decode(desk / "missing") is None
        assert "inside" not in s.metrics["error"]["msg"], s.metrics["error"]


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


def check_decrypt_real_lap():
    lap_dir = os.path.dirname(TESTLAP)
    before = listing(lap_dir)
    with tempfile.TemporaryDirectory() as tmp:
        with RunSession(tmp) as s:
            out = s.decrypt(TESTLAP)
            assert out and Path(out).name == "decrypted" and any(Path(out).iterdir()), out
        assert s.metrics["status"] == "ok", s.metrics
    assert listing(lap_dir) == before, "The input lap folder changed."


def check_decrypt_refusals():
    """A folder, an empty path, a non-ztx file and an output beside the input are refused. No output is written."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp, "in")
        src.mkdir()
        txt = src / "a.txt"
        txt.write_text("x")
        for bad in (src, "", "  ", txt):
            with RunSession(Path(tmp, "session")) as s:
                assert s.decrypt(bad) is None, bad
            assert s.metrics["status"] == "error", (bad, s.metrics)
        assert not Path(tmp, "session", "decrypted").exists()
        ztx = src / "cableData.ztx"
        shutil.copy(TESTLAP, ztx)
        # The output folder <src>/decrypted lies beside the input file. The session itself writes its logs
        # in <src>. The window refuses this case before the session starts (check_gui_decrypt_output).
        with RunSession(src) as s:
            assert s.decrypt(ztx) is None
        assert not Path(src, "decrypted").exists(), "The decrypt wrote its output inside the data folder."


def check_decrypt_crc_failure():
    """A member that fails the CRC check gives None and an error event. No done event follows."""
    events = []
    with tempfile.TemporaryDirectory() as tmp:
        ztx = Path(tmp, "in", "cableData.ztx")
        ztx.parent.mkdir()
        ztx.write_bytes(b"x")  # The stand-in does not read it.
        with mock.patch.object(session.decrypt_ztx, "decrypt_ztx", return_value=False) as fake, \
                RunSession(Path(tmp, "session"), on_event=events.append) as s:
            assert s.decrypt(ztx) is None
        assert fake.call_count == 1
        kinds = [e["type"] for e in events]
        assert "error" in kinds and "done" not in kinds, kinds
        assert s.metrics["status"] == "error", s.metrics


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


def check_package_foreign_files():
    """I4: the zip holds only files that the app wrote. The run record holds paths relative to the output folder."""
    foreign = ["logs/tax_return_2025.pdf", "logs/notes.txt", "download/notes.txt", "download/backup.bin",
               "download/download_raw_x.txt", "decoded/lap/other.csv"]
    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch.object(wtx_client, "download", fake_download):
            with RunSession(tmp) as s:
                blob = s.download()
                for name in foreign:  # A user can drop any file into the folders of the app.
                    Path(tmp, name).parent.mkdir(parents=True, exist_ok=True)
                    Path(tmp, name).write_text("private", encoding="utf-8")
                Path(tmp, "decoded", "lap", "summary.csv").write_text("name,rate_hz\n", encoding="utf-8")
                zip_path = s.package_results()
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
            (run_name,) = [n for n in names if n.startswith("logs/run_")]
            run_text = zf.read(run_name).decode("utf-8")
        assert not set(foreign) & names, sorted(set(foreign) & names)
        assert "download/" + Path(blob).name in names and "decoded/lap/summary.csv" in names, sorted(names)
        assert any(n.startswith("logs/wtx_") for n in names), sorted(names)
        out_file = json.loads(run_text)["download"]["out_file"]
        assert out_file == "download/" + Path(blob).name, out_file  # not absolute: no Windows user name in it
        assert Path(tmp).name not in run_text, "The run record holds the path of the output folder."
        assert (Path(tmp) / out_file).is_file()


def check_package_failure_leaves_no_zip():
    """M8: a zip that fails half way leaves no file behind, neither a zip nor a temporary file."""
    with tempfile.TemporaryDirectory() as tmp:
        with RunSession(tmp) as s:
            with mock.patch.object(zipfile.ZipFile, "write", side_effect=OSError(28, "No space left on device")):
                assert raises(OSError, s.package_results)
            assert not list(Path(tmp).glob("wtx_results_*")), sorted(p.name for p in Path(tmp).iterdir())
            zip_path = Path(s.package_results())  # The next call works.
        assert zip_path.is_file() and not list(Path(tmp).glob("*.part")), sorted(p.name for p in Path(tmp).iterdir())


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
    """The ECU IP must be an IPv4 address. The output folder must not sit inside an Input folder (I1)."""
    assert check_ip(" 192.168.111.254 ") == "192.168.111.254"
    for bad in ("", "ecu.local", "999.1.1.1", "10.0.0", "fe80::1"):
        assert raises(ValueError, check_ip, bad), bad
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp, "in")
        lap = Path(src, "Lap")
        lap.mkdir(parents=True)
        ztx = lap / "cableData.ztx"
        ztx.write_bytes(b"x")
        assert raises(ValueError, check_output, "")
        assert raises(ValueError, check_output, "   ")
        assert check_output(str(Path(tmp, "out")), str(src)) == Path(tmp, "out").resolve()
        assert check_output(str(Path(tmp, "in2")), str(src)) == Path(tmp, "in2").resolve()  # A sibling is outside.
        assert check_output(str(Path(tmp, "out")), "") == Path(tmp, "out").resolve()  # No Input, no check.
        assert raises(ValueError, check_output, str(src), str(src))  # The same folder.
        assert raises(ValueError, check_output, str(Path(src, "sub")), str(src))  # Inside.
        assert raises(ValueError, check_output, str(lap), str(src))  # A lap folder inside the Input folder.
        # An Input that is a file has no folder to protect. This is the user guide case: a file and a new folder
        # beside it. A path that does not exist is not a folder either.
        assert check_output(str(Path(lap, "out")), str(ztx)) == Path(lap, "out").resolve()
        assert check_output(str(lap), str(ztx)) == lap.resolve()
        assert check_output(str(Path(tmp, "out")), str(Path(tmp, "missing"))) == Path(tmp, "out").resolve()


def check_gui_beacon_text():
    assert beacon_text(None) == "No ECU found."
    assert beacon_text(BEACON) == ("The ECU is on the network. Its name is SRG_927, its alias is F4_106 "
                                   "and its state is (ABS=3012,TrigOut).")


class Field:
    """Stand-in for a Tk StringVar. The App handlers call get() and set()."""

    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class Spy(Field):
    """A Field that counts its reads."""

    def __init__(self, value=""):
        super().__init__(value)
        self.reads = 0

    def get(self):
        self.reads += 1
        return self.value


def make_app(ip, input_path, output):
    """Return an App without a window. The button handlers read three fields. _start records its job and IP."""
    app = App.__new__(App)
    app.ip_var, app.in_var, app.out_var = Field(ip), Field(input_path), Field(output)
    app.events, app.session = queue.Queue(), None
    app.started = []
    app._start = lambda label, found, job: app.started.append((job, found[0]))
    return app


def check_gui_ip_scope():
    """Test connection and Download need a valid ECU IP. Decode, Decrypt, Package and Open results run offline.

    An offline job gets no IP. This check builds no window, so it runs on any machine.
    """
    shown, opened = [], []
    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp, "in"), Path(tmp, "out")
        src.mkdir()
        out.mkdir()
        with mock.patch.object(gui.messagebox, "showerror", lambda title, msg: shown.append(msg)), \
                mock.patch.object(gui.messagebox, "askyesno", lambda title, msg, **options: True), \
                mock.patch.object(gui, "open_folder", opened.append):
            for ip in ("ecu.local", "999.1.1.1", ""):  # None of these is an IPv4 address.
                app = make_app(ip, str(src), str(out))
                app.on_decode()
                app.on_decrypt()
                app.on_package()
                app.on_open()
                assert app.started == [(gui._decode, None), (gui._decrypt, None), (gui._package, None)], (ip, app.started)
                assert opened == [out.resolve()] and not shown, (ip, opened, shown)
                opened.clear()
                app = make_app(ip, str(src), str(out))
                app.on_test()
                app.on_download()  # The confirm returns True, so only the IP check can stop it.
                assert app.started == [], (ip, app.started)
                assert len(shown) == 2 and all("IPv4" in m for m in shown), (ip, shown)
                shown.clear()
            app = make_app("10.0.0.7", str(src), str(out))  # A valid IP reaches all five jobs. Only two use it.
            for press in (app.on_test, app.on_download, app.on_decode, app.on_decrypt, app.on_package):
                press()
            ecu = "10.0.0.7"
            expected = [(gui._test, ecu), (gui._download, ecu), (gui._decode, None), (gui._decrypt, None),
                        (gui._package, None)]
            assert app.started == expected and not shown, (app.started, shown)


def check_gui_arm_dialog():
    """The arming dialog makes No the default button. A stray Enter then does not arm the ECU."""
    seen = {}

    def ask(title, message, **options):
        seen.update(options, message=message)
        return False

    with mock.patch.object(gui.messagebox, "askyesno", ask):
        assert make_app("", "", "")._confirm_arm() is False
    assert seen.get("default") == gui.messagebox.NO and "arms the ECU" in seen["message"], seen


def check_gui_test_job():
    """Test connection names the error of a failed beacon read. Only a timeout reads No ECU found."""
    with tempfile.TemporaryDirectory() as tmp:
        with RunSession(tmp) as s:
            with mock.patch.object(wtx_client, "listen_beacon", side_effect=OSError("address in use")):
                msg = gui._test(s, "")["msg"]
            assert "OSError" in msg and "address in use" in msg and "No ECU" not in msg, msg
            with mock.patch.object(wtx_client, "listen_beacon", side_effect=socket.timeout()):
                # The error above stays in the metrics. It must not turn a plain timeout into an error.
                assert gui._test(s, "") == {"msg": "No ECU found."}
            with mock.patch.object(wtx_client, "listen_beacon", lambda timeout=5.0: BEACON):
                assert "F4_106" in gui._test(s, "")["msg"]


def check_gui_session_reuse():
    """The window keeps one session for each output folder.

    The beacon that Test connection reads reaches the run record of the download and the name of the zip.
    A new output folder starts a new session. This check builds no window.
    """
    with tempfile.TemporaryDirectory() as tmp:
        out, other = Path(tmp, "out").resolve(), Path(tmp, "other").resolve()
        app = make_app("", "", "")
        events = []

        def press(job, ip, folder=out):
            """Run one job the way a button does. Keep the events that it queued."""
            app._worker(ip, str(folder), "", job)
            while not app.events.empty():
                events.append(app.events.get_nowait())

        with mock.patch.object(wtx_client, "listen_beacon", lambda timeout=5.0: BEACON), \
                mock.patch.object(wtx_client, "download", idle_download):
            press(gui._test, wtx_client.ECU_IP_DEFAULT)
            first = app.session
            assert first.metrics["device"]["alias"] == "F4_106", first.metrics["device"]
            shutil.rmtree(out / "download")  # The user can delete a folder between two jobs. A download needs it.
            press(gui._download, "10.0.0.7")
            press(gui._package, None)  # An offline job leaves the ECU IP as it is.
        assert app.session is first, "The window built a new session for the same folder."
        assert first.ecu_ip == first.metrics["ecu_ip"] == "10.0.0.7", (first.ecu_ip, first.metrics["ecu_ip"])
        assert not [e for e in events if e["type"] == "error"], events
        (zip_path,) = out.glob("wtx_results_*.zip")
        assert zip_path.name.startswith("wtx_results_F4_106_"), zip_path.name  # The alias, not the IP.
        with zipfile.ZipFile(zip_path) as zf:
            (run_name,) = [n for n in zf.namelist() if n.startswith("logs/run_")]  # One session, one run record.
            record = json.loads(zf.read(run_name))
        assert record["device"]["alias"] == "F4_106" and record["download"]["bytes"] == FAKE_N, record
        # The download ended on silence. The last progress event shows 0.0, the final line shows the average.
        assert [e for e in events if e["type"] == "progress"][-1]["rate_bps"] == 0.0
        (end,) = [e for e in events if e["type"] == "end" and "summary" in e]
        assert "4,096 bytes" in end["summary"] and "0.0 kB/s" not in end["summary"], end
        app.status, app.metrics, app._busy = Field(), Field(), lambda busy: None
        app._apply(end)
        assert app.metrics.get() == end["summary"], app.metrics.get()
        press(gui._package, None, other)  # A new output folder closes the old session. The identity stays behind.
        second = app.session
        assert second is not first and second.dir == other, second.dir
        assert first.metrics["finished"] is not None, "The old session stayed open."
        assert second.metrics["device"] == {} and second.ecu_ip == wtx_client.ECU_IP_DEFAULT, second.metrics
        (zip_two,) = other.glob("wtx_results_*.zip")
        assert zip_two.name.startswith(f"wtx_results_{wtx_client.ECU_IP_DEFAULT}_"), zip_two.name
        app._close()
        assert app.session is None and second.metrics["finished"] is not None


def check_gui_input_scope():
    """I1: only Decode reads the Input box. An old Input path cannot block the other buttons."""
    shown, opened, founds = [], [], []
    with tempfile.TemporaryDirectory() as tmp:
        desk = Path(tmp, "Desktop")
        old = desk / "old_laps"  # An Input folder from an earlier session. The output folder lies inside it.
        out = old / "out"
        out.mkdir(parents=True)
        ztx = desk / "cableData.ztx"
        ztx.write_bytes(b"x")
        with mock.patch.object(gui.messagebox, "showerror", lambda title, msg: shown.append(msg)), \
                mock.patch.object(gui.messagebox, "askyesno", lambda title, msg, **options: True), \
                mock.patch.object(gui, "open_folder", opened.append):
            app = make_app("10.0.0.7", str(old), str(out))
            app.in_var = Spy(str(old))
            app._start = lambda label, found, job: founds.append((job, found))
            for press in (app.on_test, app.on_download, app.on_package, app.on_open):
                press()
            assert not shown and app.in_var.reads == 0, (shown, app.in_var.reads)  # They never read the Input.
            ecu, folder = "10.0.0.7", str(out.resolve())
            assert founds == [(gui._test, (ecu, folder, "")), (gui._download, (ecu, folder, "")),
                              (gui._package, (None, folder, ""))], founds
            assert opened == [out.resolve()], opened
            founds.clear()
            app.on_decode()  # Decode reads the Input. The output folder lies inside it, so Decode refuses.
            assert app.in_var.reads == 1 and not founds, (app.in_var.reads, founds)
            assert len(shown) == 1 and "Input box" in shown[0], shown
            # The output folder is outside each of these. Decode starts: a file beside a new folder (the user
            # guide case), a path that does not exist, a sibling folder and an empty box.
            for case in (ztx, desk / "missing", desk / "other_laps", ""):
                shown.clear()
                founds.clear()
                app = make_app("", str(case), str(desk / "new_out"))
                app._start = lambda label, found, job: founds.append((job, found))
                app.on_decode()
                assert [job for job, _ in founds] == [gui._decode] and not shown, (case, founds, shown)


def check_gui_decrypt_output():
    """Decrypt refuses an output folder inside the folder of the input file. No job starts and the folder stays unchanged."""
    shown = []
    with tempfile.TemporaryDirectory() as tmp:
        lap = Path(tmp, "Lap")
        lap.mkdir()
        ztx = lap / "cableData.ztx"
        ztx.write_bytes(b"x")
        before = listing(lap)
        with mock.patch.object(gui.messagebox, "showerror", lambda title, msg: shown.append(msg)):
            for out in (lap, lap / "out"):
                app = make_app("", str(ztx), str(out))
                app.on_decrypt()
                assert app.started == [] and app.session is None, (out, app.started)
                assert len(shown) == 1 and "folder of the input file" in shown[0], (out, shown)
                shown.clear()
            app = make_app("", str(ztx), str(Path(tmp, "out")))  # An output folder outside the lap folder starts.
            app.on_decrypt()
            assert app.started == [(gui._decrypt, None)] and not shown, (app.started, shown)
        assert listing(lap) == before, "The refused decrypt changed the lap folder."


def check_gui_test_ip_mismatch():
    """I5: Test connection says so when the ECU reports another IP than the ECU IP box holds."""
    with tempfile.TemporaryDirectory() as tmp, \
            mock.patch.object(wtx_client, "listen_beacon", lambda timeout=5.0: BEACON):
        with RunSession(Path(tmp, "other"), ecu_ip="10.0.0.7") as s:
            msg = gui._test(s, "")["msg"]
        assert "does not match" in msg and "reports 192.168.111.254" in msg and "box has 10.0.0.7" in msg, msg
        assert "Change the box to 192.168.111.254." in msg and "F4_106" in msg, msg  # The identity still shows.
        assert s.metrics["device"]["ip"] == BEACON.ip and s.metrics["device"]["netmask"] == BEACON.netmask, s.metrics
        with RunSession(Path(tmp, "same"), ecu_ip=BEACON.ip) as s:
            assert gui._test(s, "")["msg"] == beacon_text(BEACON)  # The addresses match: the plain line.
    assert beacon_text(BEACON, BEACON.ip) == beacon_text(BEACON)
    assert beacon_text(None, "10.0.0.7") == "No ECU found."


def check_gui_download_failure_text():
    """I6: a failed download shows the cause and a hint. The log keeps the full reason."""
    assert gui.error_text({"type": "error"}) == "The run failed."
    assert gui.error_text({"type": "error", "msg": "Stop.", "error": "", "hint": ""}) == "Stop."
    full = {"type": "error", "msg": "Stop.", "error": "OSError: x", "hint": "Try again."}
    assert gui.error_text(full) == "Stop. Try again.\nCause: OSError: x", gui.error_text(full)
    with tempfile.TemporaryDirectory() as tmp:
        app = make_app("", "", "")
        with mock.patch.object(wtx_client, "download", failing_download):
            app._worker("10.0.0.7", tmp, "", gui._download)
        events = []
        while not app.events.empty():
            events.append(app.events.get_nowait())
        app._close()
        (err,) = [e for e in events if e["type"] == "error"]
        text = gui.error_text(err)
        assert text.startswith("The download failed."), text
        assert text.endswith("Cause: ProtocolError: bad ack for op 0x1"), text
        for word in ("cable", "ECU IP", "ECU power", "WinTAX"):
            assert word in text, (word, text)
        app.status, app.metrics, app._busy = Field(), Field(), lambda busy: None
        for ev in events:
            if ev["type"] in ("error", "end"):
                app._apply(ev)
        assert app.status.get() == text, app.status.get()  # The window shows the cause. The end event keeps it.
        log_text = next(Path(tmp, "logs").glob("wtx_*.log")).read_text(encoding="utf-8")
        assert "ProtocolError: bad ack for op 0x1" in log_text and "Traceback" in log_text, log_text


def check_gui_worker_errors():
    """M14: an exception outside a RunSession call reaches the log, run.json and an error event."""
    def run(app, job, folder):
        app._worker("10.0.0.7", str(folder), "", job)
        events = []
        while not app.events.empty():
            events.append(app.events.get_nowait())
        return events

    def boom(s, inp):
        raise OSError(28, "No space left on device")

    with tempfile.TemporaryDirectory() as tmp:
        out, blocked = Path(tmp, "out"), Path(tmp, "blocked")
        app = make_app("", "", "")
        events = run(app, boom, out)
        assert events[-1]["type"] == "end", events[-1]  # The buttons come back.
        (err,) = [e for e in events if e["type"] == "error"]
        assert err["msg"] == "The run stopped." and "No space left" in err["error"], err
        log_text = next(out.glob("logs/wtx_*.log")).read_text(encoding="utf-8")
        assert "The run stopped." in log_text and "No space left on device" in log_text, log_text
        record = json.loads(next(out.glob("logs/run_*.json")).read_text(encoding="utf-8"))
        assert record["status"] == "error" and record["error"]["type"] == "OSError", record
        # A package that fails on a full disk takes the same way and leaves no partial zip.
        with mock.patch.object(zipfile.ZipFile, "write", side_effect=OSError(28, "No space left on device")):
            events = run(app, gui._package, out)
        assert events[-1]["type"] == "end" and [e["type"] for e in events].count("error") == 1, events
        assert not list(out.glob("wtx_results_*")), "A partial zip stayed behind."
        # The session cannot start: a file stands where the output folder should be. No log exists.
        blocked.write_text("not a folder", encoding="utf-8")
        events = [e for e in run(app, boom, blocked) if e["type"] != "log"]
        assert [e["type"] for e in events] == ["error", "end"] and events[0]["error"], events
        assert app.session is None
        app._close()


def find_label(widget, var):
    """Return the ttk label that shows the Tk variable var."""
    for child in widget.winfo_children():
        if child.winfo_class() == "TLabel" and str(child.cget("textvariable")) == str(var):
            return child
        found = find_label(child, var)
        if found is not None:
            return found
    return None


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
        app = App(root)
        root.update()
        assert root.title() == TITLE, root.title()
        tabs = [app.tabs.tab(t, "text") for t in app.tabs.tabs()]
        assert tabs == ["Download", "Advanced"], tabs
        labels = sorted(b.cget("text") for b in app.buttons)
        assert labels == sorted(["Open results folder"] * 2 + ["Test connection", "Download", "Package to send",
                                                              "Decode", "Decrypt"]), labels
        # A long status text must wrap. It must not make the window wider.
        label = find_label(root, app.status)
        wrap = int(str(label.cget("wraplength")))
        width = root.winfo_reqwidth()
        app.status.set("The download failed. " + "A long message with many words. " * 100)
        root.update()
        assert 0 < wrap < 1000 and label.winfo_reqwidth() <= wrap + 16, (wrap, label.winfo_reqwidth())
        assert root.winfo_reqwidth() <= max(width, wrap + 80), (width, root.winfo_reqwidth())
    finally:
        root.destroy()
    return None


CHECKS = [
    check_session_log_and_metrics,
    check_download_ok,
    check_download_completeness,
    check_download_error,
    check_download_disk_precheck,
    check_discover,
    check_decode_real_lap,
    check_decode_folder_best_effort,
    check_decode_refuses_output_inside_input,
    check_decode_file_beside_output,
    check_decode_empty_path,
    check_decrypt_real_lap,
    check_decrypt_refusals,
    check_decrypt_crc_failure,
    check_package_results,
    check_package_zip_name,
    check_package_foreign_files,
    check_package_failure_leaves_no_zip,
    check_gui_config,
    check_gui_progress_text,
    check_gui_confirm_gate,
    check_gui_field_checks,
    check_gui_beacon_text,
    check_gui_ip_scope,
    check_gui_arm_dialog,
    check_gui_test_job,
    check_gui_session_reuse,
    check_gui_input_scope,
    check_gui_decrypt_output,
    check_gui_test_ip_mismatch,
    check_gui_download_failure_text,
    check_gui_worker_errors,
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
