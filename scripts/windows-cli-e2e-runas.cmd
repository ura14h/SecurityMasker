@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "SCRIPT_DIRECTORY=%~dp0"
for %%I in ("%SCRIPT_DIRECTORY%..") do set "PROJECT_DIRECTORY=%%~fI"
set "BOOTSTRAP=C:\Users\Public\SecurityMaskerTesterBootstrap"
set "SESSION_RUNNER=%PROJECT_DIRECTORY%\devtools\windows_cli_e2e_session.cmd"
set "PUBLIC_RUNNER=%BOOTSTRAP%\run-cli-e2e.cmd"

if not exist "%BOOTSTRAP%\" (
    echo error: SecurityMaskerTester bootstrap directory was not found 1>&2
    exit /b 2
)
if not exist "%SESSION_RUNNER%" (
    echo error: CLI E2E session runner was not found 1>&2
    exit /b 2
)

call "%SCRIPT_DIRECTORY%windows-firewall-gate.cmd" install "%COMPUTERNAME%\SecurityMaskerTester"
if errorlevel 1 exit /b %errorlevel%

copy /y "%SESSION_RUNNER%" "%PUBLIC_RUNNER%" >nul
if errorlevel 1 exit /b %errorlevel%

echo Enter the password for SecurityMaskerTester at the runas.exe prompt.
runas.exe /profile /user:%COMPUTERNAME%\SecurityMaskerTester "cmd.exe /k %PUBLIC_RUNNER%"
exit /b %errorlevel%
