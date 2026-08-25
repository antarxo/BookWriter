@echo off
setlocal EnableExtensions

set "APP_DIR=%~dp0"
for %%I in ("%APP_DIR%\..") do set "ROOT=%%~fI"
set "PORT=8766"
set "MAX_PORT=8785"

cd /d "%ROOT%" || (
  echo Cannot find BookWriter root:
  echo %ROOT%
  pause
  exit /b 1
)

if not exist "%ROOT%\.venv\Scripts\python.exe" (
  echo.
  echo BookWriter private Python environment is missing.
  echo Run the BookWriter setup first.
  echo.
  pause
  exit /b 1
)

if not exist "%ROOT%\server.py" (
  echo Missing gateway server:
  echo %ROOT%\server.py
  pause
  exit /b 1
)

if not exist "%ROOT%\mathpix-converter\index.html" (
  echo Missing Mathpix Converter UI:
  echo %ROOT%\mathpix-converter\index.html
  pause
  exit /b 1
)

if not exist "%ROOT%\PdfWordCanonicalPipeline\src\pdf_word_canonical_pipeline\pipeline.py" (
  echo Missing PdfWordCanonicalPipeline.
  pause
  exit /b 1
)

:find_port
netstat -ano | findstr /R /C:":%PORT% .*LISTENING" >nul
if errorlevel 1 goto port_found
set /a PORT+=1
if %PORT% GTR %MAX_PORT% (
  echo No free local port found between 8766 and %MAX_PORT%.
  pause
  exit /b 1
)
goto find_port

:port_found
set "URL=http://127.0.0.1:%PORT%/mathpix-converter/index.html"

title PDF Mathpix to DOCX Converter

echo ====================================================================
echo PDF/Mathpix to DOCX Converter
echo Folder:   %ROOT%\mathpix-converter
echo URL:      %URL%
echo Gateway:  /api/convert-mathpix-docx
echo Status:   http://127.0.0.1:%PORT%/api/status
echo ====================================================================
echo.
echo Keep this window open while working.
echo.

start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Milliseconds 1100; Start-Process '%URL%'"

"%ROOT%\.venv\Scripts\python.exe" "%ROOT%\server.py" --port %PORT% --bind 127.0.0.1

echo.
echo Mathpix Converter server stopped.
pause
