# Monitor de Cotas

Monitor local de cotas para Antigravity, Claude Code e Codex.

## Executar

Na pasta do projeto:

```powershell
python quota_monitor.py --once
.\cotas-gui.cmd
```

O comando global `cotas-gui` aponta para este projeto.

## Testar

```powershell
python -m unittest -v test_quota_core.py
python -m py_compile quota_core.py quota_monitor.py quota_widget.py test_quota_core.py
```

O monitor lê os arquivos de telemetria locais das ferramentas instaladas. Credenciais e bancos de dados locais não fazem parte deste repositório.

Para o Codex, a janela de 5 horas e a semanal usam o último `rate_limits` reportado
nos rollouts locais (`%USERPROFILE%\\.codex\\sessions`). O consumo de tokens aparece
separado como atividade observada; ele não é convertido artificialmente em quota.
Se ainda não houver rollout com esse evento, o monitor usa o fallback marcado como
estimativa.

## Configuração e histórico

Na primeira execução, os defaults apontam para o perfil atual do Windows. As preferências são salvas em:

```text
%APPDATA%\MonitorCotas\config.json
%APPDATA%\MonitorCotas\history.sqlite
```

Para usar outro arquivo de configuração:

```powershell
python quota_monitor.py --once --config .\config.local.json
.\cotas-gui.cmd --config .\config.local.json
```

A GUI coleta em segundo plano, registra snapshots normalizados e oferece o botão `▤` para consultar o histórico. A bandeja do Windows é ativada quando `pystray` e `Pillow` estão instalados; sem eles, a GUI continua funcionando.

As duas contas Claude são identificadas pelo `accountUuid`, não apenas pelo
nome do arquivo ou e-mail. Contas duplicadas não geram históricos/alertas
duplicados. Uma conta que ainda não foi usada pelo Claude Code aparece como
`Sem telemetria`; ative-a e use o Claude para que o cache de uso seja criado.

## Build Windows

```powershell
.\build.ps1 -InstallDependencies
```

O build gera `dist\MonitorCotas` usando PyInstaller em modo `onedir`.
