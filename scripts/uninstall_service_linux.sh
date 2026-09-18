#!/usr/bin/env bash
set -e

if [ "$EUID" -ne 0 ]; then
    echo "⚠️ Требуются права sudo: sudo bash $0"
    exit 1
fi

echo "Остановка и удаление службы spambuster..."
systemctl stop spambuster.service 2>/dev/null || true
systemctl disable spambuster.service 2>/dev/null || true

if [ -f "/etc/systemd/system/spambuster.service" ]; then
    rm -f "/etc/systemd/system/spambuster.service"
    systemctl daemon-reload
    echo "✅ Служба spambuster удалена."
else
    echo "Служба не была найдена."
fi
