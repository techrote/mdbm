@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run 1Setup.cmd first to create the environment and install dependencies.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" mdbm.py %*
set "rc=%errorlevel%"
if not "%rc%"=="0" pause
exit /b %rc%
