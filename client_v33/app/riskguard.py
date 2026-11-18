# -*- coding: utf-8 -*-
"""风控加固补丁：在 app 包导入后强制收禁一切「主动抢焦点 / 高频模拟输入」行为。

为什么要这一层（而不是改 engine.py）：
    engine / capture 是 v3.3 编译出来的 .pyc，反编译器（pycdc）对 3.11 的
    deep 分支支持不完整（engine 直接 Unexpected end of stream），整文件反编译
    风险过高。而风控的触发点其实只有很小的几个入口，在运行时把它们钳住即可，
    不动原模块一行逻辑。

被钳住的四个入口（都是真机实测会触发「设备环境异常」的）：
    ① capture.force_foreground  —— 主动抢前台（SetForegroundWindow）
    ② capture.set_topmost       —— 企业微信钉在最上层（会抢走点击/焦点）
    ③ Sender.auto_foreground    —— 发送前再抢一次前台 + 6×30ms 轮询
    ④ Sender.max_clicks_per_session —— 会话内点击总次数（硬上限 3）

用法：在 main.pyc 之前 import app.riskguard（或在 app/__init__.py 里 import）。
本文件是**纯 .py**，可直接编辑，不受 .pyc 限制。
"""
from __future__ import annotations

import os
import time

# ---------------------------------------------------------------- 开关
# 前台策略（真机需求变更）：
#   用户明确要求「点开始抢单后强制企业微信在前台，抢单期间必须保持前台」。
#   所以这里**允许**抢前台，但做了三处收敛，避免变成"高频抢焦点"的机器特征：
#     ① 只在检测到不在前台时才抢（不是每单都抢）
#     ② 补抢有最小间隔 REGrab_MIN_INTERVAL（1.5s）
#     ③ 抢不动就放弃这一单，不做无限重试
# 启动时的显式切换由 guipatch 完成；监控和发送均不自动夺取焦点。
_AUTO_FG = False
# 想临时放开点击上限时设 AUTOGRAB_MAX_CLICKS=N（默认 3）
_MAX_CLICKS = int(os.environ.get("AUTOGRAB_MAX_CLICKS", "3") or 3)
# 上限的绝对天花板：配置里写多大都压不过它
CLICK_CEILING = 3

_applied = False


class _ImageBody(str):
    """Display OCR text while carrying a validated image command separately."""

    def __new__(cls, text, code):
        value = super().__new__(cls, text)
        value.image_code = str(code)
        return value


# 同一条派单的永久去重窗口（秒）。
# v3.3 里是 30 秒 —— 真机实测：14:53:33 回了「阿狸低等级5」，32 秒后
# 14:54:05 又把同一条历史消息当新派单回了一次。抢单场景里同一条派单
# 绝不该回第二次，所以改成 6 小时（远超任何一场群里的有效派单时长）。
DEDUP_WINDOW_S = 6 * 3600

# 两次「自动发送」之间的最小间隔（秒）。真机日志显示一秒内连发 3 条，
# 这是最能引爆「设备环境异常」的形态。取 2.0s：既不至于漏单（派单间隔通常
# 远大于此），又能把"高频连打"压成"人手速"。设 AUTOGRAB_MIN_SEND_GAP=0 可关闭。
MIN_SEND_GAP_S = float(os.environ.get("AUTOGRAB_MIN_SEND_GAP", "2.0") or 2.0)


def apply() -> bool:
    """给 capture / sender 打上加固补丁。可重复调用。"""
    global _applied
    if _applied:
        return True

    # One shared crop keeps OCR, message keys and startup baseline identical.
    from . import bodycrop
    bodycrop.apply()

    # The compiled engine indexes self._origin before checking it. On another DPI,
    # minimized, or still-rendering WeCom window that value can be temporarily None.
    # Convert that transient state into a quiet retry instead of a hot error loop.
    try:
        from . import engine as _capture_engine
        _CaptureEngine = getattr(_capture_engine, "Engine", None)
        if _CaptureEngine is not None and not getattr(_CaptureEngine, "_riskguard_frame_guard", False):
            _original_grab_frame = getattr(_CaptureEngine, "_grab_frame", None)
            _FrameBusy = getattr(_capture_engine, "FrameBusy", RuntimeError)

            def _valid_capture_state(obj):
                origin = getattr(obj, "_origin", None)
                area = getattr(obj, "area", None)
                return (isinstance(origin, (tuple, list)) and len(origin) >= 4
                        and all(isinstance(v, (int, float)) for v in origin[:4])
                        and area is not None)

            def _guarded_grab_frame(self, *args, **kwargs):
                if not _valid_capture_state(self):
                    try:
                        self.ensure_area(force=True, quiet=True)
                    except Exception:
                        pass
                if not _valid_capture_state(self):
                    now = time.monotonic()
                    last = getattr(self, "_frame_guard_notice", 0.0)
                    if now - last >= 5.0:
                        self._frame_guard_notice = now
                        try:
                            self.log("⏳ 企业微信客户区暂不可用，正在重新检测窗口…")
                        except Exception:
                            pass
                    self.grab_mode = "waiting-area"
                    raise _FrameBusy("客户区坐标暂不可用，等待重试")
                try:
                    frame = _original_grab_frame(self, *args, **kwargs)
                except TypeError as exc:
                    if "NoneType" in str(exc) or "subscriptable" in str(exc):
                        self.grab_mode = "waiting-area"
                        raise _FrameBusy("客户区坐标在抓帧期间失效，等待重试") from exc
                    raise
                if frame is None:
                    self.grab_mode = "waiting-area"
                    raise _FrameBusy("抓帧返回空画面，等待重试")
                return frame

            _CaptureEngine._grab_frame = _guarded_grab_frame
            _CaptureEngine._riskguard_frame_guard = True
    except Exception:
        pass

    # ------------------------------------------------ ① 抢前台
    # 现在**允许**抢前台（用户明确要求），但走 fgguard 的限频实现：
    #   · 已经在前台 → 立刻返回，不产生任何调用
    #   · 不在前台   → 1.5 秒内最多抢一次
    #   · 全程不模拟按键（AttachThreadInput 是纯状态操作）
    try:
        from . import capture, fgguard

        def force_foreground(hwnd, *a, **kw):
            # Never pull the chat window over the control UI.
            return False

        capture.force_foreground = force_foreground

        # ------------------------------------------------ ② 置顶
        # 置顶会让企业微信盖住用户正在操作的一切窗口，仍然**默认不做**。
        # 用户要的是"保持在前台"，不是"永远最上层"——前者由 fgguard 保证。
        _orig_top = getattr(capture, "set_topmost", None)

        def set_topmost(hwnd, on=True, *a, **kw):
            # 只在"抢单期间要把它拉回来"这个语义下允许，且不真正置顶
            return None

        capture.set_topmost = set_topmost
    except Exception:
        pass

    # ------------------------------------------------ ③④ Sender
    try:
        from . import sender as _sender

        _Sender = getattr(_sender, "Sender", None)
        if _Sender is not None:
            # 注意：这里必须用**唯一的闭包变量名**（_orig_sender_init）。
            # 之前和 engine patch 共用 _orig_init，后者覆盖了前者，
            # 导致 Sender(...) 实际调到 Engine.__init__（TypeError）。
            _orig_sender_init = _Sender.__init__

            def _sender_init(self, *a, **kw):
                # 不在前台就不发；先做前台校验，再允许有限输入框聚焦点击。
                kw["auto_foreground"] = bool(_AUTO_FG)
                # 已校准输入框坐标后允许受硬上限保护的聚焦点击；不允许无坐标盲点。
                # 前台校验仍在 Sender.send 内先执行，点击次数仍由 CLICK_CEILING 限制。
                kw["allow_click"] = True
                kw["max_clicks_per_session"] = min(
                    int(kw.get("max_clicks_per_session", CLICK_CEILING) or 0),
                    CLICK_CEILING)
                # 点击次数硬钳制（传 200 也压成 3）
                n = int(kw.get("max_clicks_per_session", CLICK_CEILING) or 0)
                kw["max_clicks_per_session"] = max(0, min(n, min(_MAX_CLICKS, CLICK_CEILING)))
                # 不在前台就不发，永不绕过
                kw["require_foreground"] = True
                _orig_sender_init(self, *a, **kw)
                # 双保险：即使调用方事后改属性，也钳一次
                try:
                    self.auto_foreground = bool(_AUTO_FG)
                    self.max_clicks_per_session = max(
                        0, min(int(self.max_clicks_per_session), min(_MAX_CLICKS, CLICK_CEILING)))
                except Exception:
                    pass

            _Sender.__init__ = _sender_init

            # 双保险 2：_ensure_focus 到上限直接拒绝（原 v3.3 会 clicks=0 归零重来）
            _orig_ensure = getattr(_Sender, "_ensure_focus", None)
            if _orig_ensure is not None:
                def _sender_ensure_focus(self, read_input=None):
                    try:
                        if int(getattr(self, "clicks", 0)) >= int(
                                getattr(self, "max_clicks_per_session", 0)):
                            return False       # ★ 不归零、不重来
                        if not getattr(self, "allow_click", False):
                            return False
                    except Exception:
                        pass
                    return _orig_ensure(self, read_input)

                _Sender._ensure_focus = _sender_ensure_focus
    except Exception:
        pass

    # ------------------------------------------------ ⑤ 去重窗口（永不过期）
    # engine._judge 里的判重写死为：`key in self._sent and time.time() - self._sent[key] < 30`
    # 真机实测：14:53:33 回了「阿狸低等级5」，14:54:05（32 秒后）又把同一条历史
    # 消息当新派单回了一次 —— 30 秒一到，key 还在字典里但窗口已过，于是重发。
    #
    # 做法：把 _sent 换成「值永远等于当前时间」的 dict 子类 →
    # `time.time() - _sent[key]` 恒为 ~0，永远 < 30，等价于永久去重。
    # 不动 _judge 一行逻辑，也不改它的编译常量。
    try:
        from . import engine as _engine

        _E = getattr(_engine, "Engine", None)
        if _E is not None and not getattr(_E, "_riskguard_dedup", False):
            class _PinnedSent(dict):
                """已发送记录：key 永远"刚刚发过"。

                engine 只做两件事：`key in sent` 和 `time.time() - sent[key] < 30`。
                让 __getitem__ 返回当前时刻，第二个条件就永远成立。
                写入照常保存（供 on_frame/日志展示），但读出来总是"现在"。
                """

                __slots__ = ()

                def __getitem__(self, k):
                    if dict.__contains__(self, k):
                        return time.time()
                    raise KeyError(k)

                def get(self, k, default=None):
                    return time.time() if dict.__contains__(self, k) else default

            # 闭包变量名必须唯一（不能叫 _orig_init，会与别的 patch 冲突）
            _orig_engine_init = _E.__init__

            def _engine_init(self, *a, **kw):
                _orig_engine_init(self, *a, **kw)
                try:
                    old = getattr(self, "_sent", None) or {}
                    pinned = _PinnedSent()
                    for k, v in dict(old).items():
                        pinned[k] = v
                    self._sent = pinned
                except Exception:
                    pass

            _E.__init__ = _engine_init
            _E._riskguard_dedup = True
    except Exception:
        pass

    # ------------------------------------------------ ⑥ 发送节流
    # 同一秒内连发 3~4 条是最危险的形态（高频模拟输入）。给两次发送之间
    # 加最小间隔；被节流的这一单直接放弃并提示人工，宁可不抢也不引爆风控。
    try:
        from . import engine as _engine

        _E = getattr(_engine, "Engine", None)
        if _E is not None and not getattr(_E, "_riskguard_throttle", False):
            _orig_judge = getattr(_E, "_judge", None)
            if _orig_judge is not None:
                def _judge(self, *a, **kw):
                    # 取本次候选的 key（_judge 的 key 形参或第 5 个位置参数）
                    key = kw.get("key")
                    if key is None and len(a) >= 5:
                        key = a[4]
                    try:
                        sent = getattr(self, "_sent", None)
                        already = bool(key is not None and sent is not None and key in sent)
                    except Exception:
                        already = False

                    last = float(getattr(self, "_rg_last_send", 0.0) or 0.0)
                    if os.environ.get("AUTOGRAB_TRACE_JUDGE") == "1":
                        try:
                            self.log(f"[trace] judge key={key.hex()[:8] if isinstance(key, bytes) else key} "
                                     f"already={already} last={last:.1f}")
                        except Exception:
                            pass
                    if (not already) and last and (time.time() - last) < MIN_SEND_GAP_S:
                        # 节流：这一帧先不发，但**不能丢单** —— 交给界面提示人工补，
                        # 同时不写 _sent（下一帧到点后仍会被正常回复）。
                        short = max(0.0, MIN_SEND_GAP_S - (time.time() - last))
                        try:
                            if getattr(self, "_rg_throttle_notice_key", None) != key:
                                self._rg_throttle_notice_key = key
                                self.log(
                                    f"⏳ 距上次发送仅 {time.time() - last:.1f}s（<{MIN_SEND_GAP_S}s），"
                                    f"约 {short:.1f}s 后再处理；本单先挂起")
                        except Exception:
                            pass
                        # 把重检时间往后推，让引擎在间隔过去后自动再看一眼
                        try:
                            self._recheck_at = time.time() + short + 0.05
                        except Exception:
                            pass
                        return None

                    r = _orig_judge(self, *a, **kw)
                    try:
                        # _judge 成功发送后 last_sent_at 会更新
                        if float(getattr(self, "last_sent_at", 0.0) or 0.0) > last:
                            self._rg_last_send = time.time()
                    except Exception:
                        pass
                    return r

                _E._judge = _judge
                _E._riskguard_throttle = True
    except Exception:
        pass

    # ------------------------------------------------ ⑦ 贴纸/表情数字识别
    # v3.3 最大的功能缺口：`北风花园 7`（数字是**图片贴纸**，不是文字）会被
    # OCR 读成 `北风花园` → 解析不出号码 → 一条都不回。真机实测确认。
    #
    # 修法：接管 Engine._read_body 的返回值，在 OCR 文本后面补上「贴纸里认出的数字」。
    # 只对通过 is_solid_block 判定（实心块，不是汉字笔画）的区域做，避免误判。
    try:
        from . import engine as _engine

        _E = getattr(_engine, "Engine", None)
        if _E is not None and not getattr(_E, "_riskguard_sticker", False):
            _orig_read_body = getattr(_E, "_read_body", None)
            if _orig_read_body is not None:
                def _read_body(self, frame, msg):
                    text = _orig_read_body(self, frame, msg)
                    try:
                        from . import segment as _seg, sticker as _sticker
                        from .numparse import reply_code as _rc
                        from .strictnum import has_idiom_number, has_strong_signal

                        body = _seg.crop(frame, msg.body_row, pad_x=2, pad_y=2)
                        if body.size == 0:
                            return text

                        text_code = _rc(text)

                        # ★ 关键修复（真机 bug）：
                        #   '八格牙路✌️' 的 OCR 结果是 '八格牙路 鸡'，
                        #   numparse 把「八」解析成 8 → 若此时直接 return，
                        #   表情识别就永远不会执行，于是回了 8（应为 ✌️=2）。
                        #
                        #   规则改成：
                        #     ① 文字里有**强信号**（扣X/看房X）→ 文字优先，不再看表情
                        #     ② 文字的数字来自**词内中文数字**（八格牙路/五花八门…）→ 不可信，
                        #        继续跑表情识别，表情结果优先
                        #     ③ 其余情况文字有数字 → 文字优先
                        text_trusted = text_code is not None
                        if text_trusted and not has_strong_signal(text):
                            if has_idiom_number(text):
                                text_trusted = False

                        # ① 贴纸数字（棕色圆角块里的白数字）
                        ocr = getattr(self, "ocr", None)
                        if ocr is not None:
                            for _box, t in _sticker.scan(ocr, body):
                                t = (t or "").strip()
                                if t and t.isdigit() and t not in (text or ""):
                                    if text_trusted:
                                        # 文字也可信 → 保留文字（贴纸只是补充展示）
                                        return text
                                    return f"{text} {t}".strip()

                        # ② 表情表（黄色/蓝色手势表情，按表里的固定映射）
                        try:
                            from . import emojimap2 as _em
                            code, _feat, why = _em.classify_row(body)
                            if code and not has_strong_signal(text):
                                image_key = _engine._fp(body)
                                notices = getattr(self, "_rg_image_notices", None)
                                if notices is None:
                                    notices = self._rg_image_notices = {}
                                if image_key not in notices:
                                    if len(notices) >= 256:
                                        notices.pop(next(iter(notices)))
                                    notices[image_key] = code
                                    self.log(f"🙂 表情识别：{why} → {code}")
                                return _ImageBody(f"{text} {code}".strip(), code)
                        except Exception:
                            pass

                        if text_trusted is False and text_code is not None:
                            # 文字里的数字是词内中文数字，且没识别出表情 → 不能拿它当口令
                            # 交给上层 strict 过滤（order_verdict 会判 None）
                            return text
                    except Exception:
                        pass
                    return text

                _E._read_body = _read_body
                _E._riskguard_sticker = True
    except Exception:
        pass

    # ------------------------------------------------ ⑧ 版式学习兜底
    # 真机实测的根本性 bug：`ensure_area()` 只做「定位聊天区」，**从不学版式**。
    # v3.3 的 Layout.learn(rows, width) 只在 _learn_layout / _loop 里被调用，
    # 而 ensure_area 成功后 layout.name_x 仍然是 None →
    # 于是所有行都被 classify 成 CONTENT、名字行配不上气泡 →
    # _judge 里 `sender=''` → 「跳过（非派单人）」→ **一条都不回**。
    #
    # 这就是「北风花园 7 明明在屏幕上，软件毫无反应」的第一层原因（第二层是贴纸数字）。
    # 修法：ensure_area 返回成功后，强制用当前画面学一次版式。
    try:
        from . import engine as _engine

        _E = getattr(_engine, "Engine", None)
        if _E is not None and not getattr(_E, "_riskguard_layout", False):
            _orig_ensure_area = getattr(_E, "ensure_area", None)
            if _orig_ensure_area is not None:
                def _ensure_area(self, *a, **kw):
                    ok = _orig_ensure_area(self, *a, **kw)
                    if ok:
                        try:
                            _learn(self)
                        except Exception:
                            pass
                    return ok

                _E.ensure_area = _ensure_area

                def _learn(self):
                    """从当前画面学一次版式（幂等，失败不影响主流程）。"""
                    from . import segment as _seg
                    area = getattr(self, "area", None)
                    layout = getattr(self, "layout", None)
                    if area is None or layout is None:
                        return
                    # 已有锚点就不重复学（避免把用户手动兜底的锚点学歪）
                    if getattr(layout, "name_x", None) is not None and \
                            getattr(layout, "content_x", None) is not None:
                        return
                    try:
                        full = self._grab_full(*self._origin)
                    except Exception:
                        return
                    try:
                        area_img = self._area_image(full)[0]
                    except Exception:
                        return
                    if area_img is None or not getattr(area_img, "size", 0):
                        return
                    thr = _seg.auto_thr(area_img, int(self.cfg.get("dark_thr", 150)))
                    rows = _seg.analyze(area_img, _seg.Layout(), dark_thr=thr)
                    if not rows:
                        return
                    layout.learn(rows, area_img.shape[1])
                    try:
                        self.log(
                            f"📍 学习版式：名字列 x={layout.name_x} 气泡列 x={layout.content_x}")
                    except Exception:
                        pass

                _E._riskguard_layout = True
    except Exception:
        pass

    # ------------------------------------------------ ⑨ 严格派单人匹配 + 派单判定
    # v3.3 的 match_dispatcher 太宽松：`n in s or s in n` + 相似度 0.72。
    # 真机语料实测误判：
    #     '悲伤土豆丝' → 当成派单人（别人，只多一个字）
    #     '土豆' / '悲伤' / '悲' → 当成派单人（只占一部分）
    # 真实群里叫"土豆""悲伤"的人很多 —— 一旦误判就会去回别人的闲聊。
    #
    # 同时 v3.3 的 reply_code 会把中文虚指当口令：
    #     '下午2点看房' → 2、'客户说要考虑一下' → 1、'一会再回' → 1
    #
    # 这里把 engine 的 match_dispatcher 换成严格版，并在 _judge 入口加一道
    # order_verdict 预判：只有明确的"派单人 + 真口令"才放行。
    try:
        from . import engine as _engine

        _E = getattr(_engine, "Engine", None)
        if _E is not None and not getattr(_E, "_riskguard_strict", False):
            from . import strictmatch as _sm

            # ① 替换模块级 match_dispatcher（_judge 内部是通过模块名调用的）
            _engine.match_dispatcher = _sm.match_dispatcher

            # ② 在 _judge 入口做严格预判
            _orig_judge2 = getattr(_E, "_judge", None)
            if _orig_judge2 is not None:
                def _judge_strict(self, frame, msg, sender=None, text=None, key=None):
                    try:
                        from . import segment as _sg
                        if key is None:
                            key = _engine._fp(_sg.crop(frame, msg.body_row, pad_x=2, pad_y=2))
                        if key in getattr(self, "_sent", {}):
                            return None
                        # 取正文/发送人（避免重复 OCR：调用方已算好就复用）
                        if text is None:
                            text = self._read_body(frame, msg)
                        if sender is None and getattr(msg, "sender_row", None) is not None:
                            sender = self.ocr.read_name(
                                _sg.crop(frame, msg.sender_row, pad_x=3, pad_y=6))

                        # 自己的气泡永远不判（防自激）
                        if getattr(msg, "is_own", False) and not self.cfg.get("self_dispatch", False):
                            return None

                        image_code = getattr(text, "image_code", None)
                        if image_code is not None:
                            # OCR can read the hand as 鸡, 8, or 2点. Preserve the
                            # proven image command across strictnum and the pyc's
                            # second reply_code() call, without relaxing sender checks.
                            import re
                            original = str(text).rsplit(" ", 1)[0]
                            if len(re.findall(r"[A-Za-z\u4e00-\u9fff]", original)) < 2 or _sm.is_chitchat(original):
                                return None
                            text = f"图像派单 扣{image_code}"

                        act, reason = _sm.order_verdict(
                            sender or "", text or "",
                            self.cfg.get("dispatchers", []) or [],
                            self.cfg.get("my_name", "") or "")

                        if act == "skip":
                            if self.cfg.get("debug_judge"):
                                try:
                                    notices = getattr(self, "_rg_skip_notices", None)
                                    if notices is None:
                                        notices = self._rg_skip_notices = set()
                                    notice = (key, sender, str(text), reason)
                                    if notice not in notices:
                                        if len(notices) >= 256:
                                            notices.clear()
                                        notices.add(notice)
                                        self.log(f"🔍 严格过滤：{reason} 正文={text!r}")
                                except Exception:
                                    pass
                            return None
                    except Exception:
                        pass
                    return _orig_judge2(self, frame, msg, sender, text, key)

                _E._judge = _judge_strict
                _E._riskguard_strict = True
    except Exception:
        pass

    # ------------------------------------------------ ⑩ 抢单期间保持企业微信在前台
    # 用户需求：「点开始抢单后强制企业微信在前台，可以移动但必须保持在前台」。
    #
    # 做法：给 Engine 装一个 ForegroundGuard，在每帧判定**之前**确认前台状态：
    #   · 在前台 → 什么都不做（零开销）
    #   · 被挡住 → 拉起（1.5 秒内最多一次）
    #   · 拉不起来 → 这一帧不判定，等下一帧重试；发送时会因 require_foreground
    #     失败变成「请手动回」，绝不盲目敲键盘到别人的窗口里
    try:
        from . import engine as _engine

        _E = getattr(_engine, "Engine", None)
        if _E is not None and not getattr(_E, "_riskguard_fg", False):
            from . import fgguard as _fgm

            _orig_handle = getattr(_E, "_handle_frame", None)
            if _orig_handle is not None:
                def _handle_frame_fg(self, frame):
                    try:
                        guard = getattr(self, "_fg_guard", None)
                        if guard is None:
                            hwnd = getattr(self, "hwnd", None)
                            if hwnd:
                                guard = _fgm.ForegroundGuard(
                                    hwnd,
                                    on_lost=lambda: self.log(
                                        "⚠️ 企业微信被挡住了，正在尝试拉回前台"
                                        "（抢单期间请保持它在前台）"),
                                    log=self.log)
                                self._fg_guard = guard
                        if guard is not None and not guard.ensure():
                            return None      # 拉不回来 → 这一帧不判定
                    except Exception:
                        pass
                    return _orig_handle(self, frame)

                _E._handle_frame = _handle_frame_fg

            # 开始抢单时强制一次（用户明确要求的那一次）
            _orig_ensure2 = getattr(_E, "ensure_area", None)
            if _orig_ensure2 is not None:
                def _ensure_area_fg(self, *a, **kw):
                    ok = _orig_ensure2(self, *a, **kw)
                    try:
                        hwnd = getattr(self, "hwnd", None)
                        if ok and hwnd:
                            guard = getattr(self, "_fg_guard", None)
                            if guard is None:
                                guard = _fgm.ForegroundGuard(hwnd, log=self.log)
                                self._fg_guard = guard
                            guard.ensure(force=True)
                    except Exception:
                        pass
                    return ok

                _E.ensure_area = _ensure_area_fg

            _E._riskguard_fg = True
    except Exception:
        pass

    _applied = True
    return True


def status() -> dict:
    return {
        "auto_foreground": _AUTO_FG,
        "max_clicks": min(_MAX_CLICKS, CLICK_CEILING),
        "min_send_gap_s": MIN_SEND_GAP_S,
        "applied": _applied,
    }


apply()