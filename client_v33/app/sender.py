# -*- coding: utf-8 -*-
"""发送：SendInput 批量投递「点击输入框 + 数字 + 回车」。

不注入、不 hook、不读内存 —— 和 AutoHotkey 干的是同一件事。

=====================================================================
【2026-11 修复记录：设备环境异常（风控）】
=====================================================================
线上真机复现：软件一自动输入，企业微信立刻弹「当前设备环境异常，
需通过手机企业微信扫码进行安全验证」。差分实验确认触发源是
**模拟输入的量**，不是抓屏。

v3.3 相对 v3.0 退化了三处，把「点击硬上限」变成了「永远可以点」：

  ① max_clicks_per_session 默认值 3 → 200
  ② _ensure_focus 到达上限后 self.clicks = 0 归零重来（配额自我重置）
  ③ 新增 auto_foreground=True，每单发送前都 force_foreground + 6×30ms 轮询

合计效果 = 高频模拟输入 + 反复戳同一点 = 教科书级机器特征。

本版把三处全部回退，并把配额改成「真·硬上限」：

  · 到上限直接 return False，不归零、不重来
  · 默认不点鼠标（allow_click=False），只打字
  · 默认不抢前台（auto_foreground=False），不在前台就不发这一单
  · 键盘事件带 wScan（scan code），降低「裸 VK 注入」特征
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

# Scan code 表：带 scan code 的键盘事件比纯 VK 更像真实键盘
SCAN = {
    0x30: 0x0B, 0x31: 0x02, 0x32: 0x03, 0x33: 0x04, 0x34: 0x05,
    0x35: 0x06, 0x36: 0x07, 0x37: 0x08, 0x38: 0x09, 0x39: 0x0A,
    0x0D: 0x1C, 0x08: 0x0E, 0x11: 0x1D, 0x20: 0x39,
}

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

# 会话内模拟鼠标点击的绝对上限。实测：同一坐标连点 5 次 + 连敲 10 次键，
# 几分钟内就弹「需通过手机企业微信扫码进行安全验证」。3 次是实测安全值。
CLICK_HARD_CAP = 3


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
    """构造一个键盘事件。

    修复：v3.3 用 wScan=0 + dwFlags=0（纯 VK 注入），这是最容易被
    LLKHF_INJECTED 特征匹配的形态。这里带上对应的 scan code，
    让事件更接近真实键盘产生的样子。
    """
    i = INPUT(type=INPUT_KEYBOARD)
    scan = SCAN.get(vk, 0)
    flags = KEYEVENTF_KEYUP if up else 0
    if scan:
        # 有 scan code 时按 scan code 投递（dwFlags 需置 SCANCODE）
        i.u.ki = KEYBDINPUT(wVk=0, wScan=scan, dwFlags=flags | KEYEVENTF_SCANCODE,
                            time=0, dwExtraInfo=None)
    else:
        i.u.ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=None)
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
                 require_foreground: bool = True, allow_click: bool = False,
                 max_clicks_per_session: int = 3, auto_foreground: bool = False):
        self.input_pos = tuple(int(v) for v in input_pos)
        self.enter_mode = enter_mode          # enter | ctrl_enter
        self._focused_at = 0.0
        self._click_timeout = click_timeout
        self.last_cost_ms = 0.0
        # —— 风控相关的四个开关（默认值已按差分实验结论收紧）——
        # require_foreground：不在前台就宁可这一单不发，也绝不抢焦点/乱点
        # allow_click：默认**不点鼠标**（点=带 LLMHF_INJECTED 的模拟鼠标事件，机器特征最扎眼）
        # max_clicks_per_session：整个会话最多点几次，且到上限**不再重置**
        # auto_foreground：默认**不抢前台**（抢焦点本身也会产生可疑行为序列）
        self.require_foreground = require_foreground
        self.allow_click = allow_click
        # 硬性钳制：无论配置/调用方传多大的值，会话内点击绝不超过 CLICK_HARD_CAP。
        # 这是风控的最后一道防线 —— 实测连点十几次就会弹「设备环境异常」。
        self.max_clicks_per_session = max(0, min(int(max_clicks_per_session), CLICK_HARD_CAP))
        self.auto_foreground = auto_foreground
        self.clicks = 0
        self.skipped = 0
        # 输入框探针阈值（相对背景的暗像素个数）：
        self.busy_threshold = 120        # 超过认为框里有草稿，不去动它
        self.text_present_threshold = 3  # 至少这么多暗像素才算字进去了

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
        """点一次已校准的输入框（有额度才点），点完把鼠标放回原位。"""
        if not self.allow_click or self.clicks >= self.max_clicks_per_session:
            return False
        x, y = self.input_pos
        if x <= 0 or y <= 0:
            return False
        pt = wintypes.POINT()
        old = None
        if user32.GetCursorPos(ctypes.byref(pt)):
            old = (pt.x, pt.y)
        self.click_input()
        self.clicks += 1
        if old is not None:
            user32.SetCursorPos(old[0], old[1])
        time.sleep(0.06)          # 给 CEF 一点时间把焦点落到输入框
        return True

    def _ensure_focus(self, read_input=None) -> bool:
        """确保焦点在输入框。

        【关键修复】v3.3 的实现是：
            if self.clicks >= self.max_clicks_per_session:
                self.clicks = 0          # ← 到上限就归零
            return self._try_click()     # ← 然后继续点

        这等于把「点击硬上限」变成了「无限点击」，是风控被打爆的主因之一。
        这里恢复 v3.0 的语义：到上限直接 return False，永不重置。
        """
        if (time.time() - self._focused_at) < self._click_timeout:
            return False                  # 刚点过/刚发过，不用再点
        if not self.allow_click:
            return False                  # 全局禁止点击
        if self.clicks >= self.max_clicks_per_session:
            return False                  # ★ 真·硬上限：不归零、不重来
        return self._try_click()

    def send(self, text: str, hwnd: int | None = None, force_click: bool = False,
             read_input=None) -> float:
        """返回耗时（毫秒）。read_input 给了就"先确认字真的进了输入框，再按回车"。

        风险控制（2026-11 实测：连点几次 + 连敲几次会把企业微信顶到「设备环境异常」）：
          1) 不在前台 → 抛 NotForeground，这一单不发，交给界面提示人工发；
          2) 默认不点鼠标（点=带 LLMHF_INJECTED 的模拟鼠标事件，机器特征最扎眼）；
          3) 输入框里已经有草稿 → 抛 InputBusy，绝不动别人的内容；
          4) 打完发现没进去就不按回车，避免盲发一条错的。
        """
        t0 = time.perf_counter()
        text = str(text)
        if self.require_foreground and hwnd is not None:
            if user32.GetForegroundWindow() != hwnd:
                # 【修复】v3.3 在这里做 auto_foreground 抢焦点 + 6×30ms 轮询。
                # 抢焦点本身会留下可疑的操作序列，默认改为直接放弃这一单。
                if self.auto_foreground:
                    try:
                        from .capture import force_foreground, is_foreground
                        force_foreground(hwnd)
                        for _ in range(6):
                            if is_foreground(hwnd):
                                break
                            time.sleep(0.03)
                    except Exception:
                        pass
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
                    # 少量残留（自己上一次没清干净，会拼成「22」发出去）→ 先退格清掉
                    self._clear(8)
                    time.sleep(0.05)
                    before = 0

        clicked = self._ensure_focus(read_input) if (force_click or self._need_click()) else False
        self._fire(self._type_events(text))
        time.sleep(0.05)

        if read_input is not None:
            probe = read_input()
            ok = (probe is None) or (int(probe) >= self.text_present_threshold)
            if not ok:
                if not clicked:
                    clicked = self._ensure_focus(read_input)
                for _ in range(3):
                    time.sleep(0.05)
                    probe = read_input()
                    if (probe is None) or (int(probe) >= self.text_present_threshold):
                        ok = True
                        break
                if not ok:
                    self._clear(len(text) + 1)
                    raise NotFocused("输入框没收到内容，已放弃本次发送")

        self._fire(self._enter_events())
        if read_input is not None:
            # 回车之后确认输入框真的空了：中文输入法激活时，回车可能被拿去确认候选字
            for _ in range(2):
                time.sleep(0.04)
                p = read_input()
                if p is None or int(p) < self.text_present_threshold:
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