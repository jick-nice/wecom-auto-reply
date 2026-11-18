# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client_v33"))

from app.profilepatch import split_dispatchers
from app.strictmatch import base_name, match_dispatcher, order_verdict

LONG = "测试用户D（说了多少遍，换号一定要告诉我啊！！）"
assert split_dispatchers(LONG) == [LONG]
assert split_dispatchers(LONG + "，测试用户C") == [LONG, "测试用户C"]
assert base_name(LONG) == "测试用户D"
assert match_dispatcher("测试用户D", [LONG]) == LONG
assert match_dispatcher(LONG, [LONG]) == LONG
assert order_verdict("测试用户D", "测试项目扣3", [LONG], "测试用户F")[0] == "reply"
print("long_dispatcher_ok")
