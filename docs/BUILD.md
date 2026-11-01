# Build the Windows exe

This page explains how to turn the app into one Windows exe to send to users. You
build it once, then you send the single exe and the user guide to each user. There
are two ways: a local build on a Windows PC, and a build in GitHub Actions.

## What the build produces

`dist\WinTAX-ECU-Downloader.exe`. It is one file. It holds Python, the app, and
the numpy and scipy libraries, so a user needs no Python install.

## Way 1: local build on Windows

1. Install Python 3.13 (64-bit) from python.org. Select "Add Python to PATH".
2. Open Command Prompt.
3. Change to the repo folder, for example `cd C:\wintax-tool`.
4. Run `build_windows.bat`.
5. Wait. The script installs PyInstaller, numpy, and scipy, then builds the exe.
6. Find the exe at `dist\WinTAX-ECU-Downloader.exe`.

## Way 2: build in GitHub Actions

1. Push the repo to GitHub.
2. Open the Actions tab.
3. Run the "build-windows" workflow, or push a commit to trigger it.
4. Open the finished run.
5. Download the artifact from the run. The artifact is a zip. Extract
   `WinTAX-ECU-Downloader.exe` from it.

## Confirm the exe (first build)

Confirm a new exe once before you send it out.

1. Run the exe.
2. Set the output folder.
3. Click Decode on one `cableData.ztx` file you already have.
4. Open the `decoded` folder in your output folder. Confirm a `lap_decoded.mat`
   file and CSV files are there.

This confirms the exe bundled the data libraries correctly. A missing `.mat` file
means the build did not include scipy.

## Distribute to users

1. Send each user two items: the exe and `docs/USER_GUIDE.md`.
2. Use any method: email, a file share, or a USB drive.

## Notes

- The first launch can take a few seconds. The exe unpacks itself to a temporary
  folder.
- Windows SmartScreen or an antivirus can flag a new, unsigned exe. Sign the exe
  with a code-signing certificate to avoid this, or tell users to select "More
  info" then "Run anyway".
- Build the exe on Windows. PyInstaller does not build a Windows exe from Linux
  or macOS.
