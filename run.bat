@echo off
setlocal
cd /d "%~dp0"

if not exist "%~dp0.venv\Scripts\python.exe" goto setup

rem A virtual environment can remain after its base Python installation has
rem been removed or upgraded.  Checking that it can actually start prevents
rem the frontend from launching without an API service behind it.
"%~dp0.venv\Scripts\python.exe" -c "import ensurepip, sys; raise SystemExit(sys.version_info[:3] != (3, 14, 6))" >nul 2>nul
if errorlevel 1 goto setup
goto run

:setup
echo Virtual environment is missing or unusable. Running setup.bat first...
call "%~dp0setup.bat"
if errorlevel 1 exit /b %ERRORLEVEL%

:run

"%~dp0.venv\Scripts\python.exe" "%~dp0run.py" %*
exit /b %ERRORLEVEL%
