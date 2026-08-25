@echo off
setlocal EnableExtensions
set "PIPELINE_ROOT=%~dp0"
for %%I in ("%PIPELINE_ROOT%..") do set "STUDIO_ROOT=%%~fI"
set "PYTHON=%STUDIO_ROOT%\.venv\Scripts\python.exe"
cd /d "%STUDIO_ROOT%" || exit /b 1

echo ============================================================
echo PDF / Word Canonical Pipeline - shared environment setup
echo Studio folder: %STUDIO_ROOT%
echo ============================================================

echo This pipeline uses the ONE shared Studio environment:
echo   %STUDIO_ROOT%\.venv

echo.
call "%STUDIO_ROOT%\01_SETUP_FIRST_TIME.cmd"
exit /b %ERRORLEVEL%
