"""Compact avatar and detailed quota panel for the Windows quota monitor."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

import keyboard

from quota_core import (
    OK,
    ERROR,
    format_countdown,
    load_agy_snapshot,
    load_claude_snapshot,
    load_codex_snapshot,
)
from quota_ui_state import load_ui_config, save_ui_config


AGY_STATUS_JSON = r"C:\Users\MOBILTEC\scripts\agy-statusline-input.json"
CLAUDE_STATUS_JSON = r"C:\Users\MOBILTEC\scripts\claude-statusline-input.json"
CODEX_CONFIG = r"C:\Users\MOBILTEC\.codex\config.toml"
CODEX_STATE_DB = r"C:\Users\MOBILTEC\.codex\state_5.sqlite"
CODEX_HISTORY_DB = r"C:\Users\MOBILTEC\.codex\thread_history_1.sqlite"
CONFIG_PATH = Path(sys.executable).with_name("config.json") if getattr(sys, "frozen", False) else Path(__file__).with_name("config.json")

# Catppuccin Mocha palette, shared by the avatar, panel and tray icon.
BG_MAIN = "#11111b"
BG_CARD = "#181825"
BG_BAR = "#313244"
TEXT_MAIN = "#cdd6f4"
TEXT_MUTED = "#a6adc8"
COLOR_GREEN = "#a6e3a1"
COLOR_YELLOW = "#f9e2af"
COLOR_RED = "#f38ba8"
COLOR_CYAN = "#89dceb"
COLOR_PURPLE = "#cba6f7"
BORDER_COLOR = "#45475a"

notifications_state = {"claude_alerted": False, "agy_alerted": False, "codex_alerted": False}


def enable_windows_dpi_awareness() -> None:
    """Ask Windows for per-monitor DPI rendering before Tk creates its HWNDs."""
    if sys.platform != "win32":
        return
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2.
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            # Windows 8.1 fallback for older hosts.
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            pass


def percentage_to_arc(remaining_pct: int | float | None) -> int | None:
    """Convert a remaining percentage to a clockwise Canvas arc extent."""
    if remaining_pct is None:
        return None
    try:
        value = max(0.0, min(100.0, float(remaining_pct)))
    except (TypeError, ValueError):
        return None
    return -round(value * 3.6)


def metric_color(remaining_pct: int | float | None) -> str:
    """Return the health color for a remaining quota percentage."""
    if remaining_pct is None:
        return TEXT_MUTED
    try:
        value = float(remaining_pct)
    except (TypeError, ValueError):
        return TEXT_MUTED
    if value <= 10:
        return COLOR_RED
    if value <= 30:
        return COLOR_YELLOW
    return COLOR_GREEN


def release_is_click(start_x: int, start_y: int, end_x: int, end_y: int, threshold: int = 6) -> bool:
    """Return true when pointer movement is small enough to be a click."""
    return abs(end_x - start_x) <= threshold and abs(end_y - start_y) <= threshold


def visible_countdown(reset_at, *, now=None) -> str:
    """Keep the visible reset hint compact while preserving full tooltip detail."""
    if not reset_at:
        return ""
    value = format_countdown(reset_at, now=now)
    if value in {"Pronto", "Resetado"}:
        return value
    parts = value.split()
    if any(part.endswith("h") for part in parts):
        return " ".join(part for part in parts if part.endswith(("h", "m")))
    if any(part.endswith("m") for part in parts):
        return next(part for part in parts if part.endswith("m"))
    return next((part for part in parts if part.endswith("s")), value)


def send_windows_toast(title: str, message: str) -> None:
    if sys.platform != "win32":
        return
    try:
        ps_cmd = f'''
        try {{
            [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
            $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
            $textNodes = $template.GetElementsByTagName("text")
            $textNodes.Item(0).AppendChild($template.CreateTextNode('{title}')) > $null
            $textNodes.Item(1).AppendChild($template.CreateTextNode('{message}')) > $null
            $toast = [Windows.UI.Notifications.ToastNotification]::new($template)
            [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Monitor de Cotas").Show($toast)
        }} catch {{}}
        '''
        subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", ps_cmd],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:
        pass


class Tooltip:
    """Small delayed tooltip that keeps full metric names out of compact rows."""

    def __init__(self, widget: tk.Misc, text: str = ""):
        self.widget = widget
        self.text = text
        self.tip: tk.Toplevel | None = None
        self.pending: str | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def set_text(self, text: str) -> None:
        self.text = text

    def _schedule(self, _event=None) -> None:
        self._hide()
        self.pending = self.widget.after(450, self._show)

    def _show(self) -> None:
        self.pending = None
        if not self.text or not self.widget.winfo_exists():
            return
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.attributes("-topmost", True)
        label = tk.Label(self.tip, text=self.text, justify="left", bg="#242438", fg=TEXT_MAIN,
                         padx=8, pady=5, font=("Segoe UI", 8))
        label.pack()
        self.tip.geometry(f"+{self.widget.winfo_rootx() + 10}+{self.widget.winfo_rooty() + self.widget.winfo_height() + 8}")

    def _hide(self, _event=None) -> None:
        if self.pending:
            try:
                self.widget.after_cancel(self.pending)
            except tk.TclError:
                pass
            self.pending = None
        if self.tip:
            self.tip.destroy()
            self.tip = None


class CircularProgressWidget(tk.Canvas):
    """Compact circular quota indicator with explicit unknown and zero states."""

    def __init__(self, parent, size: int = 58, **kwargs):
        super().__init__(parent, width=size, height=size, bg=BG_CARD, highlightthickness=0, **kwargs)
        self.size = size
        self.value: int | None = None
        self._draw(None, TEXT_MUTED)

    def _draw(self, pct: int | None, color: str) -> None:
        from PIL import Image, ImageDraw, ImageFont, ImageTk
        scale = 4
        image = Image.new("RGBA", (self.size * scale, self.size * scale), BG_CARD)
        draw = ImageDraw.Draw(image)
        inset = 5
        box = tuple(round(value * scale) for value in (inset, inset, self.size - inset, self.size - inset))
        draw.ellipse(box, outline=BG_BAR, width=5 * scale)
        extent = percentage_to_arc(pct)
        if extent is not None and extent:
            # Drawing at 4x removes the stair-step edges from small Canvas arcs.
            draw.arc(box, start=90, end=90 + abs(extent), fill=color, width=5 * scale)
        label = "N/D" if pct is None else ("0" if pct == 0 else f"{pct}")
        try:
            label_font = ImageFont.truetype(r"C:\Windows\Fonts\segoeui.ttf", round((8 if pct is None else 9) * scale))
        except OSError:
            label_font = ImageFont.load_default()
        draw.text((self.size * scale / 2, self.size * scale / 2), label, fill=color, font=label_font, anchor="mm", stroke_width=0)
        image = image.resize((self.size, self.size), Image.Resampling.LANCZOS)
        self._indicator_image = ImageTk.PhotoImage(image)
        self.create_image(0, 0, image=self._indicator_image, anchor="nw")

    def set_value(self, pct: int | None) -> None:
        self.value = pct
        self._draw(pct, metric_color(pct))


class AvatarSurface(tk.Canvas):
    """Draggable, click-to-open compact surface."""

    DRAG_THRESHOLD = 6

    def __init__(self, app: "QuotaHUDApp", size: int):
        super().__init__(app.root, width=size, height=size, bg=BG_MAIN, highlightthickness=0, cursor="hand2")
        self.app = app
        self.size = size
        self.start_pointer = (0, 0)
        self.start_window = (0, 0)
        self.dragged = False
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._move)
        self.bind("<ButtonRelease-1>", self._release)
        self.draw((TEXT_MUTED, TEXT_MUTED, TEXT_MUTED))

    def draw(self, colors: tuple[str, str, str]) -> None:
        self.delete("all")
        # Tk Canvas primitives are visibly jagged at avatar scale. Render at 4x
        # and downsample with Lanczos so the entire icon stays smooth.
        from PIL import Image, ImageDraw, ImageFont, ImageTk
        scale = 4
        image = Image.new("RGBA", (self.size * scale, self.size * scale), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        def box(values):
            return tuple(round(value * scale) for value in values)
        margin = 4
        ring_box = box((margin, margin, self.size - margin, self.size - margin))
        draw.ellipse(box((2, 2, self.size - 2, self.size - 2)), outline="#1b1b2a", width=2 * scale)
        draw.ellipse(ring_box, outline=BG_BAR, width=5 * scale)
        center = self.size / 2
        for index, color in enumerate(colors):
            start = 90 + index * 120
            extent = 100
            draw.arc(ring_box, start=start, end=start + extent, fill=color, width=4 * scale)
        inner = max(15, self.size * 0.22)
        draw.ellipse(box((inner, inner, self.size - inner, self.size - inner)), fill=BG_CARD, outline="#3b3b52", width=scale)
        try:
            bolt_font = ImageFont.truetype(r"C:\Windows\Fonts\seguisym.ttf", round(self.size * 0.36 * scale))
        except OSError:
            bolt_font = ImageFont.load_default()
        draw.text((center * scale + scale, center * scale + scale), "⚡", fill="#0b0b12", font=bolt_font, anchor="mm")
        draw.text((center * scale, center * scale), "⚡", fill="#fff0a8", font=bolt_font, anchor="mm")
        image = image.resize((self.size, self.size), Image.Resampling.LANCZOS)
        self._avatar_image = ImageTk.PhotoImage(image)
        self.create_image(0, 0, image=self._avatar_image, anchor="nw")

    def _press(self, event) -> None:
        self.start_pointer = (event.x_root, event.y_root)
        self.start_window = (self.app.root.winfo_x(), self.app.root.winfo_y())
        self.dragged = False

    def _move(self, event) -> None:
        dx = event.x_root - self.start_pointer[0]
        dy = event.y_root - self.start_pointer[1]
        if release_is_click(*self.start_pointer, event.x_root, event.y_root, self.DRAG_THRESHOLD):
            return
        self.dragged = True
        self.app.root.geometry(f"+{self.start_window[0] + dx}+{self.start_window[1] + dy}")

    def _release(self, event) -> None:
        if not self.dragged and release_is_click(*self.start_pointer, event.x_root, event.y_root, self.DRAG_THRESHOLD):
            self.app.set_mode("panel")
        else:
            self.app.save_preferences()


class QuotaHUDApp:
    """One Tk window with two modes and one shared data collection loop."""

    def __init__(self, root: tk.Tk, config_path: str | os.PathLike[str] = CONFIG_PATH):
        self.root = root
        self.config_path = Path(config_path)
        self.config = load_ui_config(self.config_path)
        self.mode = "avatar"
        self.snapshots = {}
        self.controls = {}
        self.collapsed_cards = self.config.get("collapsed_cards", {})
        self._save_job = None
        self.hotkey_handle = None
        self.tray_icon = None
        self.is_topmost = True

        root.title("Monitor de Cotas")
        root.configure(bg=BG_MAIN)
        root.attributes("-topmost", True)
        # Keep the panel opaque: transparency lets the editor behind it bleed through
        # and makes text and circular indicators look soft.
        root.attributes("-alpha", 1.0)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.set_mode(self.config.get("ui_mode", "avatar"), initial=True)

        self.poller = threading.Thread(target=self.background_agy_poller, daemon=True)
        self.poller.start()
        # Paint the avatar first; the initial provider read is scheduled after Tk has shown it.
        self.root.after(0, self.update_data)
        try:
            self.hotkey_handle = keyboard.add_hotkey("ctrl+shift+c", lambda: self.root.after(0, self.toggle_visibility))
        except Exception:
            self.hotkey_handle = None
        self.start_tray()

    def set_mode(self, mode: str, *, initial: bool = False, force: bool = False) -> None:
        mode = mode if mode in {"avatar", "panel"} else "avatar"
        same_mode = mode == self.mode
        if not initial and not force and same_mode:
            return
        if not initial and self.mode == "panel" and not same_mode:
            self._capture_panel_geometry()
        self.mode = mode
        self.config["ui_mode"] = mode
        for child in self.root.winfo_children():
            child.destroy()

        if mode == "avatar":
            self._show_avatar()
        else:
            self._show_panel()
        self.root.deiconify()
        if not initial:
            self.save_preferences()
        self._render_snapshots()

    def _show_avatar(self) -> None:
        size = int(self.config["avatar_size"])
        self.root.overrideredirect(True)
        if os.name == "nt":
            try:
                # Make the square Canvas background transparent while preserving
                # the colored ring, inner disc and lightning bolt.
                self.root.wm_attributes("-transparentcolor", BG_MAIN)
            except tk.TclError:
                pass
        position = self.config["avatar_position"]
        x = position["x"] if position["x"] is not None else self.root.winfo_screenwidth() - size - 28
        y = position["y"] if position["y"] is not None else 40
        self.root.geometry(f"{size}x{size}+{x}+{y}")
        self.avatar = AvatarSurface(self, size)
        self.avatar.pack(fill="both", expand=True)

    def _show_panel(self) -> None:
        if os.name == "nt":
            try:
                self.root.wm_attributes("-transparentcolor", "")
            except tk.TclError:
                pass
        self.root.overrideredirect(not bool(self.config.get("show_in_taskbar", False)))
        geometry = self.config["panel_geometry"]
        width = geometry["width"] or 520
        height = geometry["height"] or 600
        x = geometry["x"] if geometry["x"] is not None else self.root.winfo_screenwidth() - width - 20
        y = geometry["y"] if geometry["y"] is not None else 40
        self.root.geometry(f"{width}x{height}+{x}+{y}")

        outer = tk.Frame(self.root, bg=BORDER_COLOR, padx=1, pady=1)
        outer.pack(fill="both", expand=True)
        self.main_frame = tk.Frame(outer, bg=BG_MAIN, padx=12, pady=9)
        self.main_frame.pack(fill="both", expand=True)
        self._setup_header()
        self._setup_sections()
        self.root.bind("<Configure>", self._on_configure, add="+")

    def _setup_header(self) -> None:
        header = tk.Frame(self.main_frame, bg=BG_MAIN)
        header.pack(fill="x", pady=(0, 6))
        title = tk.Label(header, text="⚡ COTA MONITOR", font=("Segoe UI", 10, "bold"), fg=TEXT_MAIN, bg=BG_MAIN)
        title.pack(side="left")
        self.status_lbl = tk.Label(header, text="Aguardando dados", font=("Segoe UI", 8), fg=TEXT_MUTED, bg=BG_MAIN)
        self.status_lbl.pack(side="left")

        close = tk.Label(header, text="✕", font=("Segoe UI", 12, "bold"), fg=TEXT_MUTED, bg=BG_MAIN, cursor="hand2")
        close.pack(side="right", padx=(7, 0))
        close.bind("<Button-1>", lambda _event: self.close())
        minimize = tk.Label(header, text="—", font=("Segoe UI", 14, "bold"), fg=TEXT_MUTED, bg=BG_MAIN, cursor="hand2")
        minimize.pack(side="right", padx=6)
        minimize.bind("<Button-1>", lambda _event: self.set_mode("avatar"))
        compact = tk.Label(header, text="⊟" if self.config.get("compact_mode") else "⊞",
                           font=("Segoe UI", 12, "bold"), fg=TEXT_MUTED, bg=BG_MAIN, cursor="hand2")
        compact.pack(side="right", padx=6)
        compact.bind("<Button-1>", self.toggle_compact)
        Tooltip(compact, "Alternar tamanho compacto do painel")
        self.pin_btn = tk.Label(header, text="📌", font=("Segoe UI", 11), fg=COLOR_CYAN, bg=BG_MAIN, cursor="hand2")
        self.pin_btn.pack(side="right", padx=6)
        self.pin_btn.bind("<Button-1>", self.toggle_pin)
        refresh = tk.Label(header, text="↻", font=("Segoe UI", 15), fg=COLOR_CYAN, bg=BG_MAIN, cursor="hand2")
        refresh.pack(side="right", padx=6)
        refresh.bind("<Button-1>", self.force_update)
        for draggable in (header, title, self.status_lbl):
            draggable.bind("<ButtonPress-1>", self._start_panel_drag, add="+")
            draggable.bind("<B1-Motion>", self._do_panel_drag, add="+")

    def _setup_sections(self) -> None:
        self.controls = {}
        self.controls["agy"] = self._create_card(
            "agy", "Antigravity", COLOR_CYAN,
            [("5h", "gemini_5h", "Gemini 5 horas"), ("7d", "gemini_weekly", "Gemini 7 dias"), ("3p", "3p_5h", "Modelos Claude/GPT no Antigravity")],
        )
        self.controls["claude1"] = self._create_card(
            "claude1", "Claude Code 1", COLOR_GREEN,
            [("5h", "five_hour", "Limite de 5 horas"), ("7d", "seven_day", "Limite de 7 dias"), ("ctx", "context", "Janela de contexto"), ("tokens", "tokens", "Tokens disponíveis")],
            action_cmd=self.switch_claude1,
        )
        self.controls["claude2"] = self._create_card(
            "claude2", "Claude Code 2", COLOR_GREEN,
            [("5h", "five_hour", "Limite de 5 horas"), ("7d", "seven_day", "Limite de 7 dias"), ("ctx", "context", "Janela de contexto"), ("tokens", "tokens", "Tokens disponíveis")],
            action_cmd=self.switch_claude2,
        )
        self.controls["codex"] = self._create_card(
            "codex", "Codex", COLOR_PURPLE,
            [("5h", "five_hour", "Janela de 5 horas"), ("tokens", "tokens_5h", "Tokens usados na janela de 5 horas"), ("7d", "seven_day", "Atividade dos últimos 7 dias")],
        )

    def _create_card(self, card_key: str, title: str, accent: str, items, action_cmd=None):
        compact = bool(self.config.get("compact_mode"))
        card = tk.Frame(self.main_frame, bg=BG_CARD, padx=6 if compact else 8, pady=4 if compact else 6)
        card.pack(fill="x", pady=2 if compact else 3)
        header = tk.Frame(card, bg=BG_CARD)
        header.pack(fill="x")
        tk.Label(header, text="●", font=("Segoe UI", 7 if compact else 8), fg=accent, bg=BG_CARD).pack(side="left", padx=(0, 3 if compact else 4))
        tk.Label(header, text=title, font=("Segoe UI", 8 if compact else 9, "bold"), fg=TEXT_MAIN, bg=BG_CARD).pack(side="left")
        collapsed = bool(self.collapsed_cards.get(card_key, False))
        collapse_btn = tk.Label(header, text="▸" if collapsed else "▾", font=("Segoe UI", 10, "bold"), fg=TEXT_MUTED, bg=BG_CARD, cursor="hand2")
        collapse_btn.pack(side="right", padx=(4, 0))
        collapse_btn.bind("<Button-1>", lambda _event, key=card_key: self.toggle_card(key))
        Tooltip(collapse_btn, "Expandir/recolher esta assinatura")
        sub_info = tk.Label(header, text="Aguardando", font=("Segoe UI", 7 if compact else 8), fg=TEXT_MUTED, bg=BG_CARD)
        sub_info.pack(side="right")
        action_btn = None
        if action_cmd:
            action_btn = tk.Label(header, text=" Ativar ", font=("Segoe UI", 7 if compact else 8, "bold"), fg=BG_MAIN, bg=accent, cursor="hand2")
            action_btn.pack(side="right", padx=(6, 0))
            action_btn.bind("<Button-1>", action_cmd)

        metrics_frame = tk.Frame(card, bg=BG_CARD)
        if not collapsed:
            metrics_frame.pack(fill="x", pady=(5, 0))
        tiles = {}
        for short_label, key, full_label in items:
            tile = tk.Frame(metrics_frame, bg=BG_CARD)
            tile.pack(side="left", expand=True, fill="x")
            tk.Label(tile, text=short_label, font=("Segoe UI", 7 if compact else 8, "bold"), fg=TEXT_MUTED, bg=BG_CARD).pack()
            indicator = CircularProgressWidget(tile, size=44 if compact else 54)
            indicator.pack(pady=0 if compact else 1)
            value = tk.Label(tile, text="N/D", font=("Segoe UI", 7 if compact else 8, "bold"), fg=TEXT_MUTED, bg=BG_CARD)
            value.pack()
            reset = tk.Label(tile, text="", font=("Segoe UI", 6 if compact else 7), fg=COLOR_CYAN, bg=BG_CARD)
            reset.pack()
            tooltip = Tooltip(tile)
            tiles[key] = {"indicator": indicator, "value": value, "reset": reset, "tooltip": tooltip, "full_label": full_label}
        return {"sub_info": sub_info, "action_btn": action_btn, "accent": accent, "tiles": tiles, "body": metrics_frame, "collapse_btn": collapse_btn, "card_key": card_key}

    def _metric_tooltip(self, snapshot, full_label: str, metric) -> str:
        pct = "N/D" if metric is None or metric.remaining_pct is None else f"{metric.remaining_pct}%"
        countdown = format_countdown(metric.reset_at) if metric and metric.reset_at else "sem reset informado"
        detail = f"\nDetalhe: {metric.detail}" if metric and metric.detail else ""
        age = "desconhecida" if snapshot.source_age_seconds is None else f"há {snapshot.source_age_seconds}s"
        estimate = " (estimativa)" if snapshot.estimated or (metric and metric.estimated) else ""
        return f"{full_label}\nDisponível: {pct}\nRedefinição: {countdown}{detail}\nOrigem: {snapshot.provider}, atualizada {age}{estimate}"

    def _set_tile(self, tile, snapshot, metric) -> None:
        pct = metric.remaining_pct if metric else None
        tile["indicator"].set_value(pct)
        color = metric_color(pct)
        tile["value"].config(text="N/D" if pct is None else ("0%" if pct == 0 else f"{pct}%"), fg=color)
        tile["reset"].config(text=visible_countdown(metric.reset_at) if metric and pct is not None else "")
        tile["tooltip"].set_text(self._metric_tooltip(snapshot, tile["full_label"], metric))

    def _update_card(self, key: str, snapshot, *, claude: bool = False) -> None:
        controls = self.controls[key]
        if snapshot.status == OK:
            controls["sub_info"].config(text=snapshot.account or "Disponível", fg=TEXT_MUTED)
        else:
            status = "Sem dados" if snapshot.status == "unavailable" else "Erro na leitura"
            controls["sub_info"].config(text=status, fg=TEXT_MUTED)
        if claude and controls["action_btn"]:
            active_email = self.get_user_email()
            is_active = bool(snapshot.account and snapshot.account == active_email)
            controls["action_btn"].config(text=" ✓ ATIVA " if is_active else " Ativar ", bg=BG_MAIN if is_active else controls["accent"], fg=COLOR_GREEN if is_active else BG_MAIN, cursor="arrow" if is_active else "hand2")
        for metric_key, tile in controls["tiles"].items():
            self._set_tile(tile, snapshot, snapshot.metrics.get(metric_key))

    def toggle_card(self, card_key: str) -> None:
        """Collapse or expand one provider/account card without recollecting data."""
        controls = self.controls.get(card_key)
        if not controls:
            return
        collapsed = not bool(self.collapsed_cards.get(card_key, False))
        self.collapsed_cards[card_key] = collapsed
        if collapsed:
            controls["body"].pack_forget()
        else:
            controls["body"].pack(fill="x", pady=(5, 0))
        controls["collapse_btn"].config(text="▸" if collapsed else "▾")
        self._fit_panel_to_content()
        self.config["collapsed_cards"] = dict(self.collapsed_cards)
        self.save_preferences()

    def _fit_panel_to_content(self) -> None:
        """Resize the panel after a card changes visibility, preserving x/y and width."""
        if self.mode != "panel":
            return
        self.root.update_idletasks()
        width = max(360, self.root.winfo_width())
        height = max(220, self.root.winfo_reqheight())
        self.root.geometry(f"{width}x{height}+{self.root.winfo_x()}+{self.root.winfo_y()}")

    def _render_snapshots(self) -> None:
        if not self.snapshots:
            return
        agy = self.snapshots.get("agy")
        c1 = self.snapshots.get("claude1")
        c2 = self.snapshots.get("claude2")
        codex = self.snapshots.get("codex")
        if self.mode == "panel" and hasattr(self, "controls"):
            if agy:
                self._update_card("agy", agy)
            if c1:
                self._update_card("claude1", c1, claude=True)
            if c2:
                self._update_card("claude2", c2, claude=True)
            if codex:
                self._update_card("codex", codex)
            statuses = [s.status for s in self.snapshots.values()]
            self.status_lbl.config(text="Atualizado" if any(s == OK for s in statuses) else "Sem dados")
        if self.mode == "avatar" and hasattr(self, "avatar"):
            self.avatar.draw(tuple(self._provider_color(snapshot, key) for key, snapshot in (("agy", agy), ("claude", c1), ("codex", codex))))

    def _provider_color(self, snapshot, key: str) -> str:
        if not snapshot:
            return TEXT_MUTED
        candidates = {
            "agy": ("gemini_5h", "gemini_weekly"),
            "claude": ("five_hour", "seven_day", "context"),
            "codex": ("five_hour", "tokens_5h", "seven_day"),
        }[key]
        values = [snapshot.metrics.get(item).remaining_pct for item in candidates if snapshot.metrics.get(item) and snapshot.metrics.get(item).remaining_pct is not None]
        return metric_color(min(values) if values else None)

    def collect_snapshots(self) -> dict:
        """Collect all providers once; both UI modes consume this same result."""
        agy = load_agy_snapshot(AGY_STATUS_JSON)
        active_email = self.get_user_email()
        c1_path = r"C:\Users\MOBILTEC\.claude-1.json"
        c2_path = r"C:\Users\MOBILTEC\.claude-2.json"
        c1 = load_claude_snapshot(c1_path if os.path.exists(c1_path) else CLAUDE_STATUS_JSON)
        c2 = load_claude_snapshot(c2_path if os.path.exists(c2_path) else CLAUDE_STATUS_JSON)
        if c1.account == active_email and active_email:
            c1 = load_claude_snapshot(r"C:\Users\MOBILTEC\.claude.json")
        if c2.account == active_email and active_email:
            c2 = load_claude_snapshot(r"C:\Users\MOBILTEC\.claude.json")
        codex = load_codex_snapshot(CODEX_HISTORY_DB, CODEX_STATE_DB)
        return {"agy": agy, "claude1": c1, "claude2": c2, "codex": codex}

    def update_data(self) -> None:
        try:
            self.snapshots = self.collect_snapshots()
            self._render_snapshots()
            primary = self.snapshots["agy"].metrics.get("gemini_5h")
            if primary and primary.remaining_pct is not None and primary.remaining_pct <= 15 and not notifications_state["agy_alerted"]:
                send_windows_toast("Alerta Antigravity", f"Cota Gemini em {primary.remaining_pct}%!")
                notifications_state["agy_alerted"] = True
        finally:
            if self.root.winfo_exists():
                self.root.after(1000, self.update_data)

    def get_user_email(self) -> str:
        try:
            with open(r"C:\Users\MOBILTEC\.claude.json", "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data.get("oauthAccount", {}).get("emailAddress", "")
        except (OSError, ValueError, AttributeError):
            return ""

    def force_update(self, _event=None) -> None:
        def trigger():
            try:
                subprocess.run(["agy", "--print", "/usage"], capture_output=True, timeout=30,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            except Exception:
                pass
        threading.Thread(target=trigger, daemon=True).start()
        self.update_data()

    def switch_claude1(self, _event=None) -> None:
        self._switch_claude(r"C:\Users\MOBILTEC\.claude-1.json")

    def switch_claude2(self, _event=None) -> None:
        self._switch_claude(r"C:\Users\MOBILTEC\.claude-2.json")

    def _switch_claude(self, source: str) -> None:
        import shutil
        try:
            shutil.copy(source, r"C:\Users\MOBILTEC\.claude.json")
        except OSError:
            pass
        self.update_data()

    def toggle_pin(self, _event=None) -> None:
        self.is_topmost = not self.is_topmost
        self.root.attributes("-topmost", self.is_topmost)
        if hasattr(self, "pin_btn"):
            self.pin_btn.config(fg=COLOR_CYAN if self.is_topmost else TEXT_MUTED)

    def toggle_compact(self, _event=None) -> None:
        """Switch density while keeping the current panel position."""
        if self.mode != "panel":
            return
        self._capture_panel_geometry()
        geometry = self.config["panel_geometry"]
        compact = not bool(self.config.get("compact_mode"))
        if compact:
            geometry["width"] = max(400, round((geometry["width"] or 520) * 0.84))
            geometry["height"] = max(460, round((geometry["height"] or 600) * 0.84))
        else:
            geometry["width"] = max(520, round((geometry["width"] or 440) / 0.84))
            geometry["height"] = max(600, round((geometry["height"] or 500) / 0.84))
        self.config["compact_mode"] = compact
        self.set_mode("panel", initial=False, force=True)

    def _start_panel_drag(self, event) -> None:
        self._panel_drag = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())

    def _do_panel_drag(self, event) -> None:
        if not hasattr(self, "_panel_drag"):
            return
        start_x, start_y, window_x, window_y = self._panel_drag
        self.root.geometry(f"+{window_x + event.x_root - start_x}+{window_y + event.y_root - start_y}")

    def toggle_visibility(self) -> None:
        if not self.root.winfo_viewable():
            self.root.deiconify()
        elif self.mode == "avatar":
            self.set_mode("panel")
        else:
            self.set_mode("avatar")

    def _capture_panel_geometry(self) -> None:
        if self.mode != "panel":
            return
        width, height = self.root.winfo_width(), self.root.winfo_height()
        if width > 300 and height > 200:
            self.config["panel_geometry"].update({"x": self.root.winfo_x(), "y": self.root.winfo_y(), "width": width, "height": height})

    def _on_configure(self, _event=None) -> None:
        if self.mode == "panel":
            if self._save_job:
                self.root.after_cancel(self._save_job)
            self._save_job = self.root.after(400, self.save_preferences)

    def save_preferences(self) -> None:
        if self.mode == "avatar":
            self.config["avatar_position"] = {"x": self.root.winfo_x(), "y": self.root.winfo_y()}
        else:
            self._capture_panel_geometry()
        self.config = save_ui_config(self.config, self.config_path)
        self._save_job = None

    def _make_tray_image(self):
        from PIL import Image, ImageDraw
        scale = 4
        image = Image.new("RGBA", (64 * scale, 64 * scale), BG_MAIN)
        draw = ImageDraw.Draw(image)
        def box(value):
            return tuple(int(item * scale) for item in value)
        draw.ellipse(box((2, 2, 62, 62)), fill=BG_CARD, outline="#1b1b2a", width=2 * scale)
        draw.arc(box((5, 5, 59, 59)), 90, 190, fill=COLOR_GREEN, width=5 * scale)
        draw.arc(box((5, 5, 59, 59)), 210, 310, fill=COLOR_YELLOW, width=5 * scale)
        draw.arc(box((5, 5, 59, 59)), 330, 70, fill=COLOR_GREEN, width=5 * scale)
        draw.ellipse(box((17, 17, 47, 47)), fill=BG_CARD, outline="#3b3b52", width=scale)
        draw.polygon([tuple(int(v * scale) for v in point) for point in [(35, 18), (24, 33), (31, 33), (27, 47), (42, 29), (35, 29)]], fill="#fff0a8", outline="#fff8d0")
        return image.resize((64, 64), Image.Resampling.LANCZOS)

    def start_tray(self) -> None:
        try:
            import pystray
            menu = pystray.Menu(
                pystray.MenuItem("Abrir painel", lambda _icon, _item: self.root.after(0, lambda: (self.root.deiconify(), self.set_mode("panel")))),
                pystray.MenuItem("Mostrar avatar", lambda _icon, _item: self.root.after(0, lambda: (self.root.deiconify(), self.set_mode("avatar")))),
                pystray.MenuItem("Ocultar", lambda _icon, _item: self.root.after(0, self.root.withdraw)),
                pystray.MenuItem("Sair", lambda _icon, _item: self.root.after(0, self.close)),
            )
            self.tray_icon = pystray.Icon("cotas-gui", self._make_tray_image(), "Monitor de Cotas", menu)
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
        except Exception:
            self.tray_icon = None

    def background_agy_poller(self) -> None:
        while True:
            try:
                subprocess.run(["agy", "--print", "/usage"], capture_output=True, timeout=30,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            except Exception:
                pass
            time.sleep(120)

    def close(self) -> None:
        self.save_preferences()
        if self.hotkey_handle is not None:
            try:
                keyboard.remove_hotkey(self.hotkey_handle)
            except Exception:
                pass
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        self.root.destroy()


def acquire_instance_mutex():
    """Return (is_first_instance, native_handle) on Windows."""
    if os.name != "nt":
        return True, None
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, "Local\\CotasGuiSingleton")
    already_exists = kernel32.GetLastError() == 183
    if already_exists and handle:
        kernel32.CloseHandle(handle)
        return False, None
    return bool(handle), handle


def main() -> None:
    enable_windows_dpi_awareness()
    first, mutex = acquire_instance_mutex()
    if not first:
        return
    root = tk.Tk()
    try:
        QuotaHUDApp(root)
        root.mainloop()
    finally:
        if mutex and os.name == "nt":
            ctypes.windll.kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    main()
