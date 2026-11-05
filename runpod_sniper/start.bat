@echo off
rem Starts the Runpod GPU sniper: double-click this file.
rem Needs Python 3.10 or newer and nothing else (no pip install, no Node).
rem Extra arguments are passed on, e.g.  start.bat --dry-run
setlocal
cd /d "%~dp0"

rem Find Python: the "py" launcher first, then "python".
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY goto nopython

rem The version check also catches the Microsoft Store "python" stub,
rem which exists on a fresh Windows but is not Python.
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto nopython

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo Created .env from .env.example.
    echo Put your Runpod API key in it (RUNPOD_API_KEY=...^), save, then run start.bat again.
    echo Opening it in Notepad now.
    start "" notepad ".env"
    echo.
    pause
    exit /b 1
)

echo.
echo   Runpod GPU sniper: open the http://127.0.0.1:... link below in your browser.
echo   Close this window (or press Ctrl+C) to stop it.
echo.
%PY% server.py %*
echo.
pause
exit /b 0

:nopython
echo Python 3.10 or newer was not found.
echo Install it from https://www.python.org/downloads/ - tick "Add python.exe to PATH" -
echo then run start.bat again.
echo.
pause
exit /b 1
