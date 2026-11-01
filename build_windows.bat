@echo off
cd /d "%~dp0"
python -m pip install --upgrade pyinstaller numpy scipy
if errorlevel 1 exit /b 1
pyinstaller --clean --noconfirm wtxapp.spec
if errorlevel 1 exit /b 1
echo Built dist\WinTAX-ECU-Downloader.exe
