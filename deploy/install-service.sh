#!/bin/bash
# Ставит systemd сервис для текущей папки проекта

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "Установка сервиса для $PROJECT_DIR..."
cd "$PROJECT_DIR"

# Сначала установка зависимостей
./deploy/install.sh

# Путь к проекту в service
sudo cp deploy/ba-sender.service /etc/systemd/system/
sudo sed -i "s|WorkingDirectory=.*|WorkingDirectory=$PROJECT_DIR|" /etc/systemd/system/ba-sender.service
sudo sed -i "s|ExecStart=.*|ExecStart=$PROJECT_DIR/venv/bin/python bot.py|" /etc/systemd/system/ba-sender.service
sudo sed -i "s|Environment=.*|Environment=\"PATH=$PROJECT_DIR/venv/bin\"|" /etc/systemd/system/ba-sender.service
sudo sed -i "s|User=.*|User=$USER|" /etc/systemd/system/ba-sender.service

sudo systemctl daemon-reload
sudo systemctl enable ba-sender
sudo systemctl start ba-sender

echo "Сервис ba-sender установлен и запущен"
echo "Логи: journalctl -u ba-sender -f"
