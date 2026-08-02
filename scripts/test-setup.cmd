@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "SCRIPT_DIRECTORY=%~dp0"
for %%I in ("%SCRIPT_DIRECTORY%..") do set "PROJECT_DIRECTORY=%%~fI"
set "VENV_PYTHON=%PROJECT_DIRECTORY%\.venv\Scripts\python.exe"

call "%SCRIPT_DIRECTORY%setup.cmd"
if errorlevel 1 exit /b %errorlevel%

"%VENV_PYTHON%" -m pip install --disable-pip-version-check --only-binary=:all: --no-deps -r "%PROJECT_DIRECTORY%\requirements-windows-dev.lock"
if errorlevel 1 exit /b %errorlevel%

echo SecurityMasker Windows test environment is ready.
exit /b 0

