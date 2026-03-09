# -*- coding: utf-8 -*-
"""
Модуль отправки сообщений на OLX.BA через HTTP API (без Playwright).
Использует api.olx.ba для входа и отправки сообщений.
"""

import asyncio
import random
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional
from urllib.parse import quote

import requests

try:
    from bot_storage import is_seller_blacklisted, add_seller_to_blacklist
except ImportError:
    is_seller_blacklisted = lambda x: False
    add_seller_to_blacklist = lambda x: False

OLX_API_BASE = "https://api.olx.ba"
OLX_WEB_BASE = "https://www.olx.ba"

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "bs-BA,bs;q=0.9,en;q=0.8",
    "Content-Type": "application/json",
}


@dataclass
class SendTask:
    id: str
    listing_url: str
    message: str
    message_link: Optional[str] = None
    seller_username: Optional[str] = None
    status: str = "pending"
    error: Optional[str] = None
    created_at: float = 0


def _parse_proxy_requests(proxy_str: str) -> Optional[dict]:
    """Прокси для requests: ip:port:user:password -> {http, https}"""
    proxy_str = (proxy_str or "").strip()
    if not proxy_str:
        return None
    try:
        scheme = "http"
        if "://" in proxy_str:
            scheme, proxy_str = proxy_str.split("://", 1)
            scheme = scheme.lower()
        parts = proxy_str.split(":")
        if len(parts) >= 4:
            host, port, user, password = parts[0], parts[1], parts[2], ":".join(parts[3:])
            auth = f"{quote(user, safe='')}:{quote(password, safe='')}"
            url = f"{scheme}://{auth}@{host}:{port}"
        elif len(parts) >= 2:
            url = f"{scheme}://{parts[0]}:{parts[1]}"
        else:
            return None
        return {"http": url, "https": url}
    except Exception:
        return None


def _extract_listing_id(url: str) -> str:
    m = re.search(r"artikal[/\-](\d+)", url, re.I)
    return m.group(1) if m else "0"


class OLXSenderHTTP:
    """Отправщик сообщений через HTTP API (без браузера)."""

    def __init__(
        self,
        delay_min: int = 1,
        delay_max: int = 2,
        max_concurrent: int = 5,
        on_log: Optional[Callable[[str], None]] = None,
    ):
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.max_concurrent = max_concurrent
        self._on_log = on_log
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def start(self, headless: bool = True, create_context: bool = True) -> bool:
        """Совместимость с Playwright-интерфейсом — ничего не делает."""
        return True

    async def stop(self):
        """Совместимость с Playwright-интерфейсом — ничего не делает."""
        pass

    def _log(self, msg: str):
        if self._on_log:
            self._on_log(msg)

    def login(self, email: str, password: str, proxy_str: Optional[str] = None) -> Optional[str]:
        """
        Вход через api.olx.ba/auth/login.
        Возвращает Bearer token или None при ошибке.
        """
        proxies = _parse_proxy_requests(proxy_str) if proxy_str else None
        url = f"{OLX_API_BASE}/auth/login"
        payload = {
            "username": email,
            "password": password,
            "device_name": "ba_sender_api",
        }
        try:
            resp = requests.post(
                url,
                json=payload,
                headers=BROWSER_HEADERS,
                proxies=proxies,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            token = data.get("token")
            if token:
                return token
            self._log(f"Вход: нет token в ответе — {data.get('message', data)[:80]}")
            return None
        except requests.exceptions.HTTPError as e:
            body = (e.response.text or "")[:200] if e.response else str(e)
            self._log(f"Вход HTTP {e.response.status_code if e.response else 0}: {body}")
            return None
        except Exception as e:
            self._log(f"Вход: {type(e).__name__} — {str(e)[:120]}")
            return None

    def _get_listing_seller(self, listing_id: str, token: str, proxy_str: Optional[str]) -> Optional[str]:
        """Получить username продавца из GET /listings/:id"""
        proxies = _parse_proxy_requests(proxy_str) if proxy_str else None
        headers = {**BROWSER_HEADERS, "Authorization": f"Bearer {token}"}
        try:
            r = requests.get(
                f"{OLX_API_BASE}/listings/{listing_id}",
                headers=headers,
                proxies=proxies,
                timeout=15,
            )
            if r.status_code == 200:
                data = r.json()
                user = data.get("user") or {}
                return user.get("username") or str(user.get("id", ""))
        except Exception:
            pass
        return None

    def send_message(
        self,
        task: SendTask,
        token: str,
        proxy_str: Optional[str] = None,
        on_status: Optional[Callable] = None,
    ) -> bool:
        """
        Отправить сообщение продавцу объявления.
        Пробуем эндпоинты: /conversations, /messages, /listings/:id/contact
        """
        listing_id = _extract_listing_id(task.listing_url)
        if not listing_id or listing_id == "0":
            if on_status:
                on_status(task.id, "error", "Неверный URL объявления")
            return False

        if not task.seller_username:
            task.seller_username = self._get_listing_seller(listing_id, token, proxy_str)
        if task.seller_username and is_seller_blacklisted(task.seller_username):
            self._log(f"⏭ Пропуск (ЧС): {task.seller_username}")
            if on_status:
                on_status(task.id, "skipped", "blacklist")
            return False

        proxies = _parse_proxy_requests(proxy_str) if proxy_str else None
        headers = {**BROWSER_HEADERS, "Authorization": f"Bearer {token}"}

        # Варианты эндпоинтов (api.olx.ba и www.olx.ba)
        listing_id_int = int(listing_id) if listing_id.isdigit() else 0
        base_payload = {"message": task.message}
        endpoints_to_try = [
            (f"{OLX_API_BASE}/conversations", {**base_payload, "listing_id": listing_id_int}),
            (f"{OLX_API_BASE}/listings/{listing_id}/message", base_payload),
            (f"{OLX_API_BASE}/listings/{listing_id}/contact", base_payload),
            (f"{OLX_API_BASE}/messages", {**base_payload, "listing_id": listing_id_int}),
            (f"{OLX_WEB_BASE}/api/conversations", {**base_payload, "listing_id": listing_id_int}),
            (f"{OLX_WEB_BASE}/api/listings/{listing_id}/message", base_payload),
        ]

        for url, payload in endpoints_to_try:
            try:
                r = requests.post(url, json=payload, headers=headers, proxies=proxies, timeout=25)
                if r.status_code in (200, 201, 204):
                    if task.seller_username and not task.seller_username.isdigit() and len(task.seller_username) >= 3:
                        add_seller_to_blacklist(task.seller_username)
                    if on_status:
                        on_status(task.id, "success", None)
                    return True
                if r.status_code == 404:
                    continue
                self._log(f"Отправка {url}: {r.status_code} — {r.text[:100]}")
            except requests.exceptions.RequestException as e:
                self._log(f"Отправка {url}: {type(e).__name__} — {str(e)[:80]}")
                continue

        err = "API сообщений не найден (эндпоинт не документирован)"
        if on_status:
            on_status(task.id, "error", err)
        self._log(f"⚠ {err}")
        return False

    async def run_tasks(
        self,
        tasks: list[SendTask],
        token: str,
        proxy_str: Optional[str],
        on_status: Optional[Callable] = None,
        create_link_fn: Optional[Callable] = None,
    ):
        """Запуск задач: по 2 параллельно."""
        batch_size = 2
        i = 0
        while i < len(tasks):
            batch = tasks[i : i + batch_size]
            for t in batch:
                if create_link_fn and not t.message_link:
                    if asyncio.iscoroutinefunction(create_link_fn):
                        await create_link_fn(t)
                    else:
                        create_link_fn(t)
            loop = asyncio.get_event_loop()

            def _send(t):
                return self.send_message(t, token, proxy_str, on_status)

            await asyncio.gather(*[
                loop.run_in_executor(None, _send, t)
                for t in batch
            ])
            i += batch_size
            if i < len(tasks):
                await asyncio.sleep(random.uniform(self.delay_min, self.delay_max))

    async def run_single_job(
        self,
        account: dict,
        proxy_str: Optional[str],
        tasks: list[SendTask],
        on_status: Optional[Callable] = None,
        on_screenshot: Optional[Callable] = None,
        on_login_failed: Optional[Callable[[dict], None]] = None,
        on_proxy_used: Optional[Callable[[str], None]] = None,
        create_link_fn: Optional[Callable] = None,
    ) -> bool:
        """Одна сессия: логин через API, отправка сообщений."""
        account_email = account.get("email", "?")
        self._log(f"═══ {account_email}")

        token = self.login(account.get("email", ""), account.get("password", ""), proxy_str)
        if not token:
            self._log(f"⚠ Не удалось войти: {account_email}")
            if on_login_failed:
                on_login_failed(account)
            return False

        if on_proxy_used and proxy_str:
            on_proxy_used(proxy_str)
        self._log(f"✓ {account_email} вошёл")

        def _wrapped_on_status(task_id: str, status: str, error: str = None):
            if on_status:
                try:
                    on_status(task_id, status, error, account_email=account_email)
                except TypeError:
                    on_status(task_id, status, error)

        await self.run_tasks(tasks, token, proxy_str, _wrapped_on_status, create_link_fn)
        return True

    async def run_jobs(
        self,
        jobs: list[tuple[dict, str, list[SendTask]]],
        on_status: Optional[Callable] = None,
        on_screenshot: Optional[Callable] = None,
        on_login_failed: Optional[Callable[[dict], None]] = None,
        on_proxy_used: Optional[Callable[[str], None]] = None,
        create_link_fn: Optional[Callable] = None,
    ):
        """Запуск нескольких сессий параллельно (HTTP — без браузера)."""
        coros = [
            self.run_single_job(acc, proxy_str, tasks, on_status, on_screenshot, on_login_failed, on_proxy_used, create_link_fn)
            for acc, proxy_str, tasks in jobs
        ]
        results = await asyncio.gather(*coros, return_exceptions=True)
        for i, r in enumerate(results):
            if isinstance(r, BaseException):
                acc_email = jobs[i][0].get("email", "?") if i < len(jobs) else "?"
                self._log(f"⚠ {acc_email}: {str(r)[:150]}")
