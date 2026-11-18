# -*- coding: utf-8 -*-
"""引擎判定链验证：用合成测试图当"屏幕"，不碰真窗口、不发真按键。

规则（按使用者的说明）：派单人在派单之后第一个 @ 的人，就是抢到这一单的人。
"""
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "client"))

from app.engine import Engine          # noqa: E402
from app.locate import ChatArea        # noqa: E402
from app.ocr import Ocr                # noqa: E402

IMG = np.array(Image.open(ROOT / "samples" / "s3_clean.png").convert("RGB"))
W, H = 1658, 818

# s3_clean.png 实测版式
#   y=157 名字 测试用户D   y=208 内容「测试项目 3️⃣」   y=301 测试用户B / y=360「4」
#   y=445 测试用户C / y=504「3」   y=589 测试用户B / y=648「3」   y=733 测试用户D / y=789「@测试用户C」
FRAME_DISPATCH = IMG[140:270]
FRAME_AT_ME = IMG[720:818]
FRAME_OTHER = IMG[290:400]


class FakeGrabber:
    def __init__(self, frame):
        self.frame = frame

    def grab(self, bbox, copy=False):
        return self.frame


class FakeSender:
    def __init__(self):
        self.sent = []
        self.last_cost_ms = 0.0
        self.input_pos = (0, 0)

    def send(self, text, hwnd=None, force_click=False, read_input=None):
        self.sent.append(text)
        return 9.9


def make_engine(frame, **over):
    cfg = {
        "strip_h": 340, "dispatchers": ["测试用户D", "测试用户E"], "my_name": "测试用户C",
        "max_orders": 3, "orders_today": 0, "dark_thr": 150,
    }
    cfg.update(over)
    sender = FakeSender()
    logs, wins, stops = [], [], []
    eng = Engine(cfg, Ocr(), FakeGrabber(frame), sender,
                 log=lambda m: logs.append(m), on_win=lambda n: wins.append(n),
                 on_auto_stop=lambda r: stops.append(r))
    # 测试里绕开真窗口：直接给定消息区
    eng.ensure_area = lambda force=False: True
    eng._origin = (0, 0, W, H)
    eng.area = ChatArea(0, 0, W, H, 100, H - 100, W - 100, H - 60)
    eng.sender.input_pos = (800, H - 80)
    return eng, sender, logs, wins, stops


def run(eng, times=2):
    for _ in range(times):
        eng._handle_frame(eng.grabber.frame)


def main() -> int:
    fails = 0

    def check(name, cond, extra=""):
        nonlocal fails
        print(f"{'✅' if cond else '❌'} {name} {extra}")
        if not cond:
            fails += 1

    # 1. 派单 → 回 3（连续两帧确认，防半截渲染）
    eng, sender, logs, wins, stops = make_engine(FRAME_DISPATCH)
    run(eng, 1)
    check("单帧不发送（等第二帧确认）", sender.sent == [])
    run(eng, 1)
    check("派单「测试项目 3️⃣」→ 发出 '3'", sender.sent == ["3"], f"实际={sender.sent}")
    run(eng, 3)
    check("同一单不重复发", sender.sent == ["3"], f"实际={sender.sent}")
    check("发完之后进入等 @ 状态", eng.pending_dispatch is True)

    # 2. 派单人 @我（前面有派单）→ 记一单
    eng, sender, logs, wins, stops = make_engine(FRAME_AT_ME)
    eng.pending_dispatch, eng.pending_at = True, time.time()
    run(eng, 2)
    check("派单后 @测试用户C → 判定抢到", wins == [1], f"wins={wins}")
    run(eng, 3)
    check("同一条 @ 只记一次", wins == [1], f"wins={wins}")
    check("判定后清空等 @ 状态", eng.pending_dispatch is False)

    # 3. 没有派单 precede 的 @ → 不算抢到
    eng, sender, logs, wins, stops = make_engine(FRAME_AT_ME)
    run(eng, 3)
    check("没有派单时的 @ 不计数", wins == [], f"wins={wins}")

    # 4. 同事的回复（非派单人）→ 不发送
    eng, sender, logs, wins, stops = make_engine(FRAME_OTHER)
    run(eng, 3)
    check("别人回的 '4' 不当派单", sender.sent == [], f"实际={sender.sent}")

    # 5. 抢满上限 → 自动停止
    eng, sender, logs, wins, stops = make_engine(FRAME_AT_ME, max_orders=1)
    eng.pending_dispatch, eng.pending_at = True, time.time()
    run(eng, 2)
    check("抢满 1 单自动停止", stops != [] and eng.running is False, f"stops={stops}")

    # 6. 额度用尽 → 派单也不发
    eng, sender, logs, wins, stops = make_engine(FRAME_DISPATCH, orders_today=3, max_orders=3)
    run(eng, 3)
    check("额度用尽后不再发送", sender.sent == [], f"实际={sender.sent}")

    print(f"\n{'全部通过' if not fails else str(fails) + ' 项失败'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
