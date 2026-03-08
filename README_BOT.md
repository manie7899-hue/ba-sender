# BA Sender — Telegram-бот

Запуск отправки на OLX.BA прямо из Telegram, без установки на другом ПК.

## Установка

1. Создайте бота в Telegram: [@BotFather](https://t.me/BotFather) → `/newbot`
2. Скопируйте токен
3. В `config.py` укажите: `BOT_TOKEN = "ваш_токен"`
4. Установите зависимости: `pip install -r requirements.txt`
5. Установите браузер Playwright: `playwright install chromium`

## Запуск

```bash
python bot.py
```

Бот должен работать на ПК или сервере, где установлен Playwright (Chromium). Для работы на VPS — установите зависимости и запустите бот.

## Команды

- `/start` — главное меню
- `/add_account` — добавить аккаунт (отправьте `email:пароль`)
- `/add_session` — добавить сессию
- `/session 1 proxy` — создать сессию (1=аккаунт, proxy или `-`)
- `/session 1 - url1 url2` — сессия со ссылками одной командой
- `/redscript` — настройка RedScript API
- `/del_account 1` — удалить аккаунт
- `/logs` — последние логи

## Добавление сессии

1. `/session 1 -` — создать сессию с аккаунтом 1, без прокси
2. Отправьте ссылки (каждая с новой строки)

Или одной командой: `/session 1 ip:port:user:pass https://olx.ba/artikal/123 https://olx.ba/artikal/456`

## RedScript

Отправьте боту:
- API ключ (длинная строка)
- `proxy ip:port:user:pass` — прокси для API
- `on` / `off` — вкл/выкл
