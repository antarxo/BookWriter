@echo off
setlocal EnableExtensions
set "PIPELINE_ROOT=%~dp0"
for %%I in ("%PIPELINE_ROOT%..") do set "STUDIO_ROOT=%%~fI"
set "PYTHON=%STUDIO_ROOT%\.venv\Scripts\python.exe"
cd /d "%PIPELINE_ROOT%" || exit /b 1
set "PYTHONPATH=%PIPELINE_ROOT%src;%PYTHONPATH%"

if "%~4"=="" (
  echo Usage: run_fidelity_to_bookwriter.cmd source.pdf mathpix_folder_or_all_formats.zip pages output_folder
  echo Example: run_fidelity_to_bookwriter.cmd PP.pdf C:\PDF_WORD_TEST 17-64 C:\PDF_WORD_TEST\FIDELITY_17_64
  if not defined BW_PIPELINE_NO_PAUSE pause
  exit /b 2
)

if not exist "%PYTHON%" (
  echo ERROR: The shared Studio Python environment was not found:
  echo   %PYTHON%
  echo Run first:
  echo   %STUDIO_ROOT%\01_SETUP_FIRST_TIME.cmd
  if not defined BW_PIPELINE_NO_PAUSE pause
  exit /b 3
)

"%PYTHON%" -c "import fitz, docx, lxml, rapidfuzz, PIL, pdf_word_canonical_pipeline, pdf_word_reconstructor; print('Dependency preflight OK')"
if errorlevel 1 (
  echo ERROR: The shared Studio environment is incomplete.
  echo Run again: %STUDIO_ROOT%\01_SETUP_FIRST_TIME.cmd
  if not defined BW_PIPELINE_NO_PAUSE pause
  exit /b 4
)

set "BW_CALIBRATION=%BW_PIPELINE_CALIBRATION%"
if not defined BW_CALIBRATION set "BW_CALIBRATION=fast"
set "BW_RENDER_FLAG="
if /I "%BW_PIPELINE_NO_RENDER%"=="1" set "BW_RENDER_FLAG=--no-render"

"%PYTHON%" -m pdf_word_canonical_pipeline.pipeline fidelity --pdf "%~1" --source "%~2" --pages "%~3" --output "%~4" --calibration "%BW_CALIBRATION%" %BW_RENDER_FLAG%
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" echo Fidelity pipeline failed with code %RC%.
if not defined BW_PIPELINE_NO_PAUSE pause
exit /b %RC%
