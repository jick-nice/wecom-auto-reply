# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client_v33"))

from app.profilepatch import split_dispatchers
from app.strictmatch import base_name, match_dispatcher, order_verdict

LONG = "朱洋（说了多少遍，换号一定要告诉我啊！！）"
assert split_dispatchers(LONG) == [LONG]
assert split_dispatchers(LONG + "，王琦") == [LONG, "王琦"]
assert base_name(LONG) == "朱洋"
assert match_dispatcher("朱洋", [LONG]) == LONG
assert match_dispatcher(LONG, [LONG]) == LONG
assert order_verdict("朱洋", "宜居园扣3", [LONG], "杨煜")[0] == "reply"
print("long_dispatcher_ok")
