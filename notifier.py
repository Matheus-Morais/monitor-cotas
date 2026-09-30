"""Windows notifications and optional system tray integration."""

from __future__ import annotations

import base64
import os
import subprocess
import threading
from typing import Callable


def _ps_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def send_windows_toast(title: str, message: str) -> bool:
    if os.name != "nt":
        return False
    script = f"""
try {{
  [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
  $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
  $nodes = $template.GetElementsByTagName('text')
  $nodes.Item(0).AppendChild($template.CreateTextNode({_ps_literal(title)})) > $null
  $nodes.Item(1).AppendChild($template.CreateTextNode({_ps_literal(message)})) > $null
  $toast = [Windows.UI.Notifications.ToastNotification]::new($template)
  [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Monitor de Cotas').Show($toast)
}} catch {{}}
"""
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-EncodedCommand", encoded],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return True
    except OSError:
        return False


class OptionalTray:
    """A tray icon when pystray/Pillow are installed; otherwise a no-op."""

    def __init__(self, on_show: Callable[[], None], on_quit: Callable[[], None]):
        self.on_show = on_show
        self.on_quit = on_quit
        self.icon = None
        self.available = False

    def start(self) -> bool:
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError:
            return False

        image = Image.new("RGB", (64, 64), "#11111b")
        draw = ImageDraw.Draw(image)
        draw.ellipse((12, 12, 52, 52), fill="#89dceb")
        menu = pystray.Menu(
            pystray.MenuItem("Mostrar", lambda *_: self.on_show()),
            pystray.MenuItem("Sair", lambda *_: self.on_quit()),
        )
        self.icon = pystray.Icon("Monitor de Cotas", image, "Monitor de Cotas", menu)
        self.available = True
        threading.Thread(target=self.icon.run, name="quota-tray", daemon=True).start()
        return True

    def stop(self) -> None:
        if self.icon is not None:
            self.icon.stop()
