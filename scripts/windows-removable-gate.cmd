@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "SCRIPT_DIRECTORY=%~dp0"
for %%I in ("%SCRIPT_DIRECTORY%..") do set "PROJECT_DIRECTORY=%%~fI"
set "PROBE=%PROJECT_DIRECTORY%\devtools\windows_removable_probe.py"

if defined SECURITYMASKER_PYTHON set "PYTHON=%SECURITYMASKER_PYTHON%"
if not defined PYTHON set "PYTHON=%PROJECT_DIRECTORY%\.venv\Scripts\python.exe"
"%PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if not errorlevel 1 goto python_ready

set "PYTHON="
for /d %%D in ("%LOCALAPPDATA%\Python\pythoncore-*-64") do if exist "%%~fD\python.exe" if not defined PYTHON set "PYTHON=%%~fD\python.exe"
if not defined PYTHON goto python_error
"%PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 goto python_error

:python_ready
"%PYTHON%" "%PROBE%" %*
exit /b %errorlevel%

:python_error
echo error: Python 3.11 or later was not found; set SECURITYMASKER_PYTHON 1>&2
exit /b 2
