# -*- coding: utf-8 -*-
"""
Тест RedScript API (POST /team/createAd) — запустите: python test_telegram_api.py
"""

from config import REDSCRIPT_API_KEY
from telegram_api import create_telegram_link

TEST_URL = "https://olx.ba/artikal/74301639"
TEST_PROXY = "89.31.121.13:12709:modeler_505IU5:KUr8wc00A0BY"

def main():
    print("=== Тест RedScript API (createAd) ===\n")
    print(f"API ключ: {REDSCRIPT_API_KEY[:20]}...")
    print(f"URL объявления: {TEST_URL}")
    print(f"Прокси: {TEST_PROXY.split(':')[0]}:***")
    print()
    
    def log(msg):
        print(f"  {msg}")
    
    result = create_telegram_link(REDSCRIPT_API_KEY, TEST_URL, on_debug=log, proxy=TEST_PROXY)
    
    print(f"\nРезультат: {result or 'None (ошибка)'}")

if __name__ == "__main__":
    main()
