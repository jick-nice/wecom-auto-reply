# -*- coding: utf-8 -*-
"""真机端到端验证：识别用真实企业微信截图，发送用真实 SendInput 打进真实群。

分两段：
  A. 直接发一条 ASCII 测试消息 → 截屏 OCR 确认它真的出现在群里（验证发送链路）
  B. 用真实截图里那条「宜居园 3️⃣」派单，跑完整判定链 → 确认群里出现 "3"（验证识别+决策+发送）
所有截图存 _artifacts/。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "client"))

from app import locate, segment                                  # noqa: E402
from app.capture import (Grabber, client_rect_screen, force_foreground,  # noqa: E402
                         is_foreground, pick_wecom_window, restore, set_dpi_aware)
from app.engine import Engine                                    # noqa: E402
from app.ocr import Ocr                                          # noqa: E402
from app.sender import Sender                                    # noqa: E402

ART = ROOT / "_artifacts"
ART.mkdir(exist_ok=True)
IMG = np.array(Image.open(ROOT / "samples" / "s3_clean.png").convert("RGB"))


class FakeGrabber:
    """用真实截图当屏幕；只用于识别，发送走真 Sender。"""

    def __init__(self, frame):
        self.frame = frame

    def grab(self, bbox, copy=False):
        return self.frame


def shot(g, hwnd, area, tag):
    full = np.array(g.grab_window_client(hwnd, copy=True))
    Image.fromarray(np.ascontiguousarray(full[:, :, ::-1])).save(ART / f"{tag}.png")
    return full


def read_bottom(o, g, hwnd, area, n=6):
    full = np.array(g.grab_window_client(hwnd, copy=True))
    crop = full[area.y1:area.y2, area.x1:area.x2]
    lay = segment.Layout()
    rows = segment.analyze(crop, lay)
    lay.learn(rows)
    rows = segment.analyze(crop, lay)
    out = []
    for m in segment.pair_messages(rows)[-n:]:
        who = o.read_name(segment.crop(crop, m.sender_row, pad_x=3, pad_y=6)) if m.sender_row else "(我)"
        txt = o.read_row(segment.crop(crop, m.body_row, pad_x=2, pad_y=2))
        out.append((who, txt))
    return out


def main() -> int:
    set_dpi_aware()
    hwnd = pick_wecom_window()
    if not hwnd:
        print("❌ 没找到企业微信窗口")
        return 1
    restore(hwnd)
    force_foreground(hwnd)
    time.sleep(0.5)
    print("前台 =", is_foreground(hwnd), "客户区 =", client_rect_screen(hwnd))

    g = Grabber()
    o = Ocr()
    o.warmup()

    full = np.array(g.grab_window_client(hwnd, copy=True))
    area = locate.locate(full)
    ox, oy = client_rect_screen(hwnd)[0], client_rect_screen(hwnd)[1]
    print("聊天区 =", area)

    sender = Sender((ox + area.input_center[0], oy + area.input_center[1]))

    # ---------------- A. 发送链路 ----------------
    print("\n=== A. 发送链路测试 ===")
    print("输入框屏幕坐标 =", sender.input_pos)
    cost = sender.send("auto test 1", hwnd=hwnd)
    print(f"发送耗时 {cost:.1f}ms，前台={is_foreground(hwnd)}")
    time.sleep(0.6)
    got = read_bottom(o, g, hwnd, area)
    for w, t in got:
        print(f"   [{w}] {t!r}")
    ok_a = any("auto" in t.lower() or "test" in t.lower() for _, t in got)
    print("A 结果:", "✅ 消息已进入群聊" if ok_a else "❌ 群里没看到我发的内容")
    shot(g, hwnd, area, "e2e_after_send")

    # ---------------- B. 识别 + 决策 + 发送 ----------------
    print("\n=== B. 完整抢单链路（真实派单截图 → 真实发送） ===")
    cfg = {"dispatchers": ["朱洋", "庚收盼"], "my_name": "王琦",
           "dark_thr": 150, "max_orders": 3, "orders_today": 0}
    logs, wins = [], []

    class LiveSender:
        def __init__(self, s):
            self.s = s
            self.input_pos = s.input_pos
            self.sent = []

        def send(self, text, hwnd=None, force_click=False):
            c = self.s.send(text, hwnd=hwnd)
            self.sent.append(text)
            return c

    ls = LiveSender(sender)
    eng = Engine(cfg, o, FakeGrabber(IMG[140:270]), ls,
                 log=lambda m: (logs.append(m), print("   ·", m)),
                 on_win=lambda n: wins.append(n))
    eng.hwnd = hwnd
    eng.area = locate.ChatArea(0, 0, IMG.shape[1], IMG.shape[0], 0, 0, 0, 0)
    eng._origin = (0, 0, IMG.shape[1], IMG.shape[0])

    eng._handle_frame(eng.grabber.frame)
    print("   第 1 帧后 sent =", ls.sent, "（应为空：等第二帧确认）")
    eng._handle_frame(eng.grabber.frame)
    print("   第 2 帧后 sent =", ls.sent)
    ok_b = ls.sent == ["3"]
    time.sleep(0.7)
    got = read_bottom(o, g, hwnd, area)
    for w, t in got:
        print(f"   [{w}] {t!r}")
    ok_b2 = any(t.strip() == "3" for _, t in got)
    print("B 结果:", "✅ 判定为 3 并已发出" if ok_b else "❌ 判定失败",
          "|", "✅ 群里确认出现 3" if ok_b2 else "❌ 群里没看到 3")
    shot(g, hwnd, area, "e2e_after_reply")

    g.close()
    print("\n结论:", "全链路通过" if (ok_a and ok_b and ok_b2) else "需要修")
    return 0 if (ok_a and ok_b and ok_b2) else 2


if __name__ == "__main__":
    sys.exit(main())
