@echo off
REM Start CASE. Double-click this file.
REM Minimise the black window to keep the bot running.

cd /d "%~dp0"
title CASE - Music Bot

set "PY=%~dp0venv\Scripts\python.exe"

if not exist "%PY%" (
    echo Setting up for the first time...
    echo.
    where python >nul 2>&1
    if errorlevel 1 (
        echo Python was not found on your PATH.
        echo Install Python 3.11+ from python.org and tick "Add to PATH".
        pause
        exit /b 1
    )
    python -m venv venv
    if errorlevel 1 (
        echo Could not create the virtual environment.
        pause
        exit /b 1
    )
    "%PY%" -m pip install --upgrade pip
    "%PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Could not install the packages.
        pause
        exit /b 1
    )
)

"%PY%" -c "import discord, wavelink, aiohttp, yt_dlp, nacl" >nul 2>&1
if errorlevel 1 (
    echo Installing missing packages...
    "%PY%" -m pip install -r requirements.txt
)

"%PY%" -c "import discord.abc, inspect, sys; sys.exit(0 if 'self_attrib' not in inspect.signature(discord.abc.Connectable.connect).parameters else 1)" >nul 2>&1
if errorlevel 1 (
    echo ffmpeg check: run 'ffmpeg -version' in a new terminal if playback fails.
)

echo.
echo   CASE is starting.
echo   Minimise this window - closing it stops the bot.
echo.
"%PY%" bot.py

echo.
echo Bot stopped. Safe to close this window.
pause