# Task G1: session core (headless)

Build the headless core of the WinTAX ECU Downloader GUI. No Tkinter in this
task. The GUI (a later task) will sit on top of this.

## Files

- `wtxapp/__init__.py`: just `__version__ = "0.1.0"`.
- `wtxapp/session.py`: the `RunSession` class and its helpers.
- `tests/test_app.py`: the asserts below, runnable headless.

## Reuse, do not fork

Read these first and import them. Do not reimplement the protocol or the decode.
- `src/wtx_client.py`: `download(ecu_ip, out_path)` returns a count; also
  `listen_beacon(timeout=5.0)` -> `Beacon` (fields include ip, name, alias, state,
  and a firmware or serial string; inspect the dataclass), `ECU_IP_DEFAULT`,
  `ProtocolError`.
- `src/wtx_decode.py`: `main(ztx_path, out_dir)` decodes one stored `cableData.ztx`
  into `out_dir` (writes per-channel CSVs, `summary.csv`, a `.mat`).
- `src/decrypt_ztx.py`: `decrypt_ztx(ztx_path, out_dir, password)`, `PASSWORD`.

`wtxapp/session.py` lives in `wtxapp/`, so add the sibling `src/` dir to
`sys.path` from `__file__` before importing them (see how `src/wtx.py` inserts its
own dir). Keep that shim small.

## RunSession API

```
RunSession(output_dir, ecu_ip=wtx_client.ECU_IP_DEFAULT, on_event=None)
```

- `on_event`, if given, is called with one dict per event. Keys:
  `{"type": "log"|"progress"|"phase"|"done"|"error", "msg": str, ...}`.
  progress adds `"bytes"`, `"rate_bps"`, optional `"pct"`. phase adds `"phase"`.
  error adds `"error"`. on_event must never raise back into the session (wrap it).
- A per-run logger writes `<output_dir>/logs/wtx_<timestamp>.log` at DEBUG level,
  one line per record (timestamp, level, message). A second logging handler
  forwards each record to `on_event` as a `log` event. First log line is an
  environment header: app version, `platform.platform()`, python version,
  `getattr(sys, "frozen", False)`, the ecu_ip, and the start time.
- A metrics dict is written to `<output_dir>/logs/run_<timestamp>.json` with:
  `app_version, os, python, frozen, ecu_ip, started, finished, status`
  ("ok" or "error"), `phases` (name -> {t_start, t_end, dur_s}),
  `download` ({bytes, throughput_bps, out_file, sha256}),
  `device` (from the beacon: name, alias, state, firmware/serial if present),
  `error` ({type, msg, traceback}) when status is error. Write it at the end of
  each method and at close.

Methods:

- `discover(timeout=5.0) -> Beacon | None`: call `wtx_client.listen_beacon`.
  READ ONLY, no ECU write. Log the identity. Fill `metrics["device"]`. Return the
  beacon, or None on timeout (log it, not an error).
- `download() -> str | None`: blob path =
  `<output_dir>/download/download_raw_<timestamp>.bin`. Run
  `wtx_client.download(self.ecu_ip, blob)` in a worker thread; while it runs, poll
  `os.path.getsize(blob)` about 5 times a second and emit `progress` events with
  bytes-so-far and rate (delta bytes / delta t). Do NOT modify `wtx_client.download`
  (it is byte-validated). On success record bytes, sha256 (reuse
  `wtx_client._sha256_file` if present, else hashlib), throughput; emit `done`;
  return the blob path. Catch `OSError` and `wtx_client.ProtocolError`: log the
  error with traceback, set `metrics["status"]="error"` and `metrics["error"]`,
  emit an `error` event, return None (do not crash). Note in a comment: this sends
  the SET that arms the ECU, so it is a user-triggered action.
- `decode(input_path) -> str | None`: output under `<output_dir>/decoded`. If
  `input_path` ends with `.ztx`, call `wtx_decode.main(Path(input_path),
  <output_dir>/decoded)`. If it is a folder, walk it for `cableData.ztx` files and
  decode each into `<output_dir>/decoded/<lap_folder_name>`; log and continue past
  a lap that fails (best effort). Never write inside `input_path`. Return the
  decoded dir, or None if nothing decoded.
- `package_results() -> str`: zip everything under `<output_dir>/logs` and
  `<output_dir>/download`, plus `<output_dir>/decoded/**/summary.csv` if any, into
  `<output_dir>/wtx_results_<device-serial-or-ecu_ip>_<timestamp>.zip`. Use
  `zipfile`. Return the zip path. This is what the user sends back.
- `close()`: flush and write the final `run.json`, close handlers.

Support `with RunSession(...) as s:` (close on exit).

## Tests (tests/test_app.py, headless, assert-based, no framework)

Start with `import _bootstrap` only if that helper exists here; otherwise just set
`sys.path` to the repo root. Use a `tempfile.TemporaryDirectory()` as output_dir.
Monkeypatch where the ECU is needed. Asserts:

1. A RunSession writes a log file and a `run_*.json`; the json has the required
   top-level keys.
2. download: monkeypatch `wtx_client.download` with a fake that writes N known
   bytes to the blob path and returns a count. Assert the session reports
   `bytes == N`, a 64-hex sha256, `status == "ok"`, and that at least one
   `progress` event fired.
3. download error: monkeypatch `wtx_client.download` to raise `ProtocolError`.
   Assert `download()` returns None, `status == "error"`, and `metrics["error"]`
   has type and traceback. No exception escapes.
4. discover: monkeypatch `wtx_client.listen_beacon` to return a fake Beacon.
   Assert `metrics["device"]` is filled.
5. decode: on the real stored test lap
   `TESTLAP = "/home/pk/Downloads/pk-backup-d-drive/Motorsports/WinTAX ORECA BASIC APPLICATION-7/Data/047 - Arjun/F4_047/Run_713_R1_047/Lap_00002991_00927/cableData.ztx"`,
   assert `<output_dir>/decoded/summary.csv` (or a per-lap summary) exists and the
   input lap folder still has only its original files.
6. package_results: assert the zip exists and contains the log, the run.json, and
   the blob.

Run: `python3 -I tests/test_app.py`. Print PASS per check and exit non-zero on the
first failure.

## Done when

Tests pass, `wtxapp/session.py` imports no Tk, and the src modules are reused
unchanged.
