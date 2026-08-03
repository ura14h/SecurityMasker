@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "EXPECTED_USER=SecurityMaskerTester"
set "BOOTSTRAP=C:\Users\Public\SecurityMaskerTesterBootstrap"
set "PYTHON_INSTALLER=%BOOTSTRAP%\python-3.12.10-amd64.exe"
set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
set "CODEX_SOURCE=%BOOTSTRAP%\codex.exe"
set "CLAUDE_SOURCE=%BOOTSTRAP%\claude.exe"
set "SM_BOOTSTRAP_ARCHIVE=%BOOTSTRAP%\securitymasker-1.0.0-source.tar.gz"
set "SM_BOOTSTRAP_CHECKSUM=%SM_BOOTSTRAP_ARCHIVE%.sha256"
set "DEVELOPER_DIRECTORY=%USERPROFILE%\Developer"
set "SOURCE_ROOT=%DEVELOPER_DIRECTORY%\securitymasker-1.0.0"

for /f "tokens=2 delims=\" %%U in ('whoami.exe') do set "CURRENT_USER=%%U"
if /i not "%CURRENT_USER%"=="%EXPECTED_USER%" (
    echo error: this runner must execute as SecurityMaskerTester 1>&2
    exit /b 2
)

for %%F in ("%PYTHON_INSTALLER%" "%CODEX_SOURCE%" "%CLAUDE_SOURCE%" "%SM_BOOTSTRAP_ARCHIVE%" "%SM_BOOTSTRAP_CHECKSUM%") do (
    if not exist "%%~F" (
        echo error: required bootstrap file was not found: %%~F 1>&2
        exit /b 2
    )
)

if exist "%PYTHON%" goto verify_python
start /wait "" "%PYTHON_INSTALLER%" /passive InstallAllUsers=0 TargetDir="%LOCALAPPDATA%\Programs\Python\Python312" PrependPath=1 Include_launcher=1 InstallLauncherAllUsers=0 Include_pip=1 Include_test=0
if errorlevel 1 (
    echo error: Python 3.12 installer failed 1>&2
    exit /b %errorlevel%
)

:verify_python
"%PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) and sys.maxsize > 2**32 else 1)"
if errorlevel 1 (
    echo error: 64-bit Python 3.12 was not installed for SecurityMaskerTester 1>&2
    exit /b 2
)

if not exist "%LOCALAPPDATA%\Programs\OpenAI\Codex\bin\" mkdir "%LOCALAPPDATA%\Programs\OpenAI\Codex\bin"
if errorlevel 1 exit /b %errorlevel%
copy /y "%CODEX_SOURCE%" "%LOCALAPPDATA%\Programs\OpenAI\Codex\bin\codex.exe" >nul
if errorlevel 1 exit /b %errorlevel%

if not exist "%USERPROFILE%\.local\bin\" mkdir "%USERPROFILE%\.local\bin"
if errorlevel 1 exit /b %errorlevel%
copy /y "%CLAUDE_SOURCE%" "%USERPROFILE%\.local\bin\claude.exe" >nul
if errorlevel 1 exit /b %errorlevel%

powershell.exe -NoLogo -NoProfile -NonInteractive -Command "$expected=((Get-Content -LiteralPath $env:SM_BOOTSTRAP_CHECKSUM -Raw) -split '\s+')[0].ToLowerInvariant(); $actual=(Get-FileHash -Algorithm SHA256 -LiteralPath $env:SM_BOOTSTRAP_ARCHIVE).Hash.ToLowerInvariant(); if($actual -ne $expected){throw 'source archive checksum mismatch'}; Write-Output ('verified source archive ' + $actual)"
if errorlevel 1 exit /b %errorlevel%

if exist "%SOURCE_ROOT%\" (
    echo error: fresh source directory already exists: %SOURCE_ROOT% 1>&2
    exit /b 2
)
if not exist "%DEVELOPER_DIRECTORY%\" mkdir "%DEVELOPER_DIRECTORY%"
if errorlevel 1 exit /b %errorlevel%
tar.exe -xzf "%SM_BOOTSTRAP_ARCHIVE%" -C "%DEVELOPER_DIRECTORY%"
if errorlevel 1 exit /b %errorlevel%
if not exist "%SOURCE_ROOT%\scripts\windows-source-gate.cmd" (
    echo error: extracted source archive has an unexpected layout 1>&2
    exit /b 2
)

set "SECURITYMASKER_PYTHON=%PYTHON%"
cd /d "%SOURCE_ROOT%"
if errorlevel 1 exit /b %errorlevel%
call scripts\windows-source-gate.cmd run
set "RESULT=%errorlevel%"
if "%RESULT%"=="0" echo Close this window, then install the firewall gate from an elevated developer cmd.
exit /b %RESULT%
