@echo off
cd /d "%~dp0"

set VERSION=1.7.1

if not exist "dist\MaklyxaLauncher.exe" (
    call build.bat
    if errorlevel 1 exit /b 1
)

where wix >nul 2>&1
if errorlevel 1 (
    echo WiX not found. Install it with:
    echo   dotnet tool install --global wix --version 5.0.2
    echo   wix extension add -g WixToolset.UI.wixext/5.0.2
    exit /b 1
)

wix extension add -g WixToolset.UI.wixext/5.0.2 >nul 2>&1

echo Building MaklyxaLauncher %VERSION% MSI ...
wix build "installer\MaklyxaLauncher.wxs" -arch x64 -culture ru-RU -ext WixToolset.UI.wixext -d Version=%VERSION% -o "dist\MaklyxaLauncher-%VERSION%-x64.msi"
if errorlevel 1 (
    echo BUILD FAILED
    exit /b 1
)

echo.
echo DONE: %cd%\dist\MaklyxaLauncher-%VERSION%-x64.msi
dir "dist\MaklyxaLauncher-%VERSION%-x64.msi"
