@echo off
setlocal EnableExtensions
set "PIPELINE_ROOT=%~dp0"
for %%I in ("%PIPELINE_ROOT%..") do set "STUDIO_ROOT=%%~fI"
set "PYTHON=%STUDIO_ROOT%\.venv\Scripts\python.exe"
cd /d "%PIPELINE_ROOT%" || exit /b 1
set "PYTHONPATH=%PIPELINE_ROOT%src;%PYTHONPATH%"

if "%~4"=="" (
  echo Usage: run_pdf_to_bookwriter.cmd source.pdf reference.docx pages output_folder
  echo Example: run_pdf_to_bookwriter.cmd PP.pdf PP.docx 17-64 output\run_17_64
  if not defined BW_PIPELINE_NO_PAUSE pause
  exit /b 2
)

if not exist "%PYTHON%" (
  echo ERROR: The shared Studio Python environment was not found:
  echo   %PYTHON%
  echo Run this file first:
  echo   %STUDIO_ROOT%\01_SETUP_FIRST_TIME.cmd
  if not defined BW_PIPELINE_NO_PAUSE pause
  exit /b 3
)

"%PYTHON%" -c "import fitz, docx, lxml, rapidfuzz, PIL, pdf_word_canonical_pipeline, pdf_word_reconstructor; print('Dependency preflight OK')"
if errorlevel 1 (
  echo.
  echo ERROR: The shared Studio environment is incomplete.
  echo Run again:
  echo   %STUDIO_ROOT%\01_SETUP_FIRST_TIME.cmd
  if not defined BW_PIPELINE_NO_PAUSE pause
  exit /b 4
)

"%PYTHON%" -m pdf_word_canonical_pipeline.pipeline pdf --pdf "%~1" --reference-docx "%~2" --pages "%~3" --output "%~4" --calibration none --no-render --composite-policy off
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
  echo.
  echo PDF pipeline failed with code %RC%.
)
if not defined BW_PIPELINE_NO_PAUSE pause
exit /b %RC%
