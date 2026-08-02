@echo off
call "%~dp0build-binary.cmd" --profile full %*
exit /b %errorlevel%
