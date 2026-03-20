# data/ Directory

Runtime state and ephemeral data. This directory is gitignored except for this README. Nothing here is source-controlled; everything is generated at runtime.

## Contents

| Path | Description | Cleanup Policy |
|------|-------------|----------------|
| `valor_bridge.session` | Active Telethon session file for the Telegram bridge | Do not delete while bridge is running |
| `doc_embeddings.json` | Cached document embeddings (~46MB) | Safe to delete; regenerated on next embedding run |
| `daydream_state.json` | Daydream feature state | Ephemeral; auto-recreated |
| `best_practices_cache.json` | Cached best practices for reflections | Ephemeral; auto-recreated |
| `lessons_learned.jsonl` | Append-only log of reflections lessons | Retain indefinitely |
| `module_registry.json` | Module registry for dynamic loading | Ephemeral; auto-recreated |
| `revival_cooldowns.json` | Session revival cooldown tracking | Ephemeral; auto-recreated |
| `update.log` | Log from last remote update | Overwritten each update |
| `update.txt` | Update status file | Overwritten each update |
| `checkpoints/` | Session checkpoint data for resume | Pruned automatically by checkpoint manager |
| `experiments/` | Autoexperiment results and iteration data | Retain for analysis; prune monthly |
| `media/` | Downloaded media files from Telegram | Pruned after processing |
| `pipeline/` | SDLC pipeline state files (one subdir per slug) | Cleaned up when PRs merge |
| `process_state/` | Process-level state tracking | Ephemeral; auto-recreated |
| `sessions/` | Session log archives | Retain for debugging; prune quarterly |
| `monitoring/` | Crash tracker and watchdog state | Retain for incident analysis |
| `backups/` | Automatic backups of critical state | Retain last 7 days |

## Cleanup Policy

- **Weekly**: Delete stale session files (any `.session` files other than `valor_bridge.session`)
- **Monthly**: Prune `experiments/` data older than 30 days, review `sessions/` size
- **Quarterly**: Archive or delete `sessions/` logs older than 90 days
- **On demand**: Delete `doc_embeddings.json` to force regeneration (saves ~46MB)

## Important Notes

- Never delete `valor_bridge.session` while the bridge is running -- it will disconnect from Telegram
- The `pipeline/` directory grows with each SDLC build; clean up after PR merges
- All paths are relative to the project root and referenced via `config/paths.py` constants
