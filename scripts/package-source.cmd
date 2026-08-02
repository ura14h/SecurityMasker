@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "SCRIPT_DIRECTORY=%~dp0"
for %%I in ("%SCRIPT_DIRECTORY%..") do set "PROJECT_DIRECTORY=%%~fI"
set "PYTHON=%PROJECT_DIRECTORY%\.venv\Scripts\python.exe"
set "REFERENCE=%~1"
if not defined REFERENCE set "REFERENCE=HEAD"

if not exist "%PYTHON%" (
    echo error: run scripts\setup.cmd first 1>&2
    exit /b 2
)

git -C "%PROJECT_DIRECTORY%" diff --quiet
if errorlevel 1 goto dirty
git -C "%PROJECT_DIRECTORY%" diff --cached --quiet
if errorlevel 1 goto dirty

for /f "tokens=2" %%V in ('""%PYTHON%" "%PROJECT_DIRECTORY%\securitymasker.py" --version"') do set "VERSION=%%V"
if not defined VERSION (
    echo error: could not determine the SecurityMasker version 1>&2
    exit /b 2
)

set "OUTPUT_DIRECTORY=%PROJECT_DIRECTORY%\dist"
set "ARCHIVE=%OUTPUT_DIRECTORY%\securitymasker-%VERSION%-source.tar.gz"
set "CHECKSUM=%ARCHIVE%.sha256"
if exist "%ARCHIVE%" goto exists
if exist "%CHECKSUM%" goto exists
if not exist "%OUTPUT_DIRECTORY%" mkdir "%OUTPUT_DIRECTORY%"
if errorlevel 1 exit /b %errorlevel%

git -C "%PROJECT_DIRECTORY%" archive --format=tar.gz --prefix="securitymasker-%VERSION%/" --output="%ARCHIVE%" "%REFERENCE%"
if errorlevel 1 exit /b %errorlevel%
"%PYTHON%" -c "import hashlib,pathlib,sys; p=pathlib.Path(sys.argv[1]); print(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}')" "%ARCHIVE%" > "%CHECKSUM%"
if errorlevel 1 (
    del /q "%ARCHIVE%" "%CHECKSUM%" >nul 2>&1
    exit /b 1
)

echo Created %ARCHIVE%
echo Created %CHECKSUM%
exit /b 0

:dirty
echo error: source archive requires a clean worktree 1>&2
exit /b 2

:exists
echo error: release artifact already exists for version %VERSION% 1>&2
exit /b 2
