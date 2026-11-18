# -*- coding: utf-8 -*-
"""用截图里的真实写法跑 numparse 自测"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "client"))
from app.numparse import reply_code, explain  # noqa: E402

CASES = [
    # (派单人消息, 期望回复)
    ("测试项目C3", "3"),
    ("测试项目C 3", "3"),
    ("测试项目 3️⃣", "3"),
    ("测试项目3⃣", "3"),
    ("测试项目３", "3"),
    ("测试项目 ③", "3"),
    ("测试项目 ❸", "3"),
    ("测试项目A扣2", "2"),
    ("测试项目A 扣 2", "2"),
    ("测试项目A2", "2"),
    ("测试项目A2️⃣", "2"),
    ("测试项目B 看房叁", "3"),
    ("测试项目B 看房叄", "3"),
    ("测试项目B 看房參", "3"),
    ("测试项目B 看房三", "3"),
    ("测试项目B 看房两", "2"),
    ("测试项目B 看房壹", "1"),
    ("测试项目A 扣伍", "5"),
    ("金地格林 扣12", "12"),
    # 干扰项：面积/价格/楼层/房号/时间
    ("测试项目A 89平 扣2", "2"),
    ("测试项目A二期 3号楼 扣1", "1"),
    ("总价120万 首付38万 扣4", "4"),
    ("测试项目A 2室1厅 扣5", "5"),
    ("16:05 测试项目 3️⃣", "3"),
    ("单价23500 测试项目 扣3", "3"),
    # 纯干扰 / 无答案
    ("恭喜测试用户B 测试用户E 测试用户G 测试用户H前三", None),
    ("[庆祝]恭喜测试用户B 测试用户E 测试用户G 测试用户H前三且转化率大于大区转化率", None),
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
