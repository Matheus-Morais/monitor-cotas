import sys
import datetime
import time
import queue
import ctypes
from ctypes import wintypes
import tkinter as tk
from tkinter import ttk
import keyboard
from quota_core import (
    OK,
    format_countdown,
)
from collector import CollectorWorker, DashboardSnapshot, QuotaCollector
from config import MonitorConfig, load_config, save_config
from history import HistoryStore
from notifier import OptionalTray, send_windows_toast
from profiles import switch_profile

# Cores Tema Escuro Moderno (Catppuccin Mocha)
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

notifications_state = {
    "claude_alerted": False,
    "agy_alerted": False,
    "codex_alerted": False
}

_singleton_handle = None


def acquire_single_instance() -> bool:
    """Prevent duplicate HUD windows when ``cotas-gui`` is launched twice."""
    global _singleton_handle
    if sys.platform != "win32":
        return True

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    create_mutex.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    handle = create_mutex(None, False, "Local\\MonitorCotas")
    if not handle:
        return True
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        close_handle(handle)
        return False
    _singleton_handle = handle
    return True

class ProgressBarWidget(tk.Canvas):
    def __init__(self, parent, width=280, height=8, **kwargs):
        super().__init__(parent, width=width, height=height, bg=BG_CARD, highlightthickness=0, **kwargs)
        self.w = width
        self.h = height

    def set_value(self, pct, color):
        self.delete("all")
        # Fundo da barra
        self.create_rectangle(0, 0, self.w, self.h, fill=BG_BAR, width=0)
        # Preenchimento
        fill_w = max(0, min(self.w, int((pct / 100.0) * self.w)))
        if fill_w > 0:
            self.create_rectangle(0, 0, fill_w, self.h, fill=color, width=0)

class QuotaHUDApp:
    def __init__(self, root, config: MonitorConfig, config_path=None):
        self.root = root
        self.config = config.normalized()
        self.config_path = config_path
        self.results = queue.Queue()
        self.collector = QuotaCollector(self.config)
        self.worker = CollectorWorker(self.collector, self.results)
        self.history = HistoryStore(self.config.history_db)
        self.tray = OptionalTray(
            lambda: self.root.after(0, self.show_window),
            lambda: self.root.after(0, self.close),
        )
        self.hotkey_handle = None
        self.last_snapshot = None
        self.last_prune_at = 0.0
        self.root.title("Monitor de Cotas")
        self.root.geometry(self._initial_geometry())
        self.root.configure(bg=BG_MAIN)
        self.root.overrideredirect(True) # Janela sem borda do windows
        self.root.attributes("-topmost", self.config.topmost)
        self.root.attributes("-alpha", self.config.opacity)
        self.is_topmost = self.config.topmost

        # Borda externa
        self.outer_frame = tk.Frame(self.root, bg=BORDER_COLOR, padx=1, pady=1)
        self.outer_frame.pack(fill="both", expand=True)

        self.main_frame = tk.Frame(self.outer_frame, bg=BG_MAIN, padx=12, pady=10)
        self.main_frame.pack(fill="both", expand=True)

        # Drag & Drop da janela
        self._drag_data = {"x": 0, "y": 0}
        self.setup_header()
        self.setup_sections()

        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.worker.start()
        self.drain_results()
        self.tray.start()
        try:
            self.hotkey_handle = keyboard.add_hotkey(
                self.config.global_hotkey,
                lambda: self.root.after(0, self.toggle_visibility),
            )
        except (OSError, ValueError):
            self.hotkey_handle = None

    def _initial_geometry(self):
        width = self.config.window_width
        height = self.config.window_height
        if self.config.window_x is not None and self.config.window_y is not None:
            return f"{width}x{height}+{self.config.window_x}+{self.config.window_y}"
        screen_w = self.root.winfo_screenwidth()
        return f"{width}x{height}+{screen_w - width - 20}+40"

    def show_window(self):
        self.root.deiconify()
        self.root.lift()

    def close(self):
        self.config.window_width = self.root.winfo_width() or self.config.window_width
        self.config.window_height = self.root.winfo_height() or self.config.window_height
        self.config.window_x = self.root.winfo_x()
        self.config.window_y = self.root.winfo_y()
        self.config.topmost = self.is_topmost
        try:
            save_config(self.config, self.config_path)
        except OSError:
            pass
        if self.hotkey_handle is not None:
            try:
                keyboard.remove_hotkey(self.hotkey_handle)
            except (KeyError, OSError):
                pass
        self.worker.stop(timeout=2.0)
        self.tray.stop()
        self.root.destroy()

    def toggle_visibility(self):
        if self.root.winfo_viewable():
            self.root.withdraw()
        else:
            self.root.deiconify()

    def get_user_email(self):
        return self.last_snapshot.claude.account if self.last_snapshot else ""

    def setup_header(self):
        header = tk.Frame(self.main_frame, bg=BG_MAIN)
        header.pack(fill="x", pady=(0, 10))

        # Suporte a arrastar a janela clicando no topo
        header.bind("<ButtonPress-1>", self.start_drag)
        header.bind("<ButtonRelease-1>", self.stop_drag)
        header.bind("<B1-Motion>", self.do_drag)

        title_lbl = tk.Label(header, text="⚡ COTA MONITOR", font=("Segoe UI", 11, "bold"), fg=TEXT_MAIN, bg=BG_MAIN)
        title_lbl.pack(side="left")
        title_lbl.bind("<ButtonPress-1>", self.start_drag)
        title_lbl.bind("<B1-Motion>", self.do_drag)

        self.clock_lbl = tk.Label(header, text="12:00:00", font=("Segoe UI", 10), fg=TEXT_MUTED, bg=BG_MAIN)
        self.clock_lbl.pack(side="left", padx=8)

        email_str = self.get_user_email()
        self.email_lbl = tk.Label(header, text=email_str, font=("Segoe UI", 9), fg=COLOR_CYAN, bg=BG_MAIN)
        self.email_lbl.pack(side="left", padx=4)

        self.status_lbl = tk.Label(header, text="Coletando...", font=("Segoe UI", 8), fg=TEXT_MUTED, bg=BG_MAIN)
        self.status_lbl.pack(side="left", padx=4)

        # Botoes: Pin (Sempre no topo), Minimizar, Fechar
        close_btn = tk.Label(header, text="✕", font=("Segoe UI", 14, "bold"), fg=TEXT_MUTED, bg=BG_MAIN, cursor="hand2")
        close_btn.pack(side="right", padx=(8, 0))
        close_btn.bind("<Button-1>", lambda e: self.root.destroy())

        self.pin_btn = tk.Label(header, text="📌", font=("Segoe UI", 14), fg=COLOR_CYAN, bg=BG_MAIN, cursor="hand2")
        self.pin_btn.pack(side="right", padx=6)
        self.pin_btn.bind("<Button-1>", self.toggle_pin)

        self.refresh_btn = tk.Label(header, text="🔄", font=("Segoe UI", 14), fg=COLOR_CYAN, bg=BG_MAIN, cursor="hand2")
        self.refresh_btn.pack(side="right", padx=6)
        self.refresh_btn.bind("<Button-1>", self.force_update)

        history_btn = tk.Label(header, text="▤", font=("Segoe UI", 14), fg=COLOR_CYAN, bg=BG_MAIN, cursor="hand2")
        history_btn.pack(side="right", padx=6)
        history_btn.bind("<Button-1>", self.show_history)

    def start_drag(self, event):
        self._drag_data["x"] = event.x
        self._drag_data["y"] = event.y

    def stop_drag(self, event):
        self._drag_data["x"] = 0
        self._drag_data["y"] = 0

    def do_drag(self, event):
        deltax = event.x - self._drag_data["x"]
        deltay = event.y - self._drag_data["y"]
        x = self.root.winfo_x() + deltax
        y = self.root.winfo_y() + deltay
        self.root.geometry(f"+{x}+{y}")

    def toggle_pin(self, event):
        self.is_topmost = not self.is_topmost
        self.root.attributes("-topmost", self.is_topmost)
        self.pin_btn.config(fg=COLOR_CYAN if self.is_topmost else TEXT_MUTED)

    def show_history(self, event=None):
        window = tk.Toplevel(self.root)
        window.title("Histórico de Cotas")
        window.geometry("760x420")
        window.configure(bg=BG_MAIN)
        columns = ("hora", "provedor", "métrica", "restante", "status", "detalhe")
        tree = ttk.Treeview(window, columns=columns, show="headings")
        headings = {
            "hora": "Data/hora",
            "provedor": "Provedor",
            "métrica": "Métrica",
            "restante": "Restante",
            "status": "Status",
            "detalhe": "Detalhe",
        }
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=110 if column != "detalhe" else 190, anchor="w")
        tree.pack(fill="both", expand=True, padx=10, pady=10)
        try:
            rows = self.history.recent(limit=100)
        except OSError:
            rows = []
        for collected_at, provider, metric, status, remaining, _reset, detail, estimated in rows:
            timestamp = datetime.datetime.fromtimestamp(collected_at).strftime("%d/%m %H:%M:%S")
            remaining_text = "N/D" if remaining is None else f"{remaining}%"
            if estimated:
                remaining_text += " (est.)"
            tree.insert("", "end", values=(timestamp, provider, metric, remaining_text, status, detail))

    def force_update(self, event=None):
        self.worker.refresh_now()

    def switch_claude1(self, event=None):
        try:
            switch_profile(
                self.config.claude_active_json,
                self.config.claude_profile_1_json,
                self.config.history_db.parent / "backups",
            )
            self.worker.refresh_now()
        except (OSError, FileNotFoundError) as exc:
            send_windows_toast("Monitor de Cotas", f"Não foi possível ativar o perfil: {exc}")
        
    def switch_claude2(self, event=None):
        try:
            switch_profile(
                self.config.claude_active_json,
                self.config.claude_profile_2_json,
                self.config.history_db.parent / "backups",
            )
            self.worker.refresh_now()
        except (OSError, FileNotFoundError) as exc:
            send_windows_toast("Monitor de Cotas", f"Não foi possível ativar o perfil: {exc}")

    def setup_sections(self):
        # Section 1: Antigravity
        self.agy_frame, self.agy_bars = self.create_card("Antigravity (agy)", COLOR_CYAN, [
            ("Gemini (5h)", "gemini_5h"),
            ("Gemini (Semanal)", "gemini_w"),
            ("Claude/GPT no agy", "agy_3p")
        ])

        # Section 2: Claude Code (Principal)
        self.claude_frame, self.claude_bars = self.create_card("Claude Code 1", COLOR_GREEN, [
            ("Limite 5 Horas", "claude_5h"),
            ("Limite 7 Dias", "claude_7d"),
            ("Janela Contexto", "claude_ctx")
        ], action_text="  Ativar  ", action_cmd=self.switch_claude1)

        # Section 2.5: Claude Code (Secundária)
        self.claude2_frame, self.claude2_bars = self.create_card("Claude Code 2", COLOR_GREEN, [
            ("Limite 5 Horas", "c2_5h"),
            ("Limite 7 Dias", "c2_7d"),
            ("Ativa?", "c2_ctx")
        ], action_text="  Ativar  ", action_cmd=self.switch_claude2)

        # Section 3: Codex
        self.codex_frame, self.codex_bars = self.create_card("Codex (ChatGPT Plus)", COLOR_PURPLE, [
            ("Janela 5 Horas", "codex_5h"),
            ("Tokens observados 5h", "codex_tok"),
            ("Cota semanal", "codex_7d")
        ])

    def create_card(self, title, accent_color, items, action_text=None, action_cmd=None):
        card = tk.Frame(self.main_frame, bg=BG_CARD, padx=8, pady=8)
        card.pack(fill="x", pady=4)

        header = tk.Frame(card, bg=BG_CARD)
        header.pack(fill="x", pady=(0, 4))
        
        dot = tk.Label(header, text="●", font=("Segoe UI", 9), fg=accent_color, bg=BG_CARD)
        dot.pack(side="left", padx=(0, 4))
        
        lbl = tk.Label(header, text=title, font=("Segoe UI", 9, "bold"), fg=TEXT_MAIN, bg=BG_CARD)
        lbl.pack(side="left")

        if action_text and action_cmd:
            btn = tk.Label(header, text=action_text, font=("Segoe UI", 8, "bold"), fg=BG_MAIN, bg=accent_color, cursor="hand2")
            btn.pack(side="right", padx=(6, 0))
            btn.bind("<Button-1>", action_cmd)

        sub_info = tk.Label(header, text="", font=("Segoe UI", 8), fg=TEXT_MUTED, bg=BG_CARD)
        sub_info.pack(side="right")

        bars = {"sub_info": sub_info}
        if action_text and action_cmd:
            bars["action_btn"] = btn
        for label_text, key in items:
            row = tk.Frame(card, bg=BG_CARD)
            row.pack(fill="x", pady=2)

            name_lbl = tk.Label(row, text=label_text, font=("Segoe UI", 8), fg=TEXT_MUTED, bg=BG_CARD, width=20, anchor="w")
            name_lbl.pack(side="left")

            bar = ProgressBarWidget(row, width=160, height=6)
            bar.pack(side="left", padx=4)

            val_lbl = tk.Label(row, text="--%", font=("Segoe UI", 8, "bold"), fg=TEXT_MAIN, bg=BG_CARD, width=6, anchor="e")
            val_lbl.pack(side="left")

            cd_lbl = tk.Label(row, text="", font=("Segoe UI", 8), fg=COLOR_CYAN, bg=BG_CARD, anchor="w")
            cd_lbl.pack(side="left", padx=4)

            bars[key] = {"bar": bar, "val": val_lbl, "cd": cd_lbl}

        return card, bars

    @staticmethod
    def _metric_color(remaining_pct):
        if remaining_pct is None:
            return TEXT_MUTED
        return COLOR_GREEN if remaining_pct > 30 else COLOR_YELLOW if remaining_pct > 10 else COLOR_RED

    def _set_metric(self, bars, key, metric):
        controls = bars[key]
        remaining = metric.remaining_pct if metric else None
        if remaining is None:
            controls["bar"].set_value(0, BG_BAR)
            controls["val"].config(text="N/D", fg=TEXT_MUTED)
            controls["cd"].config(text=metric.detail if metric and metric.detail else "Sem dados", fg=TEXT_MUTED)
            return
        color = self._metric_color(remaining)
        controls["bar"].set_value(remaining, color)
        controls["val"].config(text="ESGOTADO" if remaining == 0 else f"{remaining}%", fg=color)
        countdown = format_countdown(metric.reset_at) if metric.reset_at else ""
        detail = f" | {metric.detail}" if metric.detail and countdown else metric.detail
        controls["cd"].config(text=f"{countdown}{detail}" or "Disponível", fg=COLOR_CYAN)

    def _update_claude_bars(self, bars, snapshot):
        if snapshot.status != OK:
            bars["sub_info"].config(text="Não configurada" if snapshot.status == "unavailable" else "Erro na leitura")
        else:
            bars["sub_info"].config(text=snapshot.account or "Conta")
        if "action_btn" in bars:
            active_email = self.get_user_email()
            if snapshot.account and snapshot.account == active_email:
                bars["action_btn"].config(text=" ✓ ATIVA ", bg=BG_MAIN, fg=COLOR_GREEN, cursor="arrow")
            else:
                bars["action_btn"].config(text="  Ativar  ", bg=COLOR_GREEN, fg=BG_MAIN, cursor="hand2")
        self._set_metric(bars, "claude_5h" if bars is self.claude_bars else "c2_5h", snapshot.metrics.get("five_hour"))
        self._set_metric(bars, "claude_7d" if bars is self.claude_bars else "c2_7d", snapshot.metrics.get("seven_day"))
        self._set_metric(bars, "claude_ctx" if bars is self.claude_bars else "c2_ctx", snapshot.metrics.get("context"))

    def _handle_alerts(self, snapshot):
        try:
            events = self.history.evaluate_alerts(
                snapshot,
                self.config.alert_threshold_pct,
                self.config.alert_recovery_pct,
                self.config.alert_cooldown_seconds,
            )
        except OSError:
            events = []
        names = {
            "antigravity": "Antigravity",
            "claude": "Claude Code",
            "claude_profile_1": "Claude Code 1",
            "claude_profile_2": "Claude Code 2",
            "codex": "Codex",
        }
        for event in events:
            send_windows_toast(
                f"{names.get(event.provider, event.provider)} - {event.message}",
                f"{event.metric}: {event.remaining_pct}%",
            )

    def render_snapshot(self, snapshot: DashboardSnapshot):
        self.last_snapshot = snapshot
        self.email_lbl.config(text=snapshot.claude.account)
        if snapshot.collector_error:
            self.status_lbl.config(text="Erro na coleta", fg=COLOR_RED)
        else:
            self.status_lbl.config(text=f"Atualizado {datetime.datetime.fromtimestamp(snapshot.collected_at).strftime('%H:%M:%S')}", fg=TEXT_MUTED)

        agy = snapshot.agy
        for key, core_key in (("gemini_5h", "gemini_5h"), ("gemini_w", "gemini_weekly"), ("agy_3p", "3p_5h")):
            self._set_metric(self.agy_bars, key, agy.metrics.get(core_key))
        self._update_claude_bars(self.claude_bars, snapshot.claude_profile_1)
        self._update_claude_bars(self.claude2_bars, snapshot.claude_profile_2)
        source = "Cota real via rollout" if snapshot.codex.metadata.get("rate_limit_source") else "Estimativa local"
        self.codex_bars["sub_info"].config(text=source)
        self._set_metric(self.codex_bars, "codex_5h", snapshot.codex.metrics.get("five_hour"))
        self._set_metric(self.codex_bars, "codex_tok", snapshot.codex.metrics.get("tokens_5h"))
        self._set_metric(self.codex_bars, "codex_7d", snapshot.codex.metrics.get("seven_day"))

    def drain_results(self):
        self.clock_lbl.config(text=datetime.datetime.now().strftime("%H:%M:%S"))
        latest = None
        while True:
            try:
                latest = self.results.get_nowait()
            except queue.Empty:
                break
        if latest is not None:
            self.render_snapshot(latest)
            try:
                self.history.record_dashboard(latest)
                self._handle_alerts(latest)
                if time.time() - self.last_prune_at >= 3600:
                    self.history.prune(self.config.history_retention_days)
                    self.last_prune_at = time.time()
            except OSError:
                self.status_lbl.config(text="Histórico indisponível", fg=COLOR_YELLOW)
        self.root.after(100, self.drain_results)

def main():
    if not acquire_single_instance():
        return
    root = tk.Tk()
    config_path = None
    if "--config" in sys.argv:
        index = sys.argv.index("--config")
        if index + 1 < len(sys.argv):
            config_path = sys.argv[index + 1]
    app = QuotaHUDApp(root, load_config(config_path), config_path)
    root.mainloop()

if __name__ == "__main__":
    main()
