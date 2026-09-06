@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  python -c "import sys; assert sys.version_info >= (3,11), 'Python 3.11 or newer is required'" || goto :fail
  python -m venv .venv || goto :fail
) else (
  py -3 -c "import sys; assert sys.version_info >= (3,11), 'Python 3.11 or newer is required'" || goto :fail
  py -3 -m venv .venv || goto :fail
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail
echo Setup complete. Open 0Start.cmd to launch MDBM.
pause
exit /b 0
:fail
echo Setup failed. Check the messages above; no dependencies were bundled.
pause
exit /b 1
