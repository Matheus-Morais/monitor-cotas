import os
import sys
import time
import json
import re
import datetime
import sqlite3
import subprocess
import ctypes
import threading
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich import box
from rich.console import Group, Console
from rich.progress import Progress, BarColumn, TextColumn
from quota_core import (
    ERROR,
    OK,
    ProviderSnapshot,
    UNAVAILABLE,
    format_countdown,
    load_agy_snapshot,
    load_claude_snapshot,
    load_codex_snapshot,
)

# Configura encoding UTF-8 garantido no Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

AGY_STATUS_JSON = r"C:\Users\MOBILTEC\scripts\agy-statusline-input.json"
CLAUDE_STATUS_JSON = r"C:\Users\MOBILTEC\scripts\claude-statusline-input.json"
CODEX_CONFIG = r"C:\Users\MOBILTEC\.codex\config.toml"
CODEX_AUTH = r"C:\Users\MOBILTEC\.codex\auth.json"
CODEX_STATE_DB = r"C:\Users\MOBILTEC\.codex\state_5.sqlite"
CODEX_HISTORY_DB = r"C:\Users\MOBILTEC\.codex\thread_history_1.sqlite"

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
    except Exception:
        pass

def update_window_title(agy_pct, claude_pct, codex_pct):
    if sys.platform == "win32":
        try:
            title = f"[AGY: {agy_pct}% | CLAUDE: {claude_pct}% | CODEX: {codex_pct}%] - Monitor de Cotas"
            ctypes.windll.kernel32.SetConsoleTitleW(title)
        except Exception:
            pass

def get_file_age(filepath):
    """Retorna quanto tempo faz desde a ultima gravacao do arquivo."""
    if not os.path.exists(filepath):
        return "Nenhum dado"
    try:
        mtime = os.path.getmtime(filepath)
        diff = int(time.time() - mtime)
        if diff < 60:
            return f"ha {diff}s"
        elif diff < 3600:
            return f"ha {diff // 60}m"
        else:
            return f"ha {diff // 3600}h"
    except Exception:
        return ""


def metric_info(metric):
    if metric is None or metric.remaining_pct is None:
        return "N/D"
    countdown = format_countdown(metric.reset_at) if metric.reset_at else ""
    if countdown and metric.detail:
        return f"{countdown} | {metric.detail}"
    return metric.detail or countdown or "Disponivel"


def metric_color(remaining_pct):
    if remaining_pct is None:
        return "bright_black"
    return "green" if remaining_pct > 30 else "yellow" if remaining_pct > 10 else "red"


def add_metric_task(progress, label, metric):
    remaining = metric.remaining_pct if metric else None
    color = metric_color(remaining)
    progress.add_task(
        f"[{color}]{label:<26}[/{color}]",
        total=100,
        completed=remaining or 0,
        info=metric_info(metric),
    )

def get_agy_panel():
    snapshot = load_agy_snapshot(AGY_STATUS_JSON)
    prog = Progress(
        TextColumn("{task.description}"),
        BarColumn(bar_width=30),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("{task.fields[info]}")
    )
    labels = [
        ("Gemini (5h)", "gemini_5h"),
        ("Gemini (Semanal)", "gemini_weekly"),
        ("Claude/GPT no agy (5h)", "3p_5h"),
        ("Claude/GPT no agy (Semanal)", "3p_weekly"),
    ]
    for label, key in labels:
        add_metric_task(prog, label, snapshot.metrics.get(key))

    primary = snapshot.metrics.get("gemini_5h")
    primary_pct = primary.remaining_pct if primary and primary.remaining_pct is not None else "N/D"
    if primary and primary.remaining_pct is not None:
        if primary.remaining_pct <= 15 and not notifications_state["agy_alerted"]:
            send_windows_toast("Alerta Antigravity (agy)", f"Cota do Gemini 5h esta em {primary.remaining_pct}%!")
            notifications_state["agy_alerted"] = True
        elif primary.remaining_pct > 80 and notifications_state["agy_alerted"]:
            send_windows_toast("Antigravity Restabelecido", "A cota do Gemini foi totalmente renovada!")
            notifications_state["agy_alerted"] = False

    status = "Sem dados" if snapshot.status != OK else ""
    age = "Nenhum dado" if snapshot.source_age_seconds is None else get_file_age(AGY_STATUS_JSON)
    title = f"[bold cyan]Antigravity (agy)[/bold cyan] [dim]• {snapshot.plan or 'Plano desconhecido'}"
    if snapshot.model:
        title += f" | {snapshot.model}"
    title += f" (Atividade: {age})"
    if status:
        title += f" • [yellow]{status}[/yellow]"
    return Panel(prog, title=title, border_style="cyan", expand=True), primary_pct

def get_claude_panel():
    snapshot = load_claude_snapshot(CLAUDE_STATUS_JSON)
    prog = Progress(
        TextColumn("{task.description}"),
        BarColumn(bar_width=30),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("{task.fields[info]}")
    )
    add_metric_task(prog, "Limite 5 Horas (Claude Pro)", snapshot.metrics.get("five_hour"))
    add_metric_task(prog, "Limite 7 Dias (Claude Pro)", snapshot.metrics.get("seven_day"))
    add_metric_task(prog, "Janela de Contexto (Tokens)", snapshot.metrics.get("context"))

    primary = snapshot.metrics.get("five_hour")
    primary_pct = primary.remaining_pct if primary and primary.remaining_pct is not None else "N/D"
    if primary and primary.remaining_pct is not None:
        if primary.remaining_pct <= 15 and not notifications_state["claude_alerted"]:
            send_windows_toast("Alerta Claude Code", f"Sua cota do Claude Pro esta em {primary.remaining_pct}%!")
            notifications_state["claude_alerted"] = True
        elif primary.remaining_pct > 80 and notifications_state["claude_alerted"]:
            send_windows_toast("Claude Pro Restabelecido", "A janela de 5h do Claude foi renovada!")
            notifications_state["claude_alerted"] = False

    age = "Nenhum dado" if snapshot.source_age_seconds is None else get_file_age(CLAUDE_STATUS_JSON)
    title = f"[bold green]Claude Code[/bold green] [dim]• Assinatura Pro"
    if snapshot.model:
        title += f" | Modelo: {snapshot.model}"
    title += f" (Atividade: {age})"
    if snapshot.status != OK:
        title += " • [yellow]Sem dados[/yellow]"
    return Panel(prog, title=title, border_style="green", expand=True), primary_pct

def get_codex_panel():
    model = "gpt-5.6-luna"
    
    if os.path.exists(CODEX_CONFIG):
        try:
            with open(CODEX_CONFIG, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.startswith("model ="):
                        model = line.split("=")[1].strip().strip('"')
        except Exception:
            pass

    snapshot = load_codex_snapshot(CODEX_HISTORY_DB, CODEX_STATE_DB, model=model)
    prog = Progress(
        TextColumn("{task.description}"),
        BarColumn(bar_width=30),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("{task.fields[info]}")
    )
    five_hour = snapshot.metrics.get("five_hour")
    add_metric_task(prog, "Janela 5h (estimativa)", five_hour)
    add_metric_task(prog, "Consumo Tokens (estimativa)", snapshot.metrics.get("tokens_5h"))
    add_metric_task(prog, "Atividade 7 Dias (estimativa)", snapshot.metrics.get("seven_day"))

    turns_rem_pct = five_hour.remaining_pct if five_hour and five_hour.remaining_pct is not None else "N/D"
    if five_hour and five_hour.remaining_pct is not None:
        if five_hour.remaining_pct <= 15 and not notifications_state["codex_alerted"]:
            send_windows_toast("Alerta Codex", f"Janela de 5h do ChatGPT Plus esta em {five_hour.remaining_pct}%!")
            notifications_state["codex_alerted"] = True
        elif five_hour.remaining_pct > 80 and notifications_state["codex_alerted"]:
            notifications_state["codex_alerted"] = False

    activity = snapshot.metadata.get("last_active", "")
    activity_label = f" (Atividade: {activity})" if activity else ""
    status_label = " • [yellow]Sem dados[/yellow]" if snapshot.status != OK else ""
    return Panel(
        prog,
        title=f"[bold magenta]Codex CLI[/bold magenta] [dim]• ChatGPT Plus | Modelo: {model}{activity_label}[/dim]{status_label}",
        border_style="magenta",
        expand=True
    ), turns_rem_pct

def generate_dashboard():
    now_time = datetime.datetime.now().strftime("%H:%M:%S")
    pulse = "●" if int(time.time()) % 2 == 0 else "○"
    
    panel_agy, p_agy = get_agy_panel()
    panel_claude, p_claude = get_claude_panel()
    panel_codex, p_codex = get_codex_panel()
    
    update_window_title(p_agy, p_claude, p_codex)
    
    return Panel(
        Group(panel_agy, panel_claude, panel_codex),
        title=f"[bold white on dark_blue]  MONITOR UNIFICADO DE COTAS - AGY | CLAUDE CODE | CODEX  [/bold white on dark_blue]",
        subtitle=f"[bold green]{pulse}[/bold green] [dim]Relogio: {now_time} • Atualizando a cada 1s • Atalho: 'cotas' • Pressione Ctrl+C para sair[/dim]",
        border_style="blue"
    )

def background_agy_poller():
    """Executa 'agy --print /usage' a cada 2 minutos em background para manter dados sempre frescos."""
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
    console = Console()
    
    # Snapshot mode (--once, -s)
    if len(sys.argv) > 1 and any(arg in sys.argv[1:] for arg in ["--once", "-s", "once", "snapshot"]):
        console.print(generate_dashboard())
        sys.exit(0)

    # Inicia poller do agy em background
    poller = threading.Thread(target=background_agy_poller, daemon=True)
    poller.start()

    try:
        with Live(generate_dashboard(), console=console, refresh_per_second=2, screen=True) as live:
            while True:
                live.update(generate_dashboard())
                time.sleep(1)
    except KeyboardInterrupt:
        print("\nMonitor encerrado.")

if __name__ == "__main__":
    main()
