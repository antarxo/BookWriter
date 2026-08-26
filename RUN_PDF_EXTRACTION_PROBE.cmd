@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE="
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "..\BookWriter\.venv\Scripts\python.exe" set "PYTHON_EXE=..\BookWriter\.venv\Scripts\python.exe"
if not defined PYTHON_EXE set "PYTHON_EXE=python"

echo.
echo PDF extraction comparison: PyMuPDF vs Poppler
set /p "PDF_PATH=PDF path: "
if "%PDF_PATH%"=="" goto :eof

set /p "PAGES=Pages [20,26,29]: "
if "%PAGES%"=="" set "PAGES=20,26,29"

"%PYTHON_EXE%" pdf_extraction_probe.py "%PDF_PATH%" --pages "%PAGES%"
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="3" (
  echo Poppler/pdftotext.exe is not installed or was not found.
  echo Install Poppler and run this launcher again.
) else if not "%RC%"=="0" (
  echo Probe failed with exit code %RC%.
) else (
  echo Done. Open folder:
  echo   %CD%\pdf_extraction_probe_output
)
echo.
pause
exit /b %RC%
