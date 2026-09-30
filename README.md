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
