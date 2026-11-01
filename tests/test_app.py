#!/usr/bin/env python3
"""Headless checks of wtxapp/session.py. Run them without an ECU.

The download and beacon calls are stand-ins. The decode checks read the stored
test lap (TESTLAP) and decode it into a temporary folder.

Run with: python3 -I tests/test_app.py
Prints PASS per check. Exits 1 on the first failure.
"""
import hashlib
import json
import os
import shutil
import socket
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # the wtxapp package
sys.path.insert(0, str(ROOT / "src"))  # the protocol and decode modules

import wtx_client  # noqa: E402
from wtxapp.session import RunSession  # noqa: E402

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
]

if __name__ == "__main__":
    for check in CHECKS:
        try:
            check()
        except Exception as e:  # any failure ends the run with exit 1
            print(f"FAIL {check.__name__}: {type(e).__name__}: {e}")
            sys.exit(1)
        print(f"PASS {check.__name__}")
