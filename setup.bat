@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if not errorlevel 1 (
    py -3.12 -c "import sys" >nul 2>nul
    if not errorlevel 1 (
        py -3.12 "%~dp0setup.py" %*
        if errorlevel 1 exit /b 1
        exit /b 0
    )
    py -3.13 -c "import sys" >nul 2>nul
    if not errorlevel 1 (
        py -3.13 "%~dp0setup.py" %*
        if errorlevel 1 exit /b 1
        exit /b 0
    )
    py -3.11 -c "import sys" >nul 2>nul
    if not errorlevel 1 (
        py -3.11 "%~dp0setup.py" %*
        if errorlevel 1 exit /b 1
        exit /b 0
    )
)

python -c "import sys" >nul 2>nul
if not errorlevel 1 (
    python "%~dp0setup.py" %*
    if errorlevel 1 exit /b 1
    exit /b 0
)

echo ERROR: No working Python 3.11, 3.12, or 3.13 interpreter was found.
echo Install a supported Python version from python.org, then run setup.bat again.
exit /b 1
