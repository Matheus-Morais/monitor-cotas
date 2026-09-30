param(
    [switch]$InstallDependencies
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true

$distTarget = Join-Path $env:USERPROFILE "dist"
$running = Get-Process -Name MonitorCotas -ErrorAction SilentlyContinue
if ($running) {
    Write-Host "Encerrando instâncias em execução do MonitorCotas..."
    $running | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
}

if ($InstallDependencies) {
    python -m pip install -r requirements.txt
}

python -m PyInstaller --noconfirm --clean --onefile --windowed --name MonitorCotas --distpath $distTarget --add-data "ui;ui" quota_webview_app.py
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller falhou com código $LASTEXITCODE."
}
Write-Host "Build concluído em $distTarget\MonitorCotas.exe"
