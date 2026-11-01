# G3 report: PyInstaller Windows exe, spec, bat, CI

Status: DONE_WITH_CONCERNS. Spec, entry, hidden imports pass Linux PyInstaller build (Python 3.13). Windows exe not built or run here. actionlint not run (permission denied).

## Created

- `run_app.py`: 2 lines. `from wtxapp.gui import main`, `main()`.
- `wtxapp.spec`: one windowed exe `WinTAX-ECU-Downloader`, `console=False`. Entry `run_app.py` (path via `SPECPATH`). `pathex` = repo root + `src/`. `hiddenimports` = `wtx_client`, `wtx_decode`, `decrypt_ztx`, `wtx_fileformat`, `numpy`, `scipy.io.matlab`. No datas, icon, version resource, UPX option.
- `build_windows.bat`: brief content + `cd /d "%~dp0"` (runs from any folder) + `if errorlevel 1 exit /b 1` after each step (failed build no longer prints "Built"). CRLF line endings.
- `.github/workflows/build-windows.yml`: name `build-windows` (matches docs/BUILD.md). Triggers `workflow_dispatch`, `push`. `windows-latest`. Steps: checkout, setup-python 3.13, `pip install pyinstaller numpy scipy`, `pyinstaller --clean --noconfirm wtxapp.spec`, upload-artifact `WinTAX-ECU-Downloader` from `dist/WinTAX-ECU-Downloader.exe`. `permissions: contents: read`. Action majors `@v7`: tags checked on GitHub API 2026-11-01 (checkout v7.0.1, setup-python v7.1.0, upload-artifact v7.0.2).
- `.sdd/g3-report.md`: this file.

Not touched: `wtxapp/`, `src/`, `tests/`. `docs/BUILD.md` untracked, pre-existing, not mine, not committed (see concerns).

## Linux build env

- `/usr/bin/python3.13 -m venv /tmp/wtx-g3-py313`, then `/tmp/wtx-g3-py313/bin/python -m pip install --upgrade pyinstaller numpy scipy`.
- Versions: Python 3.13.12, numpy 2.5.4, scipy 1.18.1, PyInstaller 6.22.3 (same packages CI installs).
- Venv outside repo. 3.13 = Windows/CI target. Test `python3` (3.14.7) not used for build.

## Validation

1. Spec parses:

   ```
   $ python3 -c "import ast; ast.parse(open('wtxapp.spec').read())" && echo "spec parse OK"
   spec parse OK
   ```

2. Workflow YAML parses (PyYAML). Triggers, runner, steps:

   ```
   {"name": "build-windows", "triggers": ["workflow_dispatch", "push"], "runs-on": "windows-latest",
    "steps": ["actions/checkout@v7", "actions/setup-python@v7", "python -m pip install pyinstaller numpy scipy",
              "pyinstaller --clean --noconfirm wtxapp.spec", "actions/upload-artifact@v7"]}
   ```

3. Bat line endings: `file build_windows.bat` gives `DOS batch file, ASCII text, with CRLF line terminators`.

4. No em dash in new files: `grep -rlP '\x{2014}' run_app.py wtxapp.spec build_windows.bat .github/workflows/build-windows.yml` gives no match.

5. Linux PyInstaller build, repo root:

   ```
   $ /tmp/wtx-g3-py313/bin/pyinstaller --clean --noconfirm wtxapp.spec
   exit=0
   Build complete! The results are available in: .../wintax-tool/dist
   ```

   Only WARNING: `Hidden import "scipy.special._cdflib" not found!`. Contrib hook names a module scipy 1.18.1 lacks (`import scipy.special._cdflib` gives `ModuleNotFoundError`). Harmless, not one of ours. No scipy submodule reported missing, so none added. Full log: `/tmp/wtx-g3-build.log`.

6. Warn file (`build/wtxapp/warn-wtxapp.txt`; PyInstaller names work folder after spec file). Exact-name check for our modules and numpy/scipy:

   ```
   $ grep -E "^missing module named (wtx_client|wtx_decode|decrypt_ztx|wtx_fileformat|numpy|scipy|scipy\.io\.matlab|wtxapp|tkinter|run_app) - " warn-wtxapp.txt
   none
   ```

   Missing-module lines total 293: 175 numpy internals, 57 scipy internals, rest platform-only or optional (`msvcrt`, `winreg`, `_winapi`, `nt`, `pytest`, `matplotlib`, `torch`, ...). Copy: `/tmp/wtx-g3-warn-wtxapp.txt`.

7. PYZ contents (PyInstaller archive reader):

   ```
   modules in PYZ: 895
   ours: ['decrypt_ztx', 'wtx_client', 'wtx_decode', 'wtx_fileformat', 'wtxapp', 'wtxapp.gui', 'wtxapp.session']
   numpy: True | scipy.io.matlab: True | scipy.io.matlab submodules: 15
   tkinter: True | tkinter.ttk: True | tkinter.filedialog: True
   ```

8. Output `dist/WinTAX-ECU-Downloader`: 59.9 MB Linux ELF, not the Windows exe.

9. Smoke, brief command (timeout = pass):

   ```
   $ HOME=/tmp/wtx-g3-home timeout 8 xvfb-run -a ./dist/WinTAX-ECU-Downloader
   smoke exit=124 (124 = still running at 8 s, pass)
   app output: empty
   ```

10. Window check (timeout alone cannot tell a hang during import from a running GUI):

    ```
    $ xvfb-run -a sh -c './dist/WinTAX-ECU-Downloader & APP=$!; sleep 6; xdotool search --name "WinTAX" getwindowname %@; kill -0 $APP && echo alive; kill $APP; wait $APP'
    WinTAX ECU Downloader 0.1.0
    alive: yes
    app log: empty
    ```

    Title from `TITLE` in `wtxapp/gui.py`. No stray app or Xvfb process after runs.

11. actionlint: not run. `uvx --from actionlint-py actionlint` was denied by the auto-mode permission classifier (downloads and runs a third-party package). Not retried by another route. Step 2 YAML parse is the only workflow check.

12. Cleanup: `build/` and `dist/` removed (gitignored, made by this build). Scratch venv and logs stay in `/tmp`.

## Concerns

- Windows exe not built or run here. Real check: `build_windows.bat` or the `build-windows` CI run on Windows. Windows-only paths (`os.startfile` in `open_folder`, Winsock in `wtx_client`) not exercised on Linux.
- actionlint not run (step 11). Run it locally, or rely on first CI run.
- UPX off on Linux. On Windows it runs only if `upx` is on PATH, which `windows-latest` lacks. If antivirus flags the exe, add `upx=False` to the EXE call.
- `push:` builds on every branch push. Add branch filter if Actions minutes matter.
- `docs/BUILD.md` untracked, not in brief. Names match this work: bat name, workflow `build-windows`, exe name, artifact. Commit with G3 or leave to orchestrator.
- Linux binary 59.9 MB. Windows exe size not measured.
