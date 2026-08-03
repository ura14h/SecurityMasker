@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "SCRIPT_DIRECTORY=%~dp0"
for %%I in ("%SCRIPT_DIRECTORY%..") do set "PROJECT_DIRECTORY=%%~fI"
set "PREFLIGHT=%PROJECT_DIRECTORY%\devtools\windows_source_gate.ps1"
set "PYTHON=%PROJECT_DIRECTORY%\.venv\Scripts\python.exe"

if /i "%~1"=="run" goto run_gate
echo Usage: scripts\windows-source-gate.cmd run 1>&2
exit /b 2

:run_gate
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%PREFLIGHT%" -Root "%PROJECT_DIRECTORY%"
if errorlevel 1 exit /b %errorlevel%

call "%SCRIPT_DIRECTORY%test-setup.cmd"
if errorlevel 1 exit /b %errorlevel%

call "%SCRIPT_DIRECTORY%release-check.cmd"
if errorlevel 1 exit /b %errorlevel%

"%PYTHON%" "%PROJECT_DIRECTORY%\securitymasker.py" init --mode chatgpt --port 45677
if errorlevel 1 exit /b %errorlevel%
"%PYTHON%" "%PROJECT_DIRECTORY%\securitymasker.py" init --directory "%PROJECT_DIRECTORY%\securitymasker-claude" --mode claude --port 45678
if errorlevel 1 exit /b %errorlevel%

set "CHATGPT_CONFIG=%PROJECT_DIRECTORY%\securitymasker.config"
set "CLAUDE_CONFIG=%PROJECT_DIRECTORY%\securitymasker-claude\securitymasker.config"
"%PYTHON%" "%PROJECT_DIRECTORY%\securitymasker.py" doctor --config "%CHATGPT_CONFIG%"
if errorlevel 1 exit /b %errorlevel%
"%PYTHON%" "%PROJECT_DIRECTORY%\securitymasker.py" doctor --config "%CLAUDE_CONFIG%"
if errorlevel 1 exit /b %errorlevel%
"%PYTHON%" "%PROJECT_DIRECTORY%\securitymasker.py" preview "synthetic.person@example.test" --config "%CHATGPT_CONFIG%"
if errorlevel 1 exit /b %errorlevel%
"%PYTHON%" "%PROJECT_DIRECTORY%\securitymasker.py" preview "synthetic.person@example.test" --config "%CLAUDE_CONFIG%"
if errorlevel 1 exit /b %errorlevel%
"%PYTHON%" "%PROJECT_DIRECTORY%\securitymasker.py" client-config --config "%CHATGPT_CONFIG%"
if errorlevel 1 exit /b %errorlevel%
"%PYTHON%" "%PROJECT_DIRECTORY%\securitymasker.py" client-config --config "%CLAUDE_CONFIG%"
if errorlevel 1 exit /b %errorlevel%

echo Windows standard-user source archive gate passed.
echo Network-isolated real CLI E2E is optional and requires an explicit firewall-gate setup.
exit /b 0
