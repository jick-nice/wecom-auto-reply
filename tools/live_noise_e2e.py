# -*- coding: utf-8 -*-
"""真机 PC 侧：一直盯着群，把读到的每条都打出来，最后统计回了几条。"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "client"))

from app import locate                                   # noqa: E402
from app.capture import (Grabber, client_rect_screen, force_foreground,  # noqa: E402
                         pick_wecom_window, restore, set_dpi_aware)
from app.engine import Engine                            # noqa: E402
from app.ocr import Ocr                                  # noqa: E402
from app.sender import Sender                            # noqa: E402

ART = ROOT / "_artifacts"


def main(seconds=150):
    set_dpi_aware()
    ART.mkdir(exist_ok=True)
    hwnd = pick_wecom_window()
    restore(hwnd)
    force_foreground(hwnd)
    time.sleep(0.4)
    o = Ocr()
    o.warmup()
    g = Grabber()
    full = np.array(g.grab_window_client(hwnd, copy=True))
    area = locate.locate(full)
    ox, oy, cw, ch = client_rect_screen(hwnd)

    cfg = {
        "dispatchers": ["悲伤土豆"],
        "my_name": "杨煜",
        "max_orders": 3,
        "require_foreground": True,
        "allow_click": True,
        "dark_thr": 150,
    }
    s = Sender((ox + area.input_center[0], oy + area.input_center[1]))
    seen = []
    eng = Engine(cfg, o, g, s, log=lambda m: print(f"  [eng] {m}", flush=True),
                 on_frame=lambda t: seen.append(t) or print(f"  [看到] {t!r}", flush=True))
    eng.start()
    print(f"READY area={area} 等待 {seconds}s", flush=True)

    t_end = time.time() + seconds
    last = None
    while time.time() < t_end:
        time.sleep(1.0)
        if seen and seen[-1] != last:
            last = seen[-1]

    eng.stop()
    time.sleep(0.4)
    print("\n--- 汇总 ---")
    print("读到条数:", len(seen))
    print("发送次数:", eng.stats["sent"], " 点击:", s.clicks)
    print("回过的内容:", getattr(eng, "sent_texts", "见日志"))
    frame = np.array(g.grab_window_client(hwnd, copy=True))
    from PIL import Image
    Image.fromarray(np.ascontiguousarray(frame[:, :, ::-1])).save(ART / "noise_e2e.png")
    g.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 150))
