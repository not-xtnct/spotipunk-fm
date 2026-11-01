@echo off
setlocal
title Spotify FM companion
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo Python 3.10 or newer is required.
    echo Download it from https://www.python.org/downloads/ and tick "Add python.exe to PATH" during install.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo First run: preparing Python environment...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo Could not create the Python environment in "%~dp0".
        echo If the game is under Program Files, copy this companion folder somewhere else
        echo ^(e.g. Documents^) and set "mod_dir" in config.json. See README.
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
    if errorlevel 1 echo Optional package pycaw failed to install; volume fades will use Spotify's own volume instead.
)

".venv\Scripts\python.exe" spotify_fm.py %*
pause
