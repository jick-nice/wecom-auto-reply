# -*- coding: utf-8 -*-
"""防黑屏 / 防休眠：SetThreadExecutionState，不修改系统电源计划。"""
from __future__ import annotations

import ctypes
import threading

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)


class SleepGuard:
    """在**同一个线程**里周期性续期，才能持续压住休眠计时器。"""

    def __init__(self, every: float = 20.0):
        self.every = every
        self._stop = threading.Event()
        self._th: threading.Thread | None = None

    def start(self) -> None:
        if self._th and self._th.is_alive():
            return
        self._stop.clear()
        self._th = threading.Thread(target=self._run, name="sleepguard", daemon=True)
        self._th.start()

    def _run(self) -> None:
        flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
        while not self._stop.is_set():
            _k32.SetThreadExecutionState(ctypes.c_uint(flags))
            self._stop.wait(self.every)

    def stop(self) -> None:
        self._stop.set()
        try:
            _k32.SetThreadExecutionState(ctypes.c_uint(ES_CONTINUOUS))
        except Exception:
            pass
