# -*- coding: utf-8 -*-
"""验证客户端 api.py 与后端契约一致（跑在 backend/dev-server.js 的 MOCK 模式上）"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "client"))

from app.api import Api, ApiError  # noqa: E402

BASE = "http://127.0.0.1:3100/"


def main() -> int:
    a = Api(BASE)
    print("1) 错密码登录 …")
    try:
        a.login("demo", "wrong")
        print("   ❌ 竟然成功了")
        return 1
    except ApiError as e:
        print(f"   ✅ 正确拒绝: {e}")

    print("2) 正常登录 …")
    body = a.login("demo", "demo123")
    print(f"   ✅ token={a.token} ent={body.get('ent')} quota={body.get('quota')} ann={len(body.get('ann') or [])}")
    assert body.get("ent", {}).get("allowed") is True

    print("3) 心跳 …")
    ok, msg, ent = a.allowed()
    print(f"   ✅ allowed={ok} msg={msg!r}")

    print("4) 报单 3 次 …")
    for i in range(3):
        r = a.report_order(str(i + 1), "测试项目")
        print(f"   ✅ quota={r.get('quota')}")

    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
