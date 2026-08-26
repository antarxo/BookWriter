@echo off
setlocal
cd /d "%~dp0\.."

echo [1/3] Installing build dependencies...
py -m pip install --upgrade requests pyinstaller
if errorlevel 1 goto :fail

echo [2/3] Building MathpixBridge.exe...
py -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name MathpixBridge ^
  --paths tools ^
  tools\mathpix_probe_gui.py
if errorlevel 1 goto :fail

echo [3/3] Done.
echo EXE: %CD%\dist\MathpixBridge.exe
start "" "%CD%\dist"
exit /b 0

:fail
echo.
echo BUILD FAILED.
pause
exit /b 1
