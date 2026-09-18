#!/data/data/com.termux/files/usr/bin/bash
set -e

echo "=========================================================="
echo " Настройка 24/7 автозапуска в Termux (Android)"
echo "=========================================================="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

mkdir -p ~/.termux/boot

BOOT_SCRIPT=~/.termux/boot/spambuster.sh

cat <<EOF > "$BOOT_SCRIPT"
#!/data/data/com.termux/files/usr/bin/bash
termux-wake-lock
cd "$ROOT_DIR"
while true; do
    echo "Starting Spambuster 24/7..."
    python bot.py
    echo "Spambuster stopped. Restarting in 5 seconds..."
    sleep 5
done
EOF

chmod +x "$BOOT_SCRIPT"
termux-wake-lock

echo "✅ Автозапуск для Termux:Boot настроен!"
echo "Файл: $BOOT_SCRIPT"
echo "Убедитесь, что у вас установлено приложение Termux:Boot из F-Droid."
echo "Блокировка сна (wake-lock) активирована, бот не заснет при выключенном экране."
