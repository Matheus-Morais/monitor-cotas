# Instala o comando `token-watch` em ~\.local\bin (precisa estar no PATH).
# O stub apenas chama bin\token-watch.cmd deste repositorio, entao basta
# rodar este script uma vez por maquina; atualizacoes vem com o git pull.
$ErrorActionPreference = "Stop"

$launcher = Join-Path $PSScriptRoot "bin\token-watch.cmd"
$binDir = Join-Path $env:USERPROFILE ".local\bin"
New-Item -ItemType Directory -Force $binDir | Out-Null

$stub = Join-Path $binDir "token-watch.cmd"
Set-Content -Path $stub -Value "@echo off`r`ncall `"$launcher`" %*" -Encoding ascii
Write-Host "Comando instalado: $stub"

$onPath = ($env:Path -split ';') -contains $binDir
if (-not $onPath) {
    Write-Warning "$binDir nao esta no PATH. Adicione-o para chamar 'token-watch' em qualquer terminal."
}
