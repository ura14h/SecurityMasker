@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "EXPECTED_USER=SecurityMaskerTester"
set "SOURCE_ROOT=%USERPROFILE%\Developer\securitymasker-1.0.0"

for /f "tokens=2 delims=\" %%U in ('whoami.exe') do set "CURRENT_USER=%%U"
if /i not "%CURRENT_USER%"=="%EXPECTED_USER%" (
    echo error: this runner must execute as SecurityMaskerTester 1>&2
    exit /b 2
)
if not exist "%SOURCE_ROOT%\scripts\windows-cli-e2e.cmd" (
    echo error: successful source archive gate environment was not found 1>&2
    exit /b 2
)

cd /d "%SOURCE_ROOT%"
if errorlevel 1 exit /b %errorlevel%
call scripts\windows-cli-e2e.cmd
set "RESULT=%errorlevel%"
if "%RESULT%"=="0" echo Windows network-isolated real CLI E2E passed. Close this window and remove the test user.
exit /b %RESULT%
