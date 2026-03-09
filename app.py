# -*- coding: utf-8 -*-
"""
BA Sender - Приложение для автоотправки сообщений на OLX.BA
Прокси, аккаунты, мультизадачность — всё в одном приложении
"""

import asyncio
import threading
import uuid
import customtkinter as ctk
from tkinter import messagebox, filedialog, INSERT
from typing import Optional
from datetime import datetime

from sender import OLXSender, SendTask
from storage import load_data, save_data

try:
    from config import DEFAULT_DELAY_MIN, DEFAULT_DELAY_MAX, DEFAULT_MAX_CONCURRENT, REDSCRIPT_API_KEY
except ImportError:
    DEFAULT_DELAY_MIN, DEFAULT_DELAY_MAX = 2, 4
    DEFAULT_MAX_CONCURRENT = 2
    REDSCRIPT_API_KEY = ""
from telegram_api import create_telegram_link, fetch_listing_data

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class AccountDialog(ctk.CTkToplevel):
    """Диалог добавления/редактирования аккаунта"""
    def __init__(self, parent, title: str = "Добавить аккаунт", email: str = "", password: str = ""):
        super().__init__(parent)
        self.title(title)
        self.geometry("420x260")
        self.resizable(False, False)
        self.email = ""
        self.password = ""
        
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=20, pady=20)
        
        ctk.CTkLabel(frame, text="Email или логин:").pack(anchor="w")
        self.email_entry = ctk.CTkEntry(frame, width=360, placeholder_text="user@example.com или username")
        self.email_entry.pack(fill="x", pady=(5, 15))
        if email:
            self.email_entry.insert(0, email)
        
        ctk.CTkLabel(frame, text="Пароль:").pack(anchor="w")
        self.pass_entry = ctk.CTkEntry(frame, width=360, show="•", placeholder_text="Пароль")
        self.pass_entry.pack(fill="x", pady=(5, 15))
        if password:
            self.pass_entry.insert(0, password)
        
        btn_frame = ctk.CTkFrame(frame, fg_color="transparent")
        btn_frame.pack(fill="x", pady=(20, 0))
        ctk.CTkButton(btn_frame, text="Отмена", width=120, height=36, command=self._cancel).pack(side="left", padx=(0, 12))
        ctk.CTkButton(btn_frame, text="Сохранить аккаунт", width=160, height=36, fg_color="#2ecc71", hover_color="#27ae60", command=self._save).pack(side="left")
        
        self.transient(parent)
        self.grab_set()
        
    def _save(self):
        self.email = self.email_entry.get().strip()
        self.password = self.pass_entry.get()
        if not self.email:
            messagebox.showwarning("Внимание", "Введите email или логин!")
            return
        if not self.password:
            messagebox.showwarning("Внимание", "Введите пароль!")
            return
        self.grab_release()
        self.destroy()
        
    def _cancel(self):
        self.email = ""
        self.password = ""
        self.grab_release()
        self.destroy()


class BASenderApp(ctk.CTk):
    """Главное окно приложения"""
    
    def __init__(self):
        super().__init__()
        
        self.title("BA Sender - OLX.BA Автоотправка")
        self.geometry("920x750")
        self.minsize(800, 600)
        
        try:
            self.iconbitmap("icon.ico")
        except Exception:
            pass
        
        self.sender: Optional[OLXSender] = None
        self.tasks: dict[str, SendTask] = {}
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.thread: Optional[threading.Thread] = None
        self.accounts: list[dict] = []
        self.selected_account_index = 0
        self.jobs: list[dict] = []  # [{account_email, proxy, links: []}]
        self.jobs_frame: Optional[ctk.CTkScrollableFrame] = None
        self.job_widgets: list[dict] = []  # [{combo, proxy_entry, links_text, remove_btn}]
        
        self._create_ui()
        self._load_saved_data()
        
    def _create_ui(self):
        """Создание интерфейса — компактный, всё влезает, копирование работает"""
        main = ctk.CTkScrollableFrame(self, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=20, pady=15)
        
        # === Заголовок ===
        header = ctk.CTkFrame(main, fg_color="transparent")
        header.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(header, text="🚀 BA Sender", font=ctk.CTkFont(size=22, weight="bold")).pack(side="left")
        ctk.CTkLabel(header, text="Без окна браузера • всё в логе", font=ctk.CTkFont(size=12), text_color="gray").pack(side="left", padx=(10, 0), pady=(4, 0))
        
        # === Панель 1: Прокси + Аккаунты + Настройки в компактном виде ===
        top_panel = ctk.CTkFrame(main, corner_radius=10, fg_color=("gray85", "gray20"))
        top_panel.pack(fill="x", pady=(0, 8))
        tp = ctk.CTkFrame(top_panel, fg_color="transparent")
        tp.pack(fill="x", padx=15, pady=12)
        
        row1 = ctk.CTkFrame(tp, fg_color="transparent")
        row1.pack(fill="x")
        ctk.CTkLabel(row1, text="🌐 Прокси:").pack(side="left", padx=(0, 5))
        self.proxy_entry = ctk.CTkEntry(row1, width=180, height=32, placeholder_text="ip:port:user:pass или http://ip:port")
        self.proxy_entry.pack(side="left", padx=(0, 5))
        self.proxy_entry.bind("<Control-v>", lambda e: self._paste_into_entry(e))
        self.proxy_entry.bind("<Control-V>", lambda e: self._paste_into_entry(e))
        ctk.CTkButton(row1, text="📋 Вставить", width=75, height=32, command=lambda: self._paste_into_widget(self.proxy_entry)).pack(side="left", padx=(0, 5))
        ctk.CTkButton(row1, text="📁 Импорт", width=80, height=32, command=self._import_proxies).pack(side="left", padx=(0, 12))
        ctk.CTkLabel(row1, text="👤 Аккаунт:").pack(side="left", padx=(0, 5))
        self.account_combo = ctk.CTkComboBox(row1, width=180, height=32, values=["Нет аккаунтов"], state="readonly")
        self.account_combo.pack(side="left", padx=(0, 5))
        self.check_account_btn = ctk.CTkButton(row1, text="🔍 Проверить", width=95, height=32, command=self._check_account, fg_color="#3498db")
        self.check_account_btn.pack(side="left", padx=(0, 3))
        ctk.CTkButton(row1, text="➕", width=32, height=32, command=self._add_account).pack(side="left", padx=(0, 2))
        ctk.CTkButton(row1, text="✏️", width=32, height=32, command=self._edit_account).pack(side="left", padx=(0, 2))
        ctk.CTkButton(row1, text="🗑️", width=32, height=32, command=self._remove_account, fg_color="gray").pack(side="left", padx=(0, 12))
        ctk.CTkLabel(row1, text="Задержка:").pack(side="left", padx=(0, 3))
        self.delay_min = ctk.CTkEntry(row1, width=40, height=32)
        self.delay_min.insert(0, str(DEFAULT_DELAY_MIN))
        self.delay_min.pack(side="left", padx=(0, 2))
        ctk.CTkLabel(row1, text="-").pack(side="left")
        self.delay_max = ctk.CTkEntry(row1, width=40, height=32)
        self.delay_max.insert(0, str(DEFAULT_DELAY_MAX))
        self.delay_max.pack(side="left", padx=(0, 8))
        ctk.CTkLabel(row1, text="Паралл:").pack(side="left", padx=(0, 3))
        self.max_concurrent = ctk.CTkEntry(row1, width=40, height=32)
        self.max_concurrent.insert(0, str(DEFAULT_MAX_CONCURRENT))
        self.max_concurrent.pack(side="left", padx=(0, 12))
        self.show_browser_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(row1, text="👁 Показывать браузер", variable=self.show_browser_var, width=180).pack(side="left")
        
        ctk.CTkLabel(tp, text="Список прокси (каждая с новой строки): ip:port:user:pass, http/socks5://ip:port", font=ctk.CTkFont(size=11)).pack(anchor="w", pady=(6, 2))
        self.proxy_list_text = ctk.CTkTextbox(tp, height=45, font=ctk.CTkFont(size=10))
        self.proxy_list_text.pack(fill="x", pady=(0, 0))

        # === Сессии (мультиаккаунт: аккаунт + прокси + свои ссылки) ===
        jobs_header = ctk.CTkFrame(main, fg_color="transparent")
        jobs_header.pack(fill="x", pady=(8, 4))
        ctk.CTkLabel(jobs_header, text="📋 Сессии (каждая: аккаунт + прокси + свои ссылки)", font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        ctk.CTkButton(jobs_header, text="➕ Добавить сессию", width=140, height=30, command=self._add_job, fg_color="#3498db").pack(side="right")
        self.jobs_frame = ctk.CTkScrollableFrame(main, fg_color=("gray90", "gray18"), height=140)
        self.jobs_frame.pack(fill="x", pady=(0, 8))
        
        # === Сообщение + Ссылки ===
        mid_panel = ctk.CTkFrame(main, corner_radius=10, fg_color=("gray85", "gray20"))
        mid_panel.pack(fill="x", pady=(0, 8))
        mp = ctk.CTkFrame(mid_panel, fg_color="transparent")
        mp.pack(fill="x", padx=15, pady=12)
        
        ctk.CTkLabel(mp, text="✉️ Текст сообщения (при RedScript — первое сообщение; ссылка отправляется вторым):", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w")
        self.message_text = ctk.CTkTextbox(mp, height=55, font=ctk.CTkFont(size=12))
        self.message_text.pack(fill="x", pady=(4, 8))
        self.message_text.insert("1.0", "Zdravo! Zanima me ovaj oglas. Da li je još uvijek dostupan?")
        
        tg_row = ctk.CTkFrame(mp, fg_color="transparent")
        tg_row.pack(fill="x", pady=(6, 0))
        self.telegram_api_enabled_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(tg_row, text="📱 RedScript: добавлять ссылку в сообщение", variable=self.telegram_api_enabled_var, width=280).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(tg_row, text="API URL:").pack(side="left", padx=(0, 5))
        self.telegram_api_url_entry = ctk.CTkEntry(tg_row, width=140, height=28, placeholder_text="api.redscript.info")
        self.telegram_api_url_entry.pack(side="left", padx=(0, 5))
        ctk.CTkLabel(tg_row, text="Ключ:").pack(side="left", padx=(0, 5))
        self.telegram_api_key_entry = ctk.CTkEntry(tg_row, width=220, height=28, show="•", placeholder_text="access_token")
        self.telegram_api_key_entry.pack(side="left", padx=(0, 5))
        ctk.CTkLabel(tg_row, text="Прокси API:").pack(side="left", padx=(10, 5))
        self.telegram_api_proxy_entry = ctk.CTkEntry(tg_row, width=180, height=28, placeholder_text="ip:port:user:pass (опционально)")
        self.telegram_api_proxy_entry.pack(side="left", padx=(0, 5))
        
        ctk.CTkLabel(mp, text="Ссылки указываются в каждой сессии выше.", font=ctk.CTkFont(size=11), text_color="gray").pack(anchor="w")
        
        # === Управление ===
        control_frame = ctk.CTkFrame(main, fg_color="transparent")
        control_frame.pack(fill="x", pady=(0, 8))
        self.start_btn = ctk.CTkButton(
            control_frame, text="▶️ Запустить отправку", height=40,
            font=ctk.CTkFont(size=15, weight="bold"), command=self._start_sending,
            fg_color="#2ecc71", hover_color="#27ae60"
        )
        self.start_btn.pack(side="left", padx=(0, 10))
        self.stop_btn = ctk.CTkButton(
            control_frame, text="⏹️ Остановить", height=40, font=ctk.CTkFont(size=14),
            command=self._stop_sending, state="disabled", fg_color="#e74c3c", hover_color="#c0392b"
        )
        self.stop_btn.pack(side="left")
        self.status_label = ctk.CTkLabel(control_frame, text="Готов", font=ctk.CTkFont(size=12), text_color="gray")
        self.status_label.pack(side="right", padx=(15, 0))
        
        # === Лог (крупный, с копированием) ===
        log_frame = ctk.CTkFrame(main, corner_radius=10, fg_color=("gray90", "gray15"))
        log_frame.pack(fill="both", expand=True, pady=(0, 0))
        log_inner = ctk.CTkFrame(log_frame, fg_color="transparent")
        log_inner.pack(fill="both", expand=True, padx=12, pady=10)
        lh2 = ctk.CTkFrame(log_inner, fg_color="transparent")
        lh2.pack(fill="x")
        ctk.CTkLabel(lh2, text="📋 Лог (все действия, Ctrl+C — копировать)", font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
        ctk.CTkButton(lh2, text="📋 Копировать лог", width=120, height=28, command=self._copy_log).pack(side="right", padx=(0, 5))
        ctk.CTkButton(lh2, text="🗑️ Очистить", width=90, height=28, command=self._clear_log, fg_color="gray").pack(side="right")
        self.log_text = ctk.CTkTextbox(log_inner, font=ctk.CTkFont(size=11), state="normal")
        self.log_text.pack(fill="both", expand=True, pady=(6, 0))
        def _block_edit(e):
            if (e.state & 0x4) and e.keysym.lower() in ("c", "a"):
                return
            return "break"
        self.log_text.bind("<Key>", _block_edit)
        self.log_text.insert("1.0", "Здесь отображаются все действия. Браузер работает в фоне.\n")
        
    def _load_saved_data(self):
        """Запуск при загрузке"""
        data = load_data()
        self.delay_min.delete(0, "end")
        self.delay_min.insert(0, str(data.get("delay_min", DEFAULT_DELAY_MIN)))
        self.delay_max.delete(0, "end")
        self.delay_max.insert(0, str(data.get("delay_max", DEFAULT_DELAY_MAX)))
        self.max_concurrent.delete(0, "end")
        self.max_concurrent.insert(0, str(data.get("max_concurrent", DEFAULT_MAX_CONCURRENT)))
        self.show_browser_var.set(data.get("show_browser", True))
        self.proxy_entry.delete(0, "end")
        self.proxy_entry.insert(0, data.get("proxy", ""))
        self.proxy_list_text.delete("1.0", "end")
        self.proxy_list_text.insert("1.0", "\n".join(data.get("proxy_list", [])))
        self.message_text.delete("1.0", "end")
        self.message_text.insert("1.0", data.get("message", "Zdravo! Zanima me ovaj oglas. Da li je još uvijek dostupan?"))
        self.telegram_api_enabled_var.set(data.get("telegram_api_enabled", False))
        self.telegram_api_url_entry.delete(0, "end")
        self.telegram_api_url_entry.insert(0, data.get("telegram_api_url", ""))
        self.telegram_api_key_entry.delete(0, "end")
        self.telegram_api_key_entry.insert(0, data.get("telegram_api_key", "") or REDSCRIPT_API_KEY)
        self.telegram_api_proxy_entry.delete(0, "end")
        self.telegram_api_proxy_entry.insert(0, data.get("telegram_api_proxy", ""))
        raw = data.get("accounts", [])
        self.accounts = []
        for a in raw:
            acc = dict(a)
            if "status" not in acc:
                acc["status"] = "unknown"
            self.accounts.append(acc)
        self._update_account_combo()
        self.jobs = data.get("jobs", [])
        if not self.jobs:
            self.jobs = [{"account_email": "", "proxy": "", "links": []}]
        self._rebuild_jobs_ui()
        
    def _save_data(self):
        """Сохранение данных"""
        proxy_list = [s.strip() for s in self.proxy_list_text.get("1.0", "end").strip().split("\n") if s.strip()]
        jobs_data = []
        for w in self.job_widgets:
            acc_val = w["combo"].get()
            acc_email = ""
            for a in self.accounts:
                if self._account_display(a) == acc_val:
                    acc_email = a.get("email", "")
                    break
            proxy = w["proxy_entry"].get().strip()
            links = self._parse_links(w["links_text"].get("1.0", "end"))
            jobs_data.append({"account_email": acc_email, "proxy": proxy, "links": links})
        save_data({
            "accounts": self.accounts,
            "proxy": self.proxy_entry.get().strip(),
            "proxy_list": proxy_list,
            "delay_min": self._get_settings()[0],
            "delay_max": self._get_settings()[1],
            "max_concurrent": self._get_settings()[2],
            "show_browser": self.show_browser_var.get(),
            "message": self.message_text.get("1.0", "end").strip(),
            "telegram_api_url": self.telegram_api_url_entry.get().strip(),
            "telegram_api_key": self.telegram_api_key_entry.get().strip(),
            "telegram_api_proxy": self.telegram_api_proxy_entry.get().strip(),
            "telegram_api_enabled": self.telegram_api_enabled_var.get(),
            "jobs": jobs_data,
        })
        
    def _account_display(self, acc: dict) -> str:
        """Строка для отображения аккаунта со статусом"""
        email = acc.get("email", "")
        status = acc.get("status", "unknown")
        icons = {"valid": " ✓", "blocked": " ⛔", "invalid": " ✗", "error": " ?", "unknown": " ?"}
        return f"{email}{icons.get(status, ' ?')}"
    
    def _update_account_combo(self):
        """Обновить список аккаунтов в комбобоксе"""
        if not self.accounts:
            self.account_combo.configure(values=["Нет аккаунтов"])
            self.account_combo.set("Нет аккаунтов")
        else:
            vals = [self._account_display(a) for a in self.accounts]
            self.account_combo.configure(values=vals)
            idx = min(self.selected_account_index, len(self.accounts) - 1)
            self.account_combo.set(vals[idx])
            self.selected_account_index = idx
        self._update_job_combos()

    def _update_job_combos(self):
        vals = ["— Выберите —"] + [self._account_display(a) for a in self.accounts]
        for w in self.job_widgets:
            cur = w["combo"].get()
            w["combo"].configure(values=vals)
            if cur and cur in vals:
                w["combo"].set(cur)
            
    def _add_account(self):
        d = AccountDialog(self, "Добавить аккаунт")
        self.wait_window(d)
        if d.email:
            self.accounts.append({"email": d.email, "password": d.password, "status": "unknown"})
            self._update_account_combo()
            self._save_data()
            self._log("Аккаунт добавлен")
            
    def _edit_account(self):
        if not self.accounts:
            messagebox.showinfo("Инфо", "Нет аккаунтов для редактирования")
            return
        acc = self._get_selected_account()
        if not acc:
            acc = self.accounts[0]
        idx = next((i for i, a in enumerate(self.accounts) if a["email"] == acc["email"]), 0)
        d = AccountDialog(self, "Редактировать аккаунт", acc.get("email", ""), acc.get("password", ""))
        self.wait_window(d)
        if d.email:
            self.accounts[idx] = {"email": d.email, "password": d.password, "status": "unknown"}
            self._update_account_combo()
            self._save_data()
            self._log("Аккаунт обновлён")
            
    def _check_account(self):
        """Проверка выбранного аккаунта на валидность и блокировку"""
        acc = self._get_selected_account()
        if not acc:
            messagebox.showinfo("Инфо", "Выберите аккаунт для проверки")
            return
        self.check_account_btn.configure(state="disabled", text="Проверка...")
        self._log(f"Проверка аккаунта {acc['email']}...")
        proxy = self.proxy_entry.get().strip() or None
        proxy_list = [s.strip() for s in self.proxy_list_text.get("1.0", "end").strip().split("\n") if s.strip()]
        threading.Thread(target=self._run_validate, args=(acc, proxy, proxy_list if proxy_list else None), daemon=True).start()
    
    def _run_validate(self, acc: dict, proxy: Optional[str], proxy_list: Optional[list]):
        """Запуск проверки в отдельном потоке"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        sender = None
        try:
            def safe_log(m):
                self.after(0, lambda msg=m: self._log(msg))
            sender = OLXSender(proxy=proxy, proxy_list=proxy_list, on_log=safe_log)
            show_browser = self.show_browser_var.get()
            loop.run_until_complete(sender.start(headless=not show_browser))
            result = loop.run_until_complete(sender.validate_account(acc.get("email", ""), acc.get("password", "")))
            loop.run_until_complete(sender.stop())
            status = result["status"]
            msg = result["message"]
            acc_email = acc["email"]
            idx = next((i for i, a in enumerate(self.accounts) if a["email"] == acc_email), -1)
            if idx >= 0:
                self.accounts[idx]["status"] = status
            self.after(0, lambda s=status, m=msg, ae=acc_email: self._on_validate_done(s, m, ae))
        except Exception as e:
            err_msg = str(e)
            acc_email = acc["email"]
            self.after(0, lambda m=err_msg, ae=acc_email: self._on_validate_done("error", m, ae))
        finally:
            if sender and sender._running:
                try:
                    loop.run_until_complete(sender.stop())
                except Exception:
                    pass
            self.after(0, lambda: self.check_account_btn.configure(state="normal", text="🔍 Проверить"))
    
    def _on_validate_done(self, status: str, message: str, email: str):
        """Обработка результата проверки"""
        self._update_account_combo()
        self._save_data()
        icons = {"valid": "✓", "blocked": "⛔", "invalid": "✗", "error": "?"}
        self._log(f"{icons.get(status, '?')} {email}: {message}")
        if status == "valid":
            messagebox.showinfo("Проверка", f"Аккаунт активен.\n{message}")
        elif status == "blocked":
            messagebox.showwarning("Проверка", f"Аккаунт заблокирован.\n{message}")
        elif status == "invalid":
            messagebox.showwarning("Проверка", f"Неверные данные.\n{message}")
        else:
            messagebox.showerror("Проверка", f"Ошибка: {message}")
        self.check_account_btn.configure(state="normal", text="🔍 Проверить")
    
    def _remove_account(self):
        if not self.accounts:
            return
        acc = self._get_selected_account()
        if not acc:
            acc = self.accounts[0]
        idx = next((i for i, a in enumerate(self.accounts) if a["email"] == acc["email"]), 0)
        if messagebox.askyesno("Подтверждение", "Удалить выбранный аккаунт?"):
            self.accounts.pop(idx)
            self.selected_account_index = max(0, idx - 1)
            self._update_account_combo()
            self._save_data()
            self._log("Аккаунт удалён")
            
    def _import_proxies(self):
        path = filedialog.askopenfilename(filetypes=[("Текстовые файлы", "*.txt"), ("Все файлы", "*.*")])
        if path:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    lines = [l.strip() for l in f if l.strip()]
                self.proxy_list_text.delete("1.0", "end")
                self.proxy_list_text.insert("1.0", "\n".join(lines))
                self._log(f"Импортировано {len(lines)} прокси")
            except Exception as e:
                messagebox.showerror("Ошибка", str(e))
        
    def _paste_into_widget(self, widget):
        """Вставка из буфера в указанный виджет"""
        try:
            text = self.clipboard_get()
            if widget and hasattr(widget, "insert"):
                widget.focus_set()
                widget.insert(INSERT, text)
        except Exception:
            pass
    
    def _paste_into_entry(self, event=None):
        """Вставка по Ctrl+V в поле с фокусом"""
        try:
            text = self.clipboard_get()
            w = event.widget if event else self.focus_get()
            if w and hasattr(w, "insert"):
                w.insert(INSERT, text)
        except Exception:
            pass
        return "break"
    
    def _log(self, msg: str):
        """Добавить запись в лог"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{timestamp}] {msg}\n")
        self.log_text.see("end")
    
    def _copy_log(self):
        """Копировать весь лог в буфер обмена"""
        try:
            text = self.log_text.get("1.0", "end")
            self.clipboard_clear()
            self.clipboard_append(text)
            self._log("Лог скопирован в буфер обмена")
        except Exception:
            pass
    
    def _clear_log(self):
        """Очистить лог"""
        self.log_text.delete("1.0", "end")
        self.log_text.insert("1.0", "Лог очищен.\n")
        
    def _parse_links(self, text: str) -> list[str]:
        links = []
        for line in text.strip().split("\n"):
            line = line.strip()
            if line and ("olx.ba" in line or line.startswith("http")):
                if not line.startswith("http"):
                    line = "https://www.olx.ba" + line
                links.append(line)
        return links

    def _add_job(self):
        self.jobs.append({"account_email": "", "proxy": "", "links": []})
        self._rebuild_jobs_ui()
        self._save_data()
        self._log("Сессия добавлена")

    def _rebuild_jobs_ui(self):
        for w in self.job_widgets:
            f = w.get("frame")
            if f and hasattr(f, "destroy"):
                f.destroy()
        self.job_widgets.clear()
        for i, j in enumerate(self.jobs):
            self._build_job_card(i, j.get("account_email", ""), j.get("proxy", ""), j.get("links", []))

    def _remove_job(self, idx: int):
        if 0 <= idx < len(self.jobs):
            self.jobs.pop(idx)
            self._rebuild_jobs_ui()
            self._save_data()
            self._log("Сессия удалена")

    def _build_job_card(self, idx: int, account_email: str, proxy: str, links: list[str]):
        frame = ctk.CTkFrame(self.jobs_frame, fg_color="transparent")
        frame.pack(fill="x", pady=4)
        row = ctk.CTkFrame(frame, fg_color="transparent")
        row.pack(fill="x")
        ctk.CTkLabel(row, text=f"Сессия {idx + 1}:", width=70).pack(side="left", padx=(0, 5))
        combo = ctk.CTkComboBox(row, width=180, height=28, values=["— Выберите —"] + [self._account_display(a) for a in self.accounts], state="readonly")
        combo.pack(side="left", padx=(0, 8))
        if account_email:
            for i, a in enumerate(self.accounts):
                if a.get("email") == account_email:
                    combo.set(self._account_display(a))
                    break
        else:
            combo.set("— Выберите —")
        ctk.CTkLabel(row, text="Прокси:").pack(side="left", padx=(8, 5))
        proxy_entry = ctk.CTkEntry(row, width=200, height=28, placeholder_text="ip:port:user:pass")
        proxy_entry.pack(side="left", padx=(0, 8))
        if proxy:
            proxy_entry.insert(0, proxy)
        links_text = ctk.CTkTextbox(row, width=180, height=36, font=ctk.CTkFont(size=10))
        links_text.pack(side="left", padx=(0, 5))
        if links:
            links_text.insert("1.0", "\n".join(links))
        else:
            links_text.insert("1.0", "Ссылки (каждая с новой строки)")
        remove_btn = ctk.CTkButton(row, text="🗑️", width=32, height=28, command=lambda i=idx: self._remove_job(i), fg_color="gray")
        remove_btn.pack(side="left")
        self.job_widgets.append({"frame": frame, "combo": combo, "proxy_entry": proxy_entry, "links_text": links_text, "remove_btn": remove_btn})

    def _get_jobs_from_ui(self) -> list[tuple[dict, str, list[str]]]:
        """Вернуть [(account, proxy, links), ...] из UI"""
        result = []
        for i, w in enumerate(self.job_widgets):
            acc_val = w["combo"].get()
            if acc_val == "— Выберите —" or not acc_val:
                continue
            acc = None
            for a in self.accounts:
                if self._account_display(a) == acc_val:
                    acc = a
                    break
            if not acc:
                continue
            proxy = w["proxy_entry"].get().strip()
            text = w["links_text"].get("1.0", "end")
            links = self._parse_links(text)
            if links:
                result.append((acc, proxy or None, links))
        return result
    
    def _get_settings(self) -> tuple[int, int, int]:
        try:
            dmin = max(1, int(self.delay_min.get()))
            dmax = max(dmin, int(self.delay_max.get()))
            concurrent = max(1, min(10, int(self.max_concurrent.get())))
            return dmin, dmax, concurrent
        except ValueError:
            return DEFAULT_DELAY_MIN, DEFAULT_DELAY_MAX, DEFAULT_MAX_CONCURRENT
    
    def _on_task_status(self, task_id: str, status: str, error: Optional[str]):
        self.after(0, lambda tid=task_id, st=status, err=error: self._update_task_ui(tid, st, err))
        
    def _update_task_ui(self, task_id: str, status: str, error: Optional[str]):
        if task_id in self.tasks:
            self.tasks[task_id].status = status
            self.tasks[task_id].error = error
        url_short = ""
        if task_id in self.tasks:
            u = self.tasks[task_id].listing_url
            url_short = u[:50] + "..." if len(u) > 50 else u
        if status == "success":
            self._log(f"✓ Сообщение отправлено: {url_short}")
        elif status == "error":
            self._log(f"✗ Ошибка отправки {url_short}: {error}")
        elif status == "running":
            self._log(f"⏳ Отправка сообщения: {url_short}")
            
    def _run_async_sender(self, proxy: Optional[str], proxy_list: Optional[list], dmin: int, dmax: int, concurrent: int, jobs: list, tg_api_key: str = "", tg_enabled: bool = False):
        """Запуск асинхронного отправителя (jobs = [(account, proxy, links), ...])"""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        try:
            def safe_log(m):
                self.after(0, lambda msg=m: self._log(msg))
            self.sender = OLXSender(
                delay_min=dmin, delay_max=dmax, max_concurrent=concurrent,
                proxy=proxy, proxy_list=proxy_list, on_log=safe_log
            )
            
            show_browser = self.show_browser_var.get()
            message = self.message_text.get("1.0", "end").strip()
            tg_api_proxy = self.telegram_api_proxy_entry.get().strip() or None
            tg_api_url = self.telegram_api_url_entry.get().strip() or None
            
            self.loop.run_until_complete(self.sender.start(headless=not show_browser, create_context=False))
            self.after(0, self._on_browser_started)
            
            self.tasks = {}
            built_jobs = []
            for acc, proxy_str, links in jobs:
                import time as t
                tasks = []
                for link in links:
                    task_id = str(uuid.uuid4())
                    tasks.append(SendTask(id=task_id, listing_url=link, message=message, created_at=t.time()))
                tg_proxy = tg_api_proxy or proxy_str or (proxy_list[0] if proxy_list else None)
                if tg_enabled and tg_api_key:
                    self.after(0, lambda: self._log("═══ Создание ссылок Telegram ═══"))
                    def _tg_log(m):
                        self.after(0, lambda msg=m: self._log(msg))
                    for task in tasks:
                        title, price, image_url = fetch_listing_data(task.listing_url, tg_proxy)
                        link = create_telegram_link(tg_api_key, task.listing_url, title=title, price=price, image=image_url, on_debug=_tg_log, proxy=tg_proxy, api_base=tg_api_url)
                        if link:
                            task.message_link = link
                            self.after(0, lambda u=task.listing_url[:50]: self._log(f"✓ Ссылка: {u}..."))
                        else:
                            self.after(0, lambda u=task.listing_url[:50]: self._log(f"⚠ API не вернул ссылку: {u}..."))
                built_jobs.append((acc, proxy_str, tasks))
                for t in tasks:
                    self.tasks[t.id] = t
            
            self.after(0, lambda: self._log("═══ Отправка сообщений (мультиаккаунт) ═══"))
            self.loop.run_until_complete(self.sender.run_jobs(built_jobs, on_status=self._on_task_status))
            
            self.after(0, lambda: self._log("═══ Проверка аккаунтов после отправки ═══"))
            for acc, proxy_str, _ in jobs:
                try:
                    result = self.loop.run_until_complete(self.sender.validate_account(acc.get("email", ""), acc.get("password", ""), proxy_str=proxy_str))
                    status = result["status"]
                    msg = result["message"]
                    self.after(0, lambda s=status, m=msg, e=acc.get("email", ""): self._log(f"Проверка {e}: {m}"))
                    idx = next((i for i, a in enumerate(self.accounts) if a.get("email") == acc.get("email")), -1)
                    if idx >= 0:
                        self.accounts[idx]["status"] = status
                except Exception as ex:
                    self.after(0, lambda e=str(ex): self._log(f"Проверка: {e}"))
            self.after(0, lambda: (self._update_account_combo(), self._save_data()))
            
            self.loop.run_until_complete(self.sender.stop())
        except Exception as e:
            err_msg = str(e)
            self.after(0, lambda m=err_msg: self._on_error(m))
        finally:
            self.after(0, self._on_sending_finished)
            
    def _get_selected_account(self) -> Optional[dict]:
        if not self.accounts:
            return None
        val = self.account_combo.get()
        for acc in self.accounts:
            if self._account_display(acc) == val:
                return acc
        return self.accounts[0] if self.accounts else None
            
    def _on_browser_started(self):
        self._log("Браузер запущен (режим без окна)")
        self.status_label.configure(text="Работает в фоне...")
        
    def _on_error(self, msg: str):
        self._log(f"Ошибка: {msg}")
        messagebox.showerror("Ошибка", msg)
        
    def _on_sending_finished(self):
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.status_label.configure(text="Завершено")
        self._log("Отправка завершена.")
        self._save_data()
        
    def _start_sending(self):
        jobs = self._get_jobs_from_ui()
        message = self.message_text.get("1.0", "end").strip()
        
        if not jobs:
            messagebox.showwarning("Внимание", "Добавьте хотя бы одну сессию (аккаунт + прокси + ссылки)!")
            return
        if not message:
            messagebox.showwarning("Внимание", "Введите текст сообщения!")
            return
        
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.status_label.configure(text="Запуск...")
        total_links = sum(len(links) for _, _, links in jobs)
        self._log(f"Запуск отправки {total_links} сообщений из {len(jobs)} сессий...")
        
        proxy = self.proxy_entry.get().strip() or None
        proxy_list = [s.strip() for s in self.proxy_list_text.get("1.0", "end").strip().split("\n") if s.strip()]
        dmin, dmax, concurrent = self._get_settings()
        tg_key = self.telegram_api_key_entry.get().strip()
        tg_enabled = self.telegram_api_enabled_var.get()
        self.thread = threading.Thread(target=self._run_async_sender, args=(proxy, proxy_list if proxy_list else None, dmin, dmax, concurrent, jobs, tg_key, tg_enabled), daemon=True)
        self.thread.start()
        
    def _stop_sending(self):
        if self.sender:
            self.sender._running = False
            self._log("Остановка...")
        self.stop_btn.configure(state="disabled")
        
    def on_closing(self):
        self._save_data()
        if self.sender and self.sender._running:
            if messagebox.askokcancel("Выход", "Отправка в процессе. Завершить?"):
                self.sender._running = False
                self.destroy()
        else:
            self.destroy()


def main():
    app = BASenderApp()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()


if __name__ == "__main__":
    main()
