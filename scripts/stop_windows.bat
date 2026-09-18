@echo off
chcp 65001 >nul
echo [Spambuster] Поиск и остановка фоновых процессов bot.py...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*bot.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host ('[OK] Процесс остановлен: PID ' + $_.ProcessId) }"
echo Готово.
pause
