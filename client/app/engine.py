# -*- coding: utf-8 -*-
"""抓单引擎：自动定位窗口 → 截底部条带 → 切行 → rec-only OCR → 判号码 → 发送。

判定链（按你的规则）：
    派单人发「房源名 + 口令」  → 这是派单，立刻回声口令
    派单人随后 @ 的第一个人    → 就是抢到这一单的人；@ 的是我 → 计 +1
"""
from __future__ import annotations

import difflib
import hashlib
import re
import threading
import time

import numpy as np

from . import locate, segment
from .capture import client_rect_screen, pick_wecom_window, restore
from .numparse import reply_code
from .sender import InputBusy, NotFocused, NotForeground

_ALIAS = re.compile(r"[（(].*$")
_AT = re.compile(r"@\s*([^\s@，,。:：]{1,12})")


def _base(name: str) -> str:
    return _ALIAS.sub("", name or "").replace(" ", "").replace("　", "").strip()


def match_dispatcher(sender: str, dispatchers: list[str]) -> str | None:
    s = _base(sender)
    if not s:
        return None
    for d in dispatchers:
        n = _base(d)
        if not n:
            continue
        if n in s or s in n:
            return d
        if difflib.SequenceMatcher(None, n, s).ratio() >= 0.72:
            return d
    return None


def at_target(text: str) -> str | None:
    """从「@王琦」里取被 @ 的人。"""
    m = _AT.search(text or "")
    return _base(m.group(1)) if m else None


def _fp(rgb: np.ndarray) -> bytes:
    return hashlib.blake2b(rgb[::2, ::2].tobytes(), digest_size=12).digest()


class Engine:
    def __init__(self, cfg: dict, ocr, grabber, sender, log=None,
                 on_win=None, on_auto_stop=None, on_frame=None, on_manual=None,
                 hwnd: int | None = None, area=None):
        self.cfg = cfg
        self.ocr = ocr
        self.grabber = grabber
        self.sender = sender
        self.log = log or print
        self.on_win = on_win
        self.on_manual = on_manual
        self.on_auto_stop = on_auto_stop
        self.on_frame = on_frame

        self.hwnd = hwnd
        self.area: locate.ChatArea | None = area
        self._origin: tuple[int, int, int, int] | None = None
        self.layout = segment.Layout()

        self.running = False
        self._th: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self._last_fp = None
        self._last_sanity = 0.0
        self._warned_struct = False
        self._recheck_at = 0.0
        self._probe_grabber = None
        self._sent: dict[bytes, float] = {}
        self._pending: dict[bytes, int] = {}
        self.pending_dispatch = False
        self.pending_at = 0.0
        self.last_sent_at = 0.0
        self.orders = int(cfg.get("orders_today", 0))
        self.max_orders = int(cfg.get("max_orders", 3))
        self.stats = {"frames": 0, "ocr": 0, "sent": 0, "last_ms": 0.0, "locate_ms": 0.0}
        self.last_text = ""
        self.grab_mode = ""

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._stop_evt.clear()
        self._th = threading.Thread(target=self._loop, name="engine", daemon=True)
        self._th.start()

    def stop(self) -> None:
        self.running = False
        self._stop_evt.set()

    # ------------------------------------------------------------ 自动定位
    def ensure_area(self, force: bool = False, quiet: bool = False) -> bool:
        if self.hwnd is None or force:
            self.hwnd = pick_wecom_window()
        if not self.hwnd:
            self.log("❌ 没找到企业微信窗口，请先打开并登录企业微信")
            return False
        restore(self.hwnd)
        rc = client_rect_screen(self.hwnd)
        if rc is None:
            return False
        ox, oy, cw, ch = rc
        need = force or self.area is None or self._origin != (ox, oy, cw, ch)
        if not need:
            return True

        t0 = time.perf_counter()
        try:
            full = self.grabber.grab_window_client(self.hwnd) if hasattr(
                self.grabber, "grab_window_client") else None
            full = full if full is not None else self.grabber.grab((ox, oy, ox + cw, oy + ch))
        except Exception:
            full = self.grabber.grab((ox, oy, ox + cw, oy + ch))

        mr = self.cfg.get("manual_region")
        if mr and len(mr) == 4 and int(mr[2]) > int(mr[0]) > 0 and int(mr[3]) > int(mr[1]) > 0:
            # 手动兜底：坐标是**相对客户区**的，窗口怎么移动都跟得上
            a = locate.ChatArea(int(mr[0]), int(mr[1]), int(mr[2]), int(mr[3]),
                                0, 0, 0, 0, 0.0, True)
            a.input_x1, a.input_y1, a.input_x2, a.input_y2 = locate.input_box_for(full, a)
        else:
            a = locate.locate(full)

        self.area = a
        self._origin = (ox, oy, cw, ch)
        self.stats["locate_ms"] = (time.perf_counter() - t0) * 1000
        if not quiet:
            self.log(f"🎯 窗口 {cw}x{ch} @({ox},{oy}) → {a}  ({self.stats['locate_ms']:.0f}ms)")
        if not a.found:
            self.log("⚠️ 没能识别出聊天区结构：请确认企业微信停在「群聊天」窗口（不是通讯录/邮件），"
                     "现在是按比例兜底，可能不准")
        # 输入框位置也自动拿到，不必再 F8
        self.sender.input_pos = (ox + a.input_center[0], oy + a.input_center[1])
        self.log(f"⌨️ 输入框点击位置 = {self.sender.input_pos}")
        return True

    def _strip_rel(self) -> tuple[int, int, int, int]:
        """要处理的消息区（相对客户区）。

        注意：群消息不满一屏时是**顶部对齐**的，只盯底部条带会漏最新一条，
        所以默认处理整个消息区；strip_h 只是超大窗口时的上限保护。
        """
        a = self.area
        scale = max(0.6, self._origin[3] / 760.0)
        cap = int(int(self.cfg.get("strip_h", 900)) * scale)
        return (a.x1, max(a.y1, a.y2 - cap), a.x2, a.y2)

    def _bbox(self) -> tuple[int, int, int, int]:
        """底部条带（屏幕物理坐标）。"""
        ox, oy = self._origin[0], self._origin[1]
        x1, y1, x2, y2 = self._strip_rel()
        return (ox + x1, oy + y1, ox + x2, oy + y2)

    def _input_reader(self):
        """返回一个"输入框里有多少个字"的探针函数，给 Sender 做发送前后校验。

        不用 OCR：输入框里的字只有 10px 高，rec 读不出来（实测「7」读不到）。
        改成数**暗像素**——空框只有光标（~40 个），打一个数字涨到 300 上下，
        既准又快（0.3ms，不跑模型）。
        """
        if self.area is None or self._origin is None:
            return None
        a = self.area
        ox, oy = self._origin[0], self._origin[1]
        bbox = (ox + a.input_x1 + 6, oy + a.input_y1 + 4,
                ox + a.input_x1 + 300, oy + a.input_y1 + 44)

        def read():
            try:
                from .capture import Grabber, is_foreground
                # 探针用**自己的** Grabber：和主循环共用一块 DIB 时，
                # 两个线程同时读写会互相当成对方的数据（实测读到全黑→误判没收到字）
                if self._probe_grabber is None:
                    self._probe_grabber = Grabber()
                if is_foreground(self.hwnd):
                    img = np.asarray(self._probe_grabber.grab(bbox, copy=False))
                else:
                    # 后台不能用屏幕 BitBlt（会抓到压在上面的别的窗口），走 PrintWindow
                    full = self.grabber.grab_window_client(self.hwnd)
                    cy1 = a.input_y1 + 4
                    cy2 = a.input_y1 + 44
                    cx1 = a.input_x1 + 6
                    cx2 = a.input_x1 + 300
                    img = full[cy1:cy2, cx1:cx2]
                if img.ndim == 3:
                    # 必须用 int32：int16 下 255*150 会溢出成负数（踩过这个坑）
                    g = (img[:, :, 0].astype(np.int32) * 29
                         + img[:, :, 1].astype(np.int32) * 150
                         + img[:, :, 2].astype(np.int32) * 77) >> 8
                else:
                    g = img.astype(np.int32)
                bg = float(np.median(g))
                if bg < 120:                 # 深色主题或截错了窗口 → 放弃校验，不误判
                    return None
                return int((g < bg - 40).sum())   # 相对背景数暗像素，自动适配深浅主题
            except Exception:
                return None

        return read

    def _grab_frame(self) -> np.ndarray:
        """前台就用屏幕 BitBlt（4ms）；后台用 PrintWindow（12ms）—— 都不用抢焦点。"""
        from .capture import is_foreground
        try:
            if is_foreground(self.hwnd):
                self.grab_mode = "screen"
                return segment.normalize(self.grabber.grab(self._bbox()))
            self.grab_mode = "window"
            full = self.grabber.grab_window_client(self.hwnd)
            x1, y1, x2, y2 = self._strip_rel()
            return segment.normalize(full[y1:y2, x1:x2])
        except Exception:
            self.grab_mode = "screen(fallback)"
            return segment.normalize(self.grabber.grab(self._bbox()))

    def _warm_baseline(self) -> None:
        """启动基线：把画面上**已经存在**的消息记为已处理。

        不做这一步，"启动时最后 2 条"会被当成刚到的派单，一开软件就去回上一单。
        """
        if self.area is None:
            return
        try:
            frame = self._grab_frame()
        except Exception:
            return
        rows = segment.analyze(frame, self.layout, dark_thr=segment.auto_thr(frame, int(self.cfg.get("dark_thr", 150))))
        if not rows:
            return
        if self.layout.content_x is None or self.layout.name_x is None:
            self.layout.learn(rows)
            rows = segment.analyze(frame, self.layout,
                                   dark_thr=segment.auto_thr(frame, int(self.cfg.get("dark_thr", 150))))
        now = time.time()
        n = 0
        for msg in segment.pair_messages(rows)[-6:]:
            body = segment.crop(frame, msg.body_row, pad_x=2, pad_y=2)
            self._sent[_fp(body)] = now
            n += 1
        self.log(f"🧱 启动基线：当前 {n} 条已有消息记为已处理（不会去回旧单）")

    # ---------------------------------------------------------------- 主循环
    def _loop(self) -> None:
        if not self.ensure_area(force=True):
            self.running = False
            return
        self._warm_baseline()
        while self.running:
            if not self.ensure_area():          # 窗口移动/缩放会自动重算，开销≈0
                self._stop_evt.wait(0.2)
                continue
            t0 = time.perf_counter()
            try:
                frame = self._grab_frame()
            except Exception as e:
                self.log(f"截屏失败: {e}")
                self._stop_evt.wait(0.05)
                continue

            fp = _fp(frame)
            if fp == self._last_fp:
                # 刚看到一条还没确认的派单：画面静止也要补看第二眼（否则永远等不到第二帧）
                if (self._recheck_at and time.time() >= self._recheck_at
                        and time.time() < self._recheck_at + 0.5):
                    self._recheck_at = 0.0
                    try:
                        self._handle_frame(frame)
                    except Exception as e:
                        self.log(f"补帧处理异常: {e}")
                    self._stop_evt.wait(0.002)
                    continue
                # 画面不变时每 30s 做一次结构体检：企业微信弹了「设备环境异常」验证页
                # 时聊天区结构会消失，这里能第一时间发现（软件不会去点那个页面）
                now = time.time()
                if now - self._last_sanity > 30:
                    self._last_sanity = now
                    self.ensure_area(force=True, quiet=True)
                    if self.area is not None and not self.area.found:
                        if not self._warned_struct:
                            self._warned_struct = True
                            self.log("⚠️ 认不出聊天区结构：若企业微信弹出「设备环境异常」"
                                     "验证页，请在手机上扫码恢复；软件不会去点它")
                    elif self.area is not None and self.area.found:
                        self._warned_struct = False
                self._stop_evt.wait(0.002)      # 没变化：几乎空转
                continue
            self._last_fp = fp
            self.stats["frames"] += 1

            try:
                self._handle_frame(frame)
                # 画面刚变化的那一帧可能是「半张」：企业微信先画时间分割线、后画气泡，
                # 这时判完画面就再也不变了 —— 实测直接漏掉整条派单。
                # 所以 70ms 后无条件再判一次（窗口静止也补这一眼）。
                self._recheck_at = time.time() + 0.07
            except Exception as e:
                import traceback
                self.log(f"处理异常: {e}\n{traceback.format_exc()}")

            self.stats["last_ms"] = (time.perf_counter() - t0) * 1000

    # ------------------------------------------------------------ 单帧处理
    def _handle_frame(self, frame: np.ndarray) -> None:
        rows = segment.analyze(frame, self.layout, dark_thr=segment.auto_thr(frame, int(self.cfg.get("dark_thr", 150))))
        if not rows:
            return
        if self.layout.content_x is None or self.layout.name_x is None:
            self.layout.learn(rows)
            rows = segment.analyze(frame, self.layout, dark_thr=segment.auto_thr(frame, int(self.cfg.get("dark_thr", 150))))
            self.log(f"📍 学习版式：名字列 x={self.layout.name_x} 气泡列 x={self.layout.content_x}")

        for msg in segment.pair_messages(rows)[-3:]:
            self._judge(frame, msg)

    def _judge(self, frame: np.ndarray, msg: segment.Msg) -> None:
        body = segment.crop(frame, msg.body_row, pad_x=2, pad_y=2)
        key = _fp(body)
        text = self.ocr.read_row(body)
        self.stats["ocr"] += 1
        if not text:
            return

        sender = ""
        if msg.sender_row is not None:
            # 名字只有 ~10px 高、颜色又浅，切分给的 y 范围常只覆盖笔画中段，
            # 上下补足边距才能认对（实测「悲伤土豆」→ 补齐前「非伤十豆」）
            sender = self.ocr.read_name(
                segment.crop(frame, msg.sender_row, pad_x=3, pad_y=6))
        who = match_dispatcher(sender, self.cfg.get("dispatchers", []))
        self.last_text = f"{sender or '(我)'}: {text}"
        if self.on_frame:
            self.on_frame(self.last_text)

        if not who:
            return
        # 自己发的气泡（包括刚回出去那条）永远不可能是别人派给我们的单。
        # 不加这条：切行偶尔会把自己右侧的气泡和上一条派单人的名字配到一起，
        # 于是「自己回的 2」被当成新派单 → 继续回 → 自激循环。
        if msg.is_own and not self.cfg.get("self_dispatch", False):
            return
        if not msg.is_own and sender and not _base(sender):
            return

        my = _base(self.cfg.get("my_name", ""))
        code = reply_code(text)
        target = at_target(text)

        # ---------- 胜利判定：派单人派单之后 @ 的第一个人 ----------
        if target and code is None:
            if self.pending_dispatch and (time.time() - self.pending_at) < 300:
                self.pending_dispatch = False
                if my and (target == my or my in target or target in my):
                    self._win(key, text, target)
                else:
                    self.log(f"▫️ 这一单被「{target}」抢到了")
            return

        if code is None:
            return

        # ---------- 派单：一单只回一次 ----------
        if key in self._sent and (time.time() - self._sent[key]) < 30:
            return
        seen = self._pending.get(key, 0) + 1
        self._pending[key] = seen
        if len(self._pending) > 64:
            self._pending.clear()
            self._pending[key] = seen
        if seen < 2:                             # 连续两帧读到同一内容才发
            # 画面静止时不会有新帧，必须主动补看一眼才拿得到第二帧
            self._recheck_at = time.time() + 0.06
            return
        if self.orders >= self.max_orders:
            self._auto_stop(f"今日 {self.max_orders} 单已满")
            return

        t0 = time.perf_counter()
        try:
            cost = self.sender.send(code, hwnd=self.hwnd,
                                    read_input=self._input_reader())
        except NotForeground as e:
            # 企业微信不在前台：宁可这一单不发，也绝不抢焦点（抢焦点+模拟点击是风控重灾区）
            self.log(f"⚠️ {e}，本单请手动回「{code}」")
            self._sent[key] = time.time()
            if self.on_manual:
                self.on_manual(code, text)
            return
        except InputBusy as e:
            self.log(f"⚠️ 输入框里有草稿「{e}」，不去动它，本单未回")
            self._sent[key] = time.time()
            if self.on_manual:
                self.on_manual(code, text)
            return
        except NotFocused as e:
            self.log(f"⚠️ {e}，本单请手动回「{code}」")
            self._sent[key] = time.time()
            if self.on_manual:
                self.on_manual(code, text)
            return
        except Exception as e:
            self.log(f"❌ 发送失败: {e}")
            return
        total = (time.perf_counter() - t0) * 1000
        self._sent[key] = time.time()
        self.last_sent_at = time.time()
        self.pending_dispatch = True
        self.pending_at = time.time()
        self.stats["sent"] += 1
        self.log(f"⚡ 抢单 → 「{code}」 内容={text!r} 发送{cost:.0f}ms 总计{total:.0f}ms")

    def _win(self, key: bytes, text: str, target: str) -> None:
        if key in self._sent:
            return
        self._sent[key] = time.time() + 3600          # 同一条 @ 只记一次
        self.orders += 1
        self.cfg["orders_today"] = self.orders
        self.log(f"🏆 抢到单！派单人 @{target}  今日 {self.orders}/{self.max_orders}")
        if self.on_win:
            self.on_win(self.orders)
        if self.orders >= self.max_orders:
            self._auto_stop(f"今日 {self.max_orders} 单已满，自动停止")

    def _auto_stop(self, reason: str) -> None:
        self.log(f"🛑 {reason}")
        self.stop()
        if self.on_auto_stop:
            self.on_auto_stop(reason)
