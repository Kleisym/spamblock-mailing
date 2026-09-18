@echo off
chcp 65001 >nul
echo ======================================================
echo  Установка автозапуска Telegram Spambuster (Windows)
echo ======================================================
echo.

set "STARTUP_FOLDER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "TARGET_VBS=%STARTUP_FOLDER%\TelegramSpambusterAutostart.vbs"
set "ROOT_DIR=%~dp0.."

echo Создание скрипта автозапуска в папке Автозагрузки...
(
echo Set WshShell = CreateObject^("WScript.Shell"^)
echo WshShell.CurrentDirectory = "%ROOT_DIR%"
echo WshShell.Run "cmd.exe /c python bot.py", 0, False
) > "%TARGET_VBS%"

if exist "%TARGET_VBS%" (
    echo.
    echo ✅ Успешно установлено!
    echo Файл: "%TARGET_VBS%"
    echo.
    echo Бот будет автоматически тихо запускаться при каждом включении компьютера.
    echo Чтобы запустить его прямо сейчас в фоне, запускаем...
    wscript.exe "%TARGET_VBS%"
    echo ✅ Бот запущен в фоне!
) else (
    echo ❌ Ошибка создания файла автозапуска.
)

echo.
pause
