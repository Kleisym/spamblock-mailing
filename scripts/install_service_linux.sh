#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
CURRENT_USER="$(whoami)"

PYTHON_BIN="$(which python3)"
if [ -d "$ROOT_DIR/venv" ]; then
    PYTHON_BIN="$ROOT_DIR/venv/bin/python3"
fi

SERVICE_FILE="/etc/systemd/system/spambuster.service"

echo "=========================================================="
echo " Настройка 24/7 службы systemd для Telegram Spambuster"
echo "=========================================================="
echo "Папка проекта: $ROOT_DIR"
echo "Пользователь:  $CURRENT_USER"
echo "Python:        $PYTHON_BIN"
echo ""

if [ "$EUID" -ne 0 ]; then
    echo "⚠️ Для установки службы требуются права sudo."
    echo "Перезапустите: sudo bash $0"
    exit 1
fi

cat <<EOF > "$SERVICE_FILE"
[Unit]
Description=Telegram Spambuster 24/7 Background Service
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$ROOT_DIR
ExecStart=$PYTHON_BIN bot.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

chmod 644 "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable spambuster.service
systemctl restart spambuster.service

echo "✅ Служба успешно установлена и запущена 24/7!"
echo "Команды управления:"
echo "  sudo systemctl status spambuster   - статус работы"
echo "  sudo systemctl stop spambuster     - остановить"
echo "  sudo systemctl restart spambuster  - перезапустить"
echo "  sudo journalctl -u spambuster -f   - просмотр логов"
