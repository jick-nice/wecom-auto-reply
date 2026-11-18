# -*- coding: utf-8 -*-
"""Runtime guard for the compiled capture/engine modules.

The shipped v3.3 engine intentionally uses ``None`` for a transient frame or
geometry failure.  On some DPI/window-manager combinations that value reaches
``_strip_rel`` before the next geometry pass and becomes a noisy TypeError.
This module keeps the compiled code intact and turns that state into a quiet
FrameBusy retry, while retaining one useful, rate-limited diagnostic.
"""
from __future__ import annotations

import time
from collections.abc import Callable


RETRY_LOG_INTERVAL = 5.0


def frame_usable(frame) -> bool:
    """Return true only for a non-empty ndarray-like frame."""
    if frame is None:
        return False
    try:
        return int(getattr(frame, "size", 0) or 0) > 0 and len(frame.shape) >= 2
    except Exception:
        return False


def geometry_usable(origin) -> bool:
    """Validate the compiled engine's (x, y, width, height) origin tuple."""
    if origin is None:
        return False
    try:
        return len(origin) >= 4 and int(origin[2]) > 0 and int(origin[3]) > 0
    except Exception:
        return False


class FailureReporter:
    """Rate-limit transient capture diagnostics without hiding the first cause."""

    def __init__(self, log: Callable[[str], object] | None = None,
                 interval: float = RETRY_LOG_INTERVAL):
        self.log = log or (lambda _msg: None)
        self.interval = float(interval)
        self.last_at = 0.0
        self.last_reason = ""
        self.count = 0

    def report(self, reason: str) -> None:
        reason = str(reason) or "unknown capture failure"
        self.count += 1
        now = time.monotonic()
        if reason != self.last_reason or now - self.last_at >= self.interval:
            suffix = f"（此前 {self.count - 1} 次已跳过）" if self.count > 1 else ""
            self.log(f"截屏跳过: {reason}{suffix}；将自动重试")
            self.last_reason = reason
            self.last_at = now
            self.count = 0


def _reason(exc: BaseException) -> str:
    text = str(exc)
    if "subscriptable" in text:
        return "窗口几何尚未就绪（可能是 DPI/客户区变化）"
    if "PrintWindow" in text or "空帧" in text:
        return f"PrintWindow 未返回有效画面: {text}"
    if text:
        return text
    return type(exc).__name__


def apply() -> bool:
    """Patch compiled ``Grabber`` and ``Engine`` once; safe to call repeatedly."""
    try:
        from . import capture, engine
    except Exception:
        return False

    if getattr(engine.Engine, "_captureguard_applied", False):
        return True

    original_grab = getattr(capture.Grabber, "grab_window_client", None)
    original_frame = getattr(engine.Engine, "_grab_frame", None)
    frame_busy = getattr(engine, "FrameBusy", RuntimeError)
    if original_grab is None or original_frame is None:
        return False

    def grab_window_client(self, hwnd, *args, **kwargs):
        value = original_grab(self, hwnd, *args, **kwargs)
        if not frame_usable(value):
            raise OSError("PrintWindow 空帧")
        return value

    def grab_frame(self, *args, **kwargs):
        reporter = getattr(self, "_captureguard_reporter", None)
        if reporter is None:
            reporter = FailureReporter(getattr(self, "log", None))
            self._captureguard_reporter = reporter

        if not geometry_usable(getattr(self, "_origin", None)):
            reporter.report("窗口几何尚未就绪（窗口句柄/DPI/客户区变化）")
            try:
                self._area_ok = False
            except Exception:
                pass
            raise frame_busy("capture geometry unavailable")

        try:
            value = original_frame(self, *args, **kwargs)
            if not frame_usable(value):
                reporter.report("抓帧返回空画面")
                raise frame_busy("empty frame")
            return value
        except frame_busy:
            raise
        except Exception as exc:
            reporter.report(_reason(exc))
            try:
                self._area_ok = False
            except Exception:
                pass
            raise frame_busy(f"transient capture failure: {exc}") from exc

    capture.Grabber.grab_window_client = grab_window_client
    engine.Engine._grab_frame = grab_frame
    engine.Engine._captureguard_applied = True
    return True


__all__ = ["FailureReporter", "apply", "frame_usable", "geometry_usable"]
