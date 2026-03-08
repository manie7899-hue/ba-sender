# Развёртывание BA Sender на хост

## Публикация в Git

```bash
# 1. Создайте репозиторий на GitHub/GitLab (пустой, без README)

# 2. Добавьте remote и запушьте
cd "C:\Users\ucomp\OneDrive\Desktop\BA send"
git remote add origin https://github.com/ВАШ_ЛОГИН/ba-sender.git
git branch -M main
git push -u origin main
```

## Быстрый старт на VPS (Ubuntu/Debian)

```bash
# 1. Клонировать репозиторий
git clone https://github.com/ВАШ_ЛОГИН/ba-sender.git /opt/ba-sender
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
