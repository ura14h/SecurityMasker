@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "SCRIPT_DIRECTORY=%~dp0"
set "GATE_SCRIPT=%SCRIPT_DIRECTORY%..\devtools\windows_firewall_gate.ps1"

if /i "%~1"=="install" goto install
if /i "%~1"=="verify" goto verify
if /i "%~1"=="remove" goto remove_gate

echo Usage: scripts\windows-firewall-gate.cmd ^<install USER^|verify^|remove^> 1>&2
exit /b 2

:install
if "%~2"=="" (
    echo error: install requires a dedicated local standard user name 1>&2
    exit /b 2
)
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%GATE_SCRIPT%" -Action Install -User "%~2"
exit /b %errorlevel%

:verify
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%GATE_SCRIPT%" -Action Verify
exit /b %errorlevel%

:remove_gate
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%GATE_SCRIPT%" -Action Remove
exit /b %errorlevel%
