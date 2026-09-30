param(
    [switch]$InstallDependencies
)

$ErrorActionPreference = "Stop"

if ($InstallDependencies) {
    python -m pip install -r requirements.txt
}

python -m PyInstaller --noconfirm --clean --onedir --windowed --name MonitorCotas quota_widget.py
Write-Host "Build concluído em dist\\MonitorCotas"
