@echo off
rem Abre o TokenWatch (gerado por build.ps1 em %USERPROFILE%\dist).
rem Se ja houver uma instancia aberta, apenas a traz para a frente.
if not exist "%USERPROFILE%\dist\TokenWatch.exe" (
    echo TokenWatch.exe nao encontrado em %USERPROFILE%\dist. Rode build.ps1 primeiro.
    exit /b 1
)
start "" "%USERPROFILE%\dist\TokenWatch.exe"
