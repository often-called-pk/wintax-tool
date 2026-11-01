# Task G2: Tkinter GUI

Build the GUI that a non-technical person uses. It sits on the G1 session core.
Tkinter only (stdlib). No new pip dependency.

## Files

- `wtxapp/gui.py`: the window and its logic.
- `wtxapp/__main__.py`: `from wtxapp.gui import main; main()` so `python -m wtxapp` launches it.
- Append GUI tests to `tests/test_app.py` after the existing G1 tests.

## Depends on G1 (already built, do not change it)

`from wtxapp.session import RunSession`. API:
`RunSession(output_dir, ecu_ip=..., on_event=None)` with methods
`discover(timeout)` -> beacon or None (read only, safe), `download()` -> blob path
or None (sends the arming SET to the ECU), `decode(input_path)` -> decoded dir or
None, `package_results()` -> zip path, `close()`. `on_event(d)` is called with
dicts: types `log` (d["msg"]), `progress` (d["bytes"], d["rate_bps"], maybe
d["pct"]), `phase` (d["phase"]), `done`, `error` (d["error"]). Default ECU IP is
`wtx_client.ECU_IP_DEFAULT` ("192.168.111.254"); read it from the session or the
client, do not hardcode a second copy.

## Window

Title "WinTAX ECU Downloader" plus `wtxapp.__version__`.

Fields (labelled, with the current value shown):
- ECU IP: an Entry, pre-filled with the default.
- Input (optional): an Entry plus a Browse button, for decoding files you already
  have. Browse picks a `cableData.ztx` file or a folder.
- Output folder: an Entry plus a Browse button. Required. Everything is written
  here.

Buttons:
- Test connection: runs `discover()`. Shows the ECU name, alias, and state, or
  "No ECU found" after the timeout. Read only, safe to press any time.
- Download: first a confirm dialog that says this arms the ECU and stops its
  logger while it runs. On confirm, runs `download()`.
- Decode: runs `decode(input_path)` on the Input field.
- Open results folder: opens the Output folder in the OS file browser.
- Package to send: runs `package_results()`, then shows the zip path and opens its
  folder, so the user can email or upload it.

A read-only scrolling log pane (ScrolledText) that appends every `log` event.
A status line, a progress bar (`ttk.Progressbar`), and a one-line metrics summary
(bytes, throughput, elapsed) updated from `progress` and `done` events.

## Threading (important)

`on_event` fires on the worker thread (download polls progress there). Tk is not
thread safe. So:
- Run every session call in a `threading.Thread`.
- The session's `on_event` only puts the dict on a `queue.Queue`.
- The UI drains the queue on the Tk thread with `root.after(100, drain)` and
  updates widgets there.
- Disable the action buttons while an operation runs, re-enable on `done` or
  `error`.

## Config memory

Remember the last ECU IP, input, and output across runs in a small JSON at
`Path.home() / ".wtxapp.json"`. Load on start, save on change or close. Wrap all
reads and writes in try/except so a missing or unreadable file never crashes the
app. Frozen-exe: write only to this user path and the chosen output folder, never
to `sys._MEIPASS`.

## Tests (append to tests/test_app.py, headless)

Tkinter needs a display, so keep the testable logic in small functions and test
those without a window:
- `config` round-trip: save a dict, load it back, assert equal; a missing file
  loads to defaults, not an error.
- the `progress` event to summary mapping: a function that turns a progress dict
  into the status or percent string, asserted on a sample dict.
- the download confirm gate: model it as a function that returns True only when
  the confirm callback returns True, so the arming step cannot run without a
  confirm.
- one guarded smoke test: if a display is available (DISPLAY set, or `xvfb-run`
  exists), build the main window, call `update()` once, destroy it, assert no
  exception. Print SKIP and pass if there is no display.

Run: `python3 -I tests/test_app.py` (all G1 and G2 checks). If a display exists,
also try `xvfb-run -a python3 -m wtxapp` does not crash on construct.

## Notes from the G1 review (apply these)

- Before building the session, validate the output folder is NOT inside the input
  folder. The session writes `logs/` and `download/` at the output path, and the
  decode guard only protects `decoded/`.
- Validate the ECU IP with the `ipaddress` module before use, so a stray hostname
  does not trigger a DNS lookup. Reject bad input with a clear message.
- The download size is not known ahead of time and progress events carry no
  `pct`. Use an indeterminate progress bar, or show bytes and throughput, not a
  percentage.
- The session fires its first `log` event inside `RunSession.__init__`, before
  the constructor returns. The `on_event` callback must only enqueue. It must not
  call back into the session.
- `package_results()` can let an `OSError` escape. The worker thread must catch it
  and show an error, not crash the app.
- Use the session's `discover()`, not `wtx_client.BeaconWatcher` directly. That
  class has a latent attribute-shadow bug on some Python versions. The session's
  one-shot listen avoids it.

## Done when

`python -m wtxapp` launches the window, the headless tests pass, long operations
never block or touch Tk off the UI thread, and the config persists.
