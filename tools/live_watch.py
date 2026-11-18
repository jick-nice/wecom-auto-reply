# -*- coding: utf-8 -*-
"""真机只读观察：引擎在真实窗口上跑一段时间，不发任何按键。

验证三件事：
  1. 定位稳定（窗口移动/缩放会自动重算）
  2. 自己的消息（右对齐、无名字行）不会被当成派单 → 不会误发
  3. 稳态帧耗时、有无异常
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "client"))

from app.capture import set_dpi_aware                     # noqa: E402
from app.engine import Engine                             # noqa: E402
from app.ocr import Ocr                                   # noqa: E402


class NullSender:
    """绝对不发：把要发的动作只记下来。"""

    def __init__(self):
        self.input_pos = (0, 0)
        self.calls = []

    def send(self, text, hwnd=None, force_click=False):
        self.calls.append(text)
        return 0.0


def main(seconds=60.0):
    set_dpi_aware()
    logs, seen = [], []
    o = Ocr()
    o.warmup()
    from app.capture import Grabber
    g = Grabber()
    ns = NullSender()
    cfg = {"dispatchers": ["测试用户D", "测试用户E", "测试用户A"], "my_name": "测试用户F",
           "dark_thr": 150, "max_orders": 3, "orders_today": 0}
    eng = Engine(cfg, o, g, ns, log=lambda m: (logs.append(m), print("  ·", m)),
                 on_frame=lambda t: seen.append(t))
    ok = eng.ensure_area(force=True)
    print("定位:", ok, eng.area)
    if not ok:
        return 1

    eng.running = True
    t0 = time.time()
    frames0 = eng.stats["frames"]
    while time.time() - t0 < seconds:
        try:
            f = eng._grab_frame()
        except Exception as e:
            print("抓帧失败:", e)
            time.sleep(0.2)
            continue
        from app.engine import _fp
        fp = _fp(f)
        if fp == eng._last_fp:
            time.sleep(0.01)
            continue
        eng._last_fp = fp
        eng.stats["frames"] += 1
        t = time.perf_counter()
        eng._handle_frame(f)
        dt = (time.perf_counter() - t) * 1000
        print(f"  [变化] 处理 {dt:.1f}ms  frames={eng.stats['frames']}")
        time.sleep(0.01)

    print(f"\n观察 {seconds:.0f}s：变化帧 {eng.stats['frames'] - frames0}，"
          f"发送调用 {len(ns.calls)} 次 {ns.calls}")
    print("识别到的最后几条：")
    for t in seen[-6:]:
        print("   ", t)
    g.close()
    print("\n结论:", "✅ 无异常、无误发" if not ns.calls and not logs[-1].startswith("处理异常") else "⚠️ 需检查")
    return 0


if __name__ == "__main__":
    sys.exit(main(float(sys.argv[1]) if len(sys.argv) > 1 else 60.0))
