# -*- coding: utf-8 -*-
"""
Интеграция с RedScript API — создание объявлений и получение ссылок
Эндпоинт: POST /team/createAd
Документация: https://docs.redscript.info/docs/
"""

import json
import re
from typing import Optional, Callable
from urllib.parse import quote

import requests

# RedScript API
REDSCRIPT_API_URL = "https://api.redscript.info/team/createAd"

try:
    from config import REDSCRIPT_SERVICE
except ImportError:
    REDSCRIPT_SERVICE = "OLX"

# Заголовки
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
}


def _parse_proxy_for_requests(proxy_str: str, use_socks: bool = False, encode_auth: bool = True) -> Optional[dict]:
    """
    Преобразовать ip:port:user:password в формат для requests.
    use_socks: использовать socks5:// вместо http://
    encode_auth: URL-кодировать логин/пароль (некоторые прокси не принимают encoded)
    """
    proxy_str = proxy_str.strip()
    if not proxy_str:
        return None
    try:
        scheme = "socks5" if use_socks else "http"
        if "://" in proxy_str:
            orig_scheme, proxy_str = proxy_str.split("://", 1)
            if not use_socks:
                scheme = orig_scheme.lower()
        parts = proxy_str.split(":")
        if len(parts) >= 4:
            host, port, user, password = parts[0], parts[1], parts[2], ":".join(parts[3:])
            if encode_auth:
                auth = f"{quote(user, safe='')}:{quote(password, safe='')}"
            else:
                auth = f"{user}:{password}"
            proxy_url = f"{scheme}://{auth}@{host}:{port}"
        elif len(parts) == 3:
            host, port, user = parts[0], parts[1], parts[2]
            auth = f"{quote(user, safe='')}:" if encode_auth else f"{user}:"
            proxy_url = f"{scheme}://{auth}@{host}:{port}"
        elif len(parts) >= 2:
            proxy_url = f"{scheme}://{parts[0]}:{parts[1]}"
        else:
            return None
        return {"http": proxy_url, "https": proxy_url}
    except Exception:
        return None


def _extract_article_id(url: str) -> str:
    """Извлечь ID объявления из URL OLX (например olx.ba/artikal/74301639 -> 74301639)"""
    m = re.search(r"artikal[/\-](\d+)", url, re.I)
    return m.group(1) if m else "0"


def fetch_listing_data(listing_url: str, proxy: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """
    Загрузить страницу объявления и извлечь название и цену.
    Возвращает (title, price) или (None, None) при ошибке.
    """
    proxies = _parse_proxy_for_requests(proxy) if proxy else None
    title = None
    price = None
    try:
        session = requests.Session()
        session.trust_env = False
        resp = session.get(
            listing_url,
            headers=BROWSER_HEADERS,
            proxies=proxies,
            timeout=15,
        )
        resp.raise_for_status()
        html = resp.text

        # Цена: og:description часто заканчивается на " - Na upit" или " - 1.500 KM"
        m = re.search(r'<meta\s+[^>]*property=["\']og:description["\'][^>]*content=["\']([^"\']+)["\']', html, re.I)
        if m:
            desc = m.group(1).strip()
            parts = desc.rsplit(" - ", 1)
            if len(parts) == 2:
                price = parts[1].strip()
        if not price:
            # JSON-LD schema: "price": 1500 -> "1.500 KM", 0 -> "Na upit"
            m = re.search(r'"price"\s*:\s*(\d+)', html)
            if m:
                pval = int(m.group(1))
                price = "Na upit" if pval == 0 else f"{pval:,}".replace(",", ".") + " KM"

        # Название: og:title, h1 или title
        m = re.search(r'<meta\s+[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)["\']', html, re.I)
        if m:
            title = m.group(1).strip()
            if "OLX" in title:
                title = re.sub(r'\s*-\s*[^-]+-\s*OLX\.?ba\s*$', '', title, flags=re.I).strip()
        if not title:
            m = re.search(r'<h1[^>]*>([^<]+)</h1>', html, re.I)
            if m:
                title = m.group(1).strip()
        if not title:
            m = re.search(r'<title>([^<]+)</title>', html, re.I)
            if m:
                t = m.group(1).strip()
                title = re.sub(r'\s*-\s*[^-]+-\s*OLX\.?ba\s*$', '', t, flags=re.I).strip()
    except Exception:
        pass
    return (title, price)


def fetch_listing_title(listing_url: str, proxy: Optional[str] = None) -> Optional[str]:
    """Совместимость: возвращает только название"""
    title, _ = fetch_listing_data(listing_url, proxy)
    return title


def create_telegram_link(
    api_key: str,
    listing_url: str,
    title: Optional[str] = None,
    price: Optional[str] = None,
    image: Optional[str] = None,
    api_base: Optional[str] = None,
    on_debug: Optional[Callable[[str], None]] = None,
    proxy: Optional[str] = None,
) -> Optional[str]:
    """
    Создать объявление через RedScript API и получить ссылку.
    
    POST /team/createAd
    Параметры: access_token, country, type, service, version, name, amount
    
    Args:
        api_key: access_token (API-ключ)
        listing_url: URL объявления OLX
        title: Название объявления (если нет — из URL)
        price: Цена (если нет — "Na upit")
        image: URL изображения (опционально)
        api_base: Базовый URL (если не указан — api.redscript.info)
        on_debug: Колбэк для логов
        proxy: Прокси ip:port:user:password
    
    Returns:
        Ссылка из result.link или result.short
    """
    if not api_key or not listing_url:
        return None
    
    def log(msg: str):
        if on_debug:
            on_debug(msg)
    
    proxies = _parse_proxy_for_requests(proxy) if proxy else None
    if proxies:
        log(f"Используется прокси: {proxy.split(':')[0]}...")
    
    base = (api_base or "https://api.redscript.info").rstrip("/")
    url = f"{base}/team/createAd"
    
    article_id = _extract_article_id(listing_url)
    name = (title or "").strip() or f"OLX {article_id}"
    amount = (price or "").strip() or "Na upit"
    
    payload = {
        "access_token": api_key,
        "country": "Босния",
        "type": "services",
        "service": REDSCRIPT_SERVICE,
        "version": "2.0",
        "name": name,
        "amount": amount,
    }
    if image:
        payload["image"] = image
    
    def _do_request(use_proxies):
        session = requests.Session()
        session.trust_env = False
        return session.post(
            url,
            json=payload,
            headers=BROWSER_HEADERS,
            proxies=use_proxies,
            timeout=25,
        )

    def _parse_response(resp):
        body = resp.text
        try:
            result = json.loads(body) if body else {}
        except json.JSONDecodeError:
            log(f"API: неверный JSON: {body[:150]}...")
            return None
        if result.get("status") and result.get("result"):
            res = result["result"]
            link = res.get("link") or res.get("short")
            if link:
                link = link.strip()
                if link and not link.startswith(("http://", "https://")):
                    link = "https://" + link
                return link
        err = result.get("error") or result.get("message") or str(result)
        log(f"API ответ: {err}")
        return None

    proxy_variants = []
    if proxy:
        proxy_variants = [
            (True, False, "прокси (HTTP)"),
            (False, False, "прокси без кодирования"),
            (True, True, "прокси (SOCKS5)"),
        ]

    try:
        last_proxy_error = None
        for i, (encode_auth, use_socks, label) in enumerate(proxy_variants):
            try:
                proxs = _parse_proxy_for_requests(proxy, use_socks=use_socks, encode_auth=encode_auth)
                if not proxs:
                    continue
                if i > 0:
                    log(f"Пробуем {label}...")
                resp = _do_request(proxs)
                resp.raise_for_status()
                link = _parse_response(resp)
                if link:
                    log("API OK: ссылка создана")
                    return link
                return None
            except (requests.exceptions.ProxyError, requests.exceptions.ConnectionError) as ex:
                last_proxy_error = ex
                err_str = str(ex).lower()
                if "407" in err_str or "proxy" in err_str or "tunnel" in err_str:
                    continue
                log(f"Ошибка: {type(ex).__name__}: {ex}")
                return None

        if last_proxy_error and proxy:
            log("Прокси не подходит для API, пробуем без прокси...")
            proxies = None

        # Без прокси (fallback при 407 или изначально)
        resp = _do_request(proxies)
        resp.raise_for_status()
        link = _parse_response(resp)
        if link:
            log("API OK: ссылка создана")
        return link

    except requests.exceptions.HTTPError as e:
        code = e.response.status_code if e.response else 0
        body = e.response.text if e.response else ""
        log(f"HTTP {code}: {body[:300] if body else str(e)}")
        if code == 403:
            log("403 Forbidden: проверьте API-ключ и прокси. API может блокировать прямой доступ.")
        try:
            err = json.loads(body) if body else {}
            if err.get("result"):
                res = err["result"]
                link = res.get("link") or res.get("short")
                if link:
                    return link.strip()
        except Exception:
            pass
        return None
    except Exception as ex:
        log(f"Ошибка: {type(ex).__name__}: {ex}")
        return None
