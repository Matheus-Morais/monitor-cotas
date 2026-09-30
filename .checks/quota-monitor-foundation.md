# Quota monitor foundation

Sources:

- conversation - implement the first high-impact slice: reliable unknown states and shared provider logic for the terminal and GUI.
- `quota_monitor.py`, `quota_widget.py` - current provider readers and presentation surfaces.

## Out of scope

- tray icon, history charts, packaging, and automatic Claude account switching - these need a separate product decision and are not required to make quota values trustworthy.
- moving the application into a package - preserve the current `cotas-gui.cmd` launch path in this slice.

## Landing

`quota_core.py` becomes the single source for provider parsing, countdown formatting, and Codex estimates. The terminal and GUI reuse its snapshots and keep their existing presentation. Missing or malformed telemetry is represented as `unavailable`/`error`, never as a fabricated 100% value.

| One-way door | Literal shape | Alternative rejected |
| --- | --- | --- |
| Provider parsing contract | `ProviderSnapshot.status` is `ok`, `unavailable`, or `error`; each metric has `remaining_pct: int | None` | keep provider-specific default percentages in each UI - it already caused GUI/terminal drift and false green states |
| Codex estimate disclosure | `ProviderSnapshot.estimated=True` for turn-based Codex quota metrics and the UI labels the window as an estimate | present the fixed 50-turn assumption as an actual provider limit |

- Nothing else in this change is hard to reverse.

## Checks

### S1 - Shared provider snapshots and safe unknown states · 4 files · ~35 KB · ~9k

**C1** - Missing or malformed Antigravity, Claude, and Codex sources produce no numeric remaining percentage.
Proof: `python -m unittest -v test_quota_core.py` (`test_missing_sources_are_not_green`, `test_malformed_sources_are_not_green`)

**C2** - Valid Antigravity and Claude fixtures preserve their remaining percentages and reset timestamps.
Proof: `python -m unittest -v test_quota_core.py` (`test_antigravity_snapshot`, `test_claude_rate_limits_snapshot`, `test_claude_cached_usage_snapshot`)

**C3** - Codex snapshot marks the turn-based quota as estimated and leaves metrics unavailable when its databases are absent.
Proof: `python -m unittest -v test_quota_core.py` (`test_codex_snapshot_is_estimated`, `test_codex_missing_databases`)

**C4** - Both presentation modules reuse the shared countdown formatter and provider readers.
Proof: `python -m unittest -v test_quota_core.py` (`test_presentations_use_shared_core`)

**C5** - Existing terminal snapshot mode still renders all three provider panels after the refactor.
Proof: `python quota_monitor.py --once` exit 0 and output contains `Antigravity`, `Claude Code`, and `Codex CLI`.

## Swept

- validation: C1, C2
- failure modes: C1, C3
- idempotency and retry: not in scope - this slice does not persist or retry state.
- authorization: not in scope - existing local credential reads remain unchanged.
- concurrency and ordering: not in scope - collector threading is a later slice.
- data lifecycle: not in scope - no new persistent data is retained.
- external-dependency failure: C1, C3
- state transitions: not in scope - alert hysteresis remains unchanged.
- observability: C1 exposes explicit status to the presentation layer; structured logging is a later slice.

## Coverage

| Set (size) | Member -> proof | Unproven |
| --- | --- | --- |
| provider source states (3) | missing `test_missing_sources_are_not_green` · malformed `test_malformed_sources_are_not_green` · valid C2 | - |
| Claude input formats (2) | `rate_limits` C2 · `cachedUsageUtilization` C2 | - |
| Codex database states (2) | both present C3 · absent C3 | - |
| presentation assemblies (2) | terminal and GUI `test_presentations_use_shared_core` | - |

## Handoff

- Single batch: the slice is limited to the four files named in S1 and stays below the handoff budget.
