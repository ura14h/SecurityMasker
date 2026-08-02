@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "SCRIPT_DIRECTORY=%~dp0"
for %%I in ("%SCRIPT_DIRECTORY%..") do set "PROJECT_DIRECTORY=%%~fI"
set "PROFILE=lite"
set "BINARY="

:parse
if "%~1"=="" goto parsed
if /i "%~1"=="--profile" (
    if "%~2"=="" goto usage
    set "PROFILE=%~2"
    shift
    shift
    goto parse
)
if defined BINARY goto usage
set "BINARY=%~f1"
shift
goto parse

:parsed
if /i "%PROFILE%"=="lite" goto profile_ok
if /i "%PROFILE%"=="full" goto profile_ok
goto usage

:profile_ok
if not defined BINARY set "BINARY=%PROJECT_DIRECTORY%\dist\securitymasker-%PROFILE%.exe"
if defined PYTHON (
    set "TEST_PYTHON=%PYTHON%"
) else (
    set "TEST_PYTHON=%PROJECT_DIRECTORY%\.venv\Scripts\python.exe"
)
if not exist "%TEST_PYTHON%" goto python_missing
if not exist "%BINARY%" goto binary_missing
if not defined SM_BINARY_WINDOWS_TEMP_ROOT set "SM_BINARY_WINDOWS_TEMP_ROOT=%TEMP%\securitymasker-binary-test"
if not exist "%SM_BINARY_WINDOWS_TEMP_ROOT%" mkdir "%SM_BINARY_WINDOWS_TEMP_ROOT%"
if errorlevel 1 exit /b 1

if /i "%PROFILE%"=="lite" goto prepare_lite
goto run_test

:prepare_lite
if defined SM_BINARY_TEST_HF_HOME (
    set "TEST_MODEL_HOME=%SM_BINARY_TEST_HF_HOME%"
) else (
    set "TEST_MODEL_HOME=%PROJECT_DIRECTORY%\build\test-binary-model-cache-windows"
)
if not exist "%TEST_MODEL_HOME%" mkdir "%TEST_MODEL_HOME%"
if errorlevel 1 exit /b 1
set "HF_HOME=%TEST_MODEL_HOME%"
"%BINARY%" model-load --model tsmatz/xlm-roberta-ner-japanese --revision aba094e118d5ffc622e9b25e07edc49f9dd85feb
if errorlevel 1 exit /b 1
set "SM_BINARY_TEST_HF_HOME=%TEST_MODEL_HOME%"

:run_test

pushd "%PROJECT_DIRECTORY%"
if errorlevel 1 exit /b %errorlevel%
set "SM_BINARY=%BINARY%"
set "SM_BINARY_PROFILE=%PROFILE%"
"%TEST_PYTHON%" -m pytest tests\integration\test_binary_release.py -q
set "RESULT=%errorlevel%"
popd
exit /b %RESULT%

:usage
echo usage: scripts\test-binary.cmd --profile lite^|full [binary.exe] 1>&2
exit /b 2

:python_missing
echo error: run scripts\test-setup.cmd first or set PYTHON to the test Python full path 1>&2
exit /b 2

:binary_missing
echo error: executable binary required: %BINARY% 1>&2
exit /b 2
