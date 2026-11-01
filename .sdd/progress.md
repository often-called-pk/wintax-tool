# SDD ledger. plan: docs/APP_PLAN.md

Spec = docs/APP_PLAN.md (self-contained; no separate spec file).

## Preflight conflict scan

| Pair / task | Produces vs consumes | Found |
|---|---|---|
| G1 x G2 | G1 makes `wtxapp/__init__.py` + `session.py`; G2 adds `gui.py` + `__main__.py`, reads `RunSession` and `__version__` | No write conflict. G2 depends on G1 interface. Sequential. |
| G1 x G3 | G3 spec references `wtxapp/__main__.py` (from G2) and the src modules | G3 after G2. No shared file edited. |
| G2 x G3 | G3 bundles G2 entrypoint | No conflict. Sequential. |
| G1 self | tests in `tests/test_app.py` vs code in `wtxapp/session.py` | Agree: brief pins the test asserts to the session API. |
| all x src/ | reuse `wtx_client`, `wtx_decode`, `decrypt_ztx` read only | No fork. Import only. |

Scan clean. Tasks are sequential (G1 -> G2 -> G3), share the `wtxapp/` dir but
distinct files.

## Rulings

Ruling: GUI = Tkinter (stdlib). Why: zero pip deps, packs to one Windows exe for
laymen. Cost if wrong: a richer toolkit (PySide) would need bundling and a
license review; swap later if the UI outgrows Tk.
Ruling: send-back = a local results zip, not auto-upload. Why: no server or
credentials, nothing leaves a machine without the user. Cost if wrong: owner
collects zips by hand until an upload endpoint is provided.
Ruling: the exe is built on Windows (bat or CI), not here. Why: PyInstaller does
not cross-compile. Cost if wrong: none; this box writes and lints the spec and
the app, Windows produces the binary.

## Progress

(baseline commit = pre-app state of wintax-tool, eb65d83)

Task G1: implemented by Haiku, commit 7756c8f on feat/app-session-core (BASE eb65d83). 8/8 tests pass. Status DONE_WITH_CONCERNS.
Ruling: download completeness cannot be verified offline. The reused wtx_client.download stops on idle timeout with no end-of-stream flag, so the session records completeness="unconfirmed" and warns; true confirmation is a B6 live-ECU item. Cost if wrong: a partial live download reads as ok until the owner inspects the zip.
Ruling: Beacon has no firmware or serial field; the results zip name uses alias-or-ecu_ip. Cost if wrong: zips are less self-describing; owner renames by hand.
Ruling: all app work stays on feat/app-session-core; no merge to master during the build. Why: local repo, respects the no-merge-to-master rule; user merges at finish. Cost if wrong: an extra merge step for the user.
Task G1: review+test+fix dispatched to Sonnet.
Task G1: complete (commits eb65d83..c1f3a72, 11/11 tests green, APPROVED_WITH_FIXES). Fixed: decode("") walked cwd (now refused); zip name uses sanitized alias-or-ecu_ip; download completeness="unconfirmed" + warning.
Deferred minors (final review): throughput over whole call; sticky status/error across steps; run.json finished=null at package time; package_results lets OSError escape (G2 worker must catch); naive local timestamps.
Info (src, not edited): wtx_client.BeaconWatcher shadows Thread._stop -> TypeError on join/is_alive on py3.9-3.12. Unused today; app uses one-shot listen. Fix if the app ever starts a BeaconWatcher, or bundle py3.13+.

Task G2: dispatched to Haiku (BASE 32bf4da).
Task G2: implemented by Haiku, commit ac9e3c5. 17 tests pass (11 G1 + 6 G2), GUI launches under xvfb. DONE_WITH_CONCERNS.
Concern (to fix in review): IP validation runs for every action, so a bad ECU IP blocks offline Decode. Minor: two Browse buttons (file + folder), close-while-busy confirm (extra, harmless).
Task G2: review+test+fix dispatched to Sonnet.
Task G2: review fix commit abba833, 18 tests pass, APPROVED_WITH_FIXES (IP-scope bug fixed: Decode/Package no longer need a valid ECU IP).
Ruling: the GUI must use ONE RunSession per output folder, reused across buttons, not one per button press. Why: device identity from Test connection must persist into the Download run.json and the zip name (alias), which the owner needs to tell users, ECUs, and firmware apart across many bundles. Load-bearing for the monitor-and-send-back goal. Cost if wrong: zips named by IP only, empty device in run.json.
Task G2: fix round 1 dispatched to Sonnet (persistent session; plus arming dialog default No, no 0.0 kB/s line, real socket error text).
Task G2: fix round 1 commit 1712e39, 21 tests pass. Persistent session (zip named by alias, run.json carries device). Arming dialog default No; metrics line uses recorded throughput; real socket error shown.
Ruling: stale-identity-on-car-swap-in-one-folder deferred. Why: edge case (user reuses one output folder across different cars without clicking Test); run.json still has ecu_ip and timestamps. Add a beacon refresh at Download start if car-swapping becomes common. Cost if wrong: a bundle mislabeled by alias when a user swaps cars without re-testing.
Task G2: complete (commits 32bf4da..1712e39, 21/21 tests, review clean). Deferred minors (final review): G1 sticky status/error across session; zip run.json finished=null until close; close-while-busy writes no final record; download-failed shows msg only in status.
Task G3: dispatched to Haiku.
