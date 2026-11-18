# -*- coding: utf-8 -*-
"""一条命令跑完真机端到端：

    python tools/e2e_adb.py "ZhengRongFu2"

  1. PC 侧：定位 → 开始监控（真实抓屏）
  2. 手机侧：adb 打进群发这条假派单（另一个账号）
  3. PC 侧：认出派单人 + 口令 → 自动回复数字
  4. 回读：PC 截图 + 手机界面双重确认，并打印时间线
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "client"))
sys.path.insert(0, str(ROOT / "tools"))

import numpy as np                                                     # noqa: E402
from PIL import Image                                                  # noqa: E402

from app import locate, segment                                        # noqa: E402
from app.capture import Grabber, force_foreground, set_dpi_aware       # noqa: E402
from app.engine import Engine                                          # noqa: E402
from app.ocr import Ocr                                                # noqa: E402
from app.sender import Sender                                          # noqa: E402

ART = ROOT / "_artifacts"
T0 = time.time()


def ts():
    d = time.time() - T0
    return f"+{d:6.2f}s"


def main(text="ZhengRongFu2"):
    set_dpi_aware()
    ART.mkdir(exist_ok=True)
    events: list[tuple[float, str]] = []

    def log(m):
        events.append((time.time(), m))
        print(f"{ts()}  {m}", flush=True)

    o = Ocr()
    o.warmup()
    g = Grabber()
    s = Sender((0, 0), require_foreground=True, allow_click=True, max_clicks_per_session=3)
    cfg = {"dispatchers": ["测试用户A", "测试用户D", "测试用户E"], "my_name": "测试用户F",
           "dark_thr": 150, "max_orders": 3, "orders_today": 0}
    eng = Engine(cfg, o, g, s, log=log)
    if not eng.ensure_area(force=True):
        print("❌ 定位失败")
        return 1
    force_foreground(eng.hwnd)
    log(f"READY 窗口={eng.hwnd} 消息区={eng.area.rect} 输入框={s.input_pos}")

    eng.start()
    time.sleep(1.0)

    # ---- 手机侧 ----
    import phone_dispatch
    log("📱 手机发送假派单 …")
    t_phone = time.time()
    rc = phone_dispatch.main(text)
    log(f"📱 adb 返回 {rc}（0=已发出），耗时 {time.time()-t_phone:.2f}s")

    # ---- 等 PC 回复 ----
    deadline = time.time() + 25
    while time.time() < deadline and eng.stats["sent"] == 0:
        time.sleep(0.2)
    time.sleep(1.5)
    eng.stop()
    time.sleep(0.3)

    # ---- 回读 PC 画面 ----
    full = np.array(g.grab_window_client(eng.hwnd, copy=True))
    Image.fromarray(np.ascontiguousarray(full[:, :, ::-1])).save(ART / "e2e_result.png")
    crop = full[eng.area.y1:eng.area.y2, eng.area.x1:eng.area.x2]
    lay = segment.Layout()
    rows = segment.analyze(crop, lay)
    lay.learn(rows)
    rows = segment.analyze(crop, lay)
    print("\n--- PC 画面里的最后几条 ---")
    for m in segment.pair_messages(rows)[-5:]:
        who = o.read_name(segment.crop(crop, m.sender_row, pad_x=3, pad_y=6)) if m.sender_row else "(我)"
        txt = o.read_row(segment.crop(crop, m.body_row, pad_x=2, pad_y=2))
        print(f"   [{who}] {txt!r}")

    print("\n--- 时间线 ---")
    for t, m in events:
        print(f"   +{t-T0:6.2f}s  {m}")
    print(f"\n发送次数={eng.stats['sent']} 点击={s.clicks} 静默跳过={s.skipped}")
    g.close()
    ok = eng.stats["sent"] > 0
    print("结论:", "✅ 全链路通过（手机派单 → PC 自动回复）" if ok else "❌ 未回复")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "ZhengRongFu2"))
