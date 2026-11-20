"""This module holds the headless session for one WinTAX download and decode run.

Each RunSession writes a DEBUG log and a metrics file under its output folder.
The GUI calls this class. The module imports no GUI toolkit.
This module reuses the protocol and decode code in src/ without change.
"""
import contextlib
import io
import json
import logging
import os
import platform
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
import traceback
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

# python -I does not add the script folder to sys.path. Add the sibling src/ folder.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import decrypt_ztx  # noqa: E402
import wtx_client  # noqa: E402
import wtx_decode  # noqa: E402

from . import __version__  # noqa: E402

# ponytail: a sample blob is 90 MB and the size is unknown before the SET. Raise the floor if blobs grow.
MIN_FREE_BYTES = 1 << 30
# The results zip holds only files with these names. A file that a user put in these folders stays out.
ZIP_GLOBS = ("logs/wtx_*.log", "logs/run_*.json", "download/download_raw_*.bin", "decoded/**/summary.csv")
# The hint after a failed download. The cause comes from the exception.
DOWNLOAD_HINT = "Check the cable, the ECU IP and the ECU power. Close WinTAX if it runs."


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _stamp():
    return time.strftime("%Y%m%d_%H%M%S")


def _safe(text):
    """Return text that is safe in a file name. The IP field and the beacon are not trusted."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", text)


class _OneLine(logging.Formatter):
    """Write each record on one line. Newlines in a traceback become a vertical bar."""

    def format(self, record):
        return super().format(record).replace("\n", " | ")


class _Forward(logging.Handler):
    """Pass each record to a callback as a log event."""

    def __init__(self, fn):
        super().__init__(logging.DEBUG)
        self.fn = fn

    def emit(self, record):
        self.fn({"type": "log", "msg": self.format(record), "level": record.levelname})


def _lap_jobs(folder, out):
    """Return (cableData.ztx, output folder) for each lap under a folder.

    A repeated lap name gets a suffix: Lap, Lap_2, Lap_3.
    """
    jobs, used = [], set()
    for ztx in sorted(folder.rglob("cableData.ztx")):
        base = ztx.parent.name
        name, k = base, 1
        while name in used:
            k += 1
            name = f"{base}_{k}"
        used.add(name)
        jobs.append((ztx, out / name))
    return jobs


def _check_disk(folder):
    """Raise OSError when the folder cannot take a new file, or when the disk has less than MIN_FREE_BYTES free.

    Call this before the SET. A failure then leaves the ECU unchanged.
    """
    with tempfile.TemporaryFile(dir=folder) as probe:  # The file goes away at once.
        probe.write(b"0")
        probe.flush()
    free = shutil.disk_usage(folder).free
    if free < MIN_FREE_BYTES:
        raise OSError(f"The disk of the output folder has {free >> 20} MB free. "
                      f"The download needs at least {MIN_FREE_BYTES >> 20} MB.")


class RunSession:
    """A RunSession logs, records metrics, downloads, decodes and packages one run.

    Use it as a context manager, or call close() when the run ends.
    """

    def __init__(self, output_dir, ecu_ip=wtx_client.ECU_IP_DEFAULT, on_event=None):
        self.dir = Path(output_dir).resolve()
        self.ecu_ip = ecu_ip
        self.on_event = on_event
        self.metrics = {
            "app_version": __version__,
            "os": platform.platform(),
            "python": platform.python_version(),
            "frozen": bool(getattr(sys, "frozen", False)),
            "ecu_ip": ecu_ip,
            "started": _now(),
            "finished": None,
            "status": "ok",
            "phases": {},
            "download": {},
            "device": {},
        }
        # ponytail: file stamps have one-second resolution. Two sessions in one second share files.
        stamp = _stamp()
        logs = self.dir / "logs"
        for folder in (logs, self.dir / "download"):
            folder.mkdir(parents=True, exist_ok=True)
        self._metrics_path = logs / f"run_{stamp}.json"
        # Each session uses its own logger, so handlers of one run do not reach the next run.
        # ponytail: the logging registry keeps each logger. Fine for one run per click.
        self.log = logging.getLogger(f"wtxapp.run.{uuid.uuid4().hex}")
        self.log.setLevel(logging.DEBUG)
        self.log.propagate = False
        fmt = _OneLine("%(asctime)s %(levelname)s %(message)s")
        file_handler = logging.FileHandler(logs / f"wtx_{stamp}.log", encoding="utf-8")
        forward = _Forward(self._emit)
        for handler in (file_handler, forward):
            handler.setFormatter(fmt)
            self.log.addHandler(handler)
        m = self.metrics
        self.log.info("The environment is app=%s os=%s python=%s frozen=%s ecu_ip=%s started=%s.",
                      m["app_version"], m["os"], m["python"], m["frozen"], ecu_ip, m["started"])
        self._write_metrics()

    def _emit(self, event):
        """Pass one event to on_event. A callback error does not stop the run."""
        if self.on_event is None:
            return
        try:
            self.on_event(event)
        except Exception:
            # The callback owns its errors. Do not log here: the log calls this method again.
            pass

    def _write_metrics(self):
        self._metrics_path.write_text(json.dumps(self.metrics, indent=2), encoding="utf-8")

    def _error(self, msg, exc=None, hint=""):
        """Record a failed step. Set the status and the error, then log it and emit an error event.

        The event holds the message, the hint for the user, and the cause when exc exists.
        The log keeps the cause and the traceback.
        """
        kind, text = (type(exc).__name__, str(exc)) if exc else ("Error", msg)
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)) if exc else ""
        self.metrics["status"] = "error"
        self.metrics["error"] = {"type": kind, "msg": text, "traceback": tb}
        self.log.error(f"{msg} ({kind}: {text})" if exc else msg, exc_info=exc)
        self._emit({"type": "error", "msg": msg, "error": f"{kind}: {text}" if exc else "", "hint": hint})
        self._write_metrics()

    @contextlib.contextmanager
    def _phase(self, name):
        """Time one phase. Emit a phase event at the start and write the metrics at the end."""
        rec = self.metrics["phases"][name] = {"t_start": _now()}
        t0 = time.monotonic()
        self._emit({"type": "phase", "phase": name, "msg": f"The {name} phase started."})
        try:
            yield
        finally:
            rec.update(t_end=_now(), dur_s=round(time.monotonic() - t0, 3))
            self._write_metrics()

    def discover(self, timeout=5.0):
        """Read the ECU identity beacon. This call does not write to the ECU.

        Return the Beacon, or None when no beacon arrives in time.
        """
        with self._phase("discover"):
            try:
                beacon = wtx_client.listen_beacon(timeout)
            except socket.timeout:
                self.log.info("The beacon did not arrive within %.1f s. Check that the ECU is on the network.",
                              timeout)
                return None
            except OSError as e:
                self._error("The beacon could not be read.", e)
                return None
        self.metrics["device"] = {"name": beacon.name, "alias": beacon.alias, "state": beacon.state,
                                  "ip": beacon.ip, "netmask": beacon.netmask}
        self.log.info("The ECU reports name=%s alias=%s state=%s.", beacon.name, beacon.alias, beacon.state)
        self._write_metrics()
        return beacon

    # This method sends the SET that arms the ECU (wtx_client.download). Call it on a user action only.
    def download(self):
        """Download the raw data blob from the ECU. Return the blob path, or None on failure.

        The ECU sends no end-of-stream flag, so the metrics mark the download as unconfirmed.
        The session checks the disk first. A full or unwritable folder stops the call before the SET.
        """
        blob = self.dir / "download" / f"download_raw_{_stamp()}.bin"
        box = {}
        try:
            _check_disk(blob.parent)
        except OSError as e:
            self._error("The download did not start.", e,
                        "The ECU was not changed. Free some disk space or choose another output folder.")
            return None

        def work():
            try:
                wtx_client.download(self.ecu_ip, str(blob))
            except Exception as e:  # the calling thread re-raises it below
                box["exc"] = e

        # The transfer runs in a worker thread, so this thread can poll the blob size.
        with self._phase("download"):
            t0 = time.monotonic()
            worker = threading.Thread(target=work, daemon=True)
            worker.start()
            self._poll(worker, blob)
            dur = time.monotonic() - t0
            try:
                if "exc" in box:
                    raise box["exc"]
                size, sha = wtx_client._sha256_file(str(blob))
                if size == 0:
                    raise RuntimeError("The ECU sent no data.")
            except Exception as e:
                self._error("The download failed.", e, DOWNLOAD_HINT)
                return None
            self.metrics["download"] = {
                # ponytail: bytes over the whole call, idle wait included. Time the last growth if that skews it.
                "bytes": size, "throughput_bps": size / max(dur, 1e-3),
                # The path is relative to the output folder. An absolute path holds the Windows user name.
                "out_file": blob.relative_to(self.dir).as_posix(), "sha256": sha,
                # ponytail: wtx_client.download gives no clean-end flag. Add a check when the ECU can confirm one.
                "completeness": "unconfirmed",
            }
        self.log.info("The download ended: %d bytes, sha256 %s.", size, sha)
        self.log.warning("The session cannot confirm that the download is complete. "
                         "The ECU sends no end-of-stream flag, so a download that ends on the idle timeout "
                         "can be partial. Confirm the data against the ECU.")
        self._emit({"type": "done", "msg": f"Download complete: {size:,} bytes. Not yet verified against the ECU.",
                    "path": str(blob)})
        self._write_metrics()
        return str(blob)

    def _poll(self, worker, blob):
        """Emit a progress event about every 0.2 s until the worker thread ends.

        The last event comes after the worker ends. A fast transfer therefore still reports progress.
        """
        last, t_last = 0, time.monotonic()
        while True:
            worker.join(0.2)
            alive = worker.is_alive()
            now = time.monotonic()
            try:
                size = os.path.getsize(blob)
            except OSError:  # The file appears when the transfer starts.
                size = 0
            rate = (size - last) / max(now - t_last, 1e-3)
            self._emit({"type": "progress", "msg": f"{size} bytes", "bytes": size, "rate_bps": rate})
            last, t_last = size, now
            if not alive:
                return

    def decode(self, input_path):
        """Decode one lap file, or every lap under a folder. Return the decoded folder, or None.

        Output goes to <output_dir>/decoded. A folder gives one subfolder per lap.
        The session logs a failed lap and skips it. It writes nothing inside input_path.
        """
        src = Path(input_path).resolve()
        out = self.dir / "decoded"
        with self._phase("decode"):
            if not str(input_path).strip():  # Path("") is the working folder. Do not walk it.
                self._error("The input path is empty. Choose a lap file or a folder.")
                return None
            # Refuse output inside an input folder. The input folder must stay unchanged.
            # An input file has no folder to protect: a new folder beside it is fine.
            if src.is_dir() and out.resolve().is_relative_to(src):
                self._error("The decode output folder is inside the input folder. Choose another output folder.")
                return None
            if src.is_dir():
                jobs = _lap_jobs(src, out)
            elif src.suffix.lower() == ".ztx":
                jobs = [(src, out)]
            else:
                jobs = []
            done = 0
            for ztx, dest in jobs:
                try:
                    self._decode_lap(ztx, dest)
                    done += 1
                except (Exception, SystemExit) as e:  # wtx_decode calls sys.exit on a bad lap
                    self.log.error("The session skipped lap %s after an error (%s: %s).", ztx, type(e).__name__, e,
                                   exc_info=e)
            if not done:
                self._error(f"The session decoded no lap from {src}. Check the path.")
                return None
        self._emit({"type": "done", "msg": f"The session decoded {done} of {len(jobs)} lap(s).", "path": str(out)})
        return str(out)

    def decrypt(self, input_path):
        """Decrypt one cableData.ztx file. Return the decrypted folder, or None.

        Output goes to <output_dir>/decrypted. The session writes nothing in the folder of the input file.
        """
        src = Path(input_path).resolve()
        out = self.dir / "decrypted"
        with self._phase("decrypt"):
            if not str(input_path).strip():  # Path("") is the working folder.
                self._error("The input path is empty. Choose a cableData.ztx file.")
                return None
            if src.is_dir():
                self._error("Decrypt needs one cableData.ztx file. Choose a file, not a folder.")
                return None
            if src.suffix.lower() != ".ztx":
                self._error("Decrypt needs a .ztx file. Choose a cableData.ztx file.")
                return None
            if out.resolve().is_relative_to(src.parent):  # The data folder must stay unchanged.
                self._error("The decrypt output folder is inside the folder of the input file. "
                            "Choose another output folder.")
                return None
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                ok = decrypt_ztx.decrypt_ztx(src, out, decrypt_ztx.PASSWORD)
            self.log.info("The decrypt table follows: %s", buf.getvalue())
            if not ok:
                self._error("At least one member of the file failed the CRC check. The file can be damaged.")
                return None
        self._emit({"type": "done", "msg": f"The session decrypted {src.name} to {out}.", "path": str(out)})
        return str(out)

    def _decode_lap(self, ztx, dest):
        """Decode one lap into dest. Keep the check table in the log when a check fails."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = wtx_decode.main(ztx, dest)
        if rc:
            self.log.warning("Decode checks failed for %s. The decoder still wrote the files. "
                             "The check table follows: %s", ztx, buf.getvalue())
        else:
            self.log.info("The session decoded %s to %s.", ztx, dest)

    def package_results(self):
        """Zip the logs, the raw downloads and the decoded summaries that this app wrote. Return the zip path.

        The zip holds only files that match ZIP_GLOBS. The zip name holds the beacon alias, else the ECU IP.
        The user sends this zip back.
        Raise OSError when the zip cannot be written. The call then leaves no partial zip.
        """
        self._write_metrics()
        # ponytail: the beacon has no serial field. The zip name uses the beacon alias, else the ECU IP.
        tag = self.metrics["device"].get("alias", "").strip() or self.ecu_ip
        zip_path = self.dir / f"wtx_results_{_safe(tag)}_{_stamp()}.zip"
        part = zip_path.with_name(zip_path.name + ".part")
        files = sorted(p for g in ZIP_GLOBS for p in self.dir.glob(g) if p.is_file())
        try:
            with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as zf:
                for p in files:
                    zf.write(p, p.relative_to(self.dir).as_posix())
            part.replace(zip_path)
        finally:
            with contextlib.suppress(OSError):
                part.unlink()  # The rename removed it. After a failure this removes the partial zip.
        self.log.info("The session packed %d file(s) to %s.", len(files), zip_path.name)
        return str(zip_path)

    def close(self):
        """Write the final metrics and close the log handlers."""
        if self.metrics["finished"] is not None:
            return
        self.metrics["finished"] = _now()
        self.log.info("The run closed with status %s.", self.metrics["status"])
        self._write_metrics()
        for handler in list(self.log.handlers):
            self.log.removeHandler(handler)
            handler.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is not None:
            self._error("The run stopped with an error.", exc)
        self.close()
