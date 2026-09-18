@echo off
chcp 65001 >nul
echo ======================================================
echo  Удаление автозапуска Telegram Spambuster (Windows)
echo ======================================================
echo.

set "STARTUP_FOLDER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "TARGET_VBS=%STARTUP_FOLDER%\TelegramSpambusterAutostart.vbs"

if exist "%TARGET_VBS%" (
    del /f /q "%TARGET_VBS%"
    echo ✅ Автозапуск успешно удален из папки Автозагрузки.
) else (
    echo [i] Файл автозапуска не найден в Автозагрузке.
)

echo.
pause
