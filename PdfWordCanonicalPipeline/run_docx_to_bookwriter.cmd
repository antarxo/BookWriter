@echo off
setlocal EnableExtensions
set "PIPELINE_ROOT=%~dp0"
for %%I in ("%PIPELINE_ROOT%..") do set "STUDIO_ROOT=%%~fI"
set "PYTHON=%STUDIO_ROOT%\.venv\Scripts\python.exe"
cd /d "%PIPELINE_ROOT%" || exit /b 1
set "PYTHONPATH=%PIPELINE_ROOT%src;%PYTHONPATH%"

if "%~1"=="" goto usage
if "%~2"=="" goto usage

set "POLICY=%~3"
if "%POLICY%"=="" set "POLICY=auto"
if /I not "%POLICY%"=="off" if /I not "%POLICY%"=="auto" if /I not "%POLICY%"=="strict" (
  echo Invalid composite policy: %POLICY%
  echo Allowed: off, auto, strict
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

"%PYTHON%" -c "import docx, lxml, rapidfuzz, PIL, pdf_word_canonical_pipeline, pdf_word_reconstructor; print('Dependency preflight OK')"
if errorlevel 1 (
  echo.
  echo ERROR: The shared Studio environment is incomplete.
  echo Run again:
  echo   %STUDIO_ROOT%\01_SETUP_FIRST_TIME.cmd
  if not defined BW_PIPELINE_NO_PAUSE pause
  exit /b 4
)

"%PYTHON%" -m pdf_word_canonical_pipeline.pipeline docx --docx "%~1" --output "%~2" --composite-policy "%POLICY%"
set "RC=%ERRORLEVEL%"
if not defined BW_PIPELINE_NO_PAUSE pause
exit /b %RC%

:usage
echo Usage: run_docx_to_bookwriter.cmd input.docx output.docx [off^|auto^|strict]
echo Default policy for ordinary DOCX: auto
if not defined BW_PIPELINE_NO_PAUSE pause
exit /b 2
