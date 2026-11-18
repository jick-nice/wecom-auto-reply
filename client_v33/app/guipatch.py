# -*- coding: utf-8 -*-
"""「开始抢单」的准入检查 + 抢单期间前台保持。

用户需求（真机反馈）：
    ① 点「开始抢单」时，如果企业微信没开 → **不允许开始**，指导用户先打开
       企业微信并停在**群聊天窗口**
    ② 点「开始抢单」时**强制把企业微信切到前台**，并明确提示
       「抢单期间必须保持企业微信在前台，停止抢单后可以随意放后台」
    ③ 企业微信在前台时，**自动检测聊天区与输入框**，避免识别不到
    ④ 抢单期间窗口可以移动，但必须一直保持在前台

实现方式：
    不重写整个 start_grab（v3.3 的 start_grab 还管 OCR 预热、guard、影子模式等），
    而是**在外面包一层准入检查**：检查不过就直接 return，不进入 super().start_grab()。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

_applied = False

# 抢单期间前台丢失时的判据（喂给 GUI 提示用）
FRONT_RULE_TEXT = (
    "企业微信不在前台时，本轮识别或发送会跳过，不会自动切走其他窗口。\n"
    "需要发送时，请先将企业微信切到前台；停止后可正常操作其他窗口。"
)


def _make_stop_overlay(app):
    import tkinter as tk
    from tkinter import ttk

    old = getattr(app, "_stop_overlay", None)
    if old is not None:
        try:
            old.destroy()
        except Exception:
            pass

    overlay = tk.Toplevel(app)
    overlay.title("抢单控制")
    overlay.resizable(False, False)
    overlay.attributes("-topmost", True)
    overlay.protocol("WM_DELETE_WINDOW", lambda: app.stop_grab("点击停止浮条"))
    btn = ttk.Button(
        overlay, text="■  停止抢单  (F9)",
        command=lambda: app.stop_grab("点击停止浮条"))
    btn.pack(fill="both", expand=True, padx=8, pady=6)
    overlay.geometry(f"190x48+{max(0, overlay.winfo_screenwidth() - 220)}+12")
    overlay.update_idletasks()
    try:
        overlay.attributes("-topmost", True)
        overlay.lift()
    except Exception:
        pass
    app._stop_overlay = overlay


def apply() -> bool:
    """给 App 打补丁：开始抢单前做准入检查，并在开始时强制前台。"""
    global _applied
    if _applied:
        return True

    try:
        from . import gui
    except Exception:
        return False

    App = getattr(gui, "App", None)
    if App is None:
        return False

    if getattr(App, "_fg_patched", False):
        _applied = True
        return True

    try:
        from . import fgguard as _fgguard
    except Exception:
        return False

    _orig_bring_to_front = _fgguard.bring_to_front

    def bring_to_front_guarded(hwnd, *args, **kwargs):
        if getattr(_fgguard, "_user_stop_requested", False):
            return False
        return _orig_bring_to_front(hwnd, *args, **kwargs)

    _fgguard.bring_to_front = bring_to_front_guarded
    fgguard = _fgguard

    _orig_start = App.start_grab

    def start_grab(self) -> None:
        if getattr(self, "_starting_grab", False):
            _orig_start(self)
            return
        if fgguard is not None:
            fgguard._user_stop_requested = False
        # ---------------- ① 派单人必填 ----------------
        disp = self.cfg.get("dispatchers") or []
        if not disp:
            messagebox.showwarning("缺少设置", "请先填写派单人（群里发单的人的名字）")
            return

        # ---------------- ② 企业微信必须已打开 ----------------
        hwnd, why = fgguard.find_wecom()
        if not hwnd:
            messagebox.showerror(
                "还没打开企业微信",
                f"{why}。\n\n"
                "请按顺序做好这两步，再点「开始抢单」：\n"
                "  1) 打开企业微信并登录\n"
                "  2) 点开**要抢单的那个群**的聊天窗口（停在群聊界面，\n"
                "     不要停在通讯录 / 邮件 / 文档）")
            self._log("⛔ 没找到企业微信窗口，已阻止开始抢单")
            return

        # ---------------- ③ 前台化（用户只此一次被要求） ----------------
        fgguard.restore(hwnd)
        ok_fg = fgguard.bring_to_front(hwnd)
        self.hwnd = hwnd
        if ok_fg:
            self._log("📌 已把企业微信切到前台")
        else:
            self._log("⚠️ 没能把企业微信切到前台（可能被系统前台锁挡住），仍会继续尝试")

        # ---------------- ④ 聊天区/输入框自检 ----------------
        ok_area, area_msg = fgguard.check_chat_area(hwnd)
        if not ok_area:
            messagebox.showwarning(
                "聊天区检测不通过",
                f"{area_msg}\n\n"
                "请确认企业微信停在**群聊天窗口**（不是通讯录/邮件），\n"
                "让窗口显示出来后再点一次「开始抢单」。")
            self._log(f"⛔ 聊天区检测不通过：{area_msg}")
            return
        self._log(f"✅ 聊天区检测通过：{area_msg}")

        self._log(FRONT_RULE_TEXT.replace("\n", " "))

        # ---------------- ⑥ 交给原逻辑 ----------------
        try:
            self._fg_hwnd = hwnd
        except Exception:
            pass

        _orig_start(self)

        # The main window already owns the start/stop control; no extra Toplevel.
        try:
            bar = getattr(self, "bar", None)
            if bar is not None:
                bar.destroy()
                self.bar = None
        except Exception:
            pass

        # 开始后再确认一次（原逻辑可能会碰窗口）
        try:
            if not fgguard.is_foreground(hwnd):
                fgguard.bring_to_front(hwnd)
        except Exception:
            pass

    App.start_grab = start_grab
    # Older compiled GUI creates a separate always-on-top stop bar. Keep the
    # status updates but suppress that extra window; the main button remains usable.
    App._show_bar = lambda self: None
    App._fg_patched = True

    # ---------------- ⑦ 停止时可自由放后台 ----------------
    _orig_stop = App.stop_grab

    def stop_grab(self, reason: str = "") -> None:
        try:
            overlay = getattr(self, "_stop_overlay", None)
            if overlay is not None:
                overlay.destroy()
                self._stop_overlay = None
        except Exception:
            pass
        if fgguard is not None:
            fgguard._user_stop_requested = True
        _orig_stop(self, reason)
        try:
            self.deiconify()
            self.lift()
            self.attributes("-topmost", True)
            self.after(250, lambda: self.attributes("-topmost", False))
            self.focus_force()
        except Exception:
            pass
        try:
            self._log("ℹ️ 已停止：企业微信前台守卫已解除，可复制日志")
        except Exception:
            pass

    App.stop_grab = stop_grab

    _applied = True
    return True