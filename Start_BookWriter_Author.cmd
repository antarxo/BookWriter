@echo off
setlocal EnableExtensions

set "ROOT=E:\EGGRAFA\GitHub\BookWriter"
set "PORT=8766"
set "MAX_PORT=8785"

cd /d "%ROOT%" || (
  echo Cannot find BookWriter folder:
  echo %ROOT%
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
set "URL=http://127.0.0.1:%PORT%/author/index.html?v=desktop-cmd"

echo BookWriter Author
echo Folder: %ROOT%
echo URL: %URL%
echo.
echo Keep this window open while working. Close it to stop the local server.
echo.

start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Milliseconds 900; Start-Process '%URL%'"

where py >nul 2>nul
if not errorlevel 1 (
  py -3 -m http.server %PORT% --bind 127.0.0.1
) else (
  python -m http.server %PORT% --bind 127.0.0.1
)

echo.
echo Server stopped.
pause
