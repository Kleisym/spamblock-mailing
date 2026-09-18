#!/usr/bin/env bash
echo "Остановка Telegram Spambuster..."
if systemctl is-active --quiet spambuster.service 2>/dev/null; then
    sudo systemctl stop spambuster.service
    echo "✅ Служба systemd остановлена."
fi

pkill -f "python.*bot\.py" 2>/dev/null && echo "✅ Процессы bot.py завершены." || echo "Активных процессов не обнаружено."
