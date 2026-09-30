param(
    [switch]$InstallDependencies
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true

$running = Get-Process -Name MonitorCotas -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -eq (Join-Path $PSScriptRoot "dist\MonitorCotas\MonitorCotas.exe") }
if ($running) {
    throw "Feche o MonitorCotas.exe antes de executar o build para liberar os arquivos de dist."
}

if ($InstallDependencies) {
    python -m pip install -r requirements.txt
}

python -m PyInstaller --noconfirm --clean --onedir --windowed --name MonitorCotas quota_widget.py
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller falhou com código $LASTEXITCODE."
}
Write-Host "Build concluído em dist\\MonitorCotas"
