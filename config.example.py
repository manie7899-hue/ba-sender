# -*- coding: utf-8 -*-
"""Пример конфигурации. Скопируйте в config.py и заполните. На Render — env vars."""

import os

OLX_BASE_URL = "https://www.olx.ba"
OLX_LOGIN_URL = "https://www.olx.ba/login"
OLX_MESSAGES_URL = "https://www.olx.ba/poruke"
OLX_PROFILE_URL = "https://www.olx.ba/profil"

DEFAULT_DELAY_MIN = 1
DEFAULT_DELAY_MAX = 2
DEFAULT_MAX_CONCURRENT = 3

BLOCKED_KEYWORDS = [
    "blokiran", "blokirano", "blokirana", "blocked", "block",
    "suspend", "ban", "banovan", "onemogućen", "disabled",
    "zabranjen", "ukinut", "deaktiviran",
]
INVALID_CREDENTIALS_KEYWORDS = [
    "pogrešan", "pogrešna", "greška", "wrong", "invalid",
    "neispravan", "netačan", "netočan", "incorrect",
]

REDSCRIPT_API_BASE = "https://api.redscript.info"
REDSCRIPT_API_KEY = ""
REDSCRIPT_SERVICE = "OLX"

# Токен бота (env BOT_TOKEN на Render)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# ID чата для заявок (env ADMIN_CHAT_ID). 0 = модерация выключена
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0"))

SCREENSHOT_AFTER_SEND = True
