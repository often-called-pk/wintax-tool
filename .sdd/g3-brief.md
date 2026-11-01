# Task G3: packaging (Windows exe)

Package the GUI as a single Windows exe a layman can double-click, and give a
local and a CI way to build it. The exe is built on Windows; this box writes and
validates the spec.

## Files

- `run_app.py` (repo root): a two-line entry script, `from wtxapp.gui import main`
  then `main()`. PyInstaller needs a concrete script, not a package.
- `wtxapp.spec`: the PyInstaller spec.
- `build_windows.bat`: the local Windows build.
- `.github/workflows/build-windows.yml`: build the exe on a Windows runner and
  upload it as an artifact.

## wtxapp.spec

- One file, windowed (no console): `console=False`.
- Name `WinTAX-ECU-Downloader`.
- Entry: `run_app.py`.
- `pathex` must include the repo root AND the `src/` folder, because
  `wtxapp/session.py` imports the reused modules by name after a runtime
  `sys.path` insert that PyInstaller does not follow.
- `hiddenimports` must list the reused modules so PyInstaller collects them:
  `wtx_client`, `wtx_decode`, `decrypt_ztx`, `wtx_fileformat`, plus `numpy` and
  `scipy.io.matlab` (wtx_decode writes a `.mat`). Add any scipy submodule the
  build reports missing.
- No `datas` needed (no bundled data files; constants live in code).
- Target Python 3.13 on the runner (sidesteps a latent threading issue in the
  reused client on 3.9 to 3.12).

## build_windows.bat

```
@echo off
python -m pip install --upgrade pyinstaller numpy scipy
pyinstaller --clean --noconfirm wtxapp.spec
echo Built dist\WinTAX-ECU-Downloader.exe
```

## .github/workflows/build-windows.yml

- Triggers: `workflow_dispatch` and push.
- `runs-on: windows-latest`.
- Steps: checkout; `actions/setup-python` 3.13; `pip install pyinstaller numpy
  scipy`; `pyinstaller --clean --noconfirm wtxapp.spec`;
  `actions/upload-artifact` with `dist/WinTAX-ECU-Downloader.exe`.

## Validation on this Linux box (no Windows exe here)

1. The spec is Python: `python3 -c "import ast; ast.parse(open('wtxapp.spec').read())"`.
2. Best proof offline: a Linux PyInstaller build validates the spec, the entry,
   and the hidden imports. If pyinstaller installs cleanly in the venv, run
   `pyinstaller --clean --noconfirm wtxapp.spec` and confirm it finishes with no
   missing-module error for our modules (check `build/*/warn-*.txt` does not list
   `wtx_client`, `wtx_decode`, `decrypt_ztx`, `numpy`, `scipy`). This produces a
   Linux binary, not the Windows exe, but it proves the spec collects everything.
3. Optional: `timeout 8 xvfb-run -a ./dist/WinTAX-ECU-Downloader` and treat a
   timeout (still running) as a pass, an immediate non-zero exit as a crash to
   investigate. Skip if xvfb is absent.
4. If pyinstaller is too heavy to install here, stop at step 1 and say so. The CI
   workflow is then the real validation.

## Constraints

- ponytail: no extra spec options, no icon pipeline, no versioninfo unless it is
  one line. No em dash anywhere.
- Do not change `wtxapp/`, `src/`, or the tests.

## Done when

The spec parses, the Linux build smoke test passes (or is documented as skipped
with the reason), and the bat and the workflow are in place.
