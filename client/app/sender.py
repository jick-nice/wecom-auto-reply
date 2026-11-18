# -*- coding: utf-8 -*-
"""发送：一次 SendInput 批量投递「点击输入框 + 数字 + 回车」，全程 8~15ms。

不注入、不 hook、不读内存 —— 和 AutoHotkey 干的是同一件事。
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

VK = {
    **{str(d): 0x30 + d for d in range(10)},
    **{chr(0x41 + i): 0x41 + i for i in range(26)},
    " ": 0x20, ".": 0xBE, "-": 0xBD, "+": 0xBB, "=": 0xBB,
}

KEYEVENTF_KEYUP = 0x0002
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _U(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _key(vk: int, up: bool = False) -> INPUT:
    i = INPUT(type=INPUT_KEYBOARD)
    i.u.ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=KEYEVENTF_KEYUP if up else 0,
                        time=0, dwExtraInfo=None)
    return i


def _mouse(flag: int, x: int, y: int) -> INPUT:
    i = INPUT(type=INPUT_MOUSE)
    i.u.mi = MOUSEINPUT(dx=x, dy=y, mouseData=0, dwFlags=flag, time=0, dwExtraInfo=None)
    return i


class NotForeground(RuntimeError):
    """企业微信不在前台，为了不产生风险动作，直接放弃这次发送。"""


class InputBusy(RuntimeError):
    """输入框里有别的草稿，不去动它。"""


class NotFocused(RuntimeError):
    """打完字输入框里没有内容 —— 焦点不在，放弃本次（不盲按回车）。"""


class Sender:
    def __init__(self, input_pos, enter_mode: str = "enter", click_timeout: float = 1.5,
                 require_foreground: bool = True, allow_click: bool = True,
                 max_clicks_per_session: int = 3):
        self.input_pos = tuple(int(v) for v in input_pos)
        self.enter_mode = enter_mode          # enter | ctrl_enter
        self._focused_at = 0.0
        self._click_timeout = click_timeout
        self.last_cost_ms = 0.0
        # —— 风控相关的三个开关 ——
        # require_foreground：不在前台就宁可这一单不发，也绝不抢焦点/乱点
        # allow_click：是否允许软件自己点输入框（点=模拟鼠标事件，最容易被识别）
        # max_clicks_per_session：整个会话最多点几次，避免"固定点位连点"的机器特征
        self.require_foreground = require_foreground
        self.allow_click = allow_click
        self.max_clicks_per_session = max_clicks_per_session
        self.clicks = 0
        self.skipped = 0
        # 输入框探针阈值（相对背景的暗像素个数，实测空框=0、打一个数字=22）：
        # 用"涨了多少"判断字有没有进去，宁可判成功也不丢单。
        self.busy_threshold = 120        # 超过认为框里有草稿，不去动它（约 5 个字以上）
        self.grow_threshold = 12         # 发送后至少涨这么多才算字进去了

    # -------------------------------------------------------------- 基础
    def click_input(self) -> None:
        x, y = self.input_pos
        user32.SetCursorPos(x, y)
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
        user32.mouse_event(MOUSEEVENTF_LEFTUP, x, y, 0, 0)
        self._focused_at = time.time()

    def _need_click(self) -> bool:
        return (time.time() - self._focused_at) > self._click_timeout

    def _type_events(self, text: str) -> list[INPUT]:
        ev: list[INPUT] = []
        for ch in text:
            vk = VK.get(ch.upper() if ch.isalpha() else ch)
            if vk is None:
                continue
            ev.append(_key(vk))
            ev.append(_key(vk, up=True))
        return ev

    def _enter_events(self) -> list[INPUT]:
        if self.enter_mode == "ctrl_enter":
            return [_key(0x11), _key(0x0D), _key(0x0D, up=True), _key(0x11, up=True)]
        return [_key(0x0D), _key(0x0D, up=True)]

    def _fire(self, ev: list[INPUT]) -> None:
        if not ev:
            return
        arr = (INPUT * len(ev))(*ev)
        n = user32.SendInput(len(ev), arr, ctypes.sizeof(INPUT))
        if n != len(ev):
            raise OSError(ctypes.get_last_error(), "SendInput 被拦截")

    def _build(self, text: str) -> list[INPUT]:
        """兼容旧接口：字符 + 回车一次投递。"""
        return self._type_events(str(text)) + self._enter_events()

    def _clear(self, n: int) -> None:
        ev: list[INPUT] = []
        for _ in range(max(0, int(n))):
            ev += [_key(0x08), _key(0x08, up=True)]
        self._fire(ev)

    def _try_click(self) -> bool:
        """点一次输入框（有额度才点），点完把鼠标放回原位。"""
        if not self.allow_click or self.clicks >= self.max_clicks_per_session:
            return False
        pt = wintypes.POINT()
        old = None
        if user32.GetCursorPos(ctypes.byref(pt)):
            old = (pt.x, pt.y)
        self.click_input()
        self.clicks += 1
        if old is not None:
            user32.SetCursorPos(old[0], old[1])
        time.sleep(0.06)          # 给 CEF 一点时间把焦点落到输入框（12ms 实测不够）
        return True

    def send(self, text: str, hwnd: int | None = None, force_click: bool = False,
             read_input=None) -> float:
        """返回耗时（毫秒）。read_input 给了就"先确认字真的进了输入框，再按回车"。

        风险控制（2026-11 实测：连点几次 + 连敲几次会把企业微信顶到「设备环境异常」）：
          1) 不在前台 → 抛 NotForeground，这一单不发，交给界面提示人工发；
          2) 默认不点鼠标（点=带 LLMHF_INJECTED 的模拟鼠标事件，机器特征最扎眼）；
             只有当"打完字发现输入框没收到"时，才点一次补救；
          3) 输入框里已经有草稿 → 抛 InputBusy，绝不动别人的内容；
          4) 打完发现没进去就不按回车，避免盲发一条错的。
        """
        t0 = time.perf_counter()
        text = str(text)
        if self.require_foreground and hwnd is not None:
            if user32.GetForegroundWindow() != hwnd:
                self.skipped += 1
                raise NotForeground("企业微信不在前台")

        before = 0
        if read_input is not None:
            probe0 = read_input()
            if probe0 is None:
                read_input = None            # 读不到就不校验，绝不因此卡住抢单
            else:
                before = int(probe0)
                if before > self.busy_threshold:
                    raise InputBusy(f"输入框里已有内容（{before} 个暗像素）")
                if before > 6:
                    # 少量残留（自己上一次没清干净，实测会拼成「22」发出去）→ 先退格清掉
                    self._clear(8)
                    time.sleep(0.05)
                    before = 0

        clicked = self._try_click() if force_click else False
        self._fire(self._type_events(text))

        if read_input is not None:
            def landed(probe) -> bool:
                if probe is None:
                    return True              # 读不到就认为成功，别因为校验丢单
                return (int(probe) - before) >= self.grow_threshold

            probe = read_input()
            if not landed(probe):
                if not clicked:
                    clicked = self._try_click()
                    if clicked:
                        self._fire(self._type_events(text))
                        probe = read_input()
                if not landed(probe):
                    self._clear(len(text) + 1)
                    raise NotFocused("输入框没收到内容，已放弃本次发送")

        self._fire(self._enter_events())
        if read_input is not None:
            # 回车之后要确认输入框真的空了：中文输入法激活时，回车可能被拿去确认候选字，
            # 消息根本没发出去（实测会留下「暗grdengarden」这种候选残留）。
            for _ in range(3):
                time.sleep(0.03)
                p = read_input()
                if p is None or (int(p) - before) < self.grow_threshold:
                    break
                self._fire(self._enter_events())
        self._focused_at = time.time()
        self.last_cost_ms = (time.perf_counter() - t0) * 1000
        return self.last_cost_ms

    def locate_input(self, wait_key: int = 0x77, timeout: float = 30.0) -> None:
        """把鼠标移到输入框按 F8，记录坐标。"""
        t0 = time.time()
        while time.time() - t0 < timeout:
            if user32.GetAsyncKeyState(wait_key) & 0x8000:
                pt = wintypes.POINT()
                user32.GetCursorPos(ctypes.byref(pt))
                self.input_pos = (pt.x, pt.y)
                while user32.GetAsyncKeyState(wait_key) & 0x8000:
                    time.sleep(0.01)
                return
            time.sleep(0.02)
        raise TimeoutError("等待 F8 超时")
