# Contributing to TokenWatch ⚡

Thank you for your interest in improving **TokenWatch**! We welcome bug reports, feature requests, documentation improvements, and pull requests.

---

## 🛠️ Development Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Matheus-Morais/monitor-cotas.git tokenwatch
   cd tokenwatch
   ```

2. **Create and activate a virtual environment:**
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. **Install dependencies:**
   ```powershell
   pip install -r requirements.txt
   ```

4. **Run TokenWatch locally:**
   ```powershell
   python quota_webview_app.py
   ```

---

## 🧪 Testing

Before submitting a Pull Request, please ensure all unit tests pass:

```powershell
python -m unittest discover -v
```

If you add new providers or parsing logic, include corresponding unit test cases in `test_quota_core.py` or `test_quota_widget_compact.py`.

---

## 🏗️ Architecture Overview

TokenWatch consists of two decoupled layers to prevent any GUI freezing:

1. **Core Data Layer (`quota_core.py`):**
   - Headless telemetry collectors that read local session files and SQLite databases for Antigravity, Claude Code, and Codex.
   - Normalized snapshots and metric representations.

2. **UI Presentation Layer (`quota_webview_app.py` & `ui/index.html`):**
   - High-performance, GPU-accelerated WebView2 window running HTML5, CSS3, and modern SVG circular graphs.
   - Win32 interoperability (`comctl32.SetWindowSubclass`, `WM_GETMINMAXINFO`, `WM_NCLBUTTONDOWN`, `SetWindowRgn`) enabling instant toggling between the 72x72px floating Avatar disc and the resizable full panel HUD.
   - Bidirectional JSON API bridge via `pywebview`.

---

## 📦 Building the Standalone Executable

To compile a single-file executable using PyInstaller:

```powershell
.\build.ps1
```

The resulting binary will be created in `dist/TokenWatch.exe` (or `dist/MonitorCotas.exe`).

---

## 💡 Submitting a Pull Request

1. Fork the repository and create your branch from `master`:
   ```bash
   git checkout -b feat/your-feature-name
   ```
2. Commit your changes following [Conventional Commits](https://www.conventionalcommits.org/):
   ```bash
   git commit -m "feat(ui): add new metric chart"
   ```
3. Push to your branch and open a Pull Request.
4. Describe the changes, motivation, and include screenshots if UI changes were made.
