@echo off
rem Starts the AWS GPU sniper page: double-click this file.
rem Needs Python 3.10 or newer; installs boto3 (the AWS library) the first time.
rem Extra arguments are passed on, e.g.  start.bat --no-sound
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

%PY% -c "import boto3" >nul 2>nul
if errorlevel 1 (
    echo Installing boto3, the AWS library ^(one time^)...
    %PY% -m pip install --disable-pip-version-check --index-url https://pypi.org/simple -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Could not install boto3. Run this yourself, then start.bat again:
        echo     %PY% -m pip install boto3
        pause
        exit /b 1
    )
)

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo Created .env from .env.example.
    echo Fill in your AWS access key and secret, and your Ember licence key and node tag,
    echo save, then run start.bat again. Opening it in Notepad now.
    start "" notepad ".env"
    echo.
    pause
    exit /b 1
)

echo.
echo   AWS GPU sniper: opening http://127.0.0.1:7896 in your browser.
echo   Close this window (or press Ctrl+C) to stop it.
echo.
start "" cmd /c "timeout /t 3 >nul & start "" http://127.0.0.1:7896"
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
