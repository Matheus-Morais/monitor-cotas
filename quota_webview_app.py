"""Modern WebView2-based Quota Monitor application."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import keyboard
import webview

from quota_core import (
    OK,
    ERROR,
    ProviderSnapshot,
    format_countdown,
    load_agy_snapshot,
    load_claude_snapshot,
    load_codex_snapshot,
)
from quota_ui_state import (
    DEFAULT_VISIBLE_METRICS,
    load_ui_config,
    save_ui_config,
)

AGY_STATUS_JSON = r"C:\Users\MOBILTEC\scripts\agy-statusline-input.json"
CLAUDE_STATUS_JSON = r"C:\Users\MOBILTEC\scripts\claude-statusline-input.json"
CODEX_CONFIG = r"C:\Users\MOBILTEC\.codex\config.toml"
CODEX_STATE_DB = r"C:\Users\MOBILTEC\.codex\state_5.sqlite"
CODEX_HISTORY_DB = r"C:\Users\MOBILTEC\.codex\thread_history_1.sqlite"
CODEX_ROLLOUTS_DIR = r"C:\Users\MOBILTEC\.codex\sessions"
CONFIG_PATH = Path(sys.executable).with_name("config.json") if getattr(sys, "frozen", False) else Path(__file__).with_name("config.json")

SUBCLASSPROC = ctypes.WINFUNCTYPE(ctypes.c_long, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM, ctypes.c_void_p, ctypes.c_void_p) if os.name == "nt" else None


class MINMAXINFO(ctypes.Structure):
    _fields_ = [
        ("ptReserved", wintypes.POINT),
        ("ptMaxSize", wintypes.POINT),
        ("ptMaxPosition", wintypes.POINT),
        ("ptMinTrackSize", wintypes.POINT),
        ("ptMaxTrackSize", wintypes.POINT),
    ] if os.name == "nt" else []


def _subclass_minmax(hwnd: int, app: Any = None) -> None:
    """Allow windows to shrink smaller than Windows SM_CXMINTRACK (down to 32x32 for avatar),
    while maintaining appropriate min size in panel mode (240x180)."""
    if os.name != "nt":
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
        setattr(_subclass_minmax, "_cb", sub_proc_cb)
        comctl32.SetWindowSubclass(hwnd, sub_proc_cb, 101, 0)
    except Exception:
        pass



def clamp_to_work_area(x: int | None, y: int | None, width: int, height: int) -> tuple[int, int]:
    """Ensure the window fits entirely inside the monitor's usable work area."""
    if os.name != "nt":
        return (x if x is not None else 100), (y if y is not None else 100)
    try:
        user32 = ctypes.windll.user32
        SPI_GETWORKAREA = 0x0030
        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
        rect = RECT()
        if user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
            wa_left = rect.left
            wa_top = rect.top
            wa_right = rect.right
            wa_bottom = rect.bottom
        else:
            wa_left = 0
            wa_top = 0
            wa_right = user32.GetSystemMetrics(0) or 1920
            wa_bottom = user32.GetSystemMetrics(1) or 1080
    except Exception:
        wa_left, wa_top, wa_right, wa_bottom = 0, 0, 1920, 1080

    margin = 16
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


def get_user_email() -> str:
    try:
        with open(r"C:\Users\MOBILTEC\.claude.json", "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data.get("oauthAccount", {}).get("emailAddress", "")
    except (OSError, ValueError, AttributeError):
        return ""


def serialize_snapshot(snapshot: ProviderSnapshot | None, active_email: str = "") -> dict[str, Any]:
    if not snapshot:
        return {"status": "unavailable", "metrics": {}}
    return {
        "provider": snapshot.provider,
        "status": snapshot.status,
        "model": snapshot.model,
        "plan": snapshot.plan,
        "account": snapshot.account,
        "is_active_account": bool(snapshot.account and snapshot.account == active_email),
        "source_age_seconds": snapshot.source_age_seconds,
        "metrics": {
            k: {
                "remaining_pct": m.remaining_pct,
                "reset_at": m.reset_at,
                "detail": m.detail,
                "estimated": m.estimated,
            }
            for k, m in snapshot.metrics.items()
        },
    }


def collect_snapshots() -> dict[str, Any]:
    results = {}
    try:
        results["agy"] = load_agy_snapshot(AGY_STATUS_JSON)
    except Exception:
        results["agy"] = ProviderSnapshot(provider="antigravity", status="unavailable")

    active_email = get_user_email()
    c1_path = r"C:\Users\MOBILTEC\.claude-1.json"
    c2_path = r"C:\Users\MOBILTEC\.claude-2.json"
    try:
        c1 = load_claude_snapshot(c1_path if os.path.exists(c1_path) else CLAUDE_STATUS_JSON)
        if c1.account == active_email and active_email:
            c1 = load_claude_snapshot(r"C:\Users\MOBILTEC\.claude.json")
        results["claude1"] = c1
    except Exception:
        results["claude1"] = ProviderSnapshot(provider="claude", status="unavailable")

    try:
        c2 = load_claude_snapshot(c2_path if os.path.exists(c2_path) else CLAUDE_STATUS_JSON)
        if c2.account == active_email and active_email:
            c2 = load_claude_snapshot(r"C:\Users\MOBILTEC\.claude.json")
        results["claude2"] = c2
    except Exception:
        results["claude2"] = ProviderSnapshot(provider="claude", status="unavailable")

    try:
        results["codex"] = load_codex_snapshot(
            CODEX_HISTORY_DB,
            CODEX_STATE_DB,
            rollouts_dir=CODEX_ROLLOUTS_DIR,
        )
    except Exception:
        results["codex"] = ProviderSnapshot(provider="codex", status="unavailable")

    return {k: serialize_snapshot(v, active_email) for k, v in results.items()}


class QuotaAPI:
    """JS Bridge exposed to the modern WebView UI."""

    def __init__(self, app: "QuotaWebViewApp"):
        self._app = app

    def init(self) -> dict[str, Any]:
        return {
            "snapshots": self._app.snapshots or collect_snapshots(),
            "config": self._app.config,
        }

    def get_snapshots(self) -> dict[str, Any]:
        return self._app.snapshots or collect_snapshots()

    def toggle_metric(self, provider_key: str, metric_key: str) -> dict[str, Any]:
        visible = self._app.config.setdefault("visible_metrics", {})
        prov_list = visible.setdefault(provider_key, list(DEFAULT_VISIBLE_METRICS.get(provider_key, [])))
        if metric_key in prov_list:
            prov_list.remove(metric_key)
        else:
            prov_list.append(metric_key)
        self._app.save_preferences()
        return self._app.config

    def reset_all_metrics(self) -> dict[str, Any]:
        self._app.config["visible_metrics"] = {k: list(v) for k, v in DEFAULT_VISIBLE_METRICS.items()}
        self._app.save_preferences()
        return self._app.config

    def save_collapsed(self, collapsed: dict[str, bool]) -> None:
        self._app.config["collapsed_cards"] = dict(collapsed)
        self._app.save_preferences()

    def switch_claude(self, account_num: int) -> bool:
        source = rf"C:\Users\MOBILTEC\.claude-{account_num}.json"
        dest = r"C:\Users\MOBILTEC\.claude.json"
        try:
            shutil.copy(source, dest)
            self._app.snapshots = collect_snapshots()
            return True
        except OSError:
            return False

    def force_refresh(self) -> dict[str, Any]:
        def trigger_agy():
            try:
                subprocess.run(
                    ["agy", "--print", "/usage"],
                    capture_output=True,
                    timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            except Exception:
                pass
        threading.Thread(target=trigger_agy, daemon=True).start()
        self._app.snapshots = collect_snapshots()
        return self._app.snapshots

    def toggle_pin(self) -> bool:
        self._app.is_pinned = not self._app.is_pinned
        if self._app.window:
            self._app.window.on_top = self._app.is_pinned
        return self._app.is_pinned

    def toggle_mode(self) -> dict[str, Any]:
        return self._app.toggle_mode()

    def set_mode(self, mode: str) -> dict[str, Any]:
        return self._app.set_mode(mode)

    def minimize(self) -> None:
        self._app.toggle_mode()

    def toggle_avatar(self) -> None:
        self._app.toggle_mode()

    def start_resize(self, direction: str) -> None:
        self._app.start_resize(direction)

    def manual_resize(self, width: int, height: int, x: int | None = None, y: int | None = None) -> dict[str, Any]:
        return self._app.manual_resize(width, height, x, y)

    def close(self) -> None:
        self._app.close()



class QuotaWebViewApp:
    def __init__(self, config_path: Path = CONFIG_PATH):
        self.config_path = config_path
        self.config = load_ui_config(self.config_path)
        # Pre-populate snapshots immediately on startup so UI has valid data on frame 1
        self.snapshots = collect_snapshots()
        self.window = None
        self.is_pinned = True
        self.is_minimized = False
        self.mode = self.config.get("ui_mode", "panel")
        self.api = QuotaAPI(self)
        self.hotkey_handle = None
        self.tray_icon = None

        # Data Poller thread (in-memory only, no GUI calls to prevent COM/WinForms deadlocks)
        self.poller_running = True
        self.poller_thread = threading.Thread(target=self._background_poller, daemon=True)
        self.poller_thread.start()

        # Agy auto-refresh poller
        self.agy_thread = threading.Thread(target=self._background_agy_poller, daemon=True)
        self.agy_thread.start()

        try:
            self.hotkey_handle = keyboard.add_hotkey("ctrl+shift+c", self.toggle_visibility)
        except Exception:
            self.hotkey_handle = None

        self.start_tray()

    def _on_minimized(self) -> None:
        self.is_minimized = True

    def _on_restored(self) -> None:
        self.is_minimized = False

    def minimize(self) -> None:
        if self.window:
            try:
                self.is_minimized = True
                self.window.minimize()
            except Exception:
                pass

    def restore(self) -> None:
        if self.window:
            try:
                self.window.restore()
                self.is_minimized = False
            except Exception:
                pass

    def get_hwnd(self) -> int | None:
        if self.window and hasattr(self.window, "native") and self.window.native:
            try:
                return int(str(self.window.native.Handle))
            except Exception:
                return None
        return None

    def start_resize(self, direction: str) -> None:
        if self.mode != "panel":
            return
        hwnd = self.get_hwnd()
        if not hwnd or os.name != "nt":
            return
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
        code = HT_MAP.get(direction, 17)
        try:
            user32 = ctypes.windll.user32
            GWL_STYLE = -16
            WS_THICKFRAME = 0x00040000
            style = user32.GetWindowLongW(hwnd, GWL_STYLE)
            if not (style & WS_THICKFRAME):
                user32.SetWindowLongW(hwnd, GWL_STYLE, style | WS_THICKFRAME)
                user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0027) # SWP_FRAMECHANGED
            user32.ReleaseCapture()
            user32.PostMessageW(hwnd, 0x00A1, code, 0) # WM_NCLBUTTONDOWN
        except Exception:
            pass

    def manual_resize(self, width: int, height: int, x: int | None = None, y: int | None = None) -> dict[str, Any]:
        if self.mode != "panel" or not self.window:
            return {"width": width, "height": height}
        width = max(240, int(width))
        height = max(180, int(height))
        hwnd = self.get_hwnd()
        if hwnd and os.name == "nt":
            try:
                user32 = ctypes.windll.user32
                SWP_NOZORDER = 0x0004
                SWP_NOACTIVATE = 0x0010
                flags = SWP_NOZORDER | SWP_NOACTIVATE
                if x is None or y is None:
                    flags |= 0x0002 # SWP_NOMOVE
                    cur_x, cur_y = 0, 0
                else:
                    cur_x, cur_y = int(x), int(y)
                user32.SetWindowPos(hwnd, 0, cur_x, cur_y, width, height, flags)
            except Exception:
                self.window.resize(width, height)
        else:
            self.window.resize(width, height)
            if x is not None and y is not None:
                self.window.move(int(x), int(y))
        self.save_preferences()
        return {"width": width, "height": height}

    def _apply_circular_region(self, is_avatar: bool, size: int = 72) -> None:
        """Apply native elliptical clipping in avatar mode so no square borders bleed through."""
        if os.name != "nt" or not self.window:
            return
        try:
            hwnd = self.get_hwnd()
            if not hwnd:
                return
            gdi32 = ctypes.windll.gdi32
            user32 = ctypes.windll.user32
            if is_avatar:
                scale = getattr(self.window.native, "_scale", 1.0)
                phys_size = int(size * scale)
                hrgn = gdi32.CreateEllipticRgn(0, 0, phys_size, phys_size)
                user32.SetWindowRgn(hwnd, hrgn, True)
            else:
                user32.SetWindowRgn(hwnd, None, True)
        except Exception:
            pass


    def save_preferences(self) -> None:
        if self.window and not self.is_minimized:
            try:
                wx = self.window.x
                wy = self.window.y
                ww = self.window.width
                wh = self.window.height
                if wx is not None and wx > -10000 and wy is not None and wy > -10000:
                    if self.mode == "panel" and ww is not None and wh is not None and ww >= 200 and wh >= 200:
                        self.config["panel_geometry"]["width"] = ww
                        self.config["panel_geometry"]["height"] = wh
                        self.config["panel_geometry"]["x"] = wx
                        self.config["panel_geometry"]["y"] = wy
                    elif self.mode == "avatar" and ww is not None and wh is not None and ww <= 160 and wh <= 160:
                        self.config["avatar_position"]["x"] = wx
                        self.config["avatar_position"]["y"] = wy
            except Exception:
                pass
        self.config = save_ui_config(self.config, self.config_path)

    def _background_poller(self) -> None:
        while self.poller_running:
            time.sleep(3)
            try:
                self.snapshots = collect_snapshots()
            except Exception:
                pass

    def _background_agy_poller(self) -> None:
        while self.poller_running:
            time.sleep(120)
            try:
                subprocess.run(
                    ["agy", "--print", "/usage"],
                    capture_output=True,
                    timeout=30,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            except Exception:
                pass

    def toggle_visibility(self) -> None:
        if self.window:
            try:
                self.toggle_mode()
            except Exception:
                pass

    def toggle_mode(self) -> dict[str, Any]:
        target = "avatar" if self.mode == "panel" else "panel"
        return self.set_mode(target)

    def set_mode(self, target: str) -> dict[str, Any]:
        if target not in ("avatar", "panel"):
            return {"mode": self.mode, "config": self.config}
        if self.mode == target:
            return {"mode": self.mode, "config": self.config}

        prev_mode = self.mode

        # 1. Capture current geometry before changing mode
        if self.window and not self.is_minimized:
            try:
                wx = self.window.x
                wy = self.window.y
                ww = self.window.width
                wh = self.window.height
                if wx is not None and wx > -10000 and wy is not None and wy > -10000:
                    if prev_mode == "panel" and ww is not None and wh is not None and ww >= 200 and wh >= 200:
                        self.config["panel_geometry"]["width"] = ww
                        self.config["panel_geometry"]["height"] = wh
                        self.config["panel_geometry"]["x"] = wx
                        self.config["panel_geometry"]["y"] = wy
                    elif prev_mode == "avatar" and ww is not None and wh is not None and ww <= 160 and wh <= 160:
                        self.config["avatar_position"]["x"] = wx
                        self.config["avatar_position"]["y"] = wy
            except Exception:
                pass

        self.mode = target
        self.config["ui_mode"] = target

        if self.window:
            if target == "avatar":
                size = int(self.config.get("avatar_size") or 72)
                # When transitioning from panel to avatar, anchor avatar near panel top-right
                geom = self.config.get("panel_geometry") or {}
                px = geom.get("x")
                py = geom.get("y")
                pw = geom.get("width") or 384
                if px is not None and py is not None and px > -10000 and py > -10000:
                    ax = px + pw - size
                    ay = py
                else:
                    pos = self.config.get("avatar_position") or {}
                    ax = pos.get("x")
                    ay = pos.get("y")
                ax, ay = clamp_to_work_area(ax, ay, size, size)
                self.window.resize(size, size)
                self.window.move(ax, ay)
                self._apply_circular_region(True, size)
                self.config["avatar_position"]["x"] = ax
                self.config["avatar_position"]["y"] = ay
            else:
                self._apply_circular_region(False)
                hwnd = self.get_hwnd()
                if hwnd and os.name == "nt":
                    try:
                        user32 = ctypes.windll.user32
                        GWL_STYLE = -16
                        WS_THICKFRAME = 0x00040000
                        style = user32.GetWindowLongW(hwnd, GWL_STYLE)
                        user32.SetWindowLongW(hwnd, GWL_STYLE, style | WS_THICKFRAME)
                        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0027)
                    except Exception:
                        pass
                geom = self.config.get("panel_geometry") or {}

                pw = geom.get("width") or 384
                ph = geom.get("height") or 581
                pos = self.config.get("avatar_position") or {}
                ax = pos.get("x")
                ay = pos.get("y")
                size = int(self.config.get("avatar_size") or 72)
                if ax is not None and ay is not None and ax > -10000 and ay > -10000:
                    px = ax + size - pw
                    py = ay
                else:
                    px = geom.get("x")
                    py = geom.get("y")
                px, py = clamp_to_work_area(px, py, pw, ph)
                self.window.resize(pw, ph)
                self.window.move(px, py)
                self.config["panel_geometry"]["x"] = px
                self.config["panel_geometry"]["y"] = py

        self.save_preferences()
        return {"mode": self.mode, "config": self.config}

    def start_tray(self) -> None:
        try:
            import pystray
            from PIL import Image, ImageDraw

            def make_tray_icon():
                img = Image.new("RGBA", (64, 64), (17, 17, 27, 255))
                draw = ImageDraw.Draw(img)
                draw.ellipse((4, 4, 60, 60), fill=(24, 24, 37, 255), outline="#45475a", width=2)
                draw.polygon([(35, 12), (22, 32), (32, 32), (26, 52), (44, 28), (34, 28)], fill="#fbbf24")
                return img

            menu = pystray.Menu(
                pystray.MenuItem("Painel Completo", lambda: self.set_mode("panel")),
                pystray.MenuItem("Modo Avatar (Raio)", lambda: self.set_mode("avatar")),
                pystray.MenuItem("Minimizar Janela", lambda: self.minimize()),
                pystray.MenuItem("Sair", self.close),
            )
            self.tray_icon = pystray.Icon("cotas-gui", make_tray_icon(), "Monitor de Cotas", menu)
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
        except Exception:
            self.tray_icon = None

    def close(self) -> None:
        self.poller_running = False
        self.save_preferences()
        if self.hotkey_handle:
            try:
                keyboard.remove_hotkey(self.hotkey_handle)
            except Exception:
                pass
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        if self.window:
            self.window.destroy()

    def run(self) -> None:
        ui_path = Path(__file__).parent / "ui" / "index.html"
        if not ui_path.exists():
            # In PyInstaller, look in sys._MEIPASS
            ui_path = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "ui" / "index.html"

        if self.mode == "avatar":
            size = int(self.config.get("avatar_size") or 72)
            width = size
            height = size
            pos = self.config.get("avatar_position") or {}
            x, y = clamp_to_work_area(pos.get("x"), pos.get("y"), width, height)
        else:
            geometry = self.config["panel_geometry"]
            width = geometry.get("width") or 384
            height = geometry.get("height") or 581
            x, y = clamp_to_work_area(geometry.get("x"), geometry.get("y"), width, height)

        self.window = webview.create_window(
            title="Monitor de Cotas",
            url=ui_path.as_uri(),
            js_api=self.api,
            width=width,
            height=height,
            x=x,
            y=y,
            resizable=True,
            frameless=True,
            easy_drag=False,  # Managed cleanly via CSS pywebview-drag-region
            on_top=self.is_pinned,
            background_color="#0e0f17",
            min_size=(64, 64),
            shadow=True,
        )
        self.window.events.minimized += self._on_minimized
        self.window.events.restored += self._on_restored

        def _on_shown():
            try:
                hwnd = self.get_hwnd()
                if hwnd:
                    _subclass_minmax(hwnd, self)
                    if self.mode == "panel":
                        user32 = ctypes.windll.user32
                        GWL_STYLE = -16
                        WS_THICKFRAME = 0x00040000
                        style = user32.GetWindowLongW(hwnd, GWL_STYLE)
                        user32.SetWindowLongW(hwnd, GWL_STYLE, style | WS_THICKFRAME)
                        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0027)
            except Exception:
                pass
            if self.mode == "avatar":
                self.window.resize(size, size)
                self._apply_circular_region(True, size)

        self.window.events.shown += _on_shown
        self.window.events.resized += lambda *args: self.save_preferences()


        webview.start(debug=False)


def acquire_instance_mutex():
    if os.name != "nt":
        return True, None
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, "Local\\CotasGuiSingleton")
    already_exists = kernel32.GetLastError() == 183
    if already_exists and handle:
        kernel32.CloseHandle(handle)
        return False, None
    return bool(handle), handle


def signal_existing_instance() -> bool:
    if os.name != "nt":
        return False
    kernel32 = ctypes.windll.kernel32
    EVENT_MODIFY_STATE = 0x0002
    handle = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, "Local\\CotasGuiShowEvent")
    if handle:
        kernel32.SetEvent(handle)
        kernel32.CloseHandle(handle)
        return True
    return False


def main() -> None:
    first, mutex = acquire_instance_mutex()
    if not first:
        signal_existing_instance()
        return

    event_handle = None
    if os.name == "nt":
        kernel32 = ctypes.windll.kernel32
        event_handle = kernel32.CreateEventW(None, False, False, "Local\\CotasGuiShowEvent")

    app = QuotaWebViewApp()

    if event_handle and os.name == "nt":
        def _listener():
            while True:
                res = kernel32.WaitForSingleObject(event_handle, 0xFFFFFFFF)
                if res == 0 and app.window:
                    app.window.show()
                    app.window.restore()
        threading.Thread(target=_listener, daemon=True, name="activation-listener").start()

    try:
        app.run()
    finally:
        if event_handle and os.name == "nt":
            ctypes.windll.kernel32.CloseHandle(event_handle)
        if mutex and os.name == "nt":
            ctypes.windll.kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    main()
