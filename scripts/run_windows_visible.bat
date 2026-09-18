@echo off
title Spambuster Userbot
chcp 65001 >nul
cd /d "%~dp0\.."

echo [Spambuster] Starting Telegram Userbot...
python bot.py
if errorlevel 1 (
    echo.
    echo [ERROR] Process ended with error.
    pause
)
