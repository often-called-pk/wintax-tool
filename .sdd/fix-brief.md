# Fix wave: final review findings

Branch feat/app-session-core. Fix in `wtxapp/gui.py`, `wtxapp/session.py`, and
`tests/test_app.py`. Do NOT touch `src/`. The wtx_client Windows reset item (C1a)
is a live-ECU bench gate, not fixed offline.

Keep every safety invariant: SET only behind the confirm, discover read only,
never write the input tree, no secret in the zip or log or run.json, no off-thread
Tk call, no write under sys._MEIPASS. Keep the 24 existing checks green and add
the tests named below.

## Important (fix all)

- I1 (gui.py around 253, `_inputs` around 59-72). The output-inside-input guard
  runs for every button and is too broad. The user guide example (a `.ztx` on the
  Desktop, a new output folder on the Desktop) is refused, a missing Input is
  refused, and a persisted Input blocks Test connection on the next launch. Fix:
  apply the nesting guard ONLY in the Decode action, ONLY when an Input path is
  set, and refuse only when the output folder is genuinely inside the input
  DIRECTORY (resolve both; a sibling folder is fine; an input that is a file does
  not nest the output). Test connection, Download, Open results, and Package must
  not consult Input at all. Add a test.
- I2 (session.py around 186-200). Download sends the arming SET before it knows
  the output disk can be written, so a full or unwritable output folder arms the
  ECU and then fails. Fix: before `download()` arms, confirm the download folder
  is writable and has free space (`shutil.disk_usage`), and fail early with a
  clear error if not. Add a test that simulates no space or an unwritable folder.
- I4 (session.py around 304-305, `package_results`). The zip sweeps every file
  under `logs/` and `download/`, so a foreign file a user dropped there is
  shipped, and the paths inside `run.json` are absolute, which leaks the Windows
  user name. Fix: zip only the files the app wrote (track them, or whitelist by
  name), and store paths in `run.json` relative to the output folder. Add a test:
  plant a foreign file and assert it is not zipped, and assert run.json paths are
  relative.
- I5 (gui.py around 98-104, Test connection). Discover ignores the ECU IP box, so
  a wrong IP still reports success and Download then fails. Fix: after discover,
  compare the beacon IP to the IP the user entered, and say clearly when they
  differ. Add a test.
- I6 (gui.py around 320-321, session.py around 222). A failed download shows only
  "The download failed." with no cause, and success shows "finished" though the
  app cannot confirm completeness. Fix: on failure show the error cause and a
  short hint (check the cable, the IP, the ECU power, close WinTAX). On success
  word it so it does not overclaim, for example "Download complete. Not yet
  verified against the ECU." Keep the full reason in the log. Add or adjust a test
  for the failure message carrying a cause.

## Minor (cheap, include)

- `package_results` must not leave a corrupt zip on failure. Write a temp file and
  rename it on success.
- The status label needs a `wraplength` so a long message does not grow the
  window.
- Wrap the worker so an exception OUTSIDE a RunSession call still reaches the log
  and an error event (gui.py around 271-272), not only a dialog.

## Rules

ponytail, no em dash, stdlib only. Run `python3 -I tests/test_app.py` (all green)
and `python3 -I tests/test_wtx.py` (still green; you did not touch src). Commit on
the branch, plain message, no attribution. Write what you changed per finding and
the test output to `.sdd/fix-report.md`. Return verdict, commit hash, a one-line
test summary, and anything unfixed. No subagents. Reply caveman.
