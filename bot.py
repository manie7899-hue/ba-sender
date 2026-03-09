# -*- coding: utf-8 -*-
"""
BA Sender — Telegram-бот (кнопки, пошагово, редактирование)
"""

import asyncio
import logging
import os
import random
import time
import uuid
from datetime import datetime
from io import BytesIO

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

from bot_storage import (
    load_user_data, save_user_data, list_pending_users,
    get_or_create_bot_user_id, get_next_search_id,
    is_seller_blacklisted, add_seller_to_blacklist,
    is_user_banned, add_banned_user, remove_banned_user,
    is_user_approved, add_approved_user,
)
from sender import OLXSender, SendTask
from telegram_api import create_telegram_link, fetch_listing_data

try:
    from config import BOT_TOKEN
except ImportError:
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
try:
    from config import ADMIN_CHAT_ID
except (ImportError, AttributeError):
    ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0"))
try:
    from config import SCREENSHOT_AFTER_SEND
except (ImportError, AttributeError):
    SCREENSHOT_AFTER_SEND = True

_running_tasks: dict[int, asyncio.Task] = {}
_user_logs: dict[int, list[str]] = {}
_run_log_msg: dict[int, dict] = {}  # user_id -> {chat_id, message_id, last_edit}
_last_search_id: dict[int, str] = {}  # user_id -> last search_id for logs screen
LOG_MAX = 50
LOG_EDIT_THROTTLE = 1.2
_bot_app = None

STEP_ACCOUNT = "acc"
STEP_PROXY = "proxy"
STEP_LINKS = "links"
STEP_MESSAGE = "msg"
STEP_RS_KEY = "rs_key"
STEP_RS_PROXY = "rs_proxy"
STEP_ACC_EDIT = "acc_ed"       # редактирование аккаунта
STEP_SESS_PROXY_EDIT = "sess_ped"  # редактирование прокси сессии
STEP_SESS_ADD_LINKS = "sess_ln"    # добавление ссылок к сессии
STEP_RUN_LINKS = "run_links"       # запрос ссылок при запуске
STEP_ACC_MASS = "acc_mass"         # массовое добавление аккаунтов
STEP_PROXY_ADD = "proxy_add"       # добавление прокси
STEP_PROXY_MASS = "proxy_mass"     # массовое добавление прокси


def _parse_links(text: str) -> list[str]:
    links = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if line and ("olx.ba" in line or line.startswith("http")):
            if not line.startswith("http"):
                line = "https://www.olx.ba" + line
            links.append(line)
    return links


def _parse_accounts(text: str) -> list[dict]:
    """Парсинг массового добавления: email:password"""
    result = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line or ":" not in line or "@" not in line:
            continue
        parts = line.split(":", 1)
        email, password = parts[0].strip(), parts[1]
        if email and password:
            result.append({"email": email, "password": password, "status": "unknown"})
    return result


def _parse_proxies(text: str) -> list[str]:
    """Парсинг прокси: каждая строка — один прокси (ip:port:user:pass или ip:port)"""
    result = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if line and line != "-":
            result.append(line)
    return result


def _kb_back(to: str = "back"):
    """Кнопка Назад. to: back, m_acc, m_sess, m_rs"""
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀ Назад", callback_data=f"nav_{to}")]])


def _admin_chat_enabled() -> bool:
    return bool(ADMIN_CHAT_ID)


def _user_has_access(uid: int) -> bool:
    """Проверка доступа: бан > модерация. Одобренные запоминаются навсегда."""
    if is_user_banned(uid):
        return False
    if is_user_approved(uid):
        return True
    if not _admin_chat_enabled():
        return True
    data = load_user_data(uid)
    if data.get("access_status") == "approved":
        add_approved_user(uid)
        return True
    if data.get("access_status") in (None, "new") and (data.get("accounts") or data.get("jobs") or data.get("proxies")):
        return True
    return False


def _kb_request_access():
    return InlineKeyboardMarkup([[InlineKeyboardButton("📝 Запросить доступ", callback_data="req_access")]])


def _main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 Аккаунты", callback_data="m_acc"), InlineKeyboardButton("🌐 Прокси", callback_data="m_proxy")],
        [InlineKeyboardButton("📋 Сессии", callback_data="m_sess"), InlineKeyboardButton("✉️ Сообщение", callback_data="m_msg")],
        [InlineKeyboardButton("📱 RedScript", callback_data="m_rs")],
        [InlineKeyboardButton("▶️ Запустить", callback_data="run"), InlineKeyboardButton("⏹ Стоп", callback_data="stop")],
        [InlineKeyboardButton("📋 Логи", callback_data="logs")],
    ])


async def _log(user_id: int, msg: str, context=None):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    if user_id not in _user_logs:
        _user_logs[user_id] = []
    _user_logs[user_id].append(line)
    if len(_user_logs[user_id]) > LOG_MAX:
        _user_logs[user_id].pop(0)

    if user_id in _run_log_msg:
        run_state = _run_log_msg[user_id]
        logs = _user_logs[user_id][-LOG_MAX:]
        bot_id = run_state.get("bot_id") or get_or_create_bot_user_id(user_id)
        search_id = run_state.get("search_id", "")
        txt = f"📋 Лог {search_id} ({bot_id})\n\n" + "\n".join(logs[-25:])
        if len(txt) > 4000:
            txt = txt[-4000:]
        now = time.monotonic()
        if now - run_state.get("last_edit", 0) >= LOG_EDIT_THROTTLE:
            run_state["last_edit"] = now
            try:
                bot = (context.bot if context else None) or (_bot_app.bot if _bot_app else None)
                if bot:
                    await bot.edit_message_text(
                        chat_id=run_state["chat_id"],
                        message_id=run_state["message_id"],
                        text=txt,
                    )
            except Exception:
                pass
        return

    try:
        bot = (context.bot if context else None) or (_bot_app.bot if _bot_app else None)
        if bot:
            await bot.send_message(chat_id=user_id, text=line)
    except Exception:
        pass


async def _run_log_final_update(user_id: int, context=None):
    """Финальное обновление лога после завершения запуска."""
    if user_id not in _run_log_msg:
        return
    run_state = _run_log_msg.pop(user_id, None)
    if not run_state:
        return
    if run_state.get("search_id"):
        _last_search_id[user_id] = run_state["search_id"]
    logs = _user_logs.get(user_id, [])[-25:]
    bot_id = run_state.get("bot_id") or get_or_create_bot_user_id(user_id)
    search_id = run_state.get("search_id", "")
    txt = f"📋 Лог {search_id} ({bot_id})\n\n" + "\n".join(logs) if logs else f"📋 Лог {search_id} ({bot_id})\n\n(пусто)"
    if len(txt) > 4000:
        txt = txt[-4000:]
    try:
        bot = (context.bot if context else None) or (_bot_app.bot if _bot_app else None)
        if bot:
            await bot.edit_message_text(
                chat_id=run_state["chat_id"],
                message_id=run_state["message_id"],
                text=txt,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀ Назад", callback_data="nav_back")]]),
            )
    except Exception:
        pass


def _clear_step(context: ContextTypes.DEFAULT_TYPE):
    for k in ["step", "sess_acc_idx", "sess_proxy", "acc_edit_idx", "sess_edit_idx"]:
        context.user_data.pop(k, None)


async def _show_screen(bot, chat_id: int, screen: str, context: ContextTypes.DEFAULT_TYPE, text: str = "", kb: InlineKeyboardMarkup = None):
    """Показать экран (для навигации)"""
    data = load_user_data(chat_id)
    if screen == "main":
        acc_count = len(data.get("accounts", []))
        proxy_count = len(data.get("proxies", []))
        job_count = len(data.get("jobs", []))
        txt = f"🚀 BA Sender\n\nАккаунтов: {acc_count} | Прокси: {proxy_count} | Сессий: {job_count}\n\nВыберите:"
        await bot.edit_message_text(chat_id=chat_id, message_id=context._chat_data.get("msg_id"), text=txt, reply_markup=_main_keyboard())
        return
    # Для edit_message_text нужен message_id - мы вызываем из callback, там есть query
    # Упростим: передаём query и делаем edit_message_text через него
    pass


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    data = load_user_data(uid)
    d = query.data

    if is_user_banned(uid) and not d.startswith("req_approve_") and not d.startswith("req_reject_"):
        try:
            await query.edit_message_text("❌ Вы заблокированы.")
        except Exception:
            pass
        return

    # === АДМИН: ОДОБРЕНИЕ / ОТКЛОНЕНИЕ ЗАЯВКИ ===
    if d.startswith("req_approve_") or d.startswith("req_reject_"):
        if chat_id != ADMIN_CHAT_ID:
            await query.answer("Только в чате панели заявок", show_alert=True)
            return
        try:
            target_uid = int(d.split("_")[2])
        except (IndexError, ValueError):
            await query.answer("Ошибка", show_alert=True)
            return
        target_data = load_user_data(target_uid)
        approved = d.startswith("req_approve_")
        target_data["access_status"] = "approved" if approved else "rejected"
        save_user_data(target_uid, target_data)
        if approved:
            add_approved_user(target_uid)
        status_text = "✅ Одобрено" if approved else "❌ Отклонено"
        try:
            await query.edit_message_text(f"{query.message.text}\n\n{status_text}")
        except Exception:
            pass
        try:
            msg = "✅ Вам одобрен доступ к BA Sender. Нажмите /start" if approved else "❌ Ваша заявка отклонена."
            await context.bot.send_message(target_uid, msg)
        except Exception:
            pass
        return

    # === ЗАПРОС ДОСТУПА ===
    if d == "req_access":
        if not _admin_chat_enabled():
            await query.edit_message_text("Модерация отключена.", reply_markup=_main_keyboard())
            return
        if is_user_approved(uid) or data.get("access_status") == "approved":
            if not is_user_approved(uid):
                add_approved_user(uid)
            data["access_status"] = "approved"
            save_user_data(uid, data)
            await query.edit_message_text("У вас уже есть доступ.", reply_markup=_main_keyboard())
            return
        is_repeat = data.get("access_status") == "pending"
        data["access_status"] = "pending"
        save_user_data(uid, data)
        user = update.effective_user
        bot_user_id = get_or_create_bot_user_id(uid)
        name = user.full_name or user.username or str(uid)
        username = f"@{user.username}" if user.username else ""
        header = "📋 Повторная заявка на доступ\n\n" if is_repeat else "📋 Новая заявка на доступ\n\n"
        txt = (
            f"{header}"
            f"Bot ID: {bot_user_id}\n"
            f"Telegram ID: {uid}\n"
            f"Имя: {name}\n"
            f"Username: {username}\n\n"
            f"Выберите действие:"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Одобрить", callback_data=f"req_approve_{uid}"), InlineKeyboardButton("❌ Отклонить", callback_data=f"req_reject_{uid}")],
        ])
        try:
            await context.bot.send_message(ADMIN_CHAT_ID, txt, reply_markup=kb)
        except Exception as e:
            data["access_status"] = "new"
            save_user_data(uid, data)
            await query.answer(f"Ошибка отправки: {e}", show_alert=True)
            return
        msg = "📝 Заявка отправлена повторно. Ожидайте рассмотрения." if is_repeat else "📝 Заявка отправлена. Ожидайте рассмотрения."
        await query.edit_message_text(msg, reply_markup=None)
        return

    # === ПРОВЕРКА ДОСТУПА ===
    if not _user_has_access(uid):
        if data.get("access_status") == "pending":
            await query.answer("Заявка на рассмотрении", show_alert=True)
        else:
            await query.edit_message_text(
                "Для работы с сендером нужен доступ.\n\nНажмите кнопку ниже:",
                reply_markup=_kb_request_access(),
            )
        return

    # === НАВИГАЦИЯ ===
    if d.startswith("nav_"):
        target = d[4:]
        step = context.user_data.get("step")
        _clear_step(context)
        # Отмена создания сессии (назад со шага ссылок) — удалить пустую сессию
        if step == STEP_LINKS and data.get("jobs"):
            last = data["jobs"][-1]
            if not last.get("links"):
                data["jobs"].pop()
                save_user_data(uid, data)
        if target == "back":
            bot_id = get_or_create_bot_user_id(uid)
            acc_count = len(data.get("accounts", []))
            proxy_count = len(data.get("proxies", []))
            job_count = len(data.get("jobs", []))
            txt = f"🚀 BA Sender\n\nВаш ID: {bot_id}\nАккаунтов: {acc_count} | Прокси: {proxy_count} | Сессий: {job_count}\n\nВыберите:"
            kb = _main_keyboard()
        elif target == "m_acc":
            accs = data.get("accounts", [])
            lines = [f"{i+1}. {a.get('email','?')}" for i, a in enumerate(accs)]
            txt = "👤 Аккаунты\n\n" + ("\n".join(lines) if lines else "Нет аккаунтов")
            kb = [[InlineKeyboardButton("➕ Добавить", callback_data="acc_add"), InlineKeyboardButton("📦 Массово", callback_data="acc_mass")]]
            if accs:
                kb.append([InlineKeyboardButton("✏️ Изменить", callback_data="acc_edit"), InlineKeyboardButton("🗑 Удалить", callback_data="acc_del")])
            kb.append([InlineKeyboardButton("◀ Назад", callback_data="nav_back")])
            kb = InlineKeyboardMarkup(kb)
        elif target == "m_sess":
            jobs = data.get("jobs", [])
            lines = []
            for i, j in enumerate(jobs):
                acc = j.get("account_email", "?")[:28]
                links = j.get("links", [])
                lines.append(f"{i+1}. {acc}... | {len(links)} сс.")
            txt = "📋 Сессии\n\n" + ("\n".join(lines) if lines else "Нет сессий")
            kb = [[InlineKeyboardButton("➕ Добавить", callback_data="sess_add")]]
            if jobs:
                kb.append([InlineKeyboardButton("✏️ Изменить", callback_data="sess_edit"), InlineKeyboardButton("🗑 Удалить", callback_data="sess_del")])
            kb.append([InlineKeyboardButton("◀ Назад", callback_data="nav_back")])
            kb = InlineKeyboardMarkup(kb)
        elif target == "m_proxy":
            proxies = data.get("proxies", [])
            lines = [f"{i+1}. {p[:40]}..." if len(p) > 40 else f"{i+1}. {p}" for i, p in enumerate(proxies)]
            txt = "🌐 Прокси\n\n" + ("\n".join(lines) if lines else "Нет прокси.")
            kb = [[InlineKeyboardButton("➕ Добавить", callback_data="proxy_add"), InlineKeyboardButton("📦 Массово", callback_data="proxy_mass")]]
            if proxies:
                kb.append([InlineKeyboardButton("🗑 Удалить", callback_data="proxy_del")])
            kb.append([InlineKeyboardButton("◀ Назад", callback_data="nav_back")])
            kb = InlineKeyboardMarkup(kb)
        elif target == "m_rs":
            enabled = data.get("telegram_api_enabled", False)
            key = data.get("telegram_api_key", "")
            txt = f"📱 RedScript\nВключено: {'✅ Да' if enabled else '❌ Нет'}\nКлюч: {'***' if key else 'не задан'}"
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔑 Ключ API", callback_data="rs_key"), InlineKeyboardButton("🌐 Прокси API", callback_data="rs_proxy")],
                [InlineKeyboardButton("✅ Вкл" if not enabled else "❌ Выкл", callback_data="rs_toggle")],
                [InlineKeyboardButton("◀ Назад", callback_data="nav_back")],
            ])
        else:
            bot_id = get_or_create_bot_user_id(uid)
            acc_count = len(data.get("accounts", []))
            proxy_count = len(data.get("proxies", []))
            job_count = len(data.get("jobs", []))
            txt = f"🚀 BA Sender\n\nВаш ID: {bot_id}\nАккаунтов: {acc_count} | Прокси: {proxy_count} | Сессий: {job_count}\n\nВыберите:"
            kb = _main_keyboard()
        await query.edit_message_text(txt, reply_markup=kb)
        return

    # === АККАУНТЫ ===
    if d == "m_acc":
        accs = data.get("accounts", [])
        lines = [f"{i+1}. {a.get('email','?')}" for i, a in enumerate(accs)]
        txt = "👤 Аккаунты\n\n" + ("\n".join(lines) if lines else "Нет аккаунтов")
        kb = [[InlineKeyboardButton("➕ Добавить", callback_data="acc_add"), InlineKeyboardButton("📦 Массово", callback_data="acc_mass")]]
        if accs:
            kb.append([InlineKeyboardButton("✏️ Изменить", callback_data="acc_edit"), InlineKeyboardButton("🗑 Удалить", callback_data="acc_del")])
        kb.append([InlineKeyboardButton("◀ Назад", callback_data="nav_back")])
        await query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb))

    elif d == "acc_add":
        context.user_data["step"] = STEP_ACCOUNT
        await query.edit_message_text(
            "Отправьте email:пароль\n\nПример: user@gmail.com:MyPass123",
            reply_markup=_kb_back("m_acc"),
        )

    elif d == "acc_mass":
        context.user_data["step"] = STEP_ACC_MASS
        await query.edit_message_text(
            "Отправьте аккаунты (каждая с новой строки):\n\n"
            "email:password\nemail:password\nemail:password",
            reply_markup=_kb_back("m_acc"),
        )

    elif d == "acc_edit":
        accs = data.get("accounts", [])
        if not accs:
            await query.answer("Нет аккаунтов")
            return
        kb = [[InlineKeyboardButton(f"✏️ {a.get('email','?')[:32]}", callback_data=f"acc_ed_{i}")] for i, a in enumerate(accs)]
        kb.append([InlineKeyboardButton("◀ Назад", callback_data="nav_m_acc")])
        await query.edit_message_text("Выберите аккаунт для изменения:", reply_markup=InlineKeyboardMarkup(kb))

    elif d.startswith("acc_ed_"):
        idx = int(d.split("_")[2])
        accs = data.get("accounts", [])
        if 0 <= idx < len(accs):
            context.user_data["step"] = STEP_ACC_EDIT
            context.user_data["acc_edit_idx"] = idx
            await query.edit_message_text(
                f"Отправьте новый email:пароль для {accs[idx].get('email','?')}",
                reply_markup=_kb_back("m_acc"),
            )

    elif d == "acc_del":
        accs = data.get("accounts", [])
        if not accs:
            await query.answer("Нет аккаунтов")
            return
        kb = [[InlineKeyboardButton(f"🗑 {a.get('email','?')[:32]}", callback_data=f"acc_rm_{i}")] for i, a in enumerate(accs)]
        kb.append([InlineKeyboardButton("◀ Назад", callback_data="nav_m_acc")])
        await query.edit_message_text("Удалить аккаунт:", reply_markup=InlineKeyboardMarkup(kb))

    elif d.startswith("acc_rm_"):
        idx = int(d.split("_")[2])
        accs = data.get("accounts", [])
        if 0 <= idx < len(accs):
            email = accs[idx].get("email", "")
            accs.pop(idx)
            data["accounts"] = accs
            data["jobs"] = [j for j in data.get("jobs", []) if j.get("account_email") != email]
            save_user_data(uid, data)
        await query.edit_message_text("✅ Удалено", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀ К аккаунтам", callback_data="nav_m_acc")]]))

    # === ПРОКСИ ===
    elif d == "m_proxy":
        proxies = data.get("proxies", [])
        lines = [f"{i+1}. {p[:40]}..." if len(p) > 40 else f"{i+1}. {p}" for i, p in enumerate(proxies)]
        txt = "🌐 Прокси\n\n" + ("\n".join(lines) if lines else "Нет прокси. Используются вперемешку с аккаунтами.")
        kb = [[InlineKeyboardButton("➕ Добавить", callback_data="proxy_add"), InlineKeyboardButton("📦 Массово", callback_data="proxy_mass")]]
        if proxies:
            kb.append([InlineKeyboardButton("🗑 Удалить", callback_data="proxy_del")])
        kb.append([InlineKeyboardButton("◀ Назад", callback_data="nav_back")])
        await query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb))

    elif d == "proxy_add":
        context.user_data["step"] = STEP_PROXY_ADD
        await query.edit_message_text(
            "Отправьте прокси (ip:port:user:pass или ip:port):",
            reply_markup=_kb_back("m_proxy"),
        )

    elif d == "proxy_mass":
        context.user_data["step"] = STEP_PROXY_MASS
        await query.edit_message_text(
            "Отправьте прокси (каждый с новой строки):",
            reply_markup=_kb_back("m_proxy"),
        )

    elif d == "proxy_del":
        proxies = data.get("proxies", [])
        if not proxies:
            await query.answer("Нет прокси")
            return
        kb = [[InlineKeyboardButton(f"🗑 {p[:35]}...", callback_data=f"proxy_rm_{i}")] for i, p in enumerate(proxies)]
        kb.append([InlineKeyboardButton("◀ Назад", callback_data="nav_m_proxy")])
        await query.edit_message_text("Удалить прокси:", reply_markup=InlineKeyboardMarkup(kb))

    elif d.startswith("proxy_rm_"):
        idx = int(d.split("_")[2])
        proxies = data.get("proxies", [])
        if 0 <= idx < len(proxies):
            proxies.pop(idx)
            data["proxies"] = proxies
            save_user_data(uid, data)
        await query.edit_message_text("✅ Удалено", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀ К прокси", callback_data="nav_m_proxy")]]))

    # === СЕССИИ ===
    elif d == "m_sess":
        jobs = data.get("jobs", [])
        lines = [f"{i+1}. {j.get('account_email','?')[:28]}... | {len(j.get('links',[]))} сс." for i, j in enumerate(jobs)]
        txt = "📋 Сессии\n\n" + ("\n".join(lines) if lines else "Нет сессий")
        kb = [[InlineKeyboardButton("➕ Добавить", callback_data="sess_add")]]
        if jobs:
            kb.append([InlineKeyboardButton("✏️ Изменить", callback_data="sess_edit"), InlineKeyboardButton("🗑 Удалить", callback_data="sess_del")])
        kb.append([InlineKeyboardButton("◀ Назад", callback_data="nav_back")])
        await query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb))

    elif d == "sess_add":
        accs = data.get("accounts", [])
        if not accs:
            await query.edit_message_text("Сначала добавьте аккаунт.", reply_markup=_kb_back("m_acc"))
            return
        _clear_step(context)
        kb = [[InlineKeyboardButton(f"{a.get('email','?')[:35]}", callback_data=f"sess_acc_{i}")] for i, a in enumerate(accs)]
        kb.append([InlineKeyboardButton("◀ Назад", callback_data="nav_m_sess")])
        await query.edit_message_text("Шаг 1/3: Выберите аккаунт:", reply_markup=InlineKeyboardMarkup(kb))

    elif d.startswith("sess_acc_"):
        idx = int(d.split("_")[2])
        accs = data.get("accounts", [])
        if 0 <= idx < len(accs):
            context.user_data["step"] = STEP_PROXY
            context.user_data["sess_acc_idx"] = idx
            await query.edit_message_text(
                "Шаг 2/3: Отправьте прокси (ip:port:user:pass)\n\nИли - если без прокси",
                reply_markup=_kb_back("m_sess"),
            )

    elif d == "sess_edit":
        jobs = data.get("jobs", [])
        if not jobs:
            await query.answer("Нет сессий")
            return
        kb = []
        for i, j in enumerate(jobs):
            acc = j.get("account_email", "?")[:25]
            ln = len(j.get("links", []))
            kb.append([
                InlineKeyboardButton(f"✏️ {acc}...", callback_data=f"sess_ed_{i}"),
                InlineKeyboardButton(f"+ ссылки", callback_data=f"sess_ln_{i}"),
            ])
        kb.append([InlineKeyboardButton("◀ Назад", callback_data="nav_m_sess")])
        await query.edit_message_text("Изменить сессию:", reply_markup=InlineKeyboardMarkup(kb))

    elif d.startswith("sess_ed_"):
        idx = int(d.split("_")[2])
        jobs = data.get("jobs", [])
        if 0 <= idx < len(jobs):
            context.user_data["step"] = STEP_SESS_PROXY_EDIT
            context.user_data["sess_edit_idx"] = idx
            curr = jobs[idx].get("proxy", "") or "-"
            disp = curr[:50] + ("..." if len(curr) > 50 else "")
            await query.edit_message_text(
                f"Текущий прокси: {disp}\n\nОтправьте новый прокси или -",
                reply_markup=_kb_back("m_sess"),
            )

    elif d.startswith("sess_ln_"):
        idx = int(d.split("_")[2])
        jobs = data.get("jobs", [])
        if 0 <= idx < len(jobs):
            context.user_data["step"] = STEP_SESS_ADD_LINKS
            context.user_data["sess_edit_idx"] = idx
            await query.edit_message_text(
                "Отправьте ссылки (каждая с новой строки). Старые ссылки будут заменены.",
                reply_markup=_kb_back("m_sess"),
            )

    elif d == "sess_del":
        jobs = data.get("jobs", [])
        if not jobs:
            await query.answer("Нет сессий")
            return
        kb = [[InlineKeyboardButton(f"🗑 {j.get('account_email','?')[:25]}... ({len(j.get('links',[]))} сс.)", callback_data=f"sess_rm_{i}")] for i, j in enumerate(jobs)]
        kb.append([InlineKeyboardButton("◀ Назад", callback_data="nav_m_sess")])
        await query.edit_message_text("Удалить сессию:", reply_markup=InlineKeyboardMarkup(kb))

    elif d.startswith("sess_rm_"):
        idx = int(d.split("_")[2])
        jobs = data.get("jobs", [])
        if 0 <= idx < len(jobs):
            jobs.pop(idx)
            data["jobs"] = jobs
            save_user_data(uid, data)
        await query.edit_message_text("✅ Удалено", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀ К сессиям", callback_data="nav_m_sess")]]))

    # === СООБЩЕНИЕ ===
    elif d == "m_msg":
        msg = data.get("message", "")[:400]
        context.user_data["step"] = STEP_MESSAGE
        await query.edit_message_text(
            f"✉️ Текущий текст:\n\n{msg}\n\nОтправьте новый текст:",
            reply_markup=_kb_back("back"),
        )

    # === REDSCRIPT ===
    elif d == "m_rs":
        enabled = data.get("telegram_api_enabled", False)
        key = data.get("telegram_api_key", "")
        txt = f"📱 RedScript\nВключено: {'✅ Да' if enabled else '❌ Нет'}\nКлюч: {'***' if key else 'не задан'}"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔑 Ключ API", callback_data="rs_key"), InlineKeyboardButton("🌐 Прокси API", callback_data="rs_proxy")],
            [InlineKeyboardButton("✅ Вкл" if not enabled else "❌ Выкл", callback_data="rs_toggle")],
            [InlineKeyboardButton("◀ Назад", callback_data="nav_back")],
        ])
        await query.edit_message_text(txt, reply_markup=kb)

    elif d == "rs_key":
        context.user_data["step"] = STEP_RS_KEY
        await query.edit_message_text("Отправьте API ключ:", reply_markup=_kb_back("m_rs"))

    elif d == "rs_proxy":
        context.user_data["step"] = STEP_RS_PROXY
        await query.edit_message_text("Отправьте прокси или - для сброса:", reply_markup=_kb_back("m_rs"))

    elif d == "rs_toggle":
        data["telegram_api_enabled"] = not data.get("telegram_api_enabled", False)
        save_user_data(uid, data)
        await query.answer(f"RedScript: {'вкл' if data['telegram_api_enabled'] else 'выкл'}")
        enabled = data["telegram_api_enabled"]
        key = data.get("telegram_api_key", "")
        txt = f"📱 RedScript\nВключено: {'✅ Да' if enabled else '❌ Нет'}\nКлюч: {'***' if key else 'не задан'}"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔑 Ключ API", callback_data="rs_key"), InlineKeyboardButton("🌐 Прокси API", callback_data="rs_proxy")],
            [InlineKeyboardButton("✅ Вкл" if not enabled else "❌ Выкл", callback_data="rs_toggle")],
            [InlineKeyboardButton("◀ Назад", callback_data="nav_back")],
        ])
        await query.edit_message_text(txt, reply_markup=kb)

    # === ЗАПУСК / СТОП / ЛОГИ ===
    elif d == "run":
        if uid in _running_tasks:
            await query.answer("⏳ Уже выполняется")
            return
        accs = data.get("accounts", [])
        if not accs:
            await query.answer("❌ Добавьте аккаунты", show_alert=True)
            return
        context.user_data["step"] = STEP_RUN_LINKS
        await query.edit_message_text(
            "📤 Отправьте ссылки на объявления (каждая с новой строки):\n\n"
            "Ссылки распределятся по аккаунтам по кругу.",
            reply_markup=_kb_back("back"),
        )

    elif d == "stop":
        if uid in _running_tasks:
            _running_tasks[uid].cancel()
            await _log(uid, "Остановка...", context)
        await query.edit_message_text("⏹ Остановлено", reply_markup=_main_keyboard())

    elif d == "logs":
        logs = _user_logs.get(uid, [])
        bot_id = get_or_create_bot_user_id(uid)
        run_state = _run_log_msg.get(uid, {})
        search_id = run_state.get("search_id") or _last_search_id.get(uid, "")
        header = f"📋 Лог {search_id} ({bot_id})" if search_id else f"📋 Лог ({bot_id})"
        txt = "\n".join(logs[-15:]) if logs else "Лог пуст"
        if len(txt) > 3500:
            txt = txt[-3500:]
        await query.edit_message_text(f"{header}:\n\n{txt}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀ Назад", callback_data="nav_back")]]))


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    data = load_user_data(uid)

    if is_user_banned(uid):
        await update.message.reply_text("❌ Вы заблокированы.")
        return
    if not _user_has_access(uid):
        if text != "/start":
            await update.message.reply_text("Для работы нужен доступ. Нажмите /start", reply_markup=_kb_request_access())
        return

    step = context.user_data.get("step")

    if step == STEP_ACCOUNT:
        if ":" in text and "@" in text:
            parts = text.split(":", 1)
            email, password = parts[0].strip(), parts[1]
            data["accounts"] = data.get("accounts", [])
            data["accounts"].append({"email": email, "password": password, "status": "unknown"})
            save_user_data(uid, data)
            _clear_step(context)
            await update.message.reply_text(f"✅ Аккаунт добавлен: {email}", reply_markup=_main_keyboard())
        else:
            await update.message.reply_text("Формат: email:пароль")

    elif step == STEP_ACC_EDIT:
        if ":" in text and "@" in text:
            idx = context.user_data.get("acc_edit_idx", 0)
            parts = text.split(":", 1)
            email, password = parts[0].strip(), parts[1]
            accs = data.get("accounts", [])
            if 0 <= idx < len(accs):
                old_email = accs[idx].get("email", "")
                accs[idx] = {"email": email, "password": password, "status": "unknown"}
                for j in data.get("jobs", []):
                    if j.get("account_email") == old_email:
                        j["account_email"] = email
                save_user_data(uid, data)
            _clear_step(context)
            await update.message.reply_text(f"✅ Аккаунт обновлён: {email}", reply_markup=_main_keyboard())
        else:
            await update.message.reply_text("Формат: email:пароль")

    elif step == STEP_PROXY:
        context.user_data["step"] = STEP_LINKS
        context.user_data["sess_proxy"] = "" if text == "-" else text
        idx = context.user_data.get("sess_acc_idx", 0)
        accs = data.get("accounts", [])
        if 0 <= idx < len(accs):
            data["jobs"] = data.get("jobs", [])
            data["jobs"].append({
                "account_email": accs[idx]["email"],
                "proxy": context.user_data["sess_proxy"],
                "links": [],
            })
            save_user_data(uid, data)
        await update.message.reply_text("Шаг 3/3: Отправьте ссылки (каждая с новой строки):", reply_markup=_kb_back("m_sess"))

    elif step == STEP_LINKS:
        links = _parse_links(text)
        if links:
            _clear_step(context)
            data["jobs"] = data.get("jobs", [])
            if data["jobs"]:
                data["jobs"][-1]["links"] = links
                save_user_data(uid, data)
            await update.message.reply_text(f"✅ Сессия создана, {len(links)} ссылок", reply_markup=_main_keyboard())
        else:
            await update.message.reply_text("Отправьте ссылки на olx.ba")

    elif step == STEP_RUN_LINKS:
        links = _parse_links(text)
        _clear_step(context)
        if not links:
            await update.message.reply_text("Отправьте ссылки на olx.ba (каждая с новой строки)", reply_markup=_main_keyboard())
            return
        await update.message.reply_text(f"▶️ Запуск: {len(links)} ссылок", reply_markup=_main_keyboard())
        await _run_sender(uid, context, links=links)

    elif step == STEP_ACC_MASS:
        accs_new = _parse_accounts(text)
        _clear_step(context)
        if not accs_new:
            await update.message.reply_text("Формат: email:password (каждая с новой строки)", reply_markup=_main_keyboard())
            return
        data["accounts"] = data.get("accounts", []) + accs_new
        save_user_data(uid, data)
        await update.message.reply_text(f"✅ Добавлено {len(accs_new)} аккаунтов", reply_markup=_main_keyboard())

    elif step == STEP_PROXY_ADD:
        proxy = text.strip()
        _clear_step(context)
        if proxy and proxy != "-":
            data["proxies"] = data.get("proxies", []) + [proxy]
            save_user_data(uid, data)
            await update.message.reply_text("✅ Прокси добавлен", reply_markup=_main_keyboard())
        else:
            await update.message.reply_text("Отправьте прокси (ip:port:user:pass)", reply_markup=_main_keyboard())

    elif step == STEP_PROXY_MASS:
        proxies_new = _parse_proxies(text)
        _clear_step(context)
        if proxies_new:
            data["proxies"] = data.get("proxies", []) + proxies_new
            save_user_data(uid, data)
            await update.message.reply_text(f"✅ Добавлено {len(proxies_new)} прокси", reply_markup=_main_keyboard())
        else:
            await update.message.reply_text("Отправьте прокси (каждый с новой строки)", reply_markup=_main_keyboard())

    elif step == STEP_SESS_PROXY_EDIT:
        idx = context.user_data.get("sess_edit_idx", 0)
        jobs = data.get("jobs", [])
        if 0 <= idx < len(jobs):
            jobs[idx]["proxy"] = "" if text == "-" else text
            save_user_data(uid, data)
        _clear_step(context)
        await update.message.reply_text("✅ Прокси обновлён", reply_markup=_main_keyboard())

    elif step == STEP_SESS_ADD_LINKS:
        links = _parse_links(text)
        idx = context.user_data.get("sess_edit_idx", 0)
        jobs = data.get("jobs", [])
        if links and 0 <= idx < len(jobs):
            jobs[idx]["links"] = links
            save_user_data(uid, data)
            _clear_step(context)
            await update.message.reply_text(f"✅ Ссылки обновлены: {len(links)} шт.", reply_markup=_main_keyboard())
        else:
            await update.message.reply_text("Отправьте ссылки на olx.ba (каждая с новой строки)")

    elif step == STEP_MESSAGE:
        data["message"] = text
        save_user_data(uid, data)
        _clear_step(context)
        await update.message.reply_text("✅ Текст сохранён", reply_markup=_main_keyboard())

    elif step == STEP_RS_KEY:
        data["telegram_api_key"] = text
        save_user_data(uid, data)
        _clear_step(context)
        await update.message.reply_text("✅ Ключ сохранён", reply_markup=_main_keyboard())

    elif step == STEP_RS_PROXY:
        data["telegram_api_proxy"] = "" if text == "-" else text
        save_user_data(uid, data)
        _clear_step(context)
        await update.message.reply_text("✅ Прокси сохранён", reply_markup=_main_keyboard())

    else:
        if text != "/start":
            await update.message.reply_text("Нажмите /start", reply_markup=_main_keyboard())


async def _run_sender(user_id: int, context: ContextTypes.DEFAULT_TYPE, query=None, links: list = None):
    data = load_user_data(user_id)
    message = data.get("message", "Zdravo!")
    dmin, dmax = data.get("delay_min", 3), data.get("delay_max", 5)
    tg_key = data.get("telegram_api_key", "")
    tg_enabled = data.get("telegram_api_enabled", False)
    tg_proxy = data.get("telegram_api_proxy", "").strip() or None

    accounts = data.get("accounts", [])
    jobs = []

    proxies = data.get("proxies", [])

    if links:
        # Режим: ссылки при запуске, ротация по аккаунтам, рандомный прокси
        if not accounts:
            await context.bot.send_message(user_id, "❌ Добавьте аккаунты.")
            return
        n = len(accounts)
        for i, acc in enumerate(accounts):
            acc_links = [links[j] for j in range(i, len(links), n)]
            if acc_links:
                proxy_str = random.choice(proxies).strip() if proxies else None
                jobs.append((acc, proxy_str, acc_links))
    else:
        # Режим: сессии из jobs (старый), прокси из сессии или рандом
        jobs_raw = data.get("jobs", [])
        acc_map = {a.get("email"): a for a in accounts}
        for j in jobs_raw:
            acc = acc_map.get(j.get("account_email", ""))
            if not acc:
                continue
            acc_links = j.get("links", [])
            if not acc_links:
                continue
            proxy_str = j.get("proxy", "").strip() or None
            if not proxy_str and proxies:
                proxy_str = random.choice(proxies).strip()
            jobs.append((acc, proxy_str, acc_links))

    if not jobs:
        await context.bot.send_message(user_id, "❌ Нет сессий с ссылками." if not links else "❌ Нет ссылок.")
        return

    bot_user_id = get_or_create_bot_user_id(user_id)
    search_id = get_next_search_id()
    chat_id = user_id
    if query:
        await query.edit_message_text("▶️ Запуск...")
        chat_id = query.message.chat_id
    msg = await context.bot.send_message(chat_id, f"📋 Лог {search_id} ({bot_user_id})\n\nЗапуск...")
    try:
        await context.bot.pin_chat_message(chat_id=chat_id, message_id=msg.message_id)
    except Exception:
        pass
    _run_log_msg[user_id] = {"chat_id": chat_id, "message_id": msg.message_id, "last_edit": 0, "bot_id": bot_user_id, "search_id": search_id}

    stats = {"success": 0, "error": 0, "skipped": 0}

    def on_log(msg: str):
        asyncio.create_task(_log(user_id, msg, context))

    def on_status(task_id: str, status: str, error: str = None):
        if status == "success":
            stats["success"] += 1
        elif status == "error":
            stats["error"] += 1
        elif status == "skipped":
            stats["skipped"] += 1

    async def on_screenshot(screenshot_bytes: bytes, caption: str):
        try:
            await context.bot.send_photo(
                chat_id=user_id,
                photo=BytesIO(screenshot_bytes),
                caption=f"✅ {search_id} Отправлено: {caption}"[:1024],
            )
        except Exception as e:
            asyncio.create_task(_log(user_id, f"Скриншот не отправлен: {e}", context))

    async def _do_run():
        try:
            sender = OLXSender(delay_min=dmin, delay_max=dmax, max_concurrent=3, on_log=on_log)
            await sender.start(headless=True, create_context=False)
            built_jobs = []
            for acc, proxy_str, acc_links in jobs:
                tasks = [SendTask(id=str(uuid.uuid4()), listing_url=link, message=message, created_at=0) for link in acc_links]
                if tg_enabled and tg_key:
                    on_log("Создание ссылок RedScript...")
                    api_proxy = tg_proxy or proxy_str
                    for task in tasks:
                        title, price = fetch_listing_data(task.listing_url, api_proxy)
                        link = create_telegram_link(tg_key, task.listing_url, title=title, price=price, on_debug=on_log, proxy=api_proxy)
                        if link:
                            task.message_link = link
                built_jobs.append((acc, proxy_str, tasks))
            await sender.run_jobs(built_jobs, on_status=on_status, on_screenshot=on_screenshot if SCREENSHOT_AFTER_SEND else None)
            await sender.stop()
            on_log("✅ Завершено")
            total = stats["success"] + stats["error"] + stats["skipped"]
            parts = [f"Успешно {stats['success']}", f"Не успешно {stats['error']}"]
            if stats["skipped"]:
                parts.append(f"Пропущено (ч/с) {stats['skipped']}")
            on_log(f"📊 Результат: {' / '.join(parts)} (всего {total})")
        except asyncio.CancelledError:
            on_log("⏹ Остановлено")
        except Exception as e:
            on_log(f"❌ Ошибка: {e}")
        finally:
            await _run_log_final_update(user_id, context)
            _running_tasks.pop(user_id, None)

    _running_tasks[user_id] = asyncio.create_task(_do_run())


async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /ban — только в чате панели. /ban @username или /ban ID"""
    if update.effective_chat.id != ADMIN_CHAT_ID or not _admin_chat_enabled():
        return
    args = (context.args or [])
    if not args:
        await update.message.reply_text("Использование: /ban @username или /ban 123456789")
        return
    arg = args[0].strip()
    target_id = None
    if arg.isdigit():
        target_id = int(arg)
    elif arg.startswith("@"):
        try:
            chat = await context.bot.get_chat(arg)
            target_id = chat.id
        except Exception as e:
            await update.message.reply_text(f"Не удалось найти пользователя {arg}: {e}")
            return
    else:
        await update.message.reply_text("Укажите @username или числовой ID")
        return
    if target_id:
        add_banned_user(target_id)
        await update.message.reply_text(f"✅ Пользователь {arg} (ID: {target_id}) заблокирован.")


async def unban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /unban — только в чате панели. /unban @username или /unban ID"""
    if update.effective_chat.id != ADMIN_CHAT_ID or not _admin_chat_enabled():
        return
    args = (context.args or [])
    if not args:
        await update.message.reply_text("Использование: /unban @username или /unban 123456789")
        return
    arg = args[0].strip()
    target_id = None
    if arg.isdigit():
        target_id = int(arg)
    elif arg.startswith("@"):
        try:
            chat = await context.bot.get_chat(arg)
            target_id = chat.id
        except Exception as e:
            await update.message.reply_text(f"Не удалось найти пользователя {arg}: {e}")
            return
    else:
        await update.message.reply_text("Укажите @username или числовой ID")
        return
    if target_id:
        remove_banned_user(target_id)
        await update.message.reply_text(f"✅ Пользователь {arg} (ID: {target_id}) разблокирован.")


async def panel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /panel — только в чате панели. Показать заявки на рассмотрении."""
    if update.effective_chat.id != ADMIN_CHAT_ID or not _admin_chat_enabled():
        return
    pending = list_pending_users()
    if not pending:
        await update.message.reply_text("📋 Нет заявок на рассмотрении.")
        return
    txt = f"📋 Заявки на рассмотрении ({len(pending)}):\n\n"
    kb = []
    for uid, pdata in pending[:10]:
        bot_id = pdata.get("bot_user_id") or get_or_create_bot_user_id(uid)
        txt += f"• {bot_id} (TG: {uid})\n"
        kb.append([
            InlineKeyboardButton(f"✅ {bot_id}", callback_data=f"req_approve_{uid}"),
            InlineKeyboardButton(f"❌ {bot_id}", callback_data=f"req_reject_{uid}"),
        ])
    await update.message.reply_text(txt[:4000], reply_markup=InlineKeyboardMarkup(kb) if kb else None)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _clear_step(context)
    uid = update.effective_user.id
    data = load_user_data(uid)

    if is_user_banned(uid):
        await update.message.reply_text("❌ Вы заблокированы.")
        return

    if _admin_chat_enabled() and not _user_has_access(uid):
        status = data.get("access_status", "new")
        if status == "pending":
            await update.message.reply_text(
                "⏳ Ваша заявка на рассмотрении.\nОжидайте ответа администратора.",
                reply_markup=_kb_request_access(),
            )
        elif status == "rejected":
            await update.message.reply_text(
                "❌ Ваша заявка была отклонена.\nВы можете подать заявку снова.",
                reply_markup=_kb_request_access(),
            )
        else:
            await update.message.reply_text(
                "🚀 BA Sender\n\nДля работы с сендером нужен доступ.\nНажмите кнопку ниже:",
                reply_markup=_kb_request_access(),
            )
        return

    bot_user_id = get_or_create_bot_user_id(uid)
    acc_count = len(data.get("accounts", []))
    proxy_count = len(data.get("proxies", []))
    job_count = len(data.get("jobs", []))
    await update.message.reply_text(
        f"🚀 BA Sender\n\nВаш ID: {bot_user_id}\nАккаунтов: {acc_count} | Прокси: {proxy_count} | Сессий: {job_count}\n\nВыберите:",
        reply_markup=_main_keyboard(),
    )


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    logging.exception("Ошибка при обработке: %s", err)
    if isinstance(err, BadRequest) and "parse" in str(err).lower():
        try:
            chat_id = update.effective_chat.id if update and hasattr(update, "effective_chat") else None
            if chat_id:
                await context.bot.send_message(chat_id, "⚠️ Ошибка отображения. Попробуйте снова.")
        except Exception:
            pass


def main():
    global _bot_app
    if not BOT_TOKEN:
        msg = "ОШИБКА: BOT_TOKEN не задан. Добавьте переменную BOT_TOKEN в Environment (Render)."
        print(msg)
        raise SystemExit(msg)
    app = Application.builder().token(BOT_TOKEN).build()
    _bot_app = app
    app.add_error_handler(_error_handler)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("panel", panel_cmd))
    app.add_handler(CommandHandler("ban", ban_cmd))
    app.add_handler(CommandHandler("unban", unban_cmd))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущен.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
