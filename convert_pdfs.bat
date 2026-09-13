@echo off
setlocal
cd /d "%~dp0"

if not exist "%~dp0.venv\Scripts\python.exe" goto setup
"%~dp0.venv\Scripts\python.exe" -c "import ensurepip, sys; raise SystemExit(sys.version_info[:3] != (3, 14, 6))" >nul 2>nul
if errorlevel 1 goto setup
goto convert

:setup
echo Python 3.14.6 virtual environment is missing or unusable. Running setup.bat first...
call "%~dp0setup.bat"
if errorlevel 1 exit /b %ERRORLEVEL%

:convert

"%~dp0.venv\Scripts\python.exe" "%~dp0backend\convert_pdfs.py" %*
exit /b %ERRORLEVEL%
