FROM python:3.11-slim

ARG DEBIAN_FRONTEND=noninteractive

# Зависимости для Chromium
RUN apt-get update -q && apt-get install -y -qq --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libasound2 libpango-1.0-0 libcairo2 libatspi2.0-0 libdbus-1-3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements-bot.txt .
RUN pip install --no-cache-dir -r requirements-bot.txt && \
    playwright install chromium

COPY bot.py bot_storage.py sender.py telegram_api.py config.example.py ./
# config.example читает BOT_TOKEN и ADMIN_CHAT_ID из env
RUN cp config.example.py config.py
ENV PYTHONUNBUFFERED=1

CMD ["python", "bot.py"]
