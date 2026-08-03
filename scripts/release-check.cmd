@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "SCRIPT_DIRECTORY=%~dp0"
for %%I in ("%SCRIPT_DIRECTORY%..") do set "PROJECT_DIRECTORY=%%~fI"
set "PYTHON=%PROJECT_DIRECTORY%\.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo error: run scripts\test-setup.cmd first 1>&2
    exit /b 2
)

pushd "%PROJECT_DIRECTORY%"
if errorlevel 1 exit /b %errorlevel%

"%PROJECT_DIRECTORY%\.venv\Scripts\ruff.exe" check src tests devtools
if errorlevel 1 goto failed
"%PROJECT_DIRECTORY%\.venv\Scripts\mypy.exe" src
if errorlevel 1 goto failed
set "SM_REQUIRE_MODEL=1"
"%PYTHON%" -m pytest -q tests\unit tests\evaluation
if errorlevel 1 goto failed
set "SM_RUN_LIVE=1"
"%PYTHON%" -m pytest -q tests\integration\test_live_gateway.py
if errorlevel 1 goto failed
set "SM_RUN_WINDOWS_NATIVE=1"
"%PYTHON%" -m pytest -q tests\integration\test_windows_native_process.py
if errorlevel 1 goto failed

popd
echo Windows native pre-release checks passed.
echo Network-isolated real CLI E2E is an optional extended compatibility gate.
exit /b 0

:failed
set "RESULT=%errorlevel%"
popd
exit /b %RESULT%
