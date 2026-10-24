@echo off
cd /d "%~dp0"

python make_icon.py
if errorlevel 1 exit /b 1

echo Building MaklyxaLauncher.exe ...
python -m PyInstaller --noconfirm --onefile --windowed --name MaklyxaLauncher --icon icon.ico --add-data "icon.png;." launcher.py
if errorlevel 1 (
    echo BUILD FAILED
    exit /b 1
)

echo.
echo DONE: %cd%\dist\MaklyxaLauncher.exe
dir "dist\MaklyxaLauncher.exe"
