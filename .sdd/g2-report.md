# G2 report: Tkinter GUI

Status: DONE_WITH_CONCERNS.
Commit: ac9e3c5 on feat/app-session-core (code + tests only).
Report not committed. Trail commit by orchestrator, like G1.

## Built

- `wtxapp/gui.py`: window, worker thread, queue drain, config memory. Pure helpers:
  `load_config`, `save_config`, `check_ip`, `check_output`, `progress_text`,
  `beacon_text`, `run_if_confirmed`, `open_folder`. Class `App`. `main()`.
- `wtxapp/__main__.py`: `from wtxapp.gui import main; main()`.
- `tests/test_app.py`: 6 checks appended after G1. Runner prints SKIP on skip result.
  Header docstring and imports extended. G1 checks untouched.

Window per brief:
- Title `WinTAX ECU Downloader 0.1.0`.
- ECU IP (default from `wtx_client.ECU_IP_DEFAULT`), Input (optional), Output folder.
- Buttons: Test connection, Download, Decode, Open results folder, Package to send.
- Status line, `ttk.Progressbar` indeterminate, metrics line (bytes, kB/s, s elapsed),
  read-only `ScrolledText` log.

Threading:
- Every session call runs in a worker thread. `RunSession` is built there too.
- `on_event` = `queue.Queue.put` (bounded, 10000). Tk drains every 100 ms via `root.after`.
- Buttons disabled while busy. Worker always puts an `end` event, also after an exception.
  Buttons come back.
- Worker catches `Exception`: covers the OSError from `package_results`. Shows error in status.

Download: confirm dialog first. `run_if_confirmed` arms only when confirm returns True.

Validation before any session (messagebox on fail):
- ECU IP: `ipaddress.IPv4Address`. No DNS.
- Output required.
- Output must not equal or sit inside input folder (input = folder, or file's parent).

Config: `~/.wtxapp.json`. Load on start. Save on each change (trace).
Missing, broken or wrong-shape file gives defaults. Write errors ignored.

Log pane keeps last 5000 lines (memory rule).

## Deviations and choices

1. Input has two Browse buttons: "Browse file" and "Browse folder". Brief said one Browse.
   One Tk dialog cannot pick both file and folder.
2. Validation runs for every action, Decode and Test included. Each action builds a session
   with IP and output. A bad IP blocks Decode too.
3. Close while busy asks confirm. Not in brief. Daemon worker would die with the window:
   partial download, no final metrics, ECU logger left stopped.
4. ECU IP accepts IPv4 only. Session uses AF_INET sockets. IPv6 rejected.
5. Package to send creates its own `RunSession`. Its run log and json go into `logs/`,
   so the zip holds them too.
6. Open results folder creates no session. No side effects.

## Tests

Command (repo root):

    python3 -I tests/test_app.py

Output (final run, after docstring edits):

    PASS check_session_log_and_metrics
    PASS check_download_ok
    PASS check_download_completeness
    PASS check_download_error
    PASS check_discover
    PASS check_decode_real_lap
    PASS check_decode_folder_best_effort
    PASS check_decode_refuses_output_inside_input
    PASS check_decode_empty_path
    PASS check_package_results
    PASS check_package_zip_name
    PASS check_gui_config
    PASS check_gui_progress_text
    PASS check_gui_confirm_gate
    PASS check_gui_field_checks
    PASS check_gui_beacon_text
    PASS check_gui_window
    exit=0

Windowed smoke: `check_gui_window` ran, not skipped. DISPLAY=:0 set, so it built the real
window in-process, ran `update()`, destroyed it. The xvfb path runs only when DISPLAY is unset.

Extra checks (not in repo):
- Launch: `timeout 6 xvfb-run -a python3 -m wtxapp`. Exit 124 (timeout ended mainloop).
  No traceback.
- Scratch flow `/tmp/wtx_gui_scratch/run_gui_scratch.py` under `xvfb-run`, real files
  (stored test lap), stand-ins for ECU calls. 43 PASS, `RESULT ALL PASS`. Covers: test with
  no ECU and with beacon; decode; inside-input refusal; confirm No (no arming); download
  (progress metrics, blob, completeness warning); download failure (buttons back);
  package (zip, folder open); package OSError (error text, buttons back); bad output path
  (job still ends); open folder (existing, missing); log cap keeps newest; close while busy
  (No keeps window); config saved and loaded; close idle destroys.
  Ran before three docstring-only edits. Headless tests rerun after: PASS.
- ste-lint on prose only (docstrings, comments, message strings extracted from gui.py):
  0 violations, 556 words. Lint on full file: 9 flags, all on code lines. Lint merges code
  tokens into the next sentence. Not fixed.
- Em dash scan (U+2014): none in the three files.
- Unused-import check (ast): none. Pyflakes not installed.

## Not tested

- Windows: `os.startfile`, real file dialogs, Windows path handling in `check_output`,
  frozen exe (G3).
- Real ECU. None on this box. Download uses the stand-in, as in G1.
- `open_folder` with a real file browser. Patched in scratch. `xdg-open` exists here,
  not exercised.
- Python 3.9 target: no 3.10+ syntax used, checked by reading. Only 3.14 ran.

## Concerns

- Two Browse buttons for Input (deviation 1).
- Validation blocks Decode on a bad IP (deviation 2).
- Test window check flashes a real window when run on a desktop session with DISPLAY set.
- Work tree shows `M .sdd/progress.md` and `?? .sdd/g3-brief.md`. Not mine. Not staged.
