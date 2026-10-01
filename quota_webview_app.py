"""Modern WebView2-based Quota Monitor application for TokenWatch.

Presentation controller coordinating UI events, system tray, hotkeys,
and decoupled business services (telemetry, profiles, windowing, and config).
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

import keyboard
import webview

from history import HistoryStore
from services.config_manager import (
    DEFAULT_VISIBLE_METRICS,
    ConfigManager,
    resolve_history_db_path,
)
from services.profiles import ProfileService
from services.telemetry import TelemetryService
from services.window_service import (
    WindowService,
    calc_avatar_anchor,
    calc_panel_anchor,
    clamp_to_work_area,
)


class QuotaAPI:
    """JS Bridge exposed to the modern WebView UI."""

    def __init__(self, app: QuotaWebViewApp):
        self._app = app

    def init(self) -> dict[str, Any]:
        """Initial bootstrap called on page load."""
        return {
            "snapshots": self._app.telemetry_service.get_latest(),
            "config": self._app.config_manager.data,
        }

    def get_snapshots(self) -> dict[str, Any]:
        """Return latest telemetry snapshot."""
        return self._app.telemetry_service.get_latest()

    def toggle_metric(self, provider_key: str, metric_key: str) -> dict[str, Any]:
        """Toggle visibility of a metric ring."""
        cfg = self._app.config_manager.data
        visible = cfg.setdefault("visible_metrics", {})
        prov_list = visible.setdefault(provider_key, list(DEFAULT_VISIBLE_METRICS.get(provider_key, [])))
        if metric_key in prov_list:
            prov_list.remove(metric_key)
        else:
            prov_list.append(metric_key)
        self._app.config_manager.set("visible_metrics", visible)
        return self._app.config_manager.data

    def reset_all_metrics(self) -> dict[str, Any]:
        """Reset visible metrics across all providers to default."""
        reset_metrics = {k: list(v) for k, v in DEFAULT_VISIBLE_METRICS.items()}
        self._app.config_manager.set("visible_metrics", reset_metrics)
        return self._app.config_manager.data

    def save_collapsed(self, collapsed: dict[str, bool]) -> None:
        """Persist collapsed/expanded card states."""
        self._app.config_manager.set("collapsed_cards", dict(collapsed))

    def switch_claude(self, account_num: int) -> bool:
        """Switch Claude Code account profile safely."""
        success = self._app.profile_service.switch_account_number(account_num)
        if success:
            self._app.telemetry_service.collect()
        return success

    def force_refresh(self) -> dict[str, Any]:
        """Force immediate poll of telemetry and external CLI usage."""
        return self._app.telemetry_service.force_refresh()

    def toggle_pin(self) -> bool:
        """Toggle window always-on-top state."""
        self._app.is_pinned = not self._app.is_pinned
        if self._app.window:
            self._app.window.on_top = self._app.is_pinned
        return self._app.is_pinned

    def toggle_mode(self) -> dict[str, Any]:
        """Toggle between circular avatar and full HUD panel."""
        return self._app.toggle_mode()

    def set_mode(self, mode: str) -> dict[str, Any]:
        """Set UI mode directly ('avatar' or 'panel')."""
        return self._app.set_mode(mode)

    def minimize(self) -> None:
        """Minimize to taskbar/tray or avatar."""
        self._app.toggle_mode()

    def toggle_avatar(self) -> None:
        self._app.toggle_mode()

    def start_resize(self, direction: str) -> None:
        """Initiate native Win32 window resizing."""
        self._app.start_resize(direction)

    def manual_resize(self, width: int, height: int, x: int | None = None, y: int | None = None) -> dict[str, Any]:
        """Resize panel manually from JS drag grips."""
        return self._app.manual_resize(width, height, x, y)

    def get_history(self, provider: str, metric: str, range_key: str = "6h") -> dict[str, Any]:
        """Return time-series history, burn rate, and ETA for a specific provider & metric."""
        range_map = {
            "1h": 3600.0,
            "6h": 21600.0,
            "24h": 86400.0,
            "7d": 604800.0,
        }
        range_seconds = range_map.get(str(range_key).lower(), 21600.0)
        return self._app.history_store.query_history(
            provider=provider,
            metric=metric,
            range_seconds=range_seconds,
            max_points=120,
        )

    def get_history_providers(self) -> list[dict[str, str]]:
        """Return available provider and metric pairs recorded in history."""
        return self._app.history_store.get_available_metrics()

    def close(self) -> None:
        """Close the application cleanly."""
        self._app.close()


class QuotaWebViewApp:
    """Main desktop application controller."""

    def __init__(self, config_path: Path | str | None = None, history_db_path: Path | str | None = None):
        self.config_manager = ConfigManager(config_path)
        self.profile_service = ProfileService()
        self.history_store = HistoryStore(resolve_history_db_path(history_db_path))
        self.telemetry_service = TelemetryService(
            profile_service=self.profile_service,
            history_store=self.history_store,
            refresh_interval=self.config_manager.get("refresh_seconds", 3.0),
            agy_poll_interval=self.config_manager.get("agy_poll_seconds", 120.0),
        )

        # Background prune of old snapshots (7-day retention)
        threading.Thread(
            target=lambda: self.history_store.prune(retention_days=7),
            daemon=True,
            name="history-pruner",
        ).start()

        self.window = None
        self.is_pinned = True
        self.is_minimized = False
        self.mode = self.config_manager.get("ui_mode", "panel")
        self.api = QuotaAPI(self)
        self.hotkey_handle = None
        self.tray_icon = None

        # Hook telemetry listener to push live updates to WebView HUD
        self.telemetry_service.add_listener(self._on_telemetry_updated)
        self.telemetry_service.start()

        # Register global hotkey
        try:
            self.hotkey_handle = keyboard.add_hotkey("ctrl+shift+c", self.toggle_visibility)
        except Exception:
            self.hotkey_handle = None

        self.start_tray()

    def _on_telemetry_updated(self, snapshots: dict[str, Any]) -> None:
        """Push telemetry updates to the running WebView."""
        if self.window:
            try:
                # pywebview evaluate_js executes safely inside webview context
                data_json = json.dumps(snapshots)
                self.window.evaluate_js(f"if (window.updateHUD) window.updateHUD({data_json});")
            except Exception:
                pass

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
        WindowService.start_resize(hwnd, direction)

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
                    flags |= 0x0002  # SWP_NOMOVE
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
        hwnd = self.get_hwnd()
        scale = getattr(self.window.native, "_scale", 1.0) if self.window and hasattr(self.window, "native") else 1.0
        WindowService.apply_circular_region(hwnd, is_avatar, size, scale)

    def save_preferences(self) -> None:
        """Capture current window bounds and commit to config manager."""
        if self.window and not self.is_minimized:
            try:
                wx = self.window.x
                wy = self.window.y
                ww = self.window.width
                wh = self.window.height
                if wx is not None and wx > -10000 and wy is not None and wy > -10000:
                    if self.mode == "panel" and ww is not None and wh is not None and ww >= 200 and wh >= 200:
                        panel_geom = self.config_manager.get("panel_geometry", {})
                        panel_geom.update({"width": ww, "height": wh, "x": wx, "y": wy})
                        self.config_manager.set("panel_geometry", panel_geom)
                    elif self.mode == "avatar" and ww is not None and wh is not None and ww <= 160 and wh <= 160:
                        self.config_manager.set("avatar_position", {"x": wx, "y": wy})
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
            return {"mode": self.mode, "config": self.config_manager.data}
        if self.mode == target:
            return {"mode": self.mode, "config": self.config_manager.data}

        # 1. Capture current geometry before changing mode
        self.save_preferences()

        self.mode = target
        self.config_manager.set("ui_mode", target)

        if self.window:
            if target == "avatar":
                size = int(self.config_manager.get("avatar_size") or 72)
                geom = self.config_manager.get("panel_geometry") or {}
                px = geom.get("x")
                py = geom.get("y")
                pw = geom.get("width") or 384
                ax, ay = calc_avatar_anchor(px, py, pw, size)

                self.window.resize(size, size)
                self.window.move(ax, ay)
                self._apply_circular_region(True, size)
                self.config_manager.set("avatar_position", {"x": ax, "y": ay})
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
                geom = self.config_manager.get("panel_geometry") or {}
                pw = geom.get("width") or 384
                ph = geom.get("height") or 581
                pos = self.config_manager.get("avatar_position") or {}
                ax = pos.get("x")
                ay = pos.get("y")
                size = int(self.config_manager.get("avatar_size") or 72)

                px, py = calc_panel_anchor(ax, ay, size, pw, ph)
                self.window.resize(pw, ph)
                self.window.move(px, py)
                geom.update({"x": px, "y": py})
                self.config_manager.set("panel_geometry", geom)

        return {"mode": self.mode, "config": self.config_manager.data}

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
            self.tray_icon = pystray.Icon("tokenwatch", make_tray_icon(), "TokenWatch", menu)
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
        except Exception:
            self.tray_icon = None

    def close(self) -> None:
        self.telemetry_service.stop()
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
            size = int(self.config_manager.get("avatar_size") or 72)
            width = size
            height = size
            pos = self.config_manager.get("avatar_position") or {}
            x, y = clamp_to_work_area(pos.get("x"), pos.get("y"), width, height)
        else:
            geometry = self.config_manager.get("panel_geometry") or {}
            width = geometry.get("width") or 384
            height = geometry.get("height") or 581
            x, y = clamp_to_work_area(geometry.get("x"), geometry.get("y"), width, height)

        self.window = webview.create_window(
            title="TokenWatch",
            url=ui_path.as_uri(),
            js_api=self.api,
            width=width,
            height=height,
            x=x,
            y=y,
            resizable=True,
            frameless=True,
            easy_drag=False,
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
                    WindowService.subclass_minmax(hwnd, self)
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
                avatar_size = int(self.config_manager.get("avatar_size") or 72)
                self.window.resize(avatar_size, avatar_size)
                self._apply_circular_region(True, avatar_size)

        self.window.events.shown += _on_shown
        self.window.events.resized += lambda *args: self.save_preferences()

        webview.start(debug=False)


def acquire_instance_mutex():
    if os.name != "nt":
        return True, None
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, "Local\\TokenWatchSingleton")
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
    handle = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, "Local\\TokenWatchShowEvent")
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
        event_handle = kernel32.CreateEventW(None, False, False, "Local\\TokenWatchShowEvent")

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
