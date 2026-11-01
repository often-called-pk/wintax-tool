# Final whole-branch review: feat/app-session-core (eb65d83..400721f)

Reviewer: Sonnet, report only. No repo file edited except this one. Scratch harnesses live in /tmp/finalrev (outside repo).
Not tested: real Windows, real ECU, real exe. Everything about those is inferred and marked so.

## Verdict

NOT safe to hand to non-technical users worldwide as is. Safe for an owner-supervised pilot on known ECU firmware once the gates below pass.

App code is sound. Rules hold: no ARM without confirm, discover read only, input tree never written, no secret in zip/log/run.json, no widget touched off UI thread, no hang found. What blocks: one process gate (download path never ran on a live ECU, on the real exe), three small code traps (Input box blocks Download, ECU armed before disk check, zip sweeps foreign files), one docs gap that decides first-run success. Code fixes are a few lines each. Live run is the long pole.

Gates before wide send:
- G1 owner runs final Windows exe on a bench ECU (C1).
- G2 fix I1. G3 fix I2. G4 fix I3 (docs). G5 fix I4 and I6. I5 strongly advised.
- Everything else: ship and defer.

## Evidence (what I ran)

- `python3.13 -B -I tests/test_app.py` 21 PASS (HOME isolated). `tests/test_wtx.py` 3 PASS.
- Real Tk loop under xvfb, `root.tk` wrapped to record any Tk call off main thread. Test, Download, Package with fakes: 0 off-thread calls, statuses correct.
- GUI -> RunSession -> REAL `wtx_client.download` + REAL `BeaconWatcher` vs loopback stand-in ECU, py3.13 and py3.14: blob byte exact, ended by idle timeout at 10.0 s, `completeness: unconfirmed`, no thread errors.
- Failure runs through real GUI:

| case | time | what user sees |
|---|---|---|
| Test, no ECU | 5.3 s | "No ECU found." |
| Download to dead IP | 10.4 s | status "The download failed."; metrics "0 bytes, 0.0 kB/s, 10.1 s elapsed"; reason only in log pane, traceback as one long line |
| output dir read-only | 0.3 s | "The run stopped. PermissionError: [Errno 13] ... 'ro/out'" |
| output path is a file | 0.3 s | "The run stopped. NotADirectoryError ..." |
| decode missing file | 0.3 s | "The session decoded no lap from <path>. Check the path." |
| decode empty input | 0.3 s | "The input path is empty. Choose a lap file or a folder." |
| package, disk full (patched) | 0.3 s | "The run stopped. OSError: [Errno 28] No space left on device" |

- Close window during download: exit code 0, no hang. run.json `finished: null`, `download: {}`, partial 8192 B blob left.
- Zip audit (fake download, real lap decode, bad lap): entries `logs/*.log`, `logs/run_*.json`, `download/*.bin`, `decoded/summary.csv`. Scan for `K3z5GjD7` and a planted env secret: 0 hits. Absolute paths present (see I4).
- Frozen Linux build, G3 venv, repo spec's pathex + hiddenimports, entry imports `wtxapp.gui` then `RunSession.decode` on the real lap: `lap_decoded.mat` + 187 CSV written, zip written. Audit hook: 0 writes under `sys._MEIPASS`; writes only to output folder plus one scratch file in OS temp.
- GitHub Actions tags exist today (API): checkout v7.0.1, setup-python v7.1.0, upload-artifact v7.0.2. Workflow read by hand: valid, least privilege.
- Simulated ENOSPC on blob (RLIMIT_FSIZE) vs stand-in ECU: SET frame reached ECU before the write failed (I2).
- Planted foreign files in `logs/` and `download/`: both swept into zip (I4). Package write failure leaves corrupt zip (M8).
- Long error text grew window to 1298 px on a 1280 px screen (M9).

## Findings

### Critical (release gate, not a code defect)

**C1. Live download path never ran on a real ECU. First live exposure would be end users.**
Where: src/wtx_client.py:342-404 (unchanged, not in diff), wtxapp/session.py:181-224, README Limits.
Code validated only by pcap replay and loopback stand-in. App sends SET, ECU logger stops. Unknowns that only live run answers:
- a. Windows only (inferred): UDP `recv` after `sendto` to a closed port raises ConnectionResetError (WSAECONNRESET). Loop catches only `socket.timeout` (wtx_client.py:371). If ECU closes :40000 at end, or answers ICMP unreachable, the credit resend (wtx_client.py:377, 400) can turn a complete download into "The download failed." Blob stays on disk, run.json says error. Likelier when firewall blocks beacon (idle path resends credits). Linux tests cannot show it.
- b. SET/GET constants (wtx_client.py:69-72) captured from one ECU: serial H43001J58F200923, fw 14.3.3.0, ABS 3012, WinTAX 22.10.6.6 (WIRE_PROTOCOL.md:15-17). GET_VERSION body appears to carry that version tuple. No firmware check before SET. Beacon has no firmware field. Other teams may run other firmware.
- c. ECU state after armed-then-failed transfer unknown. Logger may stay stopped until power cycle. Guide and error text silent.
- d. End detection is idle 10 s or beacon-left-Download. Completeness cannot be confirmed (known).
Gate: owner runs final exe on bench ECU: Test, Download (confirm), compare size/sha with WinTAX download of same run, check logger resumes, watch firewall prompt, decode one lap on the exe. Then pilot 2-3 teams on known firmware. If (a) shows: catch ConnectionResetError in recv loop, or in `RunSession.download` treat it as end-of-stream when size > 0.

### Important (fix before distribution, all cheap)

**I1. Input box blocks Test connection and Download (false positive).**
Where: wtxapp/gui.py:253 (`_inputs` runs `check_output(out, Input)` for every button), gui.py:59-72.
Proof: ran `check_output`. Input = file on Desktop, output = new folder on Desktop (USER_GUIDE example, line 27): REFUSED "The output folder is inside the input folder." Input = missing folder beside output: REFUSED (non-dir input makes tree = its parent). Input persists in ~/.wtxapp.json, so next launch the first click (Test connection) is refused over a box user forgot. Guide never says clear it. Layman is stuck. Changing output folder to escape splits logs/blob across folders, zip then lacks the download.
Fix: run overlap check only in `on_decode` and only when Input exists. Keep session.py:260 guard as backstop. Message should name the Input box.

**I2. Download arms the ECU before it knows the disk can hold the blob.**
Where: session.py:186-200 -> wtx_client.py:365 (SET) precedes :367 (open blob). No free-space or write probe before.
Proof: stand-in ECU + write failure on blob: SET reached ECU, then OSError. Real full disk gives same order. Result: ECU armed, logger stopped, nothing saved. Read-only folder already fails early (RunSession init, measured), only full disk slips through.
Fix: before confirm/arm, `shutil.disk_usage(download dir).free` >= floor (sample blob 90 MB, suggest 1 GB), refuse with plain text before any packet.

**I3. USER_GUIDE misses first-run facts that decide success; one instruction is wrong.**
Where: docs/USER_GUIDE.md lines 4, 9-14, 43, 45, 52, 60, 78; docs/BUILD.md:46.
- a. :78 "Right-click the exe and select Run" is wrong. Unsigned exe shows SmartScreen "Windows protected your PC": More info, then Run anyway. Hits every user. Say it before they see it.
- b. Windows Firewall prompt on first Test (app listens UDP 30001). Tick Private and Public (direct cable network is usually Public, moderate confidence), Allow. If refused, Test says "No ECU found" with good cable. Add to Common problems.
- c. PC adapter must sit on 192.168.111.x as for WinTAX (capture PC .75; beacon goes to 192.168.111.255, WIRE_PROTOCOL.md:12-14, 52). Inferred: else no beacon. Guide says only "connect cable".
- d. Zip ~100 MB (sample blob 90 MB). ":60 email or file share" fails on email limits. Say file share. BUILD.md:46: mail gateways often block .exe.
- e. Mismatches: ":52 Browse next to Input" (buttons are Browse file / Browse folder); ":45 shows done" (app says "The download finished: N bytes."); ":43 log shows data rate" (metrics line shows it).
- f. Add: car stopped before Download; one output folder per car and Test connection before each Download (M5); leave Input empty unless decoding (I1); logs hold folder paths incl. Windows user name (I4); if ECU does not log after a failed download, power cycle (owner confirm on bench); Windows 10/11 64-bit; first launch is slow, wait, do not double-click (M11).
- g. USER_GUIDE.md is Markdown. Notepad shows raw markup. Ship PDF or TXT.

**I4. Package zips every file under `logs/` and `download/`, not only files the app wrote.**
Where: session.py:304-305. Proof: planted `tax_return_2025.pdf` and notes in those folders, both in zip. User who picks a folder with existing `logs/` or `download/` sends unrelated files. Guide says "empty folder", nothing enforces it.
Related: logs and run.json carry absolute paths (session.py:214 `out_file`, :292, :309, tracebacks): Windows user name and input tree names (driver/team folders). Not a secret, but unexpected. Say so in guide.
Fix: whitelist `logs/wtx_*.log`, `logs/run_*.json`, `download/download_raw_*.bin`, `decoded/**/summary.csv`. Show file count and MB in status after Package.

**I5. Test connection ignores the IP box.**
Where: gui.py:98-104, session.py:160-178 (`listen_beacon` takes no IP; `beacon.ip` never compared; device record omits ip, netmask).
Effect: wrong IP still shows "The ECU is on the network". Download then fails after ~10 s with only "The download failed." (measured). PC off ECU subnet can show OK via broadcast while unicast fails.
Fix: if `beacon.ip` != IP box: "The ECU reports IP x. Change the ECU IP box." Store ip and netmask in device.

**I6. Failed download gives no cause or next step. Success text overclaims.**
Where: gui.py:320-321 shows `msg` only; cause sits in `ev["error"]` (session.py:145), unused by GUI. session.py:222 says "finished" while :219-221 says completeness cannot be confirmed.
Fix: map error type to hint (timeout: cable, ECU power, IP box, Windows Firewall; ProtocolError: ECU answered oddly, send the zip). Reword success: "The download ended: N bytes. The app cannot confirm it is complete."

### Minor (ship, defer)

- **M1 Throughput over whole call** (session.py:213, gui.py:110-112). Idle 10 s tail included: 4096 B showed "0.4 kB/s, 10.0 s elapsed". Skews owner perf aggregate. Fix: `_poll` stamps last growth, divide by t0..last growth.
- **M2 Sticky and overwritten record** (session.py:98, 142-143, 151, 211). status never resets, error keeps last, phases and download dict replaced per repeat. run.json can say status=error AND a complete download. Aggregate on phases/download, not status. Later: attempts list.
- **M3 Zip run.json `finished: null`** (session.py:300 writes before close at :314). Zipped log lacks "packed" line. Tell owner.
- **M4 Close while busy** (gui.py:233-239). No final record, partial blob unrecorded, exit clean. Dialog silent that ECU may stay armed. No Cancel button. Fix dialog text now, Cancel later.
- **M5 Stale device identity** (gui.py:282-293, session.py:175, 302). Also not cleared when a later Test says "No ECU found". Fix: re-read beacon at Download start (read only), copy into `metrics["download"]`; or guide line.
- **M6 Frozen scipy**. Linux frozen run passes (Evidence). Windows DLL bundling still unproven. scipy import failure is silent: wtx_decode.py:46-49, 304-306; note goes to captured stdout, logged only on failed checks (session.py:289-292). Add `.mat` exists check after `wtx_decode.main`, log ERROR. First-exe decode step in BUILD.md stays a release gate.
- **M7 Build/CI**. Pip deps unpinned in workflow and .bat: shipped exe is not tested exe. No post-build smoke, no tests in CI (`test_app.py` needs TESTLAP on owner box, tests/test_app.py:36; `test_wtx.py` is hermetic and could run). No published exe checksum. Pin once first good exe is blessed. If AV flags onefile, try onedir.
- **M8 Failed package leaves corrupt zip** under final name (session.py:306-308). Fix: write `.part`, rename.
- **M9 Status label has no wraplength** (gui.py:177). Long error grows window past small screens.
- **M10 Pasted path with quotes** ("Copy as path") not stripped (gui.py:66). Inferred WinError 123 on Windows.
- **M11 No single-instance guard.** Slow onefile start invites double launch: two windows, both on UDP 30001, shared config.
- **M12 Decode details.** Non-path failures end as "Check the path." (session.py:277-279). Single-file decodes all write `decoded/` root and overwrite each other, zip carries one summary.csv (session.py:264-268, 305). `str(Path(""))` is "." so API users with a Path bypass empty check (:255); GUI passes str, fine.
- **M13 Timestamps naive local** (session.py:33). Add UTC offset for worldwide aggregation.
- **M14 Job exceptions outside RunSession not recorded** (gui.py:271-272). Package OSError or session-create failure shows only on status line, never in log or run.json. Owner never sees it.
- **M15 Repo and legal hygiene.** BUILD.md:23 "Push the repo to GitHub" must say private: tracked files hold vendor ZipCrypto password (src/decrypt_ztx.py, docs), ECU serial, dev paths with a driver name (wtx_decode.py:51, tests/test_app.py:36), .sdd notes. Exe embeds password and TEST_ZTX string too. Neither is in any output (scanned). Owner decides whether to ship vendor key. Add third-party notices (numpy, scipy, Tcl/Tk, PyInstaller).
- **M16 Log depth.** Logger is DEBUG but no debug lines; `wtx_client` logs nothing. Log cannot say how far control session got or whether end came from idle or beacon. Suggest size sample every ~5 s and beacon state before/after.
- **M17 Dev only.** `tk.Tk()` unguarded (gui.py:351-354); Windows always has a display. `BeaconWatcher` shadows `Thread._stop` (wtx_client.py:159). Ledger says "unused", but `download()` starts it every run (wtx_client.py:352). Harmless: nobody joins it, ran clean on 3.13 and 3.14. Per ledger would TypeError on join/is_alive under 3.9-3.12 (not tested here, no such Python on box). Keep exe on 3.13.

## Deferred list triage

| item | call | why |
|---|---|---|
| download completeness "unconfirmed" | ship and defer, with two changes | reword success text (I6); live run (C1) is the real answer |
| throughput over whole call | defer | M1, cheap later, skews metric only |
| status/error sticky | defer | M2, aggregate on phases/download |
| zip run.json finished=null | defer | M3, tell owner |
| close-while-busy no final record | defer, fix dialog text | M4 |
| failed download short message | FIX before distribution | I6, top failure path for laymen |
| stale device identity | defer + guide line | M5, 5-line fix exists |
| frozen scipy static only | defer, gate on first-exe decode | Linux frozen decode passes; add `.mat` check (M6) |
| exe not built, actionlint not run | defer, gate on first CI run + smoke | workflow reviewed by hand, tags exist (M7) |

Must fix before distribution from the deferred list: only the failed-download message (I6) plus the success wording. Rest is gate-by-process.

## Checked and OK

- SET only behind confirm. Chain: `on_download` -> `run_if_confirmed(..., is True)` (gui.py:88-93, 206-210) -> `_start` -> `_download` -> `RunSession.download` -> `wtx_client.download`. Dialog default No (gui.py:241-243). No other caller (grep). Cancel/close of dialog cannot arm.
- Discover read only: `listen_beacon` binds UDP 30001 and receives. `ControlSession` appears only inside `download`.
- Thread safety. Worker never touches a widget; `on_event` is `Queue.put`; Tk drains every 100 ms (gui.py:302-310). `self.session` written by worker, read by Tk only when `busy` is False (end event is last worker action). Bounded queue cannot deadlock at real event rates (about 5/s).
- Event flow. FIFO. `_worker` always posts "end" (gui.py:267-273), catches `Exception`; `SystemExit` from `wtx_decode` is caught inside `decode`.
- No network egress except UDP to ECU IP and broadcast listen. Config file holds IP and paths only.
- Input tree never written: output under `self.dir` only, guard at session.py:260, test passes.
- Frozen writes: output folder, home config (GUI), OS temp. Nothing under `_MEIPASS`.
- Spec: `console=False` safe, `print` into `redirect_stdout` buffer, PyInstaller null-writes stdout/stderr.
- `src/` untouched in diff.

## Fix order (smallest first)

1. I1 (about 3 lines). 2. I4 (about 4 lines). 3. I2 (about 6 lines). 4. I5 and I6 (about 15 lines). 5. I3 docs. 6. M6 `.mat` check. 7. Build exe, then C1 live run on bench ECU. 8. Pilot. Rest after.
