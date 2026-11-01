# G2 review: Tkinter GUI

Verdict: APPROVED_WITH_FIXES.
Fix commits on feat/app-session-core, code + tests only (`wtxapp/gui.py`, `tests/test_app.py`):
- abba833 round 0: ECU IP checked only for Test connection and Download (+68/-7).
- 1712e39 round 1, coordinator ruling: one RunSession per output folder, plus dialog default No, final metrics line, Test error text (+156/-18).
This file, .sdd/progress.md, g2-report.md, g3-brief.md, docs/USER_GUIDE.md not committed by me.
session.py and src/ untouched (git diff vs 32bf4da empty). No em dash in any touched file or commit message. No attribution lines.

## Tests

- Before round 0: `python3 -I tests/test_app.py` exit 0, 17 PASS. Implementer claim holds.
- After round 0: 18 PASS. After round 1: exit 0, 21 PASS (new `check_gui_arm_dialog`, `check_gui_test_job`, `check_gui_session_reuse`; `check_gui_ip_scope` changed).
- All four new or changed checks fail on the abba833 code for the right reason (offline jobs got the field text, no `default` option, `_test` said "No ECU found.", no `App.session`). They pass on 1712e39.
- No DISPLAY: window check re-runs under xvfb-run, PASS. No DISPLAY and no xvfb on PATH: window check prints SKIP, all others PASS (the new GUI checks build no window, never SKIP).
- `timeout 8 xvfb-run -a python3 -m wtxapp` (scratch HOME): exit 124, no traceback. Config file not written on start.
- `tests/test_wtx.py` exit 0 (run after round 0, src unchanged since).

Probes (scratch in /tmp/wtx_review, not committed), xvfb, scratch HOME:
- Thread guard: wrapped `Variable.set/get`, `Text.insert/delete/see`, `Misc.after/update/configure`, `Progressbar.start/stop`, `Widget.state`, logged any call off main thread. Flows: test (no ECU, beacon, OSError), download (fake, progress), decline, failure, decode (real lap), blank-input decode, package, package OSError, output is a file, close while busy (No), config save. 22 checks green, 0 off-thread calls, re-run after round 1.
- Real window, real `Tk`: Test (fake beacon) then Download (idle end) then Package then close. Final metrics line `4,096 bytes, 5.8 kB/s, 0.7 s elapsed`. Zip `wtx_results_F4_106_*.zip`. One `run_*.json`: status ok, device name SRG_927 alias F4_106, download bytes 4096, `finished` set. Log has "The run closed".
- Real Tk dialog: `askyesno(default="no")` gives the No button `-default active`, Yes `normal`.
- Real `root.mainloop()` run: Decode with bad IP "ecu.local" runs and writes `decoded/summary.csv`.
- Log pane cap: 6200 appends keep 4999 newest lines.

## Spec and constraints

Brief fields, buttons, log pane, status, indeterminate bar, metrics line, threading, config, tests: met. G1 notes (output-not-in-input, `ipaddress` IP check, indeterminate bar, enqueue-only callback, `OSError` from `package_results` caught, session `discover()`): met.
1 stdlib Tk only: ok. 2 session/src unchanged: ok. 3 no em dash: ok. 4 Tk thread safe: ok (queue.put only in worker, `after(100)` drain reschedules first, `end` event always sent, buttons back). 5 indeterminate bar, `Exception` caught in worker: ok. 6 config `~/.wtxapp.json`, read and write wrapped: ok.
Memory: queue bounded 10000, log pane capped 5000, session closed on folder change and on idle close. No `__file__` or cwd paths, nothing under `sys._MEIPASS`. Accepted as is: two Browse buttons, close-while-busy confirm.

## Fixed round 0 (abba833)

`_inputs(need_ip)`: Test connection and Download check the IPv4 field. Decode and Package do not, so a bad ECU IP no longer blocks them. Open results never checked it. Output and input-nesting checks unchanged for all. Test `check_gui_ip_scope` pins it with bad IPs "ecu.local", "999.1.1.1", "".

## Fixed round 1 (1712e39)

1. One session per output folder. `App.session`, built on the worker by `_session_for(out, ip)`. Same folder reuses it. A new folder `close()`s the old session (final record written) and builds a new one. So the beacon from Test connection stays in `metrics["device"]`: the Download record and the zip name (`wtx_results_<alias>_<stamp>.zip`) use it. A new folder starts with no identity. `closing()` removed. `_close()` (OSError suppressed) also runs in `on_close` when no job runs (final record written). It is skipped when busy.
   - Output-inside-input guard and IP scope unchanged. Offline jobs now get `None` for the IP. Test and Download set a valid window IP on `s.ecu_ip` and `metrics["ecu_ip"]` before the job. A session started by an offline job begins with the default IP.
   - Reuse re-makes `logs/` and `download/`. Reason: per-press sessions did this every time. A user can delete a folder between jobs, and then `wtx_client.download` arms the ECU and only afterwards fails to open the blob.
2. Arming dialog `default=messagebox.NO`.
3. Final metrics line. `_download` returns `summary` from the recorded `throughput_bps` (whole-call average), `bytes` and the phase `dur_s`. The `end` event puts it on the metrics line. Live progress lines unchanged. A failed download keeps the last live line.
4. Test connection error text. `_test` compares `metrics["error"]` by identity before and after `discover()`, so the sticky error of an earlier call does not count. New error gives "The ECU search failed. OSError: address in use". A timeout still gives "No ECU found.".

Tests: `check_gui_session_reuse` runs Test (fake beacon), deletes `download/`, Download (idle end, ECU IP 10.0.0.7), Package through `App._worker`. It asserts the same session object, the IP follows only the ECU jobs, the zip name holds the alias, ONE `logs/run_*.json` in the zip with device alias and download bytes, no error events, last progress rate 0.0 but `summary` shows the average, `_apply` puts it on the metrics line, a new folder gives a new session and closes the old one, and the new session has no device and the default IP. Also `check_gui_arm_dialog` and `check_gui_test_job` (OSError, then timeout after the error, then beacon).

## Known consequences of the ruling (not fixed)

- Identity stays until the next successful Test connection or a new output folder. Swap cars in one folder without a Test and the bundle carries the old identity. A failed Test does not clear it (clearing would drop it after a post-download re-test timeout). `ponytail:` marker in `_session_for`. Add a beacon read to Download if users swap cars.
- Windows: the session keeps `logs/wtx_*.log` open while the app runs, so Explorer cannot delete or move the output folder until the app closes. Not run on Windows.
- G1 stickiness now spans the whole session: one failed step leaves `status: error` and `error` in the run json after a good retry. `phases` keeps the latest record for each phase name. Detail stays in the log.
- The run json inside a zip is a snapshot: `finished` is null and the log has no "closed" line until the app closes. Close while busy writes no final record.
- Log header line shows the IP at session start. `metrics["ecu_ip"]` follows later.

## Not fixed (minor, unchanged)

- Other error events (download failed) still show only `msg` in the status. The reason is in the log pane.
- Input-vs-output nesting check runs for every action, as the brief says. A stale remembered Input can block Download when Output sits inside it. Message is clear.
- `end` event is not in a `finally`. A `BaseException` escaping a job would leave buttons disabled. Session catches `SystemExit` per lap, so no known path.
- Not testable here: Windows (`os.startfile`, Tk file dialog filter, real paths, file locks), frozen exe (G3), real ECU.

## Final test output

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
    PASS check_gui_ip_scope
    PASS check_gui_arm_dialog
    PASS check_gui_test_job
    PASS check_gui_session_reuse
    PASS check_gui_window
    exit=0
