@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "SCRIPT_DIRECTORY=%~dp0"
for %%I in ("%SCRIPT_DIRECTORY%..") do set "PROJECT_DIRECTORY=%%~fI"
set "PROFILE=lite"

:parse
if "%~1"=="" goto parsed
if /i "%~1"=="--profile" (
    if "%~2"=="" goto usage
    set "PROFILE=%~2"
    shift
    shift
    goto parse
)
goto usage

:parsed
if /i "%PROFILE%"=="lite" goto profile_ok
if /i "%PROFILE%"=="full" goto profile_ok
goto usage

:profile_ok
set "PROFILE_TAG=l"
if /i "%PROFILE%"=="full" set "PROFILE_TAG=f"
if defined SECURITYMASKER_PYTHON goto python_from_environment
set "PYTHON=%PROJECT_DIRECTORY%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" goto python_from_launcher
"%PYTHON%" -c "import sys" >nul 2>&1
if errorlevel 1 goto python_from_launcher
goto python_selected

:python_from_environment
set "PYTHON=%SECURITYMASKER_PYTHON%"
goto python_selected

:python_from_launcher
set "PYTHON="
for /f "delims=" %%P in ('py -3.12 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON=%%P"

:python_selected
if not defined PYTHON goto python_missing
if not exist "%PYTHON%" goto python_missing

"%PYTHON%" -c "import struct,sys; raise SystemExit(0 if sys.platform == 'win32' and sys.version_info[:2] == (3, 12) and struct.calcsize('P') == 8 else 1)"
if errorlevel 1 goto python_invalid

if defined SECURITYMASKER_BUILD_DIRECTORY (
    set "BUILD_DIRECTORY=%SECURITYMASKER_BUILD_DIRECTORY%"
) else (
    rem Torch wheel内の深いlicense pathが従来のMAX_PATHへ達しない短い作業名を使う。
    set "BUILD_DIRECTORY=%PROJECT_DIRECTORY%\build\wbin-%PROFILE_TAG%"
)
set "BUILD_VENV=%BUILD_DIRECTORY%\v"
set "WORK_DIRECTORY=%BUILD_DIRECTORY%\w"
set "DIST_DIRECTORY=%PROJECT_DIRECTORY%\dist"
set "OUTPUT=%DIST_DIRECTORY%\securitymasker-%PROFILE%.exe"
set "BUILD_METADATA=%BUILD_DIRECTORY%\securitymasker_build.json"

if exist "%BUILD_DIRECTORY%" goto build_exists
if exist "%OUTPUT%" goto output_exists
mkdir "%BUILD_DIRECTORY%"
if errorlevel 1 exit /b %errorlevel%
if not exist "%DIST_DIRECTORY%" mkdir "%DIST_DIRECTORY%"
if errorlevel 1 exit /b %errorlevel%

set "PYTHONPATH="
"%PYTHON%" -m venv "%BUILD_VENV%"
if errorlevel 1 goto failed
set "BUILD_PYTHON=%BUILD_VENV%\Scripts\python.exe"
"%BUILD_PYTHON%" -m pip install --disable-pip-version-check --only-binary=:all: --no-deps -r "%PROJECT_DIRECTORY%\requirements-windows.lock"
if errorlevel 1 goto failed
"%BUILD_PYTHON%" -m pip install --disable-pip-version-check --only-binary=:all: --no-deps -r "%PROJECT_DIRECTORY%\requirements-windows-build.lock"
if errorlevel 1 goto failed
"%BUILD_PYTHON%" -m pip install --disable-pip-version-check --no-build-isolation --no-deps -e "%PROJECT_DIRECTORY%"
if errorlevel 1 goto failed

if /i "%PROFILE%"=="full" (
    "%BUILD_PYTHON%" "%PROJECT_DIRECTORY%\securitymasker.py" model-load --model tsmatz/xlm-roberta-ner-japanese --revision aba094e118d5ffc622e9b25e07edc49f9dd85feb
    if errorlevel 1 goto failed
)

pushd "%PROJECT_DIRECTORY%"
if errorlevel 1 goto failed
set "SECURITYMASKER_BINARY_PROFILE=%PROFILE%"
set "SECURITYMASKER_BINARY_METADATA=%BUILD_METADATA%"
"%BUILD_VENV%\Scripts\pyinstaller.exe" --noconfirm --clean --distpath "%DIST_DIRECTORY%" --workpath "%WORK_DIRECTORY%" "%PROJECT_DIRECTORY%\securitymasker.spec"
set "RESULT=%errorlevel%"
popd
if not "%RESULT%"=="0" exit /b %RESULT%
if not exist "%OUTPUT%" (
    echo error: PyInstaller did not create %OUTPUT% 1>&2
    exit /b 1
)

echo Created %OUTPUT% ^(%PROFILE% profile^)
exit /b 0

:usage
echo usage: scripts\build-binary.cmd --profile lite^|full 1>&2
exit /b 2

:python_missing
echo error: 64-bit Python 3.12 was not found; set SECURITYMASKER_PYTHON to its full path 1>&2
exit /b 2

:python_invalid
echo error: Windows binary build requires 64-bit CPython 3.12 1>&2
exit /b 2

:build_exists
echo error: clean build directory required: %BUILD_DIRECTORY% 1>&2
exit /b 2

:output_exists
echo error: output already exists: %OUTPUT% 1>&2
exit /b 2

:failed
echo error: Windows %PROFILE% binary build failed; diagnostics remain in %BUILD_DIRECTORY% 1>&2
exit /b 1
