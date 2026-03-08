@echo off
chcp 65001 >nul
cd /d "%~dp0\.."

echo === BA Sender: установка на Windows ===

if not exist "config.py" (
    copy config.example.py config.py
    echo Создан config.py — отредактируйте BOT_TOKEN и ADMIN_CHAT_ID
)

if not exist "venv" (
    python -m venv venv
)
call venv\Scripts\activate.bat

pip install -r requirements-bot.txt
playwright install chromium

if not exist "bot_data" mkdir bot_data

echo.
echo Готово. Запуск: deploy\run.bat
pause
