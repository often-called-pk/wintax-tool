# WinTAX ECU Downloader GUI: app plan

Goal: a GUI app a non-technical person runs to download the recorded data off
their own Marelli F4 ECU over Ethernet. Fields for ECU IP, input folder, output
folder. Deep logs and performance metrics. A one-click results zip to send back
to the project owner for inspection. Distributed worldwide as a Windows exe.

## Reuse (do not fork the logic)

- `src/wtx_client.py`: `download(ecu_ip, out_path)` (runs the control trigger,
  then the credited stream), `listen_beacon(timeout)` -> `Beacon` (read only, no
  ECU write), `ECU_IP_DEFAULT`, `ControlSession`, `ProtocolError`.
- `src/wtx_decode.py`: `main(ztx_path, out_dir)` (decode one stored lap).
- `src/decrypt_ztx.py`: `decrypt_ztx(ztx_path, out_dir, password)`, `PASSWORD`.

The GUI and session layer wrap these. They never reimplement the protocol or
the decode.

## Global constraints

1. Lean (ponytail). GUI is Tkinter, stdlib only. No new pip dependency. Logging,
   json, zipfile, threading are stdlib.
2. No em dash anywhere (code, comments, docs). Hook-enforced.
3. Nothing leaves the user machine automatically. "Send back" = a local zip the
   user emails or uploads. No network egress except to the ECU.
4. `download()` sends a SET to the ECU and stops its logger while it runs, so it
   runs only on an explicit user click. `discover()` (beacon listen) is read only
   and may run freely.
5. Never write into the input data tree. All output goes under the user output
   folder.
6. Logs are two kinds: a human-readable per-run log file, and a machine-readable
   `run.json` of metrics for later aggregation across many users.
7. No ECU on this box. All tests are offline: fakes or the packet-capture
   stand-in. A live run is the user's job.
8. Frozen-exe aware: read bundled resources via `sys._MEIPASS`, write only under
   the user-chosen output folder. Target Python 3.9+.

## Tasks

### G1: session core (headless)

Files: `wtxapp/__init__.py` (`__version__`), `wtxapp/session.py`, `tests/test_app.py`.
No Tk. A `RunSession(output_dir, ecu_ip, on_event)` that wraps discover, download,
decode, and package_results, with the run logger and the metrics `run.json`.
Events go to `on_event(dict)` for the GUI and to the log file. See `.sdd/g1-brief.md`.

### G2: Tkinter GUI

Files: `wtxapp/gui.py`, `wtxapp/__main__.py`, GUI-logic tests appended to
`tests/test_app.py`. Fields: ECU IP (default), input path, output path. Buttons:
Test connection (discover), Download, Decode, Open results folder, Package to
send. A live log pane, a status line, a progress bar, a metrics summary. Long
operations on a worker thread, events marshalled to the UI thread by a queue.
A small config.json remembers the last IP and folders. Uses `RunSession`.

### G3: packaging

Files: `wtxapp.spec` (PyInstaller, one-file windowed, bundles numpy and scipy),
`build_windows.bat`, `.github/workflows/build-windows.yml` (build the exe on a
Windows runner, upload the artifact). The exe is built on Windows. This box
writes and lints the spec only.

## Docs (owner writes, after the build)

`docs/USER_GUIDE.md` (one page, layman: set IP, pick folder, download, send the
zip back), `docs/BUILD.md` (how to produce the exe).

## Model roles

Haiku implements each task. Sonnet reviews, tests, and fixes. Fable fixes what
Sonnet cannot. Opus orchestrates and does the final whole-branch review.
