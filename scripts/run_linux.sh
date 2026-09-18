#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$ROOT_DIR"

if [ -d "venv" ]; then
    source venv/bin/activate
fi

echo "🚀 Запуск Telegram Spambuster..."
python3 bot.py
