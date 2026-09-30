import os
import sys
import time
import datetime
import queue
import ctypes
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich import box
from rich.console import Group, Console
from rich.progress import Progress, BarColumn, TextColumn
from collector import CollectorWorker, DashboardSnapshot, QuotaCollector
from config import load_config
from history import HistoryStore
from notifier import send_windows_toast
from quota_core import OK, format_countdown

# Configura encoding UTF-8 garantido no Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

def update_window_title(agy_pct, claude_pct, codex_pct):
    if sys.platform == "win32":
        try:
            title = f"[AGY: {agy_pct}% | CLAUDE: {claude_pct}% | CODEX: {codex_pct}%] - Monitor de Cotas"
            ctypes.windll.kernel32.SetConsoleTitleW(title)
        except Exception:
            pass

def source_age(snapshot):
    age = snapshot.source_age_seconds
    if age is None:
        return "Nenhum dado"
    if age < 60:
        return f"ha {age}s"
    if age < 3600:
        return f"ha {age // 60}m"
    return f"ha {age // 3600}h"


def metric_info(metric):
    if metric is None or metric.remaining_pct is None:
        return f"N/D | {metric.detail}" if metric and metric.detail else "N/D"
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

def get_agy_panel(snapshot):
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

    status = "Sem dados" if snapshot.status != OK else ""
    age = source_age(snapshot)
    title = f"[bold cyan]Antigravity (agy)[/bold cyan] [dim]• {snapshot.plan or 'Plano desconhecido'}"
    if snapshot.model:
        title += f" | {snapshot.model}"
    title += f" (Atividade: {age})"
    if status:
        title += f" • [yellow]{status}[/yellow]"
    primary = snapshot.metrics.get("gemini_5h")
    primary_pct = primary.remaining_pct if primary and primary.remaining_pct is not None else "N/D"
    return Panel(prog, title=title, border_style="cyan", expand=True), primary_pct

def get_claude_panel(snapshot):
    prog = Progress(
        TextColumn("{task.description}"),
        BarColumn(bar_width=30),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("{task.fields[info]}")
    )
    add_metric_task(prog, "Limite 5 Horas (Claude Pro)", snapshot.metrics.get("five_hour"))
    add_metric_task(prog, "Limite 7 Dias (Claude Pro)", snapshot.metrics.get("seven_day"))
    add_metric_task(prog, "Janela de Contexto (Tokens)", snapshot.metrics.get("context"))

    age = source_age(snapshot)
    title = f"[bold green]Claude Code[/bold green] [dim]• Assinatura Pro"
    if snapshot.model:
        title += f" | Modelo: {snapshot.model}"
    title += f" (Atividade: {age})"
    if snapshot.status != OK:
        title += " • [yellow]Sem dados[/yellow]"
    primary = snapshot.metrics.get("five_hour")
    primary_pct = primary.remaining_pct if primary and primary.remaining_pct is not None else "N/D"
    return Panel(prog, title=title, border_style="green", expand=True), primary_pct

def get_codex_panel(snapshot):
    prog = Progress(
        TextColumn("{task.description}"),
        BarColumn(bar_width=30),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("{task.fields[info]}")
    )
    five_hour = snapshot.metrics.get("five_hour")
    has_real_limits = bool(snapshot.metadata.get("rate_limit_source"))
    add_metric_task(prog, "Janela 5h (cota real)" if has_real_limits else "Janela 5h (estimativa)", five_hour)
    add_metric_task(prog, "Tokens observados 5h", snapshot.metrics.get("tokens_5h"))
    add_metric_task(prog, "Cota semanal (real)" if has_real_limits else "Atividade 7 Dias (estimativa)", snapshot.metrics.get("seven_day"))

    turns_rem_pct = five_hour.remaining_pct if five_hour and five_hour.remaining_pct is not None else "N/D"
    activity = snapshot.metadata.get("last_active", "")
    activity_label = f" (Atividade: {activity})" if activity else ""
    status_label = " • [yellow]Sem dados[/yellow]" if snapshot.status != OK else ""
    source = "Cota real via rollout" if has_real_limits else "Estimativa local"
    return Panel(
        prog,
        title=f"[bold magenta]Codex CLI[/bold magenta] [dim]• {source} | Modelo: {snapshot.model}{activity_label}[/dim]{status_label}",
        border_style="magenta",
        expand=True
    ), turns_rem_pct

def generate_dashboard(snapshot: DashboardSnapshot):
    now_time = datetime.datetime.now().strftime("%H:%M:%S")
    pulse = "●" if int(time.time()) % 2 == 0 else "○"
    
    panel_agy, p_agy = get_agy_panel(snapshot.agy)
    panel_claude, p_claude = get_claude_panel(snapshot.claude)
    panel_codex, p_codex = get_codex_panel(snapshot.codex)
    
    update_window_title(p_agy, p_claude, p_codex)
    
    return Panel(
        Group(panel_agy, panel_claude, panel_codex),
        title=f"[bold white on dark_blue]  MONITOR UNIFICADO DE COTAS - AGY | CLAUDE CODE | CODEX  [/bold white on dark_blue]",
        subtitle=f"[bold green]{pulse}[/bold green] [dim]Relogio: {now_time} • Atualizando a cada 1s • Atalho: 'cotas' • Pressione Ctrl+C para sair[/dim]",
        border_style="blue"
    )

def main():
    console = Console()

    config_path = None
    if "--config" in sys.argv:
        index = sys.argv.index("--config")
        if index + 1 < len(sys.argv):
            config_path = sys.argv[index + 1]
    config = load_config(config_path)
    collector = QuotaCollector(config)
    store = HistoryStore(config.history_db)

    # Snapshot mode (--once, -s)
    if any(arg in sys.argv[1:] for arg in ["--once", "-s", "once", "snapshot"]):
        snapshot = collector.collect()
        console.print(generate_dashboard(snapshot))
        sys.exit(0)

    results = queue.Queue()
    worker = CollectorWorker(collector, results)
    worker.start()
    current = collector.collect()

    try:
        with Live(generate_dashboard(current), console=console, refresh_per_second=2, screen=True) as live:
            while True:
                try:
                    while True:
                        current = results.get_nowait()
                        store.record_dashboard(current)
                        for event in store.evaluate_alerts(
                            current,
                            config.alert_threshold_pct,
                            config.alert_recovery_pct,
                            config.alert_cooldown_seconds,
                        ):
                            send_windows_toast(
                                f"{event.provider} - {event.message}",
                                f"{event.metric}: {event.remaining_pct}%",
                            )
                except queue.Empty:
                    pass
                live.update(generate_dashboard(current))
                time.sleep(1)
    except KeyboardInterrupt:
        worker.stop()
        print("\nMonitor encerrado.")

if __name__ == "__main__":
    main()
