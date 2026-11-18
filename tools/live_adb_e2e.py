# -*- coding: utf-8 -*-
"""真机端到端：手机端（另一账号）发假派单 → PC 识别 → PC 自动回复。

PC 侧跑这个脚本，手机侧由调用方用 adb 发送。
全程只允许最少的输入事件（不点无谓的鼠标），符合低风险发送策略。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "client"))

from app.capture import Grabber, force_foreground, set_dpi_aware   # noqa: E402
from app.engine import Engine                                      # noqa: E402
from app.ocr import Ocr                                            # noqa: E402
from app.sender import Sender                                      # noqa: E402


def stamp() -> str:
    t = time.time()
    return time.strftime("%H:%M:%S", time.localtime(t)) + f".{int(t * 1000) % 1000:03d}"


def main(seconds=90.0):
    set_dpi_aware()
    logs, frames = [], []

    def log(m):
        line = f"[{stamp()}] {m}"
        print(line, flush=True)
        logs.append(line)

    def on_frame(t):
        frames.append((stamp(), t))

    o = Ocr()
    o.warmup()
    g = Grabber()
    s = Sender((0, 0), require_foreground=True, allow_click=True, max_clicks_per_session=3)
    cfg = {"dispatchers": ["悲伤土豆", "朱洋", "庚收盼"], "my_name": "杨煜",
           "dark_thr": 150, "max_orders": 3, "orders_today": 0}
    eng = Engine(cfg, o, g, s, log=log, on_frame=on_frame)
    if not eng.ensure_area(force=True):
        print("❌ 定位失败")
        return 1
    if not force_foreground(eng.hwnd):
        log("⚠️ 未能切到前台")
    log(f"READY hwnd={eng.hwnd} area={eng.area} input={s.input_pos}")
    log("等待派单 …（现在用手机发）")

    eng.start()
    t0 = time.time()
    try:
        while time.time() - t0 < seconds:
            time.sleep(0.5)
            if eng.stats["sent"] > 0:
                time.sleep(1.2)                       # 留时间让消息渲染出来
                break
    finally:
        eng.stop()
        time.sleep(0.3)

    print("\n--- 汇总 ---")
    print("发送次数:", eng.stats["sent"], " OCR 次数:", eng.stats["ocr"],
          " 点击次数:", s.clicks, " 静默跳过:", s.skipped)
    print("看到的最后几条:")
    for ts, t in frames[-6:]:
        print(f"   {ts}  {t}")
    g.close()
    return 0 if eng.stats["sent"] else 2


if __name__ == "__main__":
    sys.exit(main(float(sys.argv[1]) if len(sys.argv) > 1 else 90.0))
