@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "SCRIPT_DIRECTORY=%~dp0"
set "LIFECYCLE_SCRIPT=%SCRIPT_DIRECTORY%..\devtools\windows_test_user.ps1"

if /i "%~1"=="setup" goto setup
if /i "%~1"=="remove" goto remove_tester
if /i "%~1"=="verify-absent" goto verify_absent

echo Usage: scripts\windows-test-user.cmd ^<setup^|remove^|verify-absent^> 1>&2
exit /b 2

:setup
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%LIFECYCLE_SCRIPT%" -Action Setup
exit /b %errorlevel%

:remove_tester
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%LIFECYCLE_SCRIPT%" -Action Remove
exit /b %errorlevel%

:verify_absent
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%LIFECYCLE_SCRIPT%" -Action VerifyAbsent
exit /b %errorlevel%
