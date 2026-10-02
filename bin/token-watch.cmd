@echo off
rem Abre o TokenWatch (gerado por build.ps1 em %USERPROFILE%\dist).
rem Se ja houver uma instancia aberta, traz a janela para a frente e restaura.

if "%1"=="--help" goto help
if "%1"=="-h" goto help
if "%1"=="stop" goto stop

if not exist "%USERPROFILE%\dist\TokenWatch.exe" (
    echo TokenWatch.exe nao encontrado em %USERPROFILE%\dist.
    echo Execute .\build.ps1 na pasta C:\PProjetos\Token-Watch primeiro.
    exit /b 1
)

start "" "%USERPROFILE%\dist\TokenWatch.exe"
echo [TokenWatch] Ativado / aberto com sucesso.
exit /b 0

:stop
taskkill /f /im TokenWatch.exe /im MonitorCotas.exe >nul 2>&1
echo [TokenWatch] Processos encerrados.
exit /b 0

:help
echo Uso:
echo   token-watch         Abre o TokenWatch ou traz a janela existente para a frente
echo   token-watch stop    Encerra todas as instancias em execucao
echo   token-watch --help  Exibe esta ajuda
exit /b 0
