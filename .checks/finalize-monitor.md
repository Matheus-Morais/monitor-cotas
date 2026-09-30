# Finalização do Monitor de Cotas

Sources:

- conversation - finalizar as melhorias restantes na ordem priorizada: coleta assíncrona, configuração, histórico, alertas, UX e empacotamento.
- código atual do projeto - preserva `cotas-gui`, os três provedores e o modo snapshot.

## Out of scope

- descobrir limites oficiais de provedores sem uma API pública disponível - o Codex continua explicitamente estimado e configurável.
- publicar em GitHub, criar instalador assinado ou registrar tarefa de inicialização do Windows - são operações externas que exigem decisão de distribuição.

## Landing

`MonitorConfig` concentra caminhos e preferências; `QuotaCollector` concentra coleta e `CollectorWorker` desacopla I/O do Tkinter; `HistoryStore` persiste snapshots e estado de alertas; `notifier.py` concentra notificações; as interfaces apenas renderizam snapshots.

| One-way door | Literal shape | Alternative rejected |
| --- | --- | --- |
| Configuração persistida | JSON em `%APPDATA%\\MonitorCotas\\config.json`, somente preferências e caminhos, sem credenciais | espalhar caminhos absolutos em cada módulo |
| Histórico local | SQLite em `%APPDATA%\\MonitorCotas\\history.sqlite` com snapshots e `alert_state` | arquivos JSON ilimitados, que dificultam consulta e retenção |
| Bandeja | integração opcional via `pystray`; GUI continua utilizável quando a dependência não existe | tornar o tray obrigatório e quebrar instalações mínimas |

## Checks

### S1 - Configuração e coleta assíncrona · 7 arquivos · ~55 KB · ~14k

**C1** - Defaults usam a home/APPDATA atual e nenhum módulo de apresentação mantém caminhos absolutos de usuário.
Proof: `python -m unittest -v test_monitor_services.py` (`test_default_config_uses_runtime_home`, `test_presentations_use_config_paths`)

**C2** - O worker publica snapshots em uma fila sem executar callbacks de widgets na thread de coleta e `refresh_now` acorda a coleta.
Proof: `python -m unittest -v test_monitor_services.py` (`test_worker_collects_and_stops`, `test_worker_refresh_event`)

**C3** - O terminal aceita `--config` e continua renderizando os três provedores.
Proof: `python quota_monitor.py --once --config <fixture>` exit 0 e contém os três títulos.

### S2 - Histórico, alertas e segurança · 6 arquivos · ~45 KB · ~12k

**C4** - Snapshots persistem por provedor/métrica e podem ser consultados sem credenciais.
Proof: `python -m unittest -v test_monitor_services.py` (`test_history_records_and_reads_snapshots`)

**C5** - Alertas têm cooldown e recuperação, sem repetir notificações a cada coleta.
Proof: `python -m unittest -v test_monitor_services.py` (`test_alert_cooldown_and_recovery`)

**C6** - Troca de perfil Claude cria backup antes de substituir o arquivo ativo.
Proof: `python -m unittest -v test_monitor_services.py` (`test_profile_switch_creates_backup`)

### S3 - UX e distribuição · 6 arquivos · ~35 KB · ~9k

**C7** - Posição, tamanho, opacidade e topmost são carregados/salvos na configuração.
Proof: `python -m unittest -v test_monitor_services.py` (`test_window_preferences_round_trip`)

**C8** - A bandeja é opcional, o botão refresh apenas sinaliza o worker e o launcher segue apontando para o repo.
Proof: `python -m py_compile ...`; inspeção de `cotas-gui.cmd`; `test_tray_is_optional`.

**C9** - O projeto declara dependências, comandos de teste e build.
Proof: `python -m unittest -v test_monitor_services.py` (`test_distribution_files_exist`) e `python -m py_compile ...`.

## Swept

- validation: C1, C3, C7, C9
- failure modes: C2, C4, C5
- idempotency and retry: C5; coleta repetida não repete alerta dentro do cooldown.
- authorization: não há endpoint externo; credenciais continuam fora do histórico.
- concurrency and ordering: C2; Tkinter recebe resultados somente via `after`.
- data lifecycle: retenção configurável do histórico, com limpeza limitada por idade.
- external-dependency failure: C2, C8; `agy`, tray e fontes ausentes degradam sem derrubar a GUI.
- state transitions: C5; baixo -> recuperado.
- observability: status, idade da fonte e erros continuam visíveis; falhas de worker são armazenadas no snapshot.

## Coverage

| Set (size) | Member -> proof | Unproven |
| --- | --- | --- |
| config sources (2) | default runtime `test_default_config_uses_runtime_home` · explicit file C3 | - |
| worker lifecycle (3) | start/collect/stop C2 · refresh event C2 · callback queue C2 | - |
| alert transitions (3) | first low C5 · cooldown C5 · recovery C5 | - |
| distribution modes (2) | dependencies/build C9 · optional tray C8 | - |

## Handoff

- Single continuous build: changes share the new service boundary and will be verified together at the final filesystem state.
