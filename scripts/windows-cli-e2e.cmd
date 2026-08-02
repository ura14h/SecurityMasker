@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "SCRIPT_DIRECTORY=%~dp0"
for %%I in ("%SCRIPT_DIRECTORY%..") do set "PROJECT_DIRECTORY=%%~fI"
set "PYTHON=%PROJECT_DIRECTORY%\.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo error: run scripts\test-setup.cmd before the Windows CLI E2E 1>&2
    exit /b 2
)

call "%SCRIPT_DIRECTORY%windows-firewall-gate.cmd" verify
if errorlevel 1 (
    echo error: the verified standard-user firewall gate is required 1>&2
    exit /b 2
)

if not defined SM_CODEX_CLI (
    set "SM_CODEX_CLI=%LOCALAPPDATA%\Programs\OpenAI\Codex\bin\codex.exe"
)
if not defined SM_CLAUDE_CLI (
    set "SM_CLAUDE_CLI=%USERPROFILE%\.local\bin\claude.exe"
)
if not exist "%SM_CODEX_CLI%" (
    echo error: Codex CLI was not found; set SM_CODEX_CLI to its full path 1>&2
    exit /b 2
)
if not exist "%SM_CLAUDE_CLI%" (
    echo error: Claude Code CLI was not found; set SM_CLAUDE_CLI to its full path 1>&2
    exit /b 2
)

set "SM_RUN_CLI_E2E=1"
set "SM_REQUIRE_ALL_CLIS=1"
pushd "%PROJECT_DIRECTORY%"
if errorlevel 1 exit /b %errorlevel%
"%PYTHON%" -m pytest -v tests\integration\test_real_cli_e2e.py
set "RESULT=%errorlevel%"
popd
exit /b %RESULT%
