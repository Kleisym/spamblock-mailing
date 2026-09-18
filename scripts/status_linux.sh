#!/usr/bin/env bash
if systemctl is-active --quiet spambuster.service 2>/dev/null; then
    echo "✅ Служба spambuster АКТИВНА (работает в фоне 24/7):"
    systemctl status spambuster.service --no-pager
else
    echo "ℹ️ Проверка запущенных процессов bot.py:"
    pgrep -fl "python.*bot\.py" || echo "Процессы не найдены."
fi
