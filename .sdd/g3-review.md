# G3 review: PyInstaller exe, spec, bat, CI

Verdict: APPROVED. No fix commit. No Critical, no Important finding.

Scope: 4f246fe on feat/app-session-core (base 0351d4c). Files: run_app.py, wtxapp.spec, build_windows.bat, .github/workflows/build-windows.yml, .sdd/g3-report.md. wtxapp/, src/, tests/ untouched (git show --stat lists 5 files). Working tree matches the commit for all four packaging files.

## Checked by inspection

- run_app.py: 2 lines, `from wtxapp.gui import main` then `main()`. Same body as `wtxapp/__main__.py`. No `__name__` guard needed: PyInstaller runs the entry as `__main__`.
- wtxapp.spec: entry `SPECPATH/run_app.py`. `console=False`. Name `WinTAX-ECU-Downloader`. One file: binaries and datas go into EXE, no COLLECT (the Linux build made a single file). `pathex` = repo root + `src/`. `SPECPATH` is a PyInstaller spec global (build_main.py). No datas, icon, version resource or upx option: per brief.
- hiddenimports complete. Import graph traced by grep. No importlib, no `__import__`, no function-level imports in src/ or wtxapp/.
  - session.py: wtx_client, wtx_decode.
  - wtx_decode: decrypt_ztx, wtx_fileformat, numpy, `scipy.io.savemat` (inside try/except ImportError).
  - wtx_client, decrypt_ztx, wtx_fileformat: stdlib only. `subprocess` in wtx_client is the offline tshark check, not on the GUI path.
  - gui.py: tkinter, wtxapp.*.
  - Not imported by the app, so correctly absent: src/wtx.py (CLI), src/_crack_cipher.py (dev tool).
- scipy: app needs only `scipy.io.matlab` (savemat). Its 3 compiled modules are static imports: `_mio5_utils` and `_streams` in `_mio5.py`, `_mio_utils` in `_mio4.py`. So Analysis collects them. PyInstaller `hook-scipy.io.matlab` adds the `streams` shim. Windows scipy DLL bundle comes from PyInstaller's own `hook-scipy` (`collect_delvewheel_libs_directory`), not exercised on Linux. Warn file (copy in /tmp): no `scipy.io` or `scipy.io.matlab` miss, only `scipy.sparse.*` attribute-name noise (package is bundled). The `scipy.special._cdflib` warning comes from `hook-scipy.special._ufuncs` (adds it for scipy >= 1.13); scipy 1.18.1 has no such module. Harmless. No extra hiddenimport needed.
- With `src/` on pathex, the four wtx hiddenimports, numpy and scipy.io.matlab are redundant with static analysis. Harmless, brief asks for them. Spec comment says PyInstaller does not follow the sys.path insert: true, but plain `import wtx_client` resolves through pathex. Cosmetic, left.
- build_windows.bat: pip install --upgrade pyinstaller numpy scipy, spec build, echo of `dist\WinTAX-ECU-Downloader.exe`. Extras: `cd /d "%~dp0"` (any start folder) and `if errorlevel 1 exit /b 1` after each step (no false "Built"). Index blob is CRLF (`git ls-files --eol`: i/crlf). BUILD.md says to tick "Add Python to PATH", so bare `pyinstaller` resolves.
- Workflow: name `build-windows` (matches BUILD.md). Triggers workflow_dispatch + push. `permissions: contents: read`. windows-latest. setup-python `"3.13"` (quoted). pip install. `pyinstaller --clean --noconfirm wtxapp.spec`. Upload `dist/WinTAX-ECU-Downloader.exe` as artifact `WinTAX-ECU-Downloader`. Keys, 2-space indent and step shape are valid. Local PyYAML parse OK (`on` loads as True: YAML 1.1 quirk, GitHub reads it right).
- Action versions: `git ls-remote --tags` shows v7 on all three. checkout v7 = v7.0.1, setup-python v7 = v7.1.0, upload-artifact v7 = v7.0.2. v7 `action.yml` (raw.githubusercontent) has every input we use (`python-version`; `name`, `path`), no new required input, runtime node24.
- Hygiene: no U+2014 in the four files, the report or the commit message. Commit message plain, no attribution lines.

## Evidence trusted, not re-run

- Implementer Linux build (Python 3.13.12, PyInstaller 6.22.3, numpy 2.5.4, scipy 1.18.1): exit 0. PYZ holds decrypt_ztx, wtx_client, wtx_decode, wtx_fileformat, wtxapp.gui, wtxapp.session, scipy.io.matlab (15 submodules), tkinter. Frozen binary showed the window under xvfb. I read /tmp/wtx-g3-build.log and the warn file only. No rebuild.
- actionlint not run, not bypassed. Workflow judged by inspection (above).

## Findings (all Minor, nothing changed)

1. Report error on UPX. g3-report says UPX runs on Windows if `upx` is on PATH. In PyInstaller 6.22.3 `EXE` sets `self.upx = CONF['upx_available'] and kwargs.get('upx', False)`: default False, and the spec passes no `upx`. UPX never runs, on any OS. No `upx=False` needed. Good for antivirus false positives. Report text left as is.
2. Frozen scipy not proven by the smoke run. wtx_decode does `try: from scipy.io import savemat` / `except ImportError: savemat = None`. A bundling miss would not stop the window. It would only skip `lap_decoded.mat`, and the decode note goes to a captured buffer. Static evidence is strong (PYZ list, hooks, static imports above). First check on the Windows exe: decode one lap, confirm `decoded/<lap>/lap_decoded.mat` exists.
3. upload-artifact `if-no-files-found` defaults to `warn`. A wrong exe name would give a green run with no artifact. Not needed now: the PyInstaller step fails the job on a build error and the spec pins the name. Add `if-no-files-found: error` (one line) if wanted.
4. Doc detail. Default `archive: true`, so the run page offers a zip that holds the exe. docs/BUILD.md (untracked, owner) says "Download the exe from the run Artifacts". Reword to "download the artifact zip and unzip the exe", or set `archive: false` (new in upload-artifact v7: one file, artifact named after the file, `name` ignored). Not changed.
5. `push:` has no branch filter: every push to any branch starts a Windows build. As briefed. Add `branches:` if Actions minutes matter.

## Not verifiable here

- Windows exe build and launch, `os.startfile`, UDP broadcast and the Windows firewall prompt on first run, SmartScreen. First `build-windows` run, or `build_windows.bat` on Windows, is the real check.
- A build on Python < 3.13 carries the BeaconWatcher `_stop` shadow (progress.md Info). Unused today. BUILD.md and the workflow pin 3.13. The bat does not check the version. Left (ponytail).

## Left uncommitted

- This file, `.sdd/progress.md` (modified), `docs/BUILD.md` (untracked): for the orchestrator trail commit. BUILD.md names match the packaging files: bat, workflow `build-windows`, exe path, Python 3.13.
