param(
    [switch]$Dev,
    [switch]$Dist,
    [switch]$Stop
)

$running = Get-Process -Name TokenWatch, MonitorCotas, pythonw -ErrorAction SilentlyContinue

if ($Stop) {
    if ($running) {
        Write-Host "Encerrando instâncias em execução do TokenWatch..." -ForegroundColor Yellow
        $running | Stop-Process -Force -ErrorAction SilentlyContinue
        Write-Host "Instâncias encerradas com sucesso." -ForegroundColor Green
    } else {
        Write-Host "Nenhuma instância do TokenWatch em execução." -ForegroundColor Gray
    }
    return
}

# Se já houver processo rodando, encerra para evitar duplicatas ao reabrir
if ($running) {
    Write-Host "Já existe uma instância em execução. Reiniciando..." -ForegroundColor Yellow
    $running | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 500
}

if ($Dist) {
    $exePath = Join-Path $env:USERPROFILE "dist\TokenWatch.exe"
    if (-not (Test-Path $exePath)) {
        Write-Error "Executável não encontrado em $exePath. Execute .\build.ps1 primeiro."
        return
    }
    Write-Host "Iniciando TokenWatch.exe compilado..." -ForegroundColor Cyan
    Start-Process $exePath
} elseif ($Dev) {
    Write-Host "Iniciando TokenWatch em modo desenvolvimento (terminal interativo)..." -ForegroundColor Cyan
    python quota_webview_app.py
} else {
    Write-Host "Iniciando TokenWatch em segundo plano (HUD/Avatar)..." -ForegroundColor Green
    Start-Process pythonw -ArgumentList "quota_webview_app.py" -WorkingDirectory $PSScriptRoot
}
