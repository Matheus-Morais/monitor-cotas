import os
import sys
import time
import json
import datetime
import sqlite3
import subprocess
import threading
import tkinter as tk
from tkinter import ttk
import keyboard
from quota_core import (
    OK,
    format_countdown,
    load_agy_snapshot,
    load_claude_snapshot,
    load_codex_snapshot,
)

AGY_STATUS_JSON = r"C:\Users\MOBILTEC\scripts\agy-statusline-input.json"
CLAUDE_STATUS_JSON = r"C:\Users\MOBILTEC\scripts\claude-statusline-input.json"
CODEX_CONFIG = r"C:\Users\MOBILTEC\.codex\config.toml"
CODEX_STATE_DB = r"C:\Users\MOBILTEC\.codex\state_5.sqlite"
CODEX_HISTORY_DB = r"C:\Users\MOBILTEC\.codex\thread_history_1.sqlite"

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

def send_windows_toast(title, message):
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
            creationflags=subprocess.CREATE_NO_WINDOW
        )
    except Exception as e:
                import traceback
                traceback.print_exc()

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
    def __init__(self, root):
        self.root = root
        self.root.title("Monitor de Cotas")
        self.root.geometry("520x600")
        self.root.configure(bg=BG_MAIN)
        self.root.overrideredirect(True) # Janela sem borda do windows
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.90)
        self.is_topmost = True

        # Posiciona no canto superior direito da tela
        screen_w = self.root.winfo_screenwidth()
        self.root.geometry(f"520x600+{screen_w - 540}+40")

        # Borda externa
        self.outer_frame = tk.Frame(self.root, bg=BORDER_COLOR, padx=1, pady=1)
        self.outer_frame.pack(fill="both", expand=True)

        self.main_frame = tk.Frame(self.outer_frame, bg=BG_MAIN, padx=12, pady=10)
        self.main_frame.pack(fill="both", expand=True)

        # Drag & Drop da janela
        self._drag_data = {"x": 0, "y": 0}
        self.setup_header()
        self.setup_sections()

        # Inicia background poller
        self.poller = threading.Thread(target=self.background_agy_poller, daemon=True)
        self.poller.start()

        # Loop de atualizacao a cada 1s
        self.update_data()
        
        # Global hotkey
        keyboard.add_hotkey('ctrl+shift+c', lambda: self.root.after(0, self.toggle_visibility))

    def toggle_visibility(self):
        if self.root.winfo_viewable():
            self.root.withdraw()
        else:
            self.root.deiconify()

    def get_user_email(self):
        try:
            with open(r"C:\Users\MOBILTEC\.claude.json", "r", encoding="utf-8") as f:
                d = json.load(f)
            return d.get("oauthAccount", {}).get("emailAddress", "")
        except:
            return ""

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

    def force_update(self, event=None):
        def trigger():
            try:
                subprocess.run(
                    ["agy", "--print", "/usage"],
                    capture_output=True,
                    timeout=30,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                )
            except:
                pass
        threading.Thread(target=trigger, daemon=True).start()
        self.update_data()

    def switch_claude1(self, event=None):
        import shutil, os
        try:
            shutil.copy(r'C:\Users\MOBILTEC\.claude-1.json', r'C:\Users\MOBILTEC\.claude.json')
        except:
            pass
        self.update_data()
        
    def switch_claude2(self, event=None):
        import shutil, os
        try:
            shutil.copy(r'C:\Users\MOBILTEC\.claude-2.json', r'C:\Users\MOBILTEC\.claude.json')
        except:
            pass
        self.update_data()

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
            ("Tokens 5h", "codex_tok"),
            ("Atividade 7 Dias", "codex_7d")
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
            controls["cd"].config(text="Sem dados", fg=TEXT_MUTED)
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

    def update_data(self):
        self.clock_lbl.config(text=datetime.datetime.now().strftime("%H:%M:%S"))

        agy = load_agy_snapshot(AGY_STATUS_JSON)
        for key, core_key in (("gemini_5h", "gemini_5h"), ("gemini_w", "gemini_weekly"), ("agy_3p", "3p_5h")):
            self._set_metric(self.agy_bars, key, agy.metrics.get(core_key))
        primary = agy.metrics.get("gemini_5h")
        if primary and primary.remaining_pct is not None and primary.remaining_pct <= 15 and not notifications_state["agy_alerted"]:
            send_windows_toast("Alerta Antigravity", f"Cota Gemini em {primary.remaining_pct}%!")
            notifications_state["agy_alerted"] = True

        active_email = self.get_user_email()
        c1_path = r"C:\Users\MOBILTEC\.claude-1.json"
        c2_path = r"C:\Users\MOBILTEC\.claude-2.json"
        c1 = load_claude_snapshot(c1_path if os.path.exists(c1_path) else r"C:\Users\MOBILTEC\.claude.json")
        c2 = load_claude_snapshot(c2_path if os.path.exists(c2_path) else r"C:\Users\MOBILTEC\.claude.json")
        if c1.account == active_email and active_email:
            c1 = load_claude_snapshot(r"C:\Users\MOBILTEC\.claude.json")
        if c2.account == active_email and active_email:
            c2 = load_claude_snapshot(r"C:\Users\MOBILTEC\.claude.json")
        self._update_claude_bars(self.claude_bars, c1)
        self._update_claude_bars(self.claude2_bars, c2)

        codex = load_codex_snapshot(CODEX_HISTORY_DB, CODEX_STATE_DB)
        self._set_metric(self.codex_bars, "codex_5h", codex.metrics.get("five_hour"))
        self._set_metric(self.codex_bars, "codex_tok", codex.metrics.get("tokens_5h"))
        self._set_metric(self.codex_bars, "codex_7d", codex.metrics.get("seven_day"))

        self.root.after(1000, self.update_data)

    def background_agy_poller(self):
        while True:
            try:
                subprocess.run(
                    ["agy", "--print", "/usage"],
                    capture_output=True,
                    timeout=30,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                )
            except Exception:
                pass
            time.sleep(120)

def main():
    root = tk.Tk()
    app = QuotaHUDApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
