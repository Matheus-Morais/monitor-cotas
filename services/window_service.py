"""Window geometry, positioning, and Win32 interop service for TokenWatch."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from typing import Any, Tuple

SUBCLASSPROC = (
    ctypes.WINFUNCTYPE(
        ctypes.c_long,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
        ctypes.c_void_p,
        ctypes.c_void_p,
    )
    if os.name == "nt"
    else None
)


class MINMAXINFO(ctypes.Structure):
    _fields_ = (
        [
            ("ptReserved", wintypes.POINT),
            ("ptMaxSize", wintypes.POINT),
            ("ptMaxPosition", wintypes.POINT),
            ("ptMinTrackSize", wintypes.POINT),
            ("ptMaxTrackSize", wintypes.POINT),
        ]
        if os.name == "nt"
        else []
    )


def get_display_work_area() -> Tuple[int, int, int, int]:
    """Get the usable work area (excluding taskbar): (left, top, right, bottom)."""
    if os.name != "nt":
        return (0, 0, 1920, 1080)
    try:
        user32 = ctypes.windll.user32
        SPI_GETWORKAREA = 0x0030

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        rect = RECT()
        if user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
            return (rect.left, rect.top, rect.right, rect.bottom)
        w = user32.GetSystemMetrics(0) or 1920
        h = user32.GetSystemMetrics(1) or 1080
        return (0, 0, w, h)
    except Exception:
        return (0, 0, 1920, 1080)


def clamp_to_work_area(
    x: int | None,
    y: int | None,
    width: int,
    height: int,
    work_area: Tuple[int, int, int, int] | None = None,
    margin: int = 16,
) -> Tuple[int, int]:
    """Ensure coordinates place the window entirely within the usable screen area."""
    wa_left, wa_top, wa_right, wa_bottom = work_area or get_display_work_area()

    max_x = max(wa_left, wa_right - width - margin)
    max_y = max(wa_top, wa_bottom - height - margin)

    if x is None:
        clamped_x = max_x
    else:
        clamped_x = max(wa_left + margin, min(int(x), max_x))

    if y is None:
        clamped_y = wa_top + 40
    else:
        clamped_y = max(wa_top + margin, min(int(y), max_y))

    return int(clamped_x), int(clamped_y)


def calc_avatar_anchor(
    panel_x: int | None,
    panel_y: int | None,
    panel_width: int,
    avatar_size: int,
    work_area: Tuple[int, int, int, int] | None = None,
) -> Tuple[int, int]:
    """Calculate avatar coordinates anchored to panel top-right."""
    if panel_x is not None and panel_y is not None and panel_x > -10000 and panel_y > -10000:
        ax = panel_x + panel_width - avatar_size
        ay = panel_y
    else:
        ax, ay = None, None
    return clamp_to_work_area(ax, ay, avatar_size, avatar_size, work_area=work_area)


def calc_panel_anchor(
    avatar_x: int | None,
    avatar_y: int | None,
    avatar_size: int,
    panel_width: int,
    panel_height: int,
    work_area: Tuple[int, int, int, int] | None = None,
) -> Tuple[int, int]:
    """Calculate panel coordinates anchored so top-right aligns with avatar."""
    if avatar_x is not None and avatar_y is not None and avatar_x > -10000 and avatar_y > -10000:
        px = avatar_x + avatar_size - panel_width
        py = avatar_y
    else:
        px, py = None, None
    return clamp_to_work_area(px, py, panel_width, panel_height, work_area=work_area)


class WindowService:
    """Manages Win32 window styles, circular clipping, and resize grips."""

    HT_MAP = {
        "left": 10,
        "right": 11,
        "top": 12,
        "topleft": 13,
        "topright": 14,
        "bottom": 15,
        "bottomleft": 16,
        "bottomright": 17,
    }

    @staticmethod
    def apply_circular_region(hwnd: int | None, is_avatar: bool, size: int = 72, scale: float = 1.0) -> None:
        """Apply native elliptical clipping in avatar mode."""
        if not hwnd or os.name != "nt":
            return
        try:
            gdi32 = ctypes.windll.gdi32
            user32 = ctypes.windll.user32
            if is_avatar:
                phys_size = int(size * scale)
                hrgn = gdi32.CreateEllipticRgn(0, 0, phys_size, phys_size)
                user32.SetWindowRgn(hwnd, hrgn, True)
            else:
                user32.SetWindowRgn(hwnd, None, True)
        except Exception:
            pass

    @staticmethod
    def start_resize(hwnd: int | None, direction: str) -> None:
        """Initiate non-client drag resize on Windows via WM_NCLBUTTONDOWN."""
        if not hwnd or os.name != "nt":
            return
        code = WindowService.HT_MAP.get(direction, 17)
        try:
            user32 = ctypes.windll.user32
            GWL_STYLE = -16
            WS_THICKFRAME = 0x00040000
            style = user32.GetWindowLongW(hwnd, GWL_STYLE)
            if not (style & WS_THICKFRAME):
                user32.SetWindowLongW(hwnd, GWL_STYLE, style | WS_THICKFRAME)
                user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0027)  # SWP_FRAMECHANGED
            user32.ReleaseCapture()
            user32.PostMessageW(hwnd, 0x00A1, code, 0)  # WM_NCLBUTTONDOWN
        except Exception:
            pass

    @staticmethod
    def subclass_minmax(hwnd: int | None, app: Any = None) -> None:
        """Hook WM_GETMINMAXINFO to allow avatar shrinking down to 32x32."""
        if not hwnd or os.name != "nt" or SUBCLASSPROC is None:
            return
        try:
            comctl32 = ctypes.windll.comctl32
            WM_GETMINMAXINFO = 0x0024

            def sub_proc(hwnd_in, msg, wparam, lparam, uid, ref):
                if msg == WM_GETMINMAXINFO:
                    mmi = ctypes.cast(lparam, ctypes.POINTER(MINMAXINFO)).contents
                    if app and getattr(app, "mode", "panel") == "panel":
                        mmi.ptMinTrackSize.x = 240
                        mmi.ptMinTrackSize.y = 180
                    else:
                        mmi.ptMinTrackSize.x = 32
                        mmi.ptMinTrackSize.y = 32
                    return 0
                return comctl32.DefSubclassProc(hwnd_in, msg, wparam, lparam)

            sub_proc_cb = SUBCLASSPROC(sub_proc)
            setattr(WindowService, "_cb", sub_proc_cb)
            comctl32.SetWindowSubclass(hwnd, sub_proc_cb, 101, 0)
        except Exception:
            pass
