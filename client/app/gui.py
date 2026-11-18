# -*- coding: utf-8 -*-
"""界面：登录 → 主窗（设置/日志） → 运行时弹出置顶「大停止条」。"""
from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from . import api, cfg as cfgmod, guard, segment
from .capture import Grabber, find_wecom_windows, force_foreground
from .engine import Engine
from .sender import Sender

FONT = "微软雅黑"
RED, GREEN, GRAY = "#D93025", "#188038", "#5F6368"


class LoginFrame(ttk.Frame):
    def __init__(self, master, app: "App"):
        super().__init__(master, padding=28)
        self.app = app
        self.pack(fill=tk.BOTH, expand=True)

        ttk.Label(self, text="抢单助手", font=(FONT, 22, "bold")).pack(pady=(10, 4))
        ttk.Label(self, text="请使用管理员分配的账号登录", foreground=GRAY).pack(pady=(0, 22))

        box = ttk.Frame(self)
        box.pack()
        ttk.Label(box, text="账号", font=(FONT, 11)).grid(row=0, column=0, sticky=tk.E, pady=6, padx=(0, 8))
        self.user = ttk.Entry(box, width=26, font=(FONT, 12))
        self.user.grid(row=0, column=1, pady=6)
        ttk.Label(box, text="密码", font=(FONT, 11)).grid(row=1, column=0, sticky=tk.E, pady=6, padx=(0, 8))
        self.pwd = ttk.Entry(box, width=26, font=(FONT, 12), show="*")
        self.pwd.grid(row=1, column=1, pady=6)

        self.msg = ttk.Label(self, text="", foreground=RED, wraplength=380)
        self.msg.pack(pady=10)

        self.btn = ttk.Button(self, text="登 录", command=self.do_login)
        self.btn.pack(pady=6, ipadx=40, ipady=6)

        if not app.cfg.get("api_url"):
            ttk.Label(self, text="当前未配置后端地址，可直接进入离线调试模式",
                      foreground=GRAY, wraplength=380).pack(pady=(18, 2))
            ttk.Button(self, text="离线调试模式", command=app.enter_main).pack()

        self.pwd.bind("<Return>", lambda e: self.do_login())
        self.user.focus_set()

    def do_login(self) -> None:
        u, p = self.user.get().strip(), self.pwd.get().strip()
        if not u or not p:
            self.msg.config(text="账号和密码都要填")
            return
        self.btn.config(state="disabled", text="登录中…")

        def work():
            try:
                body = self.app.api.login(u, p)
                self.app.after(0, lambda: self.ok(body, u, p))
            except api.ApiError as e:
                self.app.after(0, lambda: self.fail(str(e)))
            except Exception as e:
                self.app.after(0, lambda: self.fail(f"异常: {e}"))

        threading.Thread(target=work, daemon=True).start()

    def ok(self, body: dict, u: str, p: str) -> None:
        ent = body.get("ent") or {}
        if not ent.get("allowed"):
            self.fail(ent.get("reason") or "该账号没有使用资格")
            return
        self.app.cfg["username"] = u
        self.app.cfg["remember_token"] = self.app.api.token
        cfgmod.save(self.app.cfg)
        self.app.enter_main(announcements=body.get("ann") or [], quota=body.get("quota") or {})

    def fail(self, msg: str) -> None:
        self.msg.config(text=msg)
        self.btn.config(state="normal", text="登 录")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("抢单助手 v3.0")
        self.geometry("760x760")
        self.minsize(700, 640)

        self.cfg = cfgmod.load()
        self.api = api.Api(self.cfg.get("api_url", ""), self.cfg.get("remember_token", ""))
        self.grabber = Grabber()
        self.ocr = None
        self.sender = Sender(self.cfg.get("input_pos", [0, 0]), self.cfg.get("enter_mode", "enter"),
                             require_foreground=bool(self.cfg.get("require_foreground", True)),
                             allow_click=bool(self.cfg.get("allow_click", True)))
        self.engine: Engine | None = None
        self.guard = guard.SleepGuard()
        self.logq: queue.Queue[str] = queue.Queue()
        self.ann: list[dict] = []
        self.running = False
        self.bar: tk.Toplevel | None = None
        self._last_hotkey = 0.0

        self.hwnd = None
        if self.cfg.get("remember_token"):
            self.enter_main()
        else:
            LoginFrame(self, self)

        self.after(120, self._drain_log)
        self.after(300, self._hotkey_loop)
        self.after(60000, self._heartbeat_loop)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------- 主界面
    def enter_main(self, announcements=None, quota=None) -> None:
        for w in self.winfo_children():
            w.destroy()
        if announcements is not None:
            self.ann = announcements
        if quota:
            used = int(quota.get("used", 0))
            self.cfg["orders_today"] = max(self.cfg.get("orders_today", 0), used)
            self.cfg["max_orders"] = int(quota.get("max", self.cfg.get("max_orders", 3)))

        root = ttk.Frame(self, padding=12)
        root.pack(fill=tk.BOTH, expand=True)

        # 顶部：账号 / 名额 / 公告
        head = ttk.Frame(root)
        head.pack(fill=tk.X)
        self.lbl_user = ttk.Label(head, text=f"账号：{self.cfg.get('username') or '离线'}",
                                  font=(FONT, 10, "bold"))
        self.lbl_user.pack(side=tk.LEFT)
        self.lbl_quota = ttk.Label(head, text="", font=(FONT, 10))
        self.lbl_quota.pack(side=tk.LEFT, padx=18)
        ttk.Button(head, text="退出登录", command=self.logout).pack(side=tk.RIGHT)

        self.lbl_ann = ttk.Label(root, text="", foreground="#B06000", wraplength=720,
                                 font=(FONT, 9), justify=tk.LEFT)
        self.lbl_ann.pack(fill=tk.X, pady=(6, 0))
        self._render_ann()

        # 超大启停按钮
        self.big = tk.Button(root, text="▶  开始抢单", font=(FONT, 30, "bold"),
                             bg=GREEN, fg="white", activebackground="#0F6B2D",
                             activeforeground="white", relief=tk.FLAT, command=self.toggle)
        self.big.pack(fill=tk.X, pady=14, ipady=26)

        # 状态行
        st = ttk.Frame(root)
        st.pack(fill=tk.X)
        self.lbl_state = ttk.Label(st, text="未运行", font=(FONT, 11, "bold"))
        self.lbl_state.pack(side=tk.LEFT)
        self.lbl_perf = ttk.Label(st, text="", foreground=GRAY, font=(FONT, 9))
        self.lbl_perf.pack(side=tk.LEFT, padx=16)
        self.lbl_last = ttk.Label(st, text="", foreground=GRAY, font=(FONT, 9))
        self.lbl_last.pack(side=tk.RIGHT)

        # 设置
        setf = ttk.LabelFrame(root, text="设置", padding=10)
        setf.pack(fill=tk.X, pady=10)

        r = 0
        ttk.Label(setf, text="派单人").grid(row=r, column=0, sticky=tk.W)
        self.e_disp = ttk.Entry(setf, width=52, font=(FONT, 9))
        self.e_disp.insert(0, "，".join(self.cfg.get("dispatchers", [])))
        self.e_disp.grid(row=r, column=1, columnspan=3, sticky=tk.W, padx=6)
        ttk.Label(setf, text="（多个用逗号分隔，括号备注可保留）", foreground=GRAY,
                  font=(FONT, 8)).grid(row=r, column=4, sticky=tk.W)
        r += 1

        ttk.Label(setf, text="我的群名").grid(row=r, column=0, sticky=tk.W, pady=4)
        self.e_me = ttk.Entry(setf, width=20, font=(FONT, 9))
        self.e_me.insert(0, self.cfg.get("my_name", ""))
        self.e_me.grid(row=r, column=1, sticky=tk.W, padx=6)
        ttk.Label(setf, text="（派单人 @ 到这个名字 = 判定抢到）", foreground=GRAY,
                  font=(FONT, 8)).grid(row=r, column=2, columnspan=2, sticky=tk.W)
        r += 1

        ttk.Label(setf, text="聊天区域").grid(row=r, column=0, sticky=tk.W, pady=4)
        self.lbl_region = ttk.Label(setf, text="未检测（点右侧按钮）", font=("Consolas", 9))
        self.lbl_region.grid(row=r, column=1, sticky=tk.W, padx=6)
        ttk.Button(setf, text="自动检测", width=10, command=self.detect_area).grid(row=r, column=2, sticky=tk.W)
        ttk.Button(setf, text="手动兜底", width=9, command=self.calibrate_region).grid(row=r, column=3, sticky=tk.W)
        r += 1

        ttk.Label(setf, text="输入框").grid(row=r, column=0, sticky=tk.W, pady=4)
        self.lbl_input = ttk.Label(setf, text="自动跟随消息区", font=("Consolas", 9))
        self.lbl_input.grid(row=r, column=1, sticky=tk.W, padx=6)
        ttk.Button(setf, text="按 F8 手工指定", width=16,
                   command=self.calibrate_input).grid(row=r, column=2, columnspan=2, sticky=tk.W)
        r += 1

        ttk.Label(setf, text="后端地址").grid(row=r, column=0, sticky=tk.W, pady=4)
        self.e_api = ttk.Entry(setf, width=52, font=(FONT, 9))
        self.e_api.insert(0, self.cfg.get("api_url", ""))
        self.e_api.grid(row=r, column=1, columnspan=2, sticky=tk.W, padx=6)
        ttk.Button(setf, text="保存", width=8, command=self.save_settings).grid(row=r, column=3, sticky=tk.W)
        r += 1

        ttk.Label(setf, text="抢满数量").grid(row=r, column=0, sticky=tk.W, pady=4)
        self.e_max = ttk.Entry(setf, width=6, font=(FONT, 9))
        self.e_max.insert(0, str(self.cfg.get("max_orders", 3)))
        self.e_max.grid(row=r, column=1, sticky=tk.W, padx=6)
        ttk.Button(setf, text="🔍 测试识别（不发送）", command=self.test_once).grid(
            row=r, column=2, columnspan=2, sticky=tk.W)

        # 日志
        lf = ttk.LabelFrame(root, text="运行日志", padding=6)
        lf.pack(fill=tk.BOTH, expand=True)
        self.log = tk.Text(lf, height=12, wrap=tk.WORD, font=("Consolas", 9), bg="#111", fg="#DDD")
        sb = ttk.Scrollbar(lf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        self._refresh_quota()
        self._log("就绪。① 打开企业微信的群聊天窗口 ② 点「自动检测」 ③ 填派单人和我的群名 ④ 开始抢单")

    def _render_ann(self) -> None:
        act = [a for a in self.ann if a.get("active", True)]
        if not act:
            self.lbl_ann.config(text="")
            return
        a = act[0]
        self.lbl_ann.config(text=f"📢 {a.get('title', '公告')}：{a.get('body', '')}")

    def _refresh_quota(self) -> None:
        self.lbl_quota.config(
            text=f"今日 {self.cfg.get('orders_today', 0)}/{self.cfg.get('max_orders', 3)} 单")

    # ------------------------------------------------------------- 设置
    def save_settings(self) -> None:
        parts = [p.strip() for p in self.e_disp.get().replace("，", ",").split(",") if p.strip()]
        self.cfg["dispatchers"] = parts
        self.cfg["my_name"] = self.e_me.get().strip()
        self.cfg["api_url"] = self.e_api.get().strip().rstrip("/")
        try:
            self.cfg["max_orders"] = max(1, int(self.e_max.get()))
        except ValueError:
            self.cfg["max_orders"] = 3
        self.api.base_url = self.cfg["api_url"]
        cfgmod.save(self.cfg)
        self._refresh_quota()
        self._log("✅ 设置已保存")

    def detect_area(self) -> None:
        """自动检测聊天区（不靠框选，靠窗口结构）。"""
        self.save_settings()
        self._log("🔍 正在自动检测聊天区…")

        def work():
            try:
                hwnd = self.pick_window()
                if not hwnd:
                    self._log("❌ 没找到企业微信窗口，请先打开并登录企业微信")
                    return
                force_foreground(hwnd)
                eng = Engine(self.cfg, self.ocr, self.grabber, self.sender, log=self._log,
                             on_manual=self._on_manual)
                if not eng.ensure_area(force=True):
                    self._log("❌ 检测失败：企业微信窗口不可用")
                    return
                a = eng.area
                ox, oy, cw, ch = eng._origin
                txt = f"({ox + a.x1},{oy + a.y1})-({ox + a.x2},{oy + a.y2})"
                self.after(0, lambda: self.lbl_region.config(
                    text=f"{a.width}x{a.height} {'✓自动' if a.found else '⚠比例兜底'}"))
                self.after(0, lambda: self.lbl_input.config(text=str(self.sender.input_pos)))
                self._log(f"✅ 聊天区 {txt}；输入框 {self.sender.input_pos}")
                if not a.found:
                    self._log("⚠️ 没识别出输入框边界：请确认企业微信停在群聊天界面；"
                              "仍不行就用「手动兜底」拖一次（会换算成相对窗口的坐标，换分辨率也不再错位）")
            except Exception as e:
                import traceback
                self._log(f"❌ 检测异常: {e}\n{traceback.format_exc()}")

        threading.Thread(target=work, daemon=True).start()

    def calibrate_region(self) -> None:
        """手动兜底：拖出来的绝对坐标 → 立刻换算成**相对客户区**的比例坐标存盘。"""
        self.withdraw()
        time.sleep(0.25)
        try:
            region = _select_region(self)
        finally:
            self.deiconify()
        if not region:
            return
        hwnd = self.pick_window()
        if hwnd:
            from .capture import client_rect_screen
            rc = client_rect_screen(hwnd)
            if rc:
                ox, oy = rc[0], rc[1]
                region = (region[0] - ox, region[1] - oy, region[2] - ox, region[3] - oy)
        self.cfg["manual_region"] = [int(v) for v in region]
        cfgmod.save(self.cfg)
        self.lbl_region.config(text=f"手动 {region[2]-region[0]}x{region[3]-region[1]}")
        self._log(f"✅ 手动区域（相对窗口）: {self.cfg['manual_region']}")

    def calibrate_input(self) -> None:
        self._log("把鼠标移到企业微信输入框，然后按 F8")
        hwnd = self.pick_window()

        def work():
            try:
                self.sender.locate_input(timeout=40)
                self.cfg["input_pos"] = list(self.sender.input_pos)
                cfgmod.save(self.cfg)
                self.after(0, lambda: self.lbl_input.config(text=str(self.cfg["input_pos"])))
                self._log(f"✅ 输入框: {self.cfg['input_pos']}")
                if hwnd:
                    force_foreground(hwnd)
                    self.sender.click_input()
                    self._log("已点击一次输入框，确认光标在里面")
            except Exception as e:
                self._log(f"❌ 定位失败: {e}")

        threading.Thread(target=work, daemon=True).start()

    def pick_window(self) -> int | None:
        if self.hwnd:
            return self.hwnd
        ws = find_wecom_windows()
        if not ws:
            return None
        self.hwnd = ws[0][0]
        return self.hwnd

    # ------------------------------------------------------------- 启停
    def toggle(self) -> None:
        if self.running:
            self.stop_grab()
        else:
            self.start_grab()

    def start_grab(self) -> None:
        if not self.cfg.get("dispatchers"):
            messagebox.showwarning("缺少设置", "请填写派单人")
            return

        if self.api.base_url:
            ok, msg, _ = self.api.allowed()
            if not ok:
                messagebox.showerror("无法开始", msg)
                return
        else:
            self._log("⚠️ 未配置后端，离线调试模式，不做资格校验")

        self.save_settings()
        if self.ocr is None:
            self._log("正在加载 OCR 引擎…")
            self.update_idletasks()
            from .ocr import Ocr
            self.ocr = Ocr()
            self.ocr.warmup()
            self._log("✅ OCR 就绪")

        self.hwnd = self.pick_window() or self.hwnd
        if self.hwnd and not force_foreground(self.hwnd):
            self._log("⚠️ 未能把企业微信切到前台，发送可能失败")

        self.guard.start()
        self.engine = Engine(self.cfg, self.ocr, self.grabber, self.sender, log=self._log,
                             on_win=self._on_win, on_auto_stop=self._on_auto_stop,
                             on_frame=self._on_frame, on_manual=self._on_manual)
        self.engine.start()
        self.running = True
        self.big.config(text="■  停止抢单", bg=RED, activebackground="#A5271D")
        self.lbl_state.config(text="抢单中", foreground=RED)
        self._log("🚀 开始监控（F9 也可停止）")
        self._show_bar()
        self.after(300, self._perf_loop)

    def stop_grab(self, reason: str = "") -> None:
        if self.engine:
            self.engine.stop()
            self.engine = None
        self.guard.stop()
        self.running = False
        if getattr(self, "big", None):
            self.big.config(text="▶  开始抢单", bg=GREEN, activebackground="#0F6B2D")
            self.lbl_state.config(text="已停止", foreground=GRAY)
        self._hide_bar()
        cfgmod.save(self.cfg)
        self._log(f"⏹️ 已停止{('：' + reason) if reason else ''}")

    def _on_win(self, n: int) -> None:
        self.after(0, lambda: (self._refresh_quota(), self._bar_count(n)))
        if self.api.base_url:
            threading.Thread(target=lambda: self._report(n), daemon=True).start()

    def _report(self, n: int) -> None:
        try:
            self.engine and self.api.report_order(str(n), "")
        except Exception as e:
            self._log(f"报单失败（不影响抢单）: {e}")

    def _on_auto_stop(self, reason: str) -> None:
        self.after(0, lambda: self.stop_grab(reason))

    def _on_frame(self, text: str) -> None:
        self.after(0, lambda: self.lbl_last.config(text=text[:60]))

    def _on_manual(self, code: str, text: str) -> None:
        """企业微信不在前台 → 不抢焦点，改成大字提示，让人自己按一下。"""
        def show():
            if self.bar:
                self.bar_cnt.config(text=f"⚠️ 手动回 {code}", fg="#FFD54F")
                self.bar.attributes("-topmost", True)
        self.after(0, show)

    # ------------------------------------------------- 顶部置顶停止条
    def _show_bar(self) -> None:
        if self.bar:
            return
        b = tk.Toplevel(self)
        b.title("控制")
        b.attributes("-topmost", True)
        b.geometry(f"300x150+{self.winfo_screenwidth() - 330}+60")
        b.configure(bg="#202124")
        tk.Label(b, text="抢单中", fg="white", bg="#202124", font=(FONT, 10)).pack(pady=(8, 0))
        self.bar_cnt = tk.Label(b, text="", fg="#8AB4F8", bg="#202124", font=(FONT, 11, "bold"))
        self.bar_cnt.pack()
        tk.Button(b, text="停 止", font=(FONT, 20, "bold"), bg=RED, fg="white",
                  activebackground="#A5271D", activeforeground="white", relief=tk.FLAT,
                  command=lambda: self.stop_grab("手动停止")).pack(fill=tk.BOTH, expand=True,
                                                                  padx=10, pady=10)
        self.bar = b
        self._bar_count(self.cfg.get("orders_today", 0))
        self._warn_overlap()

    def _bar_count(self, n: int) -> None:
        if self.bar:
            self.bar_cnt.config(text=f"今日 {n}/{self.cfg.get('max_orders', 3)}")

    def _warn_overlap(self) -> None:
        if not self.bar:
            return
        hwnd = self.pick_window()
        if not hwnd:
            return
        from .capture import client_rect_screen
        rc = client_rect_screen(hwnd)
        if not rc:
            return
        wx, wy, ww, wh = rc
        bx, by = self.bar.winfo_x(), self.bar.winfo_y()
        bw, bh = self.bar.winfo_width(), self.bar.winfo_height()
        if bx < wx + ww and bx + bw > wx and by < wy + wh and by + bh > wy:
            self._log("⚠️ 停止条压在企业微信窗口上，会被截进画面；请拖开它或把窗口挪开")

    def _hide_bar(self) -> None:
        if self.bar:
            self.bar.destroy()
            self.bar = None

    # ------------------------------------------------------------- 杂项
    def _perf_loop(self) -> None:
        if not self.running or not self.engine:
            return
        s = self.engine.stats
        self.lbl_perf.config(
            text=f"帧 {s['frames']} · OCR {s['ocr']} · 已发 {s['sent']} · 单帧 {s['last_ms']:.1f}ms")
        self.after(500, self._perf_loop)

    def _hotkey_loop(self) -> None:
        try:
            import ctypes
            vk = int(self.cfg.get("hotkey_vk", 0x78))
            if ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000:
                if time.time() - self._last_hotkey > 0.6:
                    self._last_hotkey = time.time()
                    self.toggle()
        except Exception:
            pass
        self.after(60, self._hotkey_loop)

    def _heartbeat_loop(self) -> None:
        if self.api.base_url and self.api.token:
            def work():
                ok, msg, ent = self.api.allowed()
                self.after(0, lambda: self._hb_result(ok, msg))
            threading.Thread(target=work, daemon=True).start()
        self.after(60000, self._heartbeat_loop)

    def _hb_result(self, ok: bool, msg: str) -> None:
        if not ok:
            self._log(f"⛔ 资格校验失败：{msg}")
            if self.running:
                self.stop_grab("使用资格失效")
            messagebox.showerror("无法使用", msg)

    def test_once(self) -> None:
        if self.ocr is None:
            from .ocr import Ocr
            self._log("加载 OCR…")
            self.ocr = Ocr()
            self.ocr.warmup()
        try:
            hwnd = self.pick_window()
            if hwnd:
                force_foreground(hwnd)
            eng = Engine(self.cfg, self.ocr, self.grabber, self.sender, log=self._log,
                             on_manual=self._on_manual)
            if not eng.ensure_area(force=True):
                messagebox.showwarning("检测失败", "没找到企业微信窗口")
                return
            frame = eng._grab_frame()
            self._log(f"抓取方式: {eng.grab_mode}，条带 {frame.shape[1]}x{frame.shape[0]}")
        except Exception as e:
            messagebox.showerror("截屏失败", str(e))
            return
        t0 = time.perf_counter()
        rows = segment.analyze(frame, segment.Layout(), dark_thr=int(self.cfg.get("dark_thr", 150)))
        lay = segment.Layout()
        lay.learn(rows)
        rows = segment.analyze(frame, lay, dark_thr=int(self.cfg.get("dark_thr", 150)))
        msgs = segment.pair_messages(rows)
        elapsed = (time.perf_counter() - t0) * 1000
        self._log(f"切分：{len(rows)} 行 / {len(msgs)} 条，{elapsed:.1f}ms，锚点 name_x={lay.name_x} content_x={lay.content_x}")
        for m in msgs[-4:]:
            s = self.ocr.line(segment.crop(frame, m.sender_row)) if m.sender_row else "(我)"
            b = self.ocr.read_row(segment.crop(frame, m.body_row))
            from .numparse import reply_code
            self._log(f"   [{s}] {b!r} → 号码 {reply_code(b)}")

    def logout(self) -> None:
        self.cfg["remember_token"] = ""
        cfgmod.save(self.cfg)
        for w in self.winfo_children():
            w.destroy()
        LoginFrame(self, self)

    def _log(self, msg: str) -> None:
        self.logq.put(f"[{time.strftime('%H:%M:%S')}] {msg}")

    def _drain_log(self) -> None:
        while True:
            try:
                line = self.logq.get_nowait()
            except queue.Empty:
                break
            try:
                if hasattr(self, "log"):
                    self.log.insert(tk.END, line + "\n")
                    self.log.see(tk.END)
            except Exception:
                pass
        self.after(120, self._drain_log)

    def on_close(self) -> None:
        try:
            if self.running:
                self.stop_grab()
            cfgmod.save(self.cfg)
        finally:
            self.destroy()


def _select_region(parent) -> tuple[int, int, int, int] | None:
    """全屏半透明拖拽框选。"""
    from PIL import ImageGrab, ImageTk

    shot = ImageGrab.grab()
    top = tk.Toplevel(parent)
    top.attributes("-fullscreen", True)
    top.attributes("-topmost", True)
    top.configure(cursor="cross", bg="black")
    cv = tk.Canvas(top, highlightthickness=0, bg="black")
    cv.pack(fill=tk.BOTH, expand=True)
    img = ImageTk.PhotoImage(shot)
    cv.create_image(0, 0, anchor=tk.NW, image=img)
    cv.create_text(shot.width // 2, 26, text="拖拽框选「聊天消息区」，ESC 取消",
                   fill="yellow", font=(FONT, 18, "bold"))
    state = {"x0": 0, "y0": 0, "rect": None, "res": None}

    def down(e):
        state["x0"], state["y0"] = e.x, e.y
        if state["rect"]:
            cv.delete(state["rect"])
        state["rect"] = cv.create_rectangle(e.x, e.y, e.x, e.y, outline="red", width=3)

    def drag(e):
        if state["rect"]:
            cv.coords(state["rect"], state["x0"], state["y0"], e.x, e.y)
            cv.create_text((state["x0"] + e.x) // 2, state["y0"] - 16,
                           text=f"{abs(e.x - state['x0'])}×{abs(e.y - state['y0'])}",
                           fill="yellow", font=(FONT, 12, "bold"), tags="sz")
            cv.delete("sz_old")
            cv.itemconfig("sz", tags=("sz", "sz_old"))

    def up(e):
        x1, x2 = sorted((state["x0"], e.x))
        y1, y2 = sorted((state["y0"], e.y))
        if x2 - x1 > 80 and y2 - y1 > 80:
            state["res"] = (x1, y1, x2, y2)
        top.destroy()

    cv.bind("<Button-1>", down)
    cv.bind("<B1-Motion>", drag)
    cv.bind("<ButtonRelease-1>", up)
    top.bind("<Escape>", lambda e: top.destroy())
    top.grab_set()
    parent.wait_window(top)
    return state["res"]
