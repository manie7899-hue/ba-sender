@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo BA Sender - Запуск...
echo.

if not exist "venv" (
    echo Создание виртуального окружения...
    python -m venv venv
)

call venv\Scripts\activate.bat

pip install -r requirements.txt -q 2>nul
echo Установка браузера Chromium для Playwright...
playwright install chromium
if errorlevel 1 (
    echo.
    echo Ошибка установки браузера. Попробуйте: playwright install chromium
    pause
    exit /b 1
)
echo.
python app.py

pause
