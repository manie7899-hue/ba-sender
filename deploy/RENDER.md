# Развёртывание на Render.com

## 1. Подключите репозиторий

Dashboard → New → **Background Worker** → подключите `manie7899-hue/ba-sender`.

## 2. Настройки (что вводить)

| Поле | Значение |
|-----|----------|
| **Name** | ba-sender |
| **Region** | Oregon (US West) или ближе |
| **Branch** | main |
| **Root Directory** | оставить пустым |
| **Runtime** | **Docker** (обязательно, не Native) |
| **Build Command** | не нужно (Docker сам собирает) |
| **Start Command** | не нужно (в Dockerfile: `python bot.py`) |
| **Instance Type** | **Standard** (2 GB RAM) — 512 MB мало для Chromium |

## 3. Persistent Disk (чтобы данные не терялись при деплое)

Без диска **аккаунты, одобрения и заявки сбрасываются** при каждом обновлении.

1. В настройках сервиса → **Disks** → **Add Disk**
2. **Name:** `ba-sender-data`
3. **Mount Path:** `/data`
4. **Size:** 1 GB
5. Добавьте переменную окружения:
   - **Key:** `BOT_DATA_PATH`
   - **Value:** `/data`

## 4. Environment Variables

| Key | Value | Secret? |
|-----|-------|---------|
| `BOT_TOKEN` | токен от @BotFather | ✅ Да |
| `ADMIN_CHAT_ID` | ID чата для заявок | Нет |
| `BOT_DATA_PATH` | `/data` (если добавлен диск) | Нет |

## 5. Deploy

Нажмите **Create Background Worker**. Render соберёт Docker-образ и запустит бота.

## Важно

- **Runtime = Docker** — без Docker Playwright не заработает
- **Persistent Disk** — без него после каждого деплоя нужно подавать заявки заново
- **Минимум 2 GB RAM** — Chromium требует памяти
