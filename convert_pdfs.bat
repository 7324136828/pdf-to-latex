@echo off
setlocal
cd /d "%~dp0"

if not exist "%~dp0.venv\Scripts\python.exe" (
    echo Virtual environment not found. Running setup.bat first...
    call "%~dp0setup.bat"
    if %ERRORLEVEL% neq 0 exit /b %ERRORLEVEL%
)

"%~dp0.venv\Scripts\python.exe" "%~dp0backend\convert_pdfs.py" %*
exit /b %ERRORLEVEL%
