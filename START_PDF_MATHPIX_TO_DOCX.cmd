@echo off
setlocal EnableExtensions

set "ROOT=%~dp0"
set "STARTER=%ROOT%mathpix-converter\Start_Mathpix_Converter.cmd"

if not exist "%STARTER%" (
  echo Missing standalone PDF/Mathpix converter starter:
  echo %STARTER%
  pause
  exit /b 1
)

call "%STARTER%"
exit /b %ERRORLEVEL%
