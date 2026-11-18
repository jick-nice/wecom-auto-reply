# -*- coding: utf-8 -*-
"""前台状态检查：窗口不在前台时跳过识别/发送，不主动夺取用户焦点。

开始操作时由 GUI 做一次性前台切换；监控期间保持被动检查，避免主控窗口
被后台循环反复压到后面。
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

SW_RESTORE = 9
HWND_TOP = 0
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010

# 补抢的最小间隔：1.5 秒内不重复抢，避免"高频抢焦点"这种机器特征
REGrab_MIN_INTERVAL = 1.5


def is_foreground(hwnd: int) -> bool:
    try:
        return user32.GetForegroundWindow() == hwnd
    except Exception:
        return False


def is_minimized(hwnd: int) -> bool:
    try:
        return bool(user32.IsIconic(hwnd))
    except Exception:
        return False


def restore(hwnd: int) -> None:
    try:
        if is_minimized(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
    except Exception:
        pass


def bring_to_front(hwnd: int, *, raise_z: bool = False) -> bool:
    """把窗口切到前台。**不改位置、不改大小**，用户随时可拖动。

    做法（按成功率排序，全程不产生任何输入事件）：
      ① 已经在前台 → 直接成功
      ② SetForegroundWindow（正常情况下系统会允许）
      ③ 失败 → AttachThreadInput 借用前台线程的输入队列后再 SetForeground
      ④ 再失败 → BringWindowToTop + SetWindowPos(SWP_NOACTIVATE) 抬到顶层

    注意：**不模拟 Alt 键**。老版本靠 keybd_event 按 Alt 骗过前台锁，
    那会多产生一个 injected 键盘事件，是风控的额外风险面。
    """
    if hwnd is None:
        return False
    try:
        if user32.GetForegroundWindow() == hwnd:
            return True

        restore(hwnd)

        # ② 直接尝试
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            if user32.GetForegroundWindow() == hwnd:
                return True
        except Exception:
            pass

        # ③ AttachThreadInput
        try:
            cur = user32.GetForegroundWindow()
            tid_cur = user32.GetWindowThreadProcessId(cur, None) if cur else 0
            tid_me = user32.GetWindowThreadProcessId(hwnd, None) or 0
            tid_self = ctypes.windll.kernel32.GetCurrentThreadId()
            attached = []
            for tid in (tid_cur, tid_me):
                if tid and tid != tid_self:
                    user32.AttachThreadInput(tid_self, tid, True)
                    attached.append(tid)
            user32.SetForegroundWindow(hwnd)
            user32.BringWindowToTop(hwnd)
            for tid in attached:
                user32.AttachThreadInput(tid_self, tid, False)
            if user32.GetForegroundWindow() == hwnd:
                return True
        except Exception:
            pass

        # ④ 抬到最上层（不激活，不改位置）
        if raise_z:
            try:
                user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0,
                                    SWP_NOMOVE | SWP_NOSIZE)
                user32.SetForegroundWindow(hwnd)
                if user32.GetForegroundWindow() == hwnd:
                    return True
            except Exception:
                pass

        return user32.GetForegroundWindow() == hwnd
    except Exception:
        return False


class ForegroundGuard:
    """抢单期间维护「企业微信在前台」。

    用法：
        fg = ForegroundGuard(hwnd, on_lost=lambda: log("被挡住了"))
        fg.ensure(force=True)      # 开始抢单时强制一次
        ... 主循环里 ...
        if not fg.ensure():        # 发现不在前台就补抢（有限频）
            # 抢不回来 → 这一单不发
    """

    def __init__(self, hwnd: int, on_lost=None, log=None):
        self.hwnd = hwnd
        self.on_lost = on_lost
        self.log = log or (lambda *_a: None)
        self._last_grab = 0.0
        self._last_ok = True
        self.grabs = 0
        self.losts = 0

    def is_ok(self) -> bool:
        return is_foreground(self.hwnd)

    def ensure(self, force: bool = False) -> bool:
        """只检查前台状态；监控循环中不主动夺取用户焦点。"""
        ok = self.is_ok()
        if ok:
            self._last_ok = True
            return True

        self.losts += 1
        if self._last_ok and self.on_lost:
            try:
                self.on_lost()
            except Exception:
                pass
        self._last_ok = False
        return False


def find_wecom() -> tuple[int | None, str]:
    """找企业微信主窗口。返回 (hwnd, 说明)。"""
    try:
        from . import capture
        ws = capture.find_wecom_windows()
        if not ws:
            return (None, "没找到企业微信窗口")
        named = [w for w in ws if w[1]]
        if not named:
            return (None, "企业微信窗口没有标题（可能还没登录）")
        hwnd = capture.pick_wecom_window()
        if not hwnd:
            return (None, "企业微信主窗口不可用")
        return (hwnd, "ok")
    except Exception as e:
        return (None, f"查找失败: {e}")


def check_chat_area(hwnd: int) -> tuple[bool, str]:
    """检测企业微信是否停在**群聊天界面**（而不是通讯录/邮件/文档）。

    依据：能定位出聊天区 + 能学到版式锚点。两条都成立才算"可以抢单"。
    """
    try:
        from . import capture, locate, segment
        g = capture.Grabber()
        img = None
        for _ in range(3):
            try:
                img = g.grab_window_client(hwnd, copy=True)
                break
            except Exception:
                time.sleep(0.05)
        if img is None:
            return (False, "抓不到企业微信画面（可能被最小化）")

        area = locate.locate(img)
        if not area.found:
            return (False, "认不出聊天区结构：请打开**群聊天窗口**（不是通讯录/邮件）")

        crop = img[area.y1:area.y2, area.x1:area.x2]
        if crop.size == 0:
            return (False, "消息区为空")

        thr = segment.auto_thr(crop, 150)
        lay = segment.Layout()
        rows = segment.analyze(crop, lay, dark_thr=thr)
        if not rows:
            return (False, "聊天区里切不出任何消息行")
        lay.learn(rows, crop.shape[1])
        if lay.name_x is None or lay.content_x is None:
            return (False, "学不到版式锚点（可能不是群聊界面）")

        return (True, f"ok area={area.width}x{area.height} "
                      f"name_x={lay.name_x} content_x={lay.content_x} rows={len(rows)}")
    except Exception as e:
        return (False, f"检测异常: {e}")