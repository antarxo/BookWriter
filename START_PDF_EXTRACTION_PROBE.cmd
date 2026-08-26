@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE="
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "..\BookWriter\.venv\Scripts\python.exe" set "PYTHON_EXE=..\BookWriter\.venv\Scripts\python.exe"
if not defined PYTHON_EXE set "PYTHON_EXE=python"

where pdftotext >nul 2>nul
if errorlevel 1 (
  echo.
  echo ERROR: Poppler/pdftotext.exe was not found in PATH.
  echo Close this window, reopen PowerShell/Explorer after installation, and run again.
  echo.
  pause
  exit /b 3
)

echo Starting PDF extraction probe at http://127.0.0.1:8776/
start "" http://127.0.0.1:8776/
"%PYTHON_EXE%" pdf_extraction_probe_server.py

pause
