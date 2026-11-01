# -*- mode: python ; coding: utf-8 -*-
# One windowed exe. Build from the repo root: pyinstaller --clean --noconfirm wtxapp.spec
# wtxapp/session.py adds src/ to sys.path at run time. PyInstaller does not follow
# that insert, so src/ goes on pathex and its modules go in hiddenimports.
import os

a = Analysis(
    [os.path.join(SPECPATH, "run_app.py")],
    pathex=[SPECPATH, os.path.join(SPECPATH, "src")],
    hiddenimports=[
        "wtx_client",
        "wtx_decode",
        "decrypt_ztx",
        "wtx_fileformat",
        "numpy",
        "scipy.io.matlab",
    ],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="WinTAX-ECU-Downloader",
    console=False,
)
