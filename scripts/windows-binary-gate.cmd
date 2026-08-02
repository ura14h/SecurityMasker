@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "SCRIPT_DIRECTORY=%~dp0"

call "%SCRIPT_DIRECTORY%build-binary.cmd" --profile lite
if errorlevel 1 exit /b %errorlevel%
call "%SCRIPT_DIRECTORY%test-binary.cmd" --profile lite
if errorlevel 1 exit /b %errorlevel%
call "%SCRIPT_DIRECTORY%build-binary.cmd" --profile full
if errorlevel 1 exit /b %errorlevel%
call "%SCRIPT_DIRECTORY%test-binary.cmd" --profile full
if errorlevel 1 exit /b %errorlevel%

echo Windows x64 Lite and Full one-file technical spike gates passed.
exit /b 0
