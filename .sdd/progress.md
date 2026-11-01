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

(baseline commit = pre-app state of wintax-tool)
