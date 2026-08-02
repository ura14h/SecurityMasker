@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "SCRIPT_DIRECTORY=%~dp0"
for %%I in ("%SCRIPT_DIRECTORY%..") do set "PROJECT_DIRECTORY=%%~fI"
set "VENV_DIRECTORY=%PROJECT_DIRECTORY%\.venv"
set "VENV_PYTHON=%VENV_DIRECTORY%\Scripts\python.exe"

if exist "%VENV_PYTHON%" goto verify_venv

if defined SECURITYMASKER_PYTHON (
    "%SECURITYMASKER_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) and sys.maxsize > 2**32 else 1)"
    if errorlevel 1 goto python_error
    "%SECURITYMASKER_PYTHON%" -m venv "%VENV_DIRECTORY%"
) else (
    py -3.12 -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) and sys.maxsize > 2**32 else 1)" >nul 2>&1
    if errorlevel 1 goto python_error
    py -3.12 -m venv "%VENV_DIRECTORY%"
)
if errorlevel 1 exit /b %errorlevel%

:verify_venv
"%VENV_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) and sys.maxsize > 2**32 else 1)"
if errorlevel 1 (
    echo error: .venv must use 64-bit Python 3.12 1>&2
    exit /b 2
)

"%VENV_PYTHON%" -m pip install --disable-pip-version-check --only-binary=:all: -r "%PROJECT_DIRECTORY%\requirements-windows.lock"
if errorlevel 1 exit /b %errorlevel%
"%VENV_PYTHON%" -m pip install --disable-pip-version-check --no-build-isolation --no-deps -e "%PROJECT_DIRECTORY%"
if errorlevel 1 exit /b %errorlevel%
"%VENV_PYTHON%" "%PROJECT_DIRECTORY%\securitymasker.py" model-load --model tsmatz/xlm-roberta-ner-japanese --revision aba094e118d5ffc622e9b25e07edc49f9dd85feb
if errorlevel 1 exit /b %errorlevel%

echo SecurityMasker Windows source environment and verified Japanese NER model are ready.
echo Initialize chatgpt: "%VENV_PYTHON%" "%PROJECT_DIRECTORY%\securitymasker.py" init --mode chatgpt
echo Initialize claude:  "%VENV_PYTHON%" "%PROJECT_DIRECTORY%\securitymasker.py" init --mode claude
exit /b 0

:python_error
echo error: 64-bit Python 3.12 was not found; install it with: py install 3.12 1>&2
exit /b 2

