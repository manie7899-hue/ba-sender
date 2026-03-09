# -*- coding: utf-8 -*-
"""Хранение настроек, аккаунтов и прокси"""

import json
from pathlib import Path

STORAGE_FILE = Path(__file__).parent / "ba_sender_data.json"


def _get_storage_path() -> Path:
    """Путь к файлу хранения"""
    return STORAGE_FILE


def load_data() -> dict:
    """Загрузить все данные"""
    path = _get_storage_path()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            import logging
            logging.getLogger(__name__).warning("Не удалось загрузить данные: %s", e)
    return {
        "accounts": [],
        "proxy": "",
        "proxy_list": [],
        "delay_min": 1,
        "delay_max": 2,
        "max_concurrent": 3,
        "show_browser": True,
        "message": "Zdravo! Zanima me ovaj oglas. Da li je još uvijek dostupan?",
        "telegram_api_url": "",
        "telegram_api_key": "",
        "telegram_api_proxy": "",
        "telegram_api_enabled": False,
        "jobs": [],
    }


def save_data(data: dict) -> bool:
    """Сохранить данные"""
    try:
        path = _get_storage_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False
