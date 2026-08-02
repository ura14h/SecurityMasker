@echo off
call "%~dp0build-binary.cmd" --profile lite %*
exit /b %errorlevel%
