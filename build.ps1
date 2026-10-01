param(
    [switch]$InstallDependencies
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true

$distTarget = Join-Path $env:USERPROFILE "dist"
$running = Get-Process -Name TokenWatch, MonitorCotas -ErrorAction SilentlyContinue
if ($running) {
    Write-Host "Encerrando instâncias em execução do TokenWatch / MonitorCotas..."
    $running | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
}

if ($InstallDependencies) {
    python -m pip install -r requirements.txt
}

python -m PyInstaller --noconfirm --clean --onefile --windowed --name TokenWatch --distpath $distTarget --icon "assets\icon\tokenwatch.ico" --add-data "ui;ui" --add-data "assets\icon;assets\icon" quota_webview_app.py
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller falhou com código $LASTEXITCODE."
}

# Also maintain MonitorCotas.exe copy for backward compatibility
Copy-Item "$distTarget\TokenWatch.exe" -Destination "$distTarget\MonitorCotas.exe" -Force

Write-Host "Build concluído em $distTarget\TokenWatch.exe e $distTarget\MonitorCotas.exe"

