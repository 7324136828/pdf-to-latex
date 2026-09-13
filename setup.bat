@echo off
setlocal
cd /d "%~dp0"

if defined VIRTUAL_ENV goto active
if defined CONDA_PREFIX goto active

where py >nul 2>nul
if not errorlevel 1 (
    py -3.14 -c "import sys; raise SystemExit(sys.version_info[:3] != (3, 14, 6))" >nul 2>nul
    if not errorlevel 1 (
        py -3.14 "%~dp0setup.py" %*
        if errorlevel 1 exit /b 1
        exit /b 0
    )
)

python -c "import sys; raise SystemExit(sys.version_info[:3] != (3, 14, 6))" >nul 2>nul
if not errorlevel 1 (
    python "%~dp0setup.py" %*
    if errorlevel 1 exit /b 1
    exit /b 0
)

echo ERROR: Python 3.14.6 was not found.
echo Install Python 3.14.6 from python.org, then run setup.bat again.
exit /b 1

:active
python "%~dp0setup.py" %*
exit /b %ERRORLEVEL%
