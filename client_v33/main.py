# -*- coding: utf-8 -*-
"""入口：抢单助手 v3.3（风控加固版）

与原始 main.pyc 的行为一致，唯一区别是**在导入 GUI 之前先加载 app.riskguard**，
把「抢前台 / 置顶 / 高频点击」这几个会触发企业微信「设备环境异常」的入口钳住。

为什么要在最前面：
    Engine._loop 在**开始抢单的那一刻**就会调 set_topmost + force_foreground，
    必须在 GUI/Engine 构造之前完成 patch，否则第一次就已经抢过了。

运行：
    python main.py            正常启动界面
    python main.py --selftest 依赖自检（结果写到 %TEMP%\\autograb_selftest.txt）
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.capture import set_dpi_aware  # noqa: E402

set_dpi_aware()                        # 必须在 tkinter 之前

# 编译版 capture/engine 的跨 DPI 空帧保护；必须早于 GUI/Engine 构造。
try:
    from app import captureguard
    captureguard.apply()
except Exception:
    captureguard = None


def selftest() -> int:
    """不开界面，跑一遍关键依赖自检。"""
    out = os.path.join(os.environ.get("TEMP", "."), "autograb_selftest.txt")
    lines = []
    rc = 0

    def p(*a):
        lines.append(" ".join(str(x) for x in a))

    p("=== 抢单助手 v3.3 selftest ===")

    # 1) 风控补丁
    try:
        from app import riskguard
        st = riskguard.status()
        p(f"[OK] riskguard: {st}")
    except Exception as e:
        rc = 1
        p(f"[FAIL] riskguard: {e}")

    # 2) 抓屏 / 定位
    try:
        from app import capture, locate
        hwnd = capture.pick_wecom_window()
        p(f"[OK] 找到企业微信窗口: {hwnd}")
        if hwnd:
            g = capture.Grabber()
            img = g.grab_window_client(hwnd, copy=True)
            p(f"[OK] PrintWindow: {img.shape}")
            a = locate.locate(img)
            p(f"[OK] 定位: {a} found={a.found}")
    except Exception as e:
        rc = 1
        p(f"[FAIL] 抓屏/定位: {e}")

    # 3) OCR
    try:
        from app.ocr import Ocr
        o = Ocr()
        o.warmup()
        p("[OK] OCR 就绪")
    except Exception as e:
        rc = 1
        p(f"[FAIL] OCR: {e}")

    # 4) 手势 / 单字模板
    try:
        from app.gesture import GestureMatcher
        gm = GestureMatcher()
        gm.build()
        p("[OK] 手势模板就绪")
    except Exception as e:
        p(f"[WARN] 手势模板: {e}")
    try:
        from app.digitmatch import DigitMatcher
        dm = DigitMatcher()
        dm.build()
        p("[OK] 单字模板就绪")
    except Exception as e:
        p(f"[WARN] 单字模板: {e}")

    # 5) 口令解析
    try:
        from app.numparse import reply_code
        cases = {"正荣府扣2": "2", "宜居园 3️⃣": "3", "蔡徐坤✋🏻": "5", "蔡徐坤👌": "3"}
        bad = []
        for t, want in cases.items():
            got = reply_code(t)
            if got != want:
                bad.append(f"{t!r} 期望{want} 实得{got!r}")
        p("[OK] 口令解析" if not bad else f"[FAIL] 口令解析: {bad}")
        if bad:
            rc = 1
    except Exception as e:
        rc = 1
        p(f"[FAIL] 口令解析: {e}")

    # 6) 贴纸数字模板（北风花园 7 这类图片数字）
    try:
        from app import stickerdigit
        okn = 0
        for d in "0123456789":
            m = stickerdigit._render_glyph(d, r"C:\Windows\Fonts\msyh.ttc", 32)
            if m is None:
                continue
            num, _, _ = stickerdigit.classify(m)
            if num == int(d):
                okn += 1
        p(f"[OK] 贴纸数字模板自洽 {okn}/10" if okn == 10
          else f"[WARN] 贴纸数字模板自洽仅 {okn}/10")
    except Exception as e:
        p(f"[WARN] 贴纸数字模板: {e}")

    # 7) 严格派单人匹配 + 派单判定（真实群抗干扰的关键）
    try:
        from app.strictmatch import match_dispatcher as _md, order_verdict as _ov
        from app.strictnum import reply_code_strict as _rcs

        disp = ["悲伤土豆"]
        # 不该被当成派单人
        should_not = ["土豆", "悲伤", "悲", "悲伤土豆丝", "于超凡", "王琦", ""]
        bad_disp = [n for n in should_not if _md(n, disp) is not None]
        # 应该被当成派单人
        should_yes = ["悲伤土豆", "悲伤土豆（小编）", "悲伤土豆2"]
        miss_disp = [n for n in should_yes if _md(n, disp) is None]

        # 派单人闲聊不该回
        chitchat = ["好的收到", "下午2点看房", "客户说要考虑一下", "我先吃饭，一会再回",
                    "我带了3个客户", "明天见", "差不多5点回来"]
        bad_chat = [t for t in chitchat if _rcs(t) is not None]
        # 真派单必须认对
        real = {"正荣府扣2": "2", "北风花园 7": "7", "看房叁": "3",
                "蔡徐坤✋🏻": "5", "ZhengRongFu8": "8"}
        bad_real = [(t, w, _rcs(t)) for t, w in real.items() if _rcs(t) != w]

        problems = []
        if bad_disp:
            problems.append(f"误认派单人{bad_disp}")
        if miss_disp:
            problems.append(f"漏认派单人{miss_disp}")
        if bad_chat:
            problems.append(f"闲聊误回{bad_chat}")
        if bad_real:
            problems.append(f"真派单漏认{bad_real}")

        p("[OK] 严格过滤（派单人/闲聊/真派单）" if not problems
          else f"[FAIL] 严格过滤: {problems}")
        if problems:
            rc = 1

        # 端到端判定抽查
        e2e = [
            ("悲伤土豆", "正荣府扣2", "reply"),
            ("悲伤土豆", "下午2点看房", "skip"),
            ("于超凡", "正荣府扣2", "skip"),
            ("土豆", "正荣府扣2", "skip"),
            ("悲伤土豆丝", "正荣府扣2", "skip"),
        ]
        bad_e2e = []
        for snd, txt, want in e2e:
            act, _ = _ov(snd, txt, disp, "杨煜")
            if act != want:
                bad_e2e.append((snd, txt, want, act))
        p("[OK] 端到端判定抽查" if not bad_e2e else f"[FAIL] 端到端判定: {bad_e2e}")
        if bad_e2e:
            rc = 1
    except Exception as e:
        rc = 1
        p(f"[FAIL] 严格过滤: {e}")

    # 8) 前台守卫 + 准入检查
    try:
        from app import fgguard as _fg
        hwnd3, why3 = _fg.find_wecom()
        if hwnd3:
            p(f"[OK] 找到企业微信窗口 hwnd={hwnd3}，当前前台={_fg.is_foreground(hwnd3)}")
            okc, msgc = _fg.check_chat_area(hwnd3)
            p(f"[OK] 聊天区检查：{msgc}" if okc else f"[WARN] 聊天区检查：{msgc}")
            r0 = None
            try:
                from app import capture as _c2
                r0 = _c2.window_rect(hwnd3)
            except Exception:
                pass
            brought = _fg.bring_to_front(hwnd3)
            p(f"[OK] 前台拉起：{brought}")
            try:
                from app import capture as _c3
                r1 = _c3.window_rect(hwnd3)
                if r0 and r1 and r0 != r1:
                    p(f"[WARN] 拉起后窗口位置被改动了: {r0} -> {r1}")
                else:
                    p("[OK] 拉起后窗口位置未被改动")
            except Exception:
                pass
        else:
            p(f"[WARN] {why3}（此时点开始抢单会被拦住，符合预期）")
    except Exception as e:
        p(f"[WARN] 前台守卫: {e}")

    # 9) GUI 准入补丁
    try:
        from app import guipatch
        from app.gui import App as _App
        okp = guipatch.apply()
        p(f"[OK] 准入补丁已挂载={okp} start_grab来自="
          f"{_App.start_grab.__code__.co_filename.split(chr(92))[-1]}")
    except Exception as e:
        p(f"[WARN] 准入补丁: {e}")

    # 9.5) 表情表
    try:
        from app import emojimap2 as _em2
        tb = _em2.load_table()
        if tb:
            desc = ", ".join(f"{t.get('name')}→{t.get('code')}" for t in tb)
            p(f"[OK] 表情表 {len(tb)} 项：{desc}")
        else:
            p("[WARN] 表情表为空（%APPDATA%\\AutoGrab\\emoji_map.json 不存在）")
    except Exception as e:
        p(f"[WARN] 表情表: {e}")

    # 10) 版式学习 + 贴纸补数字（真机链路）
    try:
        from app import capture as _cap
        from app.engine import Engine as _E

        class _NullSender:
            input_pos = (0, 0)
            clicks = 0
            allow_click = False
            max_clicks_per_session = 3
            auto_foreground = False
            require_foreground = True
            skipped = 0
            def send(self, *a, **k):
                return 0.0

        hwnd2 = _cap.pick_wecom_window()
        if hwnd2:
            g2 = _cap.Grabber()
            e2 = _E({"dark_thr": 150}, None, g2, _NullSender(), log=lambda *_a: None)
            if e2.ensure_area(force=True):
                p(f"[OK] 版式学习: name_x={e2.layout.name_x} content_x={e2.layout.content_x}")
                if e2.layout.name_x is None:
                    p("[WARN] 版式没学到锚点，可能消息区为空")
    except Exception as e:
        p(f"[WARN] 版式学习: {e}")

    text = "\n".join(lines)
    try:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception:
        pass
    print(text)
    return rc


BUILD_ID = "v3.3.0"


def verify_build() -> int:
    """Verify the shipped runtime without opening windows or sending messages."""
    import json
    import time
    from pathlib import Path

    result = {"build_id": BUILD_ID, "executable": sys.executable, "ok": False}
    try:
        from app import riskguard, guipatch, profilepatch, emojimap2
        from app.gui import App
        if not (riskguard.apply() and guipatch.apply() and profilepatch.apply()):
            raise RuntimeError("启动补丁加载失败")
        result["foreground"] = riskguard.status()["auto_foreground"]
        if result["foreground"]:
            raise RuntimeError("前台策略仍是自动抢回")
        table = emojimap2.load_table()
        result["gesture_codes"] = [str(item["code"]) for item in table]
        if result["gesture_codes"][:4] != ["3", "2", "5", "2"]:
            raise RuntimeError("固定手势模板未生效")
        result["startup_module"] = App.start_grab.__module__
        if result["startup_module"] != "app.profilepatch":
            raise RuntimeError("启动取消补丁未生效")
        from app.ocr import Ocr
        started = time.perf_counter()
        ocr = Ocr()
        ocr.warmup()
        result["ocr_seconds"] = round(time.perf_counter() - started, 3)
        result["ok"] = True
    except Exception as exc:
        result["error"] = str(exc)
    index = sys.argv.index("--verify-build")
    output = Path(sys.argv[index + 1]) if len(sys.argv) > index + 1 else Path(sys.executable).with_suffix(".check.json")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    if "--verify-build" in sys.argv:
        raise SystemExit(verify_build())
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())

    # ★ 关键：先上风控补丁，再开界面
    try:
        from app import riskguard  # noqa: F401
    except Exception:
        pass

    from app.gui import App

    # ★ GUI 补丁：开始抢单的准入检查（企业微信必须已打开 + 停在群聊 + 强制前台）
    #   必须在 App 实例化之前打，否则按钮绑的还是旧方法
    from app import guipatch, profilepatch
    guipatch.apply()
    if not profilepatch.apply():
        raise RuntimeError("账号资料隔离补丁初始化失败")

    _enter_main = App.enter_main

    def enter_main_versioned(self, *args, **kwargs):
        _enter_main(self, *args, **kwargs)
        self.title(f"抢单助手 修复版 {BUILD_ID}")
        self._log(f"🛠 当前版本：{BUILD_ID}；手势固定映射 3/2/5/2；无停止浮窗；不自动抢回前台")
        self._log(f"📦 程序路径：{sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__)}")

    App.enter_main = enter_main_versioned
    app = App()
    app.title(f"抢单助手 修复版 {BUILD_ID}")
    app.mainloop()