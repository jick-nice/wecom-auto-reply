# -*- coding: utf-8 -*-
"""极速截屏：GetDC + CreateDIBSection + BitBlt，一帧 1000x300 约 2~8ms。

同一个实例可交替抓"整个窗口"和"底部条带"——内部按尺寸缓存 DIB，不反复重建。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

import numpy as np

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
BI_RGB = 0
SW_RESTORE = 9


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def set_dpi_aware() -> str:
    """必须最先调用：让所有 Win32 坐标 == 物理像素。返回实际生效的模式（便于诊断）。"""
    try:
        # Win8.1+：per-monitor v2，多屏/混合缩放最正确
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return "per-monitor-v2"
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()          # Vista/Win7 起就有
        return "system-aware"
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return "system-aware(fallback)"
    except Exception:
        return "none"


def windows_version() -> str:
    try:
        v = ctypes.windll.ntdll.RtlGetVersion
    except Exception:
        return "unknown"
    class OSVERSIONINFOW(ctypes.Structure):
        _fields_ = [("dwOSVersionInfoSize", wintypes.DWORD), ("dwMajorVersion", wintypes.DWORD),
                    ("dwMinorVersion", wintypes.DWORD), ("dwBuildNumber", wintypes.DWORD),
                    ("dwPlatformId", wintypes.DWORD), ("szCSDVersion", wintypes.WCHAR * 128)]
    info = OSVERSIONINFOW()
    info.dwOSVersionInfoSize = ctypes.sizeof(OSVERSIONINFOW)
    try:
        v(ctypes.byref(info))
        return f"{info.dwMajorVersion}.{info.dwMinorVersion}.{info.dwBuildNumber}"
    except Exception:
        return "unknown"


class Grabber:
    """按尺寸缓存 DIB 的屏幕抓取器。"""

    def __init__(self) -> None:
        self._cache: dict[tuple[int, int], tuple] = {}
        self._hdc_screen = None

    def _buf(self, w: int, h: int):
        key = (w, h)
        got = self._cache.get(key)
        if got is not None:
            return got
        if self._hdc_screen is None:
            self._hdc_screen = user32.GetDC(0)
        hdc_mem = gdi32.CreateCompatibleDC(self._hdc_screen)
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h            # 负数 = top-down
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB
        ptr = ctypes.c_void_p()
        hbmp = gdi32.CreateDIBSection(self._hdc_screen, ctypes.byref(bmi),
                                      DIB_RGB_COLORS, ctypes.byref(ptr), None, 0)
        gdi32.SelectObject(hdc_mem, hbmp)
        raw = (ctypes.c_ubyte * (w * h * 4)).from_address(ptr.value)
        arr = np.frombuffer(raw, dtype=np.uint8)
        got = (hdc_mem, hbmp, arr)
        self._cache[key] = got
        return got

    def grab(self, bbox, copy: bool = False) -> np.ndarray:
        """bbox=(x1,y1,x2,y2) 屏幕**物理**坐标。

        默认返回零拷贝 BGR 视图：本管线只判明暗、只 OCR 黑白文字，通道顺序不影响结果。
        需要长期持有请传 copy=True。
        """
        x1, y1, x2, y2 = (int(v) for v in bbox)
        w, h = x2 - x1, y2 - y1
        if w <= 0 or h <= 0:
            raise ValueError(f"非法区域 {bbox}")
        hdc_mem, _hbmp, raw = self._buf(w, h)
        if not gdi32.BitBlt(hdc_mem, 0, 0, w, h, self._hdc_screen, x1, y1, SRCCOPY):
            raise OSError(ctypes.get_last_error(), "BitBlt 失败（锁屏/安全桌面？）")
        bgr = raw.reshape(h, w, 4)[:, :, :3]
        return bgr.copy() if copy else bgr

    def grab_window_client(self, hwnd: int, copy: bool = False) -> np.ndarray:
        """PrintWindow 抓窗口客户区（BGR）。

        **窗口在后台、被遮挡也照样能抓**，所以不必抢前台、也不受多屏 DPI 影响。
        实测 1080×760 约 12ms；画质与真截屏一致（能正确切行、正确认字）。
        """
        wr = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(wr)):
            raise OSError("GetWindowRect 失败")
        ww, wh = wr.right - wr.left, wr.bottom - wr.top
        if ww <= 0 or wh <= 0:
            raise OSError("窗口尺寸为 0（最小化了？）")
        hdc_mem, _hbmp, raw = self._buf(ww, wh)
        if not user32.PrintWindow(hwnd, hdc_mem, 2):      # PW_RENDERFULLCONTENT
            raise OSError("PrintWindow 失败")
        arr = raw.reshape(wh, ww, 4)
        off = client_offset_in_window(hwnd)
        if off is None:
            raise OSError("客户区不可用")
        cx, cy, cw, ch = off
        sub = arr[cy:cy + ch, cx:cx + cw, :3]
        if float(sub[::4, ::4].std()) < 1.0:
            raise OSError("PrintWindow 返回空画面（该版本可能不支持后台渲染）")
        return np.ascontiguousarray(sub) if copy else sub

    def close(self) -> None:
        for hdc_mem, hbmp, _raw in self._cache.values():
            try:
                gdi32.DeleteObject(hbmp)
                gdi32.DeleteDC(hdc_mem)
            except Exception:
                pass
        self._cache.clear()
        if self._hdc_screen:
            try:
                user32.ReleaseDC(0, self._hdc_screen)
            except Exception:
                pass
            self._hdc_screen = None


# ------------------------------------------------------------------ 窗口
_EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def find_wecom_windows() -> list[tuple[int, str, str]]:
    found: list[tuple[int, str, str]] = []

    def cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if "企业微信" in buf.value or "WXWork" in cls.value or "WeWork" in cls.value:
            found.append((hwnd, buf.value, cls.value))
        return True

    user32.EnumWindows(_EnumProc(cb), 0)
    return found


def client_rect_screen(hwnd: int) -> tuple[int, int, int, int] | None:
    """客户区在屏幕上的物理矩形 (x, y, w, h)。这是全链路唯一坐标来源。"""
    rc = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rc)):
        return None
    if rc.right <= 0 or rc.bottom <= 0:
        return None
    pt = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
        return None
    return (pt.x, pt.y, rc.right, rc.bottom)


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """整窗矩形 (x, y, w, h)，物理像素。"""
    wr = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(wr)):
        return None
    w, h = wr.right - wr.left, wr.bottom - wr.top
    if w <= 0 or h <= 0:
        return None
    return (wr.left, wr.top, w, h)


def client_offset_in_window(hwnd: int) -> tuple[int, int, int, int] | None:
    """客户区相对整窗的偏移 (dx, dy, w, h)——用于从 PrintWindow 结果里裁客户区。"""
    wr = window_rect(hwnd)
    rc = client_rect_screen(hwnd)
    if not wr or not rc:
        return None
    return (rc[0] - wr[0], rc[1] - wr[1], rc[2], rc[3])


def is_minimized(hwnd: int) -> bool:
    return bool(user32.IsIconic(hwnd))


def restore(hwnd: int) -> None:
    if is_minimized(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)


def force_foreground(hwnd: int) -> bool:
    """把窗口切到前台。

    老写法在这里 keybd_event 模拟按一下 Alt 来"解除前台锁"——
    那会产生一个 injected 键盘事件，属于多余的风险面，已经删掉。
    改用 AttachThreadInput（纯 Win32 状态操作，不产生任何输入事件）。
    """
    if user32.GetForegroundWindow() == hwnd:
        return True
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        if user32.GetForegroundWindow() == hwnd:
            return True
        cur = user32.GetForegroundWindow()
        tid_cur = user32.GetWindowThreadProcessId(cur, None)
        tid_me = kernel32.GetCurrentThreadId()
        if tid_cur and tid_me:
            user32.AttachThreadInput(tid_me, tid_cur, True)
            user32.SetForegroundWindow(hwnd)
            user32.AttachThreadInput(tid_me, tid_cur, False)
        return user32.GetForegroundWindow() == hwnd
    except Exception:
        return False


def is_foreground(hwnd: int) -> bool:
    return user32.GetForegroundWindow() == hwnd


def pick_wecom_window() -> int | None:
    ws = find_wecom_windows()
    if not ws:
        return None
    # 主窗口通常是有标题的、面积最大的那个
    def area(h):
        r = wintypes.RECT()
        user32.GetWindowRect(h, ctypes.byref(r))
        return (r.right - r.left) * (r.bottom - r.top)
    named = [w for w in ws if w[1]]
    return max(named or ws, key=lambda w: area(w[0]))[0]
