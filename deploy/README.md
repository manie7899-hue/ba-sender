# Развёртывание BA Sender на хост

## Быстрый старт (VPS Ubuntu/Debian)

```bash
# 1. Клонировать репозиторий
git clone <URL_РЕПОЗИТОРИЯ> /opt/ba-sender
cd /opt/ba-sender

# 2. Установить зависимости
chmod +x deploy/*.sh
./deploy/install.sh

# 3. Настроить config.py
nano config.py   # BOT_TOKEN, ADMIN_CHAT_ID

# 4. Запуск вручную
./deploy/run.sh

# Или как сервис (автозапуск)
sudo ./deploy/install-service.sh
```

## Переменные окружения (опционально)

- `BOT_TOKEN` — токен бота (если не в config.py)
- `ADMIN_CHAT_ID` — ID чата для заявок

## Логи

```bash
journalctl -u ba-sender -f
```
