# -*- coding: utf-8 -*-
"""Модуль отправки сообщений на OLX.BA с поддержкой мультизадачности"""

import asyncio
import random
import threading
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from playwright.async_api import async_playwright, Browser, BrowserContext

try:
    from config import OLX_LOGIN_URL, OLX_PROFILE_URL, OLX_MESSAGES_URL, BLOCKED_KEYWORDS, INVALID_CREDENTIALS_KEYWORDS
except ImportError:
    OLX_LOGIN_URL = "https://www.olx.ba/login"
    OLX_PROFILE_URL = "https://www.olx.ba/profil"
    OLX_MESSAGES_URL = "https://www.olx.ba/poruke"
    BLOCKED_KEYWORDS = ["blokiran", "blocked", "suspend", "ban", "disabled"]
    INVALID_CREDENTIALS_KEYWORDS = ["pogrešan", "wrong", "invalid", "incorrect"]

try:
    from bot_storage import is_seller_blacklisted, add_seller_to_blacklist
except ImportError:
    is_seller_blacklisted = lambda x: False
    add_seller_to_blacklist = lambda x: False


@dataclass
class SendTask:
    """Задача на отправку сообщения"""
    id: str
    listing_url: str
    message: str
    message_link: Optional[str] = None  # второе сообщение (ссылка RedScript)
    seller_username: Optional[str] = None  # никнейм продавца для поиска чата при скриншоте
    status: str = "pending"  # pending, running, success, error
    error: Optional[str] = None
    created_at: float = 0


# Поддерживаемые протоколы прокси (Playwright)
PROXY_SCHEMES = ("http", "https", "socks5", "socks4")


def parse_proxy(proxy_str: str) -> Optional[dict]:
    """
    Парсинг прокси. Поддерживаемые форматы:
    - ip:port:user:password (по умолчанию HTTP)
    - ip:port
    - protocol://ip:port:user:password
    - protocol://ip:port
    - protocol://user:pass@ip:port
    Протоколы: http, https, socks5, socks4
    """
    proxy_str = proxy_str.strip()
    if not proxy_str:
        return None
    try:
        scheme = "http"
        if "://" in proxy_str:
            scheme, proxy_str = proxy_str.split("://", 1)
            scheme = scheme.lower()
        if scheme not in PROXY_SCHEMES:
            scheme = "http"

        username = password = None

        if "@" in proxy_str:
            # Формат: user:pass@host:port
            auth, proxy_str = proxy_str.rsplit("@", 1)
            if ":" in auth:
                username, password = auth.split(":", 1)
            parts = proxy_str.split(":")
        else:
            parts = proxy_str.split(":")

        if len(parts) >= 4 and not username:
            # Формат: ip:port:user:password (пароль может содержать :)
            host = parts[0]
            port = int(parts[1]) if parts[1].isdigit() else 80
            username = parts[2]
            password = ":".join(parts[3:])
        elif len(parts) >= 2:
            host = parts[0]
            port = int(parts[1]) if parts[1].isdigit() else 80
        else:
            host = parts[0] if parts else ""
            port = 80

        if not host:
            return None
        server = f"{scheme}://{host}:{port}"
        proxy = {"server": server}
        if username is not None and password is not None:
            proxy["username"] = username
            proxy["password"] = password
        return proxy
    except Exception:
        return None


class OLXSender:
    """Отправщик сообщений на OLX.BA с мультизадачностью (headless — без окна браузера)"""
    
    def __init__(
        self,
        delay_min: int = 1,
        delay_max: int = 2,
        max_concurrent: int = 5,
        proxy: Optional[str] = None,
        proxy_list: Optional[list[str]] = None,
        on_log: Optional[Callable[[str], None]] = None,
    ):
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.max_concurrent = max_concurrent
        self._proxy = proxy
        self._proxy_list = proxy_list or []
        self._proxy_lock = threading.Lock()
        self._on_log = on_log
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._playwright = None
        self._running = False
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._proxy_index = 0
    
    def _log(self, msg: str):
        if self._on_log:
            self._on_log(msg)
    
    async def _accept_consent_dialog(self, page) -> bool:
        """Закрытие диалога согласия с cookies/Privacy (qc-cmp2). Удаление overlay — приоритет (блокирует клики)."""
        # Сначала удаляем overlay — qc-cmp-cleanslate блокирует pointer events
        try:
            removed = await page.evaluate("""() => {
                let n = 0;
                const sel = ['#qc-cmp2-container', '.qc-cmp2-container', '.qc-cmp-cleanslate', '[class*="qc-cmp"]'];
                for (const s of sel) {
                    try {
                        document.querySelectorAll(s).forEach(el => { el.remove(); n++; });
                    } catch(e) {}
                }
                return n > 0;
            }""")
            if removed:
                await asyncio.sleep(0.15)
                return True
        except Exception:
            pass
        # Пробуем клик по кнопке Accept
        consent_selectors = [
            '#qc-cmp2-ui button[class*="accept"]',
            '#qc-cmp2-ui button:has-text("Accept")',
            '#qc-cmp2-ui button:has-text("Prihvati")',
            '#qc-cmp2-ui button:has-text("Slažem se")',
            '#qc-cmp2-ui button:has-text("Allow")',
            '#qc-cmp2-ui button:has-text("Allow all")',
            '#qc-cmp2-ui button:has-text("Prihvati sve")',
            '#qc-cmp2-ui a:has-text("Accept")',
            '#qc-cmp2-ui a:has-text("Prihvati")',
            '[id="qc-cmp2-ui"] button',
            '.qc-cmp2-summary-buttons button',
            '[aria-label="Privacy"] button',
            '#qc-cmp2-container button',
            '.qc-cmp2-container button',
        ]
        for sel in consent_selectors:
            try:
                btn = await page.query_selector(sel)
                if btn and await btn.is_visible():
                    await btn.click(force=True, timeout=5000)
                    await asyncio.sleep(0.2)
                    return True
            except Exception:
                continue
        try:
            clicked = await page.evaluate("""() => {
                const btn = document.querySelector('#qc-cmp2-ui button, #qc-cmp2-container button, [aria-label="Privacy"] button, .qc-cmp2-summary-buttons button');
                if (btn) { btn.click(); return true; }
                return false;
            }""")
            if clicked:
                await asyncio.sleep(0.2)
                return True
        except Exception:
            pass
        # Fallback: удаляем overlay, если клик не сработал
        try:
            removed = await page.evaluate("""() => {
                let n = 0;
                const sel = ['#qc-cmp2-container', '.qc-cmp2-container', '.qc-cmp-cleanslate'];
                for (const s of sel) {
                    try {
                        document.querySelectorAll(s).forEach(el => { el.remove(); n++; });
                    } catch(e) {}
                }
                return n > 0;
            }""")
            if removed:
                await asyncio.sleep(0.2)
                return True
        except Exception:
            pass
        return False
        
    def _get_proxy_for_task(self) -> Optional[dict]:
        """Получить прокси для текущей задачи (ротация из списка)"""
        if self._proxy_list:
            with self._proxy_lock:
                proxy_str = self._proxy_list[self._proxy_index % len(self._proxy_list)]
                self._proxy_index += 1
            return parse_proxy(proxy_str)
        if self._proxy:
            return parse_proxy(self._proxy)
        return None
        
    async def start(self, headless: bool = True, create_context: bool = True) -> bool:
        """Запуск браузера. create_context=False для мультиаккаунта (контексты создаются per-job)"""
        try:
            self._playwright = await async_playwright().start()
            launch_args = [
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-infobars',
                '--window-size=1280,800',
            ]
            self._browser = await self._playwright.chromium.launch(
                headless=headless,
                args=launch_args,
            )
            self._context = None
            if create_context:
                proxy_config = self._get_proxy_for_task() if self._proxy or self._proxy_list else None
                self._context = await self._browser.new_context(
                    viewport={'width': 1280, 'height': 800},
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                    locale='bs-BA',
                    proxy=proxy_config
                )
                await self._context.add_init_script(self._CONSENT_REMOVE_SCRIPT)
            self._running = True
            return True
        except Exception as e:
            raise Exception(f"Ошибка запуска браузера: {e}")

    _CONSENT_REMOVE_SCRIPT = """
        setInterval(() => {
            ['#qc-cmp2-container', '.qc-cmp2-container', '.qc-cmp-cleanslate', '[class*="qc-cmp"]'].forEach(s => {
                try { document.querySelectorAll(s).forEach(el => el.remove()); } catch(e) {}
            });
        }, 500);
    """

    async def _create_context_with_proxy(self, proxy_str: Optional[str]):
        """Создать контекст с указанным прокси (для мультиаккаунта)"""
        proxy_config = parse_proxy(proxy_str) if proxy_str and proxy_str.strip() else None
        ctx = await self._browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            locale='bs-BA',
            proxy=proxy_config
        )
        await ctx.add_init_script(self._CONSENT_REMOVE_SCRIPT)
        return ctx
    
    async def login(self, email: str, password: str, on_status: Optional[Callable] = None, context = None) -> bool:
        """Вход в аккаунт OLX.BA"""
        ctx = context or self._context
        if not ctx:
            return False
        page = None
        try:
            page = await ctx.new_page()
            await page.goto(OLX_LOGIN_URL, wait_until="domcontentloaded", timeout=25000)
            await asyncio.sleep(0.4)
            await self._accept_consent_dialog(page)
            await asyncio.sleep(0.2)
            
            # Если уже залогинены — редирект
            if "login" not in page.url.lower():
                self._log("Уже выполнен вход")
                await page.close()
                return True
            
            # Приоритет: get_by_placeholder (OLX.BA: "Korisničko ime ili email", "Šifra")
            email_input = None
            for method, arg in [
                ("placeholder", "Korisničko ime ili email"),
                ("placeholder", "Korisničko"),
                ("placeholder", "email"),
                ("selector", 'input[name="username"]'),
                ("selector", 'input[name="email"]'),
                ("selector", 'input[type="email"]'),
                ("selector", 'input[type="text"]'),
            ]:
                try:
                    if method == "placeholder":
                        loc = page.get_by_placeholder(arg)
                        if await loc.count() > 0:
                            email_input = loc.first
                            break
                    else:
                        el = await page.wait_for_selector(arg, state="visible", timeout=2000)
                        if el:
                            email_input = el
                            break
                except Exception:
                    continue
            if not email_input:
                raise Exception("Не найдено поле для ввода email/логина")
            
            pass_input = None
            for method, arg in [
                ("placeholder", "Šifra"),
                ("placeholder", "šifra"),
                ("selector", 'input[name="password"]'),
                ("selector", 'input[type="password"]'),
            ]:
                try:
                    if method == "placeholder":
                        loc = page.get_by_placeholder(arg)
                        if await loc.count() > 0:
                            pass_input = loc.first
                            break
                    else:
                        el = await page.wait_for_selector(arg, state="visible", timeout=2000)
                        if el:
                            pass_input = el
                            break
                except Exception:
                    continue
            if not pass_input:
                raise Exception("Не найдено поле для ввода пароля")
            
            await email_input.click()
            await asyncio.sleep(0.15)
            await email_input.fill(email, force=True)
            await asyncio.sleep(0.2)
            await pass_input.click()
            await asyncio.sleep(0.1)
            try:
                await pass_input.press_sequentially(password, delay=60)
            except (AttributeError, Exception):
                await pass_input.fill(password, force=True)
            await asyncio.sleep(0.2)
            
            # Кнопка "Prijavi se" — приоритет get_by_role
            clicked = False
            for method, arg in [
                ("role", ("button", "Prijavi se")),
                ("role", ("button", "Prijavi")),
                ("text", "Prijavi se"),
                ("selector", 'button[type="submit"]'),
                ("selector", 'button:has-text("Prijavi se")'),
                ("selector", 'button:has-text("Prijavi")'),
                ("selector", 'form button'),
            ]:
                try:
                    if method == "role":
                        btn = page.get_by_role("button", name=arg[1])
                        if await btn.count() > 0:
                            await btn.first.click(force=True)
                            clicked = True
                            break
                    elif method == "text":
                        btn = page.get_by_text(arg, exact=True)
                        if await btn.count() > 0:
                            await btn.first.click(force=True)
                            clicked = True
                            break
                    else:
                        el = await page.query_selector(arg)
                        if el and await el.is_visible():
                            await el.click(force=True)
                            clicked = True
                            break
                except Exception:
                    continue
            if not clicked:
                try:
                    await page.evaluate("() => { const f = document.querySelector('form'); if (f) f.submit(); }")
                except Exception:
                    await page.keyboard.press("Enter")
            
            try:
                await page.wait_for_url(lambda u: "login" not in u.lower(), timeout=12000)
            except Exception:
                await asyncio.sleep(0.4)
            url = page.url
            if "login" in url.lower():
                try:
                    err_el = await page.query_selector('[class*="error"], [class*="alert"], .text-danger, [role="alert"]')
                    if err_el:
                        err_text = await err_el.inner_text()
                        if err_text and len(err_text) < 200:
                            self._log(f"Ошибка входа: {err_text.strip()[:100]}")
                except Exception:
                    pass
                await page.close()
                return False
            await page.close()
            return True
        except Exception as e:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass
            if on_status:
                on_status("login", "error", str(e))
            raise
    
    async def validate_account(
        self, email: str, password: str, proxy_str: Optional[str] = None
    ) -> dict:
        """
        Проверка аккаунта: валидность и блокировка.
        proxy_str: для мультиаккаунта — прокси сессии
        """
        ctx = self._context
        if not ctx and self._browser and proxy_str is not None:
            ctx = await self._create_context_with_proxy(proxy_str)
            temp_ctx = True
        else:
            temp_ctx = False
        if not ctx:
            return {"status": "error", "message": "Браузер не запущен"}
        page = None
        try:
            page = await ctx.new_page()
            self._log("Проверка: переход на страницу входа...")
            await page.goto(OLX_LOGIN_URL, wait_until="load", timeout=30000)
            await asyncio.sleep(0.2)
            await self._accept_consent_dialog(page)
            await asyncio.sleep(0.2)
            
            # Если уже залогинены — редирект со страницы логина
            if "login" not in page.url.lower():
                self._log("Проверка: аккаунт активен (уже выполнен вход)")
                await page.close()
                return {"status": "valid", "message": "Аккаунт активен"}
            
            self._log("Проверка: заполнение формы...")
            email_input = pass_input = None
            for loc in [page.get_by_placeholder("Korisničko ime ili email"), page.get_by_placeholder("Korisničko")]:
                if await loc.count() > 0:
                    email_input = loc.first
                    break
            if not email_input:
                for sel in ['input[name="username"]', 'input[name="email"]', 'input[type="email"]']:
                    el = await page.query_selector(sel)
                    if el and await el.is_visible():
                        email_input = el
                        break
            for loc in [page.get_by_placeholder("Šifra")]:
                if await loc.count() > 0:
                    pass_input = loc.first
                    break
            if not pass_input:
                for sel in ['input[name="password"]', 'input[type="password"]']:
                    el = await page.query_selector(sel)
                    if el and await el.is_visible():
                        pass_input = el
                        break
            if not email_input or not pass_input:
                await page.close()
                return {"status": "error", "message": "Не найдена форма входа"}
            
            await email_input.fill(email)
            await asyncio.sleep(0.05)
            await pass_input.fill(password)
            await asyncio.sleep(0.05)
            
            login_btn = await page.query_selector('button[type="submit"], button:has-text("Prijavi se"), button:has-text("Prijavi")')
            if login_btn:
                await login_btn.click()
            self._log("Проверка: ожидание ответа...")
            
            await asyncio.sleep(0.4)
            url = page.url
            page_text = (await page.content()).lower()
            body_text = await page.evaluate("() => document.body.innerText") or ""
            body_text_lower = body_text.lower()
            
            # Успешный вход — ушли со страницы логина
            if "login" not in url.lower():
                self._log("Проверка: аккаунт активен")
                await page.close()
                return {"status": "valid", "message": "Аккаунт активен"}
            
            # Остались на логине — ищем причину
            combined = body_text_lower + " " + page_text
            
            for kw in BLOCKED_KEYWORDS:
                if kw in combined:
                    self._log("Проверка: аккаунт заблокирован")
                    await page.close()
                    return {"status": "blocked", "message": "Аккаунт заблокирован или приостановлен"}
            
            for kw in INVALID_CREDENTIALS_KEYWORDS:
                if kw in combined:
                    self._log("Проверка: неверный логин или пароль")
                    await page.close()
                    return {"status": "invalid", "message": "Неверный логин или пароль"}
            
            await page.close()
            return {"status": "invalid", "message": "Не удалось войти — проверьте логин и пароль"}
            
        except Exception as e:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass
            return {"status": "error", "message": str(e)}
        finally:
            if temp_ctx and ctx:
                await ctx.close()
    
    async def stop(self):
        """Остановка браузера"""
        self._running = False
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
    
    async def _take_chat_screenshot(self, page, task: SendTask, on_screenshot) -> bool:
        """Перейти в Moje poruke, зайти в диалог с продавцом и сделать скриншот."""
        if not on_screenshot:
            return True
        try:
            url = page.url
            chat_clicked = False
            if re.search(r"/poruke/\d+|/poruka/\d+", url):
                await asyncio.sleep(0.4)
                chat_clicked = True
            else:
                navigated = False
                # Сначала пробуем клик по ссылке (надёжнее с прокси, чем page.goto)
                try:
                    for el in await page.query_selector_all('a[href*="/poruke"], a[href*="/poruka"]'):
                        href = (await el.get_attribute("href") or "").split("#")[0].split("?")[0]
                        if re.match(r".*/poruke/?$|.*/poruka/?$", href) and not re.search(r"/poruke/\d+|/poruka/\d+", href):
                            if await el.is_visible():
                                await el.click()
                                await page.wait_for_load_state("domcontentloaded", timeout=10000)
                                navigated = True
                                break
                except Exception:
                    pass
                if not navigated:
                    for attempt in range(2):
                        try:
                            await page.goto(OLX_MESSAGES_URL, wait_until="domcontentloaded", timeout=15000)
                            navigated = True
                            break
                        except Exception as e:
                            self._log(f"Переход poruke (попытка {attempt+1}): {e}")
                            if attempt == 0:
                                await asyncio.sleep(0.4)
                    if not navigated:
                        raise Exception("Не удалось перейти в Moje poruke (проверьте прокси)")
                await asyncio.sleep(0.4)
                await self._accept_consent_dialog(page)
                await asyncio.sleep(0.15)
                # Обновляем страницу, чтобы список диалогов показал только что отправленное сообщение
                try:
                    await page.reload(wait_until="domcontentloaded", timeout=15000)
                    await asyncio.sleep(0.4)
                    await self._accept_consent_dialog(page)
                    await asyncio.sleep(0.15)
                except Exception as e:
                    self._log(f"Обновление страницы: {e}")

            if not chat_clicked:
                seller = (task.seller_username or "").strip().lower()
                if seller:
                    # Ищем ссылку/элемент, содержащий никнейм продавца
                    all_links = await page.query_selector_all('a[href*="/poruke/"], a[href*="/poruka/"]')
                    for el in all_links:
                        try:
                            href = await el.get_attribute("href") or ""
                            if not re.search(r"/poruke/\d+|/poruka/\d+", href):
                                continue
                            in_nav = await el.evaluate("""el => !!(el.closest('nav') || el.closest('header'))""")
                            if in_nav:
                                continue
                            # Проверяем, есть ли никнейм в тексте элемента или родителя (имя в чате)
                            text = await el.evaluate("""el => {
                                const p = el.closest('[class*="chat"], [class*="conversation"], [class*="item"]') || el.parentElement;
                                return (p ? p.textContent : '') + (el.textContent || '');
                            }""")
                            if text and seller in (text or "").lower():
                                await el.click(force=True)
                                chat_clicked = True
                                await asyncio.sleep(0.15)
                                break
                        except Exception:
                            continue
                if not chat_clicked:
                    # Только чаты с продавцами: в превью есть ссылка olx.one/artikal — системные сообщения OLX её не имеют
                    clicked = await page.evaluate("""() => {
                        const links = Array.from(document.querySelectorAll('a[href*="/poruke/"], a[href*="/poruka/"]'));
                        const sellerChats = [];
                        for (const a of links) {
                            const href = (a.getAttribute('href') || '').split('?')[0];
                            if (!/\\/poruke\\/\\d+|\\/poruka\\/\\d+/.test(href)) continue;
                            const inNav = a.closest('nav') || a.closest('header') || a.closest('[role="navigation"]');
                            if (inNav || a.offsetParent === null) continue;
                            const item = a.closest('[class*="chat"],[class*="item"],[class*="conversation"],[class*="thread"],[class*="list"]') || a.parentElement;
                            const text = (item ? item.textContent : '') + ' ' + (a.textContent || '');
                            if (/olx\\.one|artikal|https?:\\/\\//i.test(text)) sellerChats.push(a);
                        }
                        if (sellerChats.length > 0) {
                            sellerChats[0].click();
                            return true;
                        }
                        return false;
                    }""")
                    if clicked:
                        chat_clicked = True
                        await asyncio.sleep(0.15)
                        try:
                            await page.wait_for_url(re.compile(r"/poruke/\d+|/poruka/\d+"), timeout=5000)
                            await asyncio.sleep(0.4)
                        except Exception:
                            pass
                    if not chat_clicked:
                        all_links = await page.query_selector_all('a[href*="/poruke/"], a[href*="/poruka/"]')
                        for el in all_links:
                            try:
                                href = await el.get_attribute("href") or ""
                                if re.search(r"/poruke/\d+|/poruka/\d+", href):
                                    in_nav = await el.evaluate("""el => !!(el.closest('nav') || el.closest('header'))""")
                                    if not in_nav and await el.is_visible():
                                        await el.click(force=True)
                                        chat_clicked = True
                                        await asyncio.sleep(0.15)
                                        break
                            except Exception:
                                continue

                if not chat_clicked:
                    for sel in [
                        '[class*="chat-item"]',
                        '[class*="conversation"]',
                        '[class*="message-preview"]',
                        '[class*="thread"]',
                        'aside a',
                        '[role="list"] a',
                        '.conversation-list a',
                    ]:
                        try:
                            items = await page.query_selector_all(sel)
                            for el in items[:3]:
                                if await el.is_visible():
                                    await el.click(force=True)
                                    chat_clicked = True
                                    break
                            if chat_clicked:
                                break
                        except Exception:
                            continue

            await asyncio.sleep(0.4)
            # Скриншот только области диалога (без шапки и поля ввода)
            screenshot_bytes = None
            for sel in [
                '[class*="message-list"]', '[class*="messages"]', '[class*="chat-messages"]',
                '[class*="conversation-body"]', '[class*="chat-body"]', '[class*="thread-messages"]',
                '[role="log"]', '[class*="MessageList"]', '[class*="message-list"]',
                'main [class*="scroll"]', '[class*="conversation"] [class*="scroll"]',
            ]:
                try:
                    el = await page.query_selector(sel)
                    if el and await el.is_visible():
                        box = await el.bounding_box()
                        if box and box.get("width", 0) > 100 and box.get("height", 0) > 80:
                            screenshot_bytes = await el.screenshot(type="png")
                            break
                except Exception:
                    continue
            if not screenshot_bytes:
                try:
                    rect = await page.evaluate("""() => {
                        const bubbles = document.querySelectorAll('[class*="message"], [class*="bubble"], [class*="chat-message"]');
                        if (bubbles.length === 0) return null;
                        let minX=1e9, minY=1e9, maxX=0, maxY=0;
                        for (const b of bubbles) {
                            const r = b.getBoundingClientRect();
                            if (r.width > 0 && r.height > 0) {
                                minX = Math.min(minX, r.left); minY = Math.min(minY, r.top);
                                maxX = Math.max(maxX, r.right); maxY = Math.max(maxY, r.bottom);
                            }
                        }
                        if (minX >= maxX || minY >= maxY) return null;
                        return { x: minX, y: minY, width: maxX - minX, height: maxY - minY };
                    }""")
                    if rect and rect.get("width", 0) > 50 and rect.get("height", 0) > 50:
                        screenshot_bytes = await page.screenshot(type="png", clip=rect)
                except Exception:
                    pass
            if not screenshot_bytes:
                screenshot_bytes = await page.screenshot(type="png", full_page=False)
            caption = task.listing_url[:80] + ("..." if len(task.listing_url) > 80 else "")
            if asyncio.iscoroutinefunction(on_screenshot):
                await on_screenshot(screenshot_bytes, caption)
            else:
                on_screenshot(screenshot_bytes, caption)
            self._log("✓ Скрин")
            return True
        except Exception as e:
            self._log(f"Скриншот: {e}")
            return False

    async def send_message(
        self, 
        task: SendTask, 
        on_status: Optional[Callable] = None,
        on_screenshot: Optional[Callable[[bytes, str], Awaitable[None]]] = None,
        context = None
    ) -> bool:
        """Отправка одного сообщения"""
        ctx = context or self._context
        async with self._semaphore:
            if not self._running or not ctx:
                return False
                
            page = None
            try:
                if on_status:
                    on_status(task.id, "running", None)
                    
                page = await ctx.new_page()
                
                try:
                    # Переход на страницу объявления
                    short_url = task.listing_url[:60] + "..." if len(task.listing_url) > 60 else task.listing_url
                    await page.goto(task.listing_url, wait_until="domcontentloaded", timeout=25000)
                    await asyncio.sleep(0.6)
                    await self._accept_consent_dialog(page)
                    await asyncio.sleep(0.2)
                    # Извлекаем никнейм продавца — ТОЛЬКО из блока с кнопкой Poruka (карточка продавца)
                    # Игнорируем шапку: там "Moj profil" = текущий юзер, его нельзя путать с продавцом
                    try:
                        seller_username = await page.evaluate("""() => {
                            const skip = new Set(['aktivni','zavrseni','dojmovi','artikal','profil','oglas','poruke','poruka','login']);
                            const btn = document.querySelector('a[href*="poruke"], a[href*="poruka"]') ||
                                Array.from(document.querySelectorAll('a, button')).find(el => /Poruka|Kontaktiraj/i.test(el.textContent || ''));
                            if (!btn) return null;
                            const card = btn.closest('[class*="card"],[class*="sidebar"],[class*="seller"],[class*="contact"],[class*="user"],[class*="flex"],aside,section,div');
                            const scope = card || btn.parentElement;
                            if (!scope) return null;
                            const links = scope.querySelectorAll('a[href*="/profil/"]');
                            for (const a of links) {
                                const m = (a.getAttribute('href') || '').match(/\\/profil\\/([^/?]+)/);
                                if (m) {
                                    const u = m[1].trim().toLowerCase();
                                    if (u.length >= 2 && !/^\\d+$/.test(u) && !skip.has(u)) return m[1].trim();
                                }
                            }
                            return null;
                        }""")
                        if seller_username:
                            task.seller_username = seller_username
                    except Exception:
                        pass
                    # Проверка чёрного списка (глобальный для всех пользователей)
                    if task.seller_username and is_seller_blacklisted(task.seller_username):
                        self._log(f"⏭ Пропуск (ЧС): {task.seller_username} — {task.listing_url[:50]}...")
                        if on_status:
                            on_status(task.id, "skipped", "blacklist")
                        return False
                    if not task.seller_username:
                        pass
                    await asyncio.sleep(0.3)
                    await self._accept_consent_dialog(page)
                    await asyncio.sleep(0.2)
                    # Шаг 1: Нажать кнопку "Poruka" (Сообщение) в сайдбаре объявления
                    contact_clicked = False
                    for attempt in range(3):
                        for method, arg in [
                            ("text", "Poruka"),
                            ("role", ("button", "Poruka")),
                            ("selector", 'button:has-text("Poruka")'),
                            ("selector", 'a:has-text("Poruka")'),
                            ("selector", 'button:has-text("Kontaktiraj")'),
                            ("selector", 'a:has-text("Kontaktiraj")'),
                            ("selector", 'a[href*="poruke"]'),
                            ("selector", 'a[href*="poruka"]'),
                        ]:
                            try:
                                if method == "text":
                                    loc = page.get_by_text(arg, exact=True)
                                    if await loc.count() > 0:
                                        await loc.first.click(force=True, timeout=8000)
                                        contact_clicked = True
                                        break
                                elif method == "role":
                                    loc = page.get_by_role("button", name=arg[1])
                                    if await loc.count() > 0:
                                        await loc.first.click(force=True, timeout=8000)
                                        contact_clicked = True
                                        break
                                else:
                                    btn = await page.query_selector(arg)
                                    if btn and await btn.is_visible():
                                        await btn.click(force=True, timeout=8000)
                                        contact_clicked = True
                                        break
                            except Exception as e:
                                err = str(e).lower()
                                if "intercepts" in err or "qc-cmp" in err or "pointer" in err:
                                    await self._accept_consent_dialog(page)
                                    await asyncio.sleep(0.2)
                                continue
                        if contact_clicked:
                            break
                        await self._accept_consent_dialog(page)
                        await asyncio.sleep(0.3)
                    
                    if not contact_clicked:
                        raise Exception("Не найдена кнопка Poruka")
                    
                    # Шаг 2: Ожидание появления формы сообщения (модал или новая страница)
                    await asyncio.sleep(0.15)
                    
                    # Ждём навигации, если переход на страницу сообщений
                    try:
                        await page.wait_for_url(re.compile(r"poruke|poruka"), timeout=6000)
                        await asyncio.sleep(0.1)
                    except Exception:
                        pass
                    
                    # Ждём появления поля ввода в модале (#modals-container) или на странице /poruke
                    msg_input = None
                    for method, arg in [
                        ("placeholder", "Napišite poruku"),
                        ("placeholder", "Napiši poruku"),
                        ("placeholder", "poruku"),
                        ("selector", 'textarea[placeholder*="Napiši"]'),
                        ("selector", 'textarea[placeholder*="poruk"]'),
                        ("selector", '#modals-container textarea'),
                        ("selector", 'textarea.flex-auto'),
                        ("selector", 'textarea'),
                        ("selector", '[contenteditable="true"][role="textbox"]'),
                    ]:
                        try:
                            if method == "placeholder":
                                loc = page.get_by_placeholder(arg)
                                if await loc.count() > 0:
                                    await loc.first.wait_for(state="visible", timeout=3000)
                                    msg_input = loc.first
                                    break
                            else:
                                msg_input = await page.wait_for_selector(arg, state="visible", timeout=5000)
                                if msg_input:
                                    break
                        except Exception:
                            continue
                    
                    if not msg_input:
                        raise Exception("Не найдено поле для ввода сообщения")
                    
                    await asyncio.sleep(0.15)
                    
                    async def _fill_and_send(input_el, text_to_send: str) -> bool:
                        """Заполнить поле и отправить сообщение (с сохранением переносов строк)"""
                        if not input_el:
                            return False
                        # Нормализуем переносы: \r\n -> \n
                        text_to_send = text_to_send.replace("\r\n", "\n").replace("\r", "\n")
                        try:
                            await input_el.click()
                            await asyncio.sleep(0.15)
                        except Exception:
                            pass
                        is_ce = await input_el.evaluate("el => el.getAttribute('contenteditable') === 'true'")
                        if is_ce:
                            # contenteditable: insertText не сохраняет \n — вставляем построчно с insertLineBreak
                            lines = text_to_send.split("\n")
                            await input_el.evaluate("""(el, lines) => {
                                el.focus();
                                const r = document.createRange();
                                r.selectNodeContents(el);
                                const s = window.getSelection();
                                s.removeAllRanges();
                                s.addRange(r);
                                for (let i = 0; i < lines.length; i++) {
                                    document.execCommand('insertText', false, lines[i]);
                                    if (i < lines.length - 1) document.execCommand('insertLineBreak');
                                }
                                el.dispatchEvent(new InputEvent('input', { bubbles: true }));
                            }""", lines)
                        else:
                            # textarea: value сохраняет \n
                            await input_el.evaluate("""(el, text) => {
                                el.value = text;
                                el.dispatchEvent(new Event('input', { bubbles: true }));
                                el.dispatchEvent(new Event('change', { bubbles: true }));
                            }""", text_to_send)
                        await asyncio.sleep(0.1)
                        for method, arg in [
                            ("selector", '#modals-container button:has-text("Pošalji poruku")'),
                            ("selector", '#modals-container button:has-text("Pošalji")'),
                            ("role", ("button", "Pošalji poruku")),
                            ("text", "Pošalji poruku"),
                            ("selector", 'button:has-text("Pošalji poruku")'),
                            ("selector", 'button:has-text("Pošalji")'),
                            ("selector", 'form button[type="submit"]'),
                            ("selector", 'button[type="submit"]'),
                            ("selector", 'button:has-text("Send")'),
                            ("selector", 'input[type="submit"]'),
                        ]:
                            try:
                                if method == "text":
                                    loc = page.get_by_text(arg, exact=True)
                                    if await loc.count() > 0:
                                        await loc.first.click(force=True)
                                        return True
                                elif method == "role":
                                    loc = page.get_by_role("button", name=arg[1])
                                    if await loc.count() > 0:
                                        await loc.first.click(force=True)
                                        return True
                                else:
                                    send_btn = await page.query_selector(arg)
                                    if send_btn and await send_btn.is_visible():
                                        await send_btn.click(force=True)
                                        return True
                            except Exception:
                                continue
                        return False
                    
                    # Первое сообщение — текст из меню (с повтором при неудаче)
                    sent = False
                    for attempt in range(3):
                        sent = await _fill_and_send(msg_input, task.message)
                        if sent:
                            break
                        await asyncio.sleep(0.1)
                        if attempt < 2:
                            msg_input = await page.query_selector('textarea[placeholder*="poruk"], textarea, [contenteditable="true"][role="textbox"]')
                            if not msg_input:
                                break
                    if not sent:
                        await asyncio.sleep(0.15)
                        try:
                            await page.keyboard.press("Enter")
                            sent = True
                        except Exception:
                            pass
                    if not sent:
                        raise Exception("Не найдена кнопка Pošalji poruku")
                    await asyncio.sleep(0.15)
                    self._log(f"✓ {short_url}")
                    
                    # Второе сообщение — ссылка (если есть). Возвращаемся на страницу объявления
                    if task.message_link:
                        await asyncio.sleep(0.1)
                        await page.goto(task.listing_url, wait_until="domcontentloaded", timeout=30000)
                        await asyncio.sleep(random.uniform(0.08, 0.15))
                        await self._accept_consent_dialog(page)
                        await asyncio.sleep(0.1)
                        contact_clicked2 = False
                        for method, arg in [
                            ("text", "Poruka"),
                            ("role", ("button", "Poruka")),
                            ("selector", 'button:has-text("Poruka")'),
                            ("selector", 'a:has-text("Poruka")'),
                            ("selector", 'a[href*="poruka"]'),
                        ]:
                            try:
                                if method == "text":
                                    loc = page.get_by_text(arg, exact=True)
                                    if await loc.count() > 0:
                                        await loc.first.click(force=True)
                                        contact_clicked2 = True
                                        break
                                elif method == "role":
                                    loc = page.get_by_role("button", name=arg[1])
                                    if await loc.count() > 0:
                                        await loc.first.click(force=True)
                                        contact_clicked2 = True
                                        break
                                else:
                                    btn = await page.query_selector(arg)
                                    if btn and await btn.is_visible():
                                        await btn.click(force=True)
                                        contact_clicked2 = True
                                        break
                            except Exception:
                                continue
                        if contact_clicked2:
                            await asyncio.sleep(0.15)
                            try:
                                await page.wait_for_url(re.compile(r"poruke|poruka"), timeout=6000)
                                await asyncio.sleep(0.1)
                            except Exception:
                                pass
                            msg_input2 = None
                            for method, arg in [
                                ("selector", '#modals-container textarea'),
                                ("placeholder", "Napišite poruku"),
                                ("selector", 'textarea[placeholder*="poruk"]'),
                                ("selector", 'textarea'),
                            ]:
                                try:
                                    if method == "placeholder":
                                        loc = page.get_by_placeholder(arg)
                                        if await loc.count() > 0:
                                            msg_input2 = loc.first
                                            break
                                    else:
                                        msg_input2 = await page.wait_for_selector(arg, state="visible", timeout=4000)
                                        if msg_input2:
                                            break
                                except Exception:
                                    continue
                            if msg_input2:
                                if await _fill_and_send(msg_input2, task.message_link):
                                    await asyncio.sleep(0.1)
                                    self._log("✓ Ссылка")
                    await asyncio.sleep(0.15)
                    await self._take_chat_screenshot(page, task, on_screenshot)
                    # Добавляем в ЧС только валидные никнеймы (не ID, не служебные)
                    if task.seller_username and not task.seller_username.isdigit() and len(task.seller_username) >= 3:
                        add_seller_to_blacklist(task.seller_username)
                    if on_status:
                        on_status(task.id, "success", None)
                    return True
                        
                finally:
                    if page:
                        try:
                            await page.close()
                        except Exception:
                            pass
                    
            except Exception as e:
                if page:
                    try:
                        await page.close()
                    except Exception:
                        pass
                if on_status:
                    on_status(task.id, "error", str(e))
                return False
    
    async def run_tasks(
        self, 
        tasks: list[SendTask], 
        on_status: Optional[Callable] = None,
        on_screenshot: Optional[Callable[[bytes, str], Awaitable[None]]] = None,
        context = None,
        create_link_fn: Optional[Callable] = None,
    ):
        """Запуск задач: по 2 параллельно (ускорение), с паузой между парами"""
        ctx = context or self._context
        batch_size = 2
        i = 0
        while i < len(tasks) and self._running:
            batch = tasks[i : i + batch_size]
            for task in batch:
                if create_link_fn and not task.message_link:
                    if asyncio.iscoroutinefunction(create_link_fn):
                        await create_link_fn(task)
                    else:
                        create_link_fn(task)
            task_results = await asyncio.gather(*[
                self.send_message(t, on_status=on_status, on_screenshot=on_screenshot, context=ctx)
                for t in batch
            ], return_exceptions=True)
            for j, tr in enumerate(task_results):
                if isinstance(tr, BaseException) and j < len(batch):
                    if on_status:
                        on_status(batch[j].id, "error", str(tr))
            i += batch_size
            if i < len(tasks):
                delay = random.uniform(self.delay_min, self.delay_max)
                await asyncio.sleep(delay)

    JOB_TIMEOUT_SEC = 180  # таймаут на один аккаунт (вход + отправка), чтобы не зависать

    async def run_single_job(
        self,
        account: dict,
        proxy_str: Optional[str],
        tasks: list[SendTask],
        on_status: Optional[Callable] = None,
        on_screenshot: Optional[Callable[[bytes, str], Awaitable[None]]] = None,
        on_login_failed: Optional[Callable[[dict], None]] = None,
        on_proxy_used: Optional[Callable[[str], None]] = None,
        create_link_fn: Optional[Callable] = None,
    ) -> bool:
        """Выполнить одну сессию: контекст с прокси, вход, отправка"""
        if not self._browser or not self._running:
            return False
        account_email = account.get("email", "?")
        def _wrapped_on_status(task_id: str, status: str, error: str = None):
            if on_status:
                try:
                    on_status(task_id, status, error, account_email=account_email)
                except TypeError:
                    on_status(task_id, status, error)
        job_ctx = await self._create_context_with_proxy(proxy_str)
        try:
            self._log(f"═══ {account_email}")
            ok = await self.login(account.get("email", ""), account.get("password", ""), context=job_ctx)
            if not ok:
                self._log(f"⚠ Не удалось войти: {account_email}")
                if on_login_failed:
                    on_login_failed(account)
                return False
            if on_proxy_used and proxy_str:
                on_proxy_used(proxy_str)
            self._log(f"✓ {account_email} вошёл")
            await self.run_tasks(tasks, on_status=_wrapped_on_status, on_screenshot=on_screenshot, context=job_ctx, create_link_fn=create_link_fn)
            return True
        except Exception as e:
            self._log(f"⚠ {account_email}: {str(e)[:120]}")
            raise
        finally:
            await job_ctx.close()

    async def run_jobs(
        self,
        jobs: list[tuple[dict, str, list[SendTask]]],
        on_status: Optional[Callable] = None,
        on_screenshot: Optional[Callable[[bytes, str], Awaitable[None]]] = None,
        on_login_failed: Optional[Callable[[dict], None]] = None,
        on_proxy_used: Optional[Callable[[str], None]] = None,
        create_link_fn: Optional[Callable] = None,
    ):
        """Запуск нескольких сессий параллельно. Ошибка в одном аккаунте не останавливает остальные."""
        async def _run_with_timeout(acc, proxy_str, tasks):
            try:
                return await asyncio.wait_for(
                    self.run_single_job(acc, proxy_str, tasks, on_status, on_screenshot, on_login_failed, on_proxy_used, create_link_fn),
                    timeout=self.JOB_TIMEOUT_SEC,
                )
            except asyncio.TimeoutError:
                raise TimeoutError(f"Таймаут ({self.JOB_TIMEOUT_SEC} сек) — аккаунт завис")
        coros = [_run_with_timeout(acc, proxy_str, tasks) for acc, proxy_str, tasks in jobs]
        results = await asyncio.gather(*coros, return_exceptions=True)
        for i, r in enumerate(results):
            if isinstance(r, BaseException):
                acc_email = jobs[i][0].get("email", "?") if i < len(jobs) else "?"
                err_msg = str(r)[:150]
                self._log(f"⚠ {acc_email}: {err_msg}")
