@echo off
setlocal EnableExtensions

set "ROOT=%~dp0"
set "PORT=8766"
set "MAX_PORT=8785"
set "PYTHON=%ROOT%.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=%ROOT%..\BookWriter\.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
  echo.
  echo BookWriter Python environment not found.
  echo Checked:
  echo %ROOT%.venv\Scripts\python.exe
  echo %ROOT%..\BookWriter\.venv\Scripts\python.exe
  echo.
  pause
  exit /b 1
)

if not exist "%ROOT%donorless_server.py" (
  echo Missing donorless_server.py
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
set "URL=http://127.0.0.1:%PORT%/mathpix-converter/donorless.html"
title PDF + Markdown donorless baseline

echo ====================================================================
echo PDF + Mathpix Markdown to DOCX - DONORLESS BASELINE
echo Root:    %ROOT%
echo Python:  %PYTHON%
echo URL:     %URL%
echo DOCX donor: DISABLED BY POLICY
echo ====================================================================
echo.
echo Keep this window open while working.
echo.

start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Milliseconds 900; Start-Process '%URL%'"
"%PYTHON%" "%ROOT%donorless_server.py" --port %PORT% --bind 127.0.0.1

echo.
echo Donorless baseline server stopped.
pause
