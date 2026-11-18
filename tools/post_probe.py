# -*- coding: utf-8 -*-
"""试探：能不能不产生"模拟输入事件"就把字送进企业微信输入框。

原理：SendInput / mouse_event 产生的按键带 LLKHF_INJECTED 标记，
任何低级键盘钩子都能看出来 —— 这是最容易被风控抓的特征。
而 PostMessage(WM_CHAR/WM_KEYDOWN) 只是在窗口消息队列里放一条消息，
**不经过输入注入层、不带 injected 标记、不移动鼠标**。

本脚本只做探测：
  1. PostMessage 一个字符 → 截图 OCR 看输入框里有没有出现
  2. 有 → 再 PostMessage 退格清掉，不发送任何消息
  3. 没有 → 打印结论，改用其它方案
"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "client"))

from app import locate                                          # noqa: E402
from app.capture import (Grabber, client_rect_screen, force_foreground,  # noqa: E402
                         is_foreground, pick_wecom_window, restore, set_dpi_aware)
from app.ocr import Ocr                                         # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
WM_KEYDOWN, WM_KEYUP, WM_CHAR = 0x0100, 0x0101, 0x0102
VK_X, VK_BACK = 0x58, 0x08


def post_char(hwnd, ch, vk):
    """按 CEF/Qt 常见的键序投递：KEYDOWN → CHAR → KEYUP。"""
    scan = user32.MapVirtualKeyW(vk, 0)
    lp_down = 1 | (scan << 16)
    lp_up = 1 | (scan << 16) | (1 << 30) | (1 << 31)
    user32.PostMessageW(hwnd, WM_KEYDOWN, vk, lp_down)
    user32.PostMessageW(hwnd, WM_CHAR, ord(ch), lp_down)
    user32.PostMessageW(hwnd, WM_KEYUP, vk, lp_up)


def post_key(hwnd, vk):
    scan = user32.MapVirtualKeyW(vk, 0)
    user32.PostMessageW(hwnd, WM_KEYDOWN, vk, 1 | (scan << 16))
    user32.PostMessageW(hwnd, WM_KEYUP, vk, 1 | (scan << 16) | (1 << 30) | (1 << 31))


def main():
    set_dpi_aware()
    hwnd = pick_wecom_window()
    if not hwnd:
        print("❌ 没找到企业微信窗口")
        return 1
    restore(hwnd)
    force_foreground(hwnd)
    time.sleep(0.4)
    g = Grabber()
    o = Ocr()
    o.warmup()
    full = np.array(g.grab_window_client(hwnd, copy=True))
    area = locate.locate(full)
    print("前台 =", is_foreground(hwnd), "| 聊天区 =", area)
    if not area.found:
        print("⚠️ 定位为比例兜底，结果仍可参考")

    # 输入框取样区（相对客户区）
    def read_input():
        f = np.array(g.grab_window_client(hwnd, copy=True))
        y1 = max(0, area.input_y1 - 10)
        y2 = min(f.shape[0], area.input_y2 + 10)
        x1 = max(0, area.input_x1)
        x2 = min(f.shape[1], area.input_x2)
        return o.line(f[y1:y2, x1:x2])

    print("\n[0] 投递前输入框内容:", repr(read_input()))

    print("[1] PostMessage 一个 x …")
    post_char(hwnd, "x", VK_X)
    time.sleep(0.35)
    got = read_input()
    print("    输入框内容:", repr(got))
    ok = "x" in got.lower()
    print("    结论:", "✅ PostMessage 能进输入框（零注入通道可用）" if ok else "❌ 进不去，PostMessage 无效")

    print("[2] 不管成功与否，清掉（退格 x3）")
    for _ in range(3):
        post_key(hwnd, VK_BACK)
        time.sleep(0.05)
    time.sleep(0.2)
    print("    清理后:", repr(read_input()))

    # 附：如果 PostMessage 无效，验证是不是"必须窗口有焦点"导致的
    if not ok:
        print("\n[3] 诊断：窗口焦点状态")
        gw = user32.GetForegroundWindow()
        ft = user32.GetWindowThreadProcessId(gw, None)
        print("    前台窗口 =", gw, "本窗口 =", hwnd, "前台线程 =", ft)

    g.close()
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
