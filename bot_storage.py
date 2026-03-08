# -*- coding: utf-8 -*-
"""Хранение данных пользователей Telegram-бота"""

import json
from pathlib import Path
from typing import Optional

BOT_STORAGE_DIR = Path(__file__).parent / "bot_data"
_COUNTER_FILE = BOT_STORAGE_DIR / "_next_bot_id.json"
_SEARCH_ID_FILE = BOT_STORAGE_DIR / "_next_search_id.json"


def get_next_search_id() -> str:
    """Уникальный ID лога в формате #search000001"""
    BOT_STORAGE_DIR.mkdir(exist_ok=True)
    next_id = 1
    if _SEARCH_ID_FILE.exists():
        try:
            with open(_SEARCH_ID_FILE, "r", encoding="utf-8") as f:
                next_id = json.load(f).get("next", 1)
        except Exception:
            pass
    sid = f"#search{next_id:06d}"
    with open(_SEARCH_ID_FILE, "w", encoding="utf-8") as f:
        json.dump({"next": next_id + 1}, f)
    return sid


def get_or_create_bot_user_id(telegram_user_id: int) -> str:
    """Уникальный ID пользователя в боте (USR-0001, USR-0002, ...)."""
    data = load_user_data(telegram_user_id)
    if data.get("bot_user_id"):
        return data["bot_user_id"]
    BOT_STORAGE_DIR.mkdir(exist_ok=True)
    next_id = 1
    if _COUNTER_FILE.exists():
        try:
            with open(_COUNTER_FILE, "r", encoding="utf-8") as f:
                next_id = json.load(f).get("next", 1)
        except Exception:
            pass
    bot_id = f"USR-{next_id:04d}"
    with open(_COUNTER_FILE, "w", encoding="utf-8") as f:
        json.dump({"next": next_id + 1}, f)
    data["bot_user_id"] = bot_id
    save_user_data(telegram_user_id, data)
    return bot_id


def _user_file(user_id: int) -> Path:
    BOT_STORAGE_DIR.mkdir(exist_ok=True)
    return BOT_STORAGE_DIR / f"user_{user_id}.json"


def _default_data() -> dict:
    return {
        "accounts": [],
        "message": "Zdravo! Zanima me ovaj oglas. Da li je još uvijek dostupan?",
        "delay_min": 1,
        "delay_max": 2,
        "telegram_api_key": "",
        "telegram_api_proxy": "",
        "telegram_api_enabled": False,
        "jobs": [],
        "access_status": "new",  # new | pending | approved | rejected
    }


def load_user_data(user_id: int) -> dict:
    path = _user_file(user_id)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k, v in _default_data().items():
                    if k not in data:
                        data[k] = v
                return data
        except Exception:
            pass
    return _default_data().copy()


def save_user_data(user_id: int, data: dict) -> bool:
    try:
        path = _user_file(user_id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def list_pending_users() -> list[tuple[int, dict]]:
    """Список пользователей со статусом pending."""
    result = []
    BOT_STORAGE_DIR.mkdir(exist_ok=True)
    for path in BOT_STORAGE_DIR.glob("user_*.json"):
        try:
            uid = int(path.stem.split("_")[1])
            data = load_user_data(uid)
            if data.get("access_status") == "pending":
                result.append((uid, data))
        except (ValueError, Exception):
            continue
    return result
