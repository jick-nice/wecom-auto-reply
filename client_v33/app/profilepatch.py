# -*- coding: utf-8 -*-
"""Keep per-account operator settings separate on shared Windows installs."""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox

_applied = False
_FIELDS = ("dispatchers", "my_name")


def split_dispatchers(text: str) -> list[str]:
    """Split multiple names without breaking punctuation inside remarks."""
    result, buf, stack = [], [], []
    pairs = {"（": "）", "(": ")", "【": "】", "[": "]"}
    closers = set(pairs.values())
    for ch in str(text or ""):
        if ch in pairs:
            stack.append(pairs[ch])
        elif stack and ch == stack[-1]:
            stack.pop()
        if ch in ",，、;；/" and not stack:
            value = "".join(buf).strip()
            if value:
                result.append(value)
            buf = []
        else:
            buf.append(ch)
    value = "".join(buf).strip()
    if value:
        result.append(value)
    return result


def _key(username: str) -> str:
    return str(username or "").strip().casefold()


def _put(cfg: dict, username: str, values: dict) -> None:
    key = _key(username)
    if not key:
        return
    profiles = dict(cfg.get("account_profiles") or {})
    profiles[key] = {
        "dispatchers": list(values.get("dispatchers") or []),
        "my_name": str(values.get("my_name") or "").strip(),
    }
    cfg["account_profiles"] = profiles
    cfg["profile_owner"] = key


def activate(cfg: dict, username: str) -> bool:
    """Load one account's values; return True when first-run setup is needed."""
    key = _key(username)
    if not key:
        return True

    profiles = cfg.get("account_profiles") or {}
    profile = profiles.get(key)
    if profile is not None:
        cfg.update(profile)
        cfg["profile_owner"] = key
        return not (cfg.get("dispatchers") and cfg.get("my_name"))

    previous = _key(cfg.get("profile_owner") or cfg.get("username"))
    if previous == key:
        _put(cfg, key, cfg)
        return not (cfg.get("dispatchers") and cfg.get("my_name"))

    cfg["dispatchers"] = []
    cfg["my_name"] = ""
    cfg["profile_owner"] = key
    return True


def save_current(cfg: dict) -> bool:
    username = cfg.get("username")
    if not _key(username):
        return False
    _put(cfg, username, cfg)
    return bool(cfg.get("dispatchers") and cfg.get("my_name"))


def apply() -> bool:
    global _applied
    if _applied:
        return True
    try:
        from . import cfg as cfgmod, gui
    except Exception:
        return False

    load_original = cfgmod.load

    def load():
        cfg = load_original()
        username = cfg.get("username")
        if username and cfg.get("remember_token"):
            activate(cfg, username)
        return cfg

    cfgmod.load = load

    login_frame = getattr(gui, "LoginFrame", None)
    if login_frame is not None and hasattr(login_frame, "ok"):
        ok_original = login_frame.ok

        def login_ok(self, body, username, password):
            ent = body.get("ent") or {}
            if ent.get("allowed"):
                self.app._profile_needs_setup = activate(self.app.cfg, username)
            ok_original(self, body, username, password)
            if ent.get("allowed") and self.app._profile_needs_setup:
                self.app.after(120, lambda: __import__("tkinter.messagebox", fromlist=["showinfo"]).showinfo(
                    "首次设置", "请填写自己的派单人和群内昵称，再点击「保存」。"))

        login_frame.ok = login_ok

    save_original = gui.App.save_settings

    def save_settings(self):
        save_original(self)
        raw = self.e_disp.get()
        self.cfg["dispatchers"] = split_dispatchers(raw)
        self._profile_needs_setup = not save_current(self.cfg)
        cfgmod.save(self.cfg)
        if not self._profile_needs_setup:
            self._log("✅ 已保存当前账号专属信息")

    gui.App.save_settings = save_settings

    start_original = gui.App.start_grab

    def start_grab(self):
        if getattr(self, "_starting_grab", False):
            cancel_event = getattr(self, "_startup_cancel", None)
            if cancel_event is not None:
                cancel_event.set()
            self._startup_generation = getattr(self, "_startup_generation", 0) + 1
            self._starting_grab = False
            self._startup_cancel = None
            button = getattr(self, "big", None)
            if button is not None:
                button.config(state=tk.NORMAL, text="▶  开始抢单")
            self._log("⏹️ 已取消启动；后台初始化完成后不会启动监听。")
            return
        if getattr(self, "running", False):
            return
        if not (self.cfg.get("dispatchers") and str(self.cfg.get("my_name") or "").strip()):
            messagebox.showwarning(
                "请先完成个人设置", "请填写自己的派单人和群内昵称，并点击「保存」后再开始抢单。")
            return
        self._starting_grab = True
        self._startup_generation = getattr(self, "_startup_generation", 0) + 1
        generation = self._startup_generation
        cancel_event = threading.Event()
        self._startup_cancel = cancel_event
        button = getattr(self, "big", None)
        if button is not None:
            button.config(state=tk.NORMAL, text="■  取消启动")
        self._log("正在后台检查资格并加载识别引擎，点击按钮可取消…")

        def reset_startup(message: str, title: str = "初始化失败"):
            if generation != getattr(self, "_startup_generation", 0):
                return
            self._starting_grab = False
            self._startup_cancel = None
            if button is not None:
                button.config(state=tk.NORMAL, text="▶  开始抢单")
            self._log(f"⛔ {message}")
            if title:
                messagebox.showerror(title, message)

        def watchdog():
            if self._starting_grab and generation == getattr(self, "_startup_generation", 0):
                cancel_event.set()
                self._startup_generation += 1
                self._starting_grab = False
                self._startup_cancel = None
                if button is not None:
                    button.config(state=tk.NORMAL, text="▶  开始抢单")
                self._log("⛔ 初始化超过 30 秒，已取消本次启动；可稍后重试。")

        self.after(30000, watchdog)

        def work():
            try:
                allowed_result = (True, "", None)
                if self.api.base_url:
                    self._log("正在检查账号资格…")
                    allowed_result = self.api.allowed()
                    if cancel_event.is_set():
                        return
                    if not allowed_result[0]:
                        raise RuntimeError(allowed_result[1] or "使用资格已失效")
                self._log("账号资格通过，正在加载 OCR 引擎…")
                prepared_ocr = self.ocr
                if prepared_ocr is None:
                    from .ocr import Ocr
                    if cancel_event.is_set():
                        return
                    prepared_ocr = Ocr()
                    self._log("OCR 引擎已载入，正在预热模型…")
                    prepared_ocr.warmup()
                if cancel_event.is_set():
                    return
                self._log("识别引擎准备完成，正在启动监听…")

                def finish():
                    if cancel_event.is_set() or generation != getattr(self, "_startup_generation", 0):
                        return
                    self._starting_grab = False
                    self._startup_cancel = None
                    self.ocr = prepared_ocr
                    # Eligibility has already been checked in this worker. Avoid a
                    # second network round-trip inside the legacy GUI start method.
                    allowed_original = self.api.allowed
                    self.api.allowed = lambda: allowed_result
                    try:
                        start_original(self)
                    except Exception as exc:
                        error_text = str(exc)
                        if button is not None:
                            button.config(state=tk.NORMAL, text="▶  开始抢单")
                        messagebox.showerror("启动失败", error_text)
                        self._log(f"⛔ 启动失败：{error_text}")
                        return
                    finally:
                        self.api.allowed = allowed_original
                    if not self.running and button is not None:
                        button.config(state=tk.NORMAL, text="▶  开始抢单")

                self.after(0, finish)
            except Exception as exc:
                if cancel_event.is_set():
                    return
                error_text = str(exc)
                self.after(0, lambda: reset_startup(error_text))

        threading.Thread(target=work, daemon=True, name="autograb-startup").start()

    gui.App.start_grab = start_grab
    _applied = True
    return True
