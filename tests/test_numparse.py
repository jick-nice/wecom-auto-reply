# -*- coding: utf-8 -*-
"""用截图里的真实写法跑 numparse 自测"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "client"))
from app.numparse import reply_code, explain  # noqa: E402

CASES = [
    # (派单人消息, 期望回复)
    ("双街3", "3"),
    ("双街 3", "3"),
    ("宜居园 3️⃣", "3"),
    ("宜居园3⃣", "3"),
    ("宜居园３", "3"),
    ("宜居园 ③", "3"),
    ("宜居园 ❸", "3"),
    ("正荣府扣2", "2"),
    ("正荣府 扣 2", "2"),
    ("正荣府2", "2"),
    ("正荣府2️⃣", "2"),
    ("双街新家园 看房叁", "3"),
    ("双街新家园 看房叄", "3"),
    ("双街新家园 看房參", "3"),
    ("双街新家园 看房三", "3"),
    ("双街新家园 看房两", "2"),
    ("双街新家园 看房壹", "1"),
    ("正荣府 扣伍", "5"),
    ("金地格林 扣12", "12"),
    # 干扰项：面积/价格/楼层/房号/时间
    ("正荣府 89平 扣2", "2"),
    ("正荣府二期 3号楼 扣1", "1"),
    ("总价120万 首付38万 扣4", "4"),
    ("正荣府 2室1厅 扣5", "5"),
    ("16:05 宜居园 3️⃣", "3"),
    ("单价23500 宜居园 扣3", "3"),
    # 纯干扰 / 无答案
    ("恭喜于超凡 庚收盼 王金锐 詹正凌前三", None),
    ("[庆祝]恭喜于超凡 庚收盼 王金锐 詹正凌前三且转化率大于大区转化率", None),
    ("刘子真@自如 8625", None),
    # 同事的回复（不是派单，由上层过滤，这里只验证能解析成数字）
    ("3", "3"),
    ("6", "6"),
]


def main():
    ok = bad = 0
    for text, want in CASES:
        got = reply_code(text)
        flag = "OK " if got == want else "FAIL"
        if got == want:
            ok += 1
        else:
            bad += 1
            print(f"{flag} {text!r:<50} got={got!r} want={want!r}")
            print("      " + explain(text).replace("\n", "\n      "))
    print(f"\n{ok} passed, {bad} failed / {len(CASES)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
