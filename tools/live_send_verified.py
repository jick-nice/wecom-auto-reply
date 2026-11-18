# -*- coding: utf-8 -*-
"""最终验证：新发送链路（打字 → 校验输入框真的收到 → 才按回车）在真机跑一次。

只发一条、最多点一次鼠标，随后立刻进入被动观察，看会不会再弹「设备环境异常」。
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "client"))

from app import locate, segment                                  # noqa: E402
from app.capture import (Grabber, force_foreground, is_foreground,  # noqa: E402
                         pick_wecom_window, restore, set_dpi_aware)
from app.engine import Engine                                    # noqa: E402
from app.ocr import Ocr                                          # noqa: E402
from app.sender import Sender                                    # noqa: E402

ART = ROOT / "_artifacts"
CODE = "4"


def main():
    set_dpi_aware()
    ART.mkdir(exist_ok=True)
    hwnd = pick_wecom_window()
    restore(hwnd)
    force_foreground(hwnd)
    time.sleep(0.4)
    print("前台 =", is_foreground(hwnd))

    o = Ocr()
    o.warmup()
    g = Grabber()
    full = np.array(g.grab_window_client(hwnd, copy=True))
    area = locate.locate(full)
    print("聊天区 =", area)

    # 复用一个空跑的 Engine 只为了拿 _input_reader / _origin
    eng = Engine({"dispatchers": ["x"], "my_name": "杨煜"}, o, g,
                 Sender((0, 0)), log=lambda m: None)
    eng.hwnd = hwnd
    eng.area = area
    from app.capture import client_rect_screen
    ox, oy, cw, ch = client_rect_screen(hwnd)
    eng._origin = (ox, oy, cw, ch)
    eng.sender.input_pos = (ox + area.input_center[0], oy + area.input_center[1])
    print("输入框点击点 =", eng.sender.input_pos)

    read = eng._input_reader()
    print("发送前输入框内容 =", repr(read()))

    t = time.perf_counter()
    try:
        cost = eng.sender.send(CODE, hwnd=hwnd, read_input=read)
        print(f"✅ 发送成功 {cost:.1f}ms  实际点击={eng.sender.clicks} 次")
    except Exception as e:
        print(f"❌ 发送被拒: {type(e).__name__}: {e}  点击={eng.sender.clicks}")
    total = (time.perf_counter() - t) * 1000
    print(f"端到端（判定→发出）{total:.1f}ms")

    time.sleep(0.8)
    full = np.array(g.grab_window_client(hwnd, copy=True))
    Image.fromarray(np.ascontiguousarray(full[:, :, ::-1])).save(ART / "verified_send.png")
    crop = full[area.y1:area.y2, area.x1:area.x2]
    lay = segment.Layout()
    rows = segment.analyze(crop, lay)
    lay.learn(rows)
    rows = segment.analyze(crop, lay)
    print("--- 画面里最后几条 ---")
    for m in segment.pair_messages(rows)[-4:]:
        who = o.read_name(segment.crop(crop, m.sender_row, pad_x=3, pad_y=6)) if m.sender_row else "(我)"
        print(f"   [{who}] {o.read_row(segment.crop(crop, m.body_row, pad_x=2, pad_y=2))!r}")

    # ---- 被动观察：只抓屏，看会不会弹验证页 ----
    print("\n进入 180s 被动观察（零输入）…")
    hwnd0 = pick_wecom_window()
    n = 0
    t_end = time.time() + 180
    while time.time() < t_end:
        n += 1
        if pick_wecom_window() != hwnd0:
            print(f"🚨 第 {n} 次观察时窗口句柄变了 → 可能又弹验证页")
            g.close()
            return 2
        try:
            f = np.array(g.grab_window_client(hwnd0, copy=True))
            if not locate.locate(f).found:
                print(f"🚨 第 {n} 次观察定位失败 → 可能进入验证页")
                g.close()
                return 2
        except Exception as e:
            print("抓屏异常", e)
        time.sleep(2.0)
    print(f"✅ 观察 {n} 次，没有再出现验证页")
    g.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
