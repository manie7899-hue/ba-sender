@echo off
chcp 65001 >nul
echo ========================================
echo   BA Sender - Сборка EXE
echo ========================================
echo.

REM Проверка Python
python --version >nul 2>&1
if errorlevel 1 (
    echo Ошибка: Python не найден! Установите Python 3.10+
    pause
    exit /b 1
)

REM Установка зависимостей
echo Установка зависимостей...
pip install -r requirements.txt -q
playwright install chromium

echo.
echo Сборка EXE...
pyinstaller --noconfirm --onefile --windowed ^
    --name "BA_Sender" ^
    --add-data "config.py;." ^
    --hidden-import "playwright" ^
    --hidden-import "customtkinter" ^
    --collect-all "customtkinter" ^
    --collect-all "playwright" ^
    app.py

if errorlevel 1 (
    echo.
    echo Ошибка сборки!
    pause
    exit /b 1
)

echo.
echo ========================================
echo   Готово! EXE: dist\BA_Sender.exe
echo ========================================
pause
