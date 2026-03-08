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

## 3. Environment Variables

В разделе **Environment** добавьте:

| Key | Value | Secret? |
|-----|-------|---------|
| `BOT_TOKEN` | токен от @BotFather | ✅ Да |
| `ADMIN_CHAT_ID` | ID чата для заявок (например -1001234567890) | Нет |

## 4. Deploy

Нажмите **Create Background Worker**. Render соберёт Docker-образ и запустит бота.

## Важно

- **Runtime = Docker** — без Docker Playwright не заработает
- **Минимум 2 GB RAM** — Chromium требует памяти
- Starter ($7, 512 MB) скорее всего не подойдёт
