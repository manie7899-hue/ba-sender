#!/bin/bash
# Установка BA Sender на Linux (Ubuntu/Debian)

set -e
cd "$(dirname "$0")/.."

echo "=== BA Sender: установка ==="

# Python + зависимости
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv

# Зависимости для Playwright (Chromium)
sudo apt-get install -y libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
  libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
  libxrandr2 libgbm1 libasound2 libpango-1.0-0 libcairo2

# Виртуальное окружение
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-bot.txt

# Playwright browsers
playwright install chromium
playwright install-deps chromium 2>/dev/null || true

# Конфиг
if [ ! -f config.py ]; then
  cp config.example.py config.py
  echo "Создан config.py — отредактируйте BOT_TOKEN и ADMIN_CHAT_ID"
fi

# Папка данных
mkdir -p bot_data

echo ""
echo "Готово. Запуск: ./deploy/run.sh"
echo "Или через systemd: sudo ./deploy/install-service.sh"
