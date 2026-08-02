@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "SCRIPT_DIRECTORY=%~dp0"
for %%I in ("%SCRIPT_DIRECTORY%..") do set "PROJECT_DIRECTORY=%%~fI"
set "FIXTURE_SCRIPT=%PROJECT_DIRECTORY%\devtools\windows_owner_gate.ps1"
set "PROBE=%PROJECT_DIRECTORY%\devtools\windows_owner_probe.py"
set "FIXTURE=%ProgramData%\SecurityMaskerOwnerGate\wrong-owner.txt"

if defined SECURITYMASKER_PYTHON set "PYTHON=%SECURITYMASKER_PYTHON%"
if not defined PYTHON set "PYTHON=%PROJECT_DIRECTORY%\.venv\Scripts\python.exe"
"%PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if not errorlevel 1 goto python_ready

set "PYTHON="
for /f "delims=" %%P in ('dir /b /s "%LOCALAPPDATA%\Python\pythoncore-*-64\python.exe" 2^>nul') do if not defined PYTHON set "PYTHON=%%P"
if not defined PYTHON goto python_error
"%PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 goto python_error

:python_ready
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%FIXTURE_SCRIPT%" -Action Setup
if errorlevel 1 exit /b %errorlevel%

"%PYTHON%" "%PROBE%" "%FIXTURE%"
set "RESULT=%errorlevel%"
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%FIXTURE_SCRIPT%" -Action Remove
if errorlevel 1 (
    echo error: owner gate fixture cleanup failed 1>&2
    exit /b 2
)
exit /b %RESULT%

:python_error
echo error: Python 3.11 or later was not found; set SECURITYMASKER_PYTHON 1>&2
exit /b 2
