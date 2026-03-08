# Развёртывание BA Sender на хост

## Windows (локально или VPS)

```cmd
cd C:\opt\ba-sender
deploy\install.bat
notepad config.py   REM BOT_TOKEN, ADMIN_CHAT_ID
deploy\run.bat
```

## Linux (Ubuntu/Debian VPS)

```bash
git clone https://github.com/manie7899-hue/ba-sender.git /opt/ba-sender
cd /opt/ba-sender
chmod +x deploy/*.sh
./deploy/install.sh
nano config.py   # BOT_TOKEN, ADMIN_CHAT_ID
./deploy/run.sh
# Или как сервис: sudo ./deploy/install-service.sh
```

## Переменные окружения (опционально)

- `BOT_TOKEN` — токен бота (если не в config.py)
- `ADMIN_CHAT_ID` — ID чата для заявок

## Логи

```bash
journalctl -u ba-sender -f
```
