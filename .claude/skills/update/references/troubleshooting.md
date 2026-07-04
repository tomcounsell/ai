# Update Troubleshooting

Load this when an update run fails or the environment misbehaves afterward.

## Virtual environment issues
```bash
cd ~/src/ai
rm -rf .venv
uv venv
uv sync --all-extras
```

## Missing dependencies after update
```bash
cd ~/src/ai
uv sync --all-extras --reinstall
```

## Calendar integration not working
1. Check OAuth token: `ls ~/Desktop/Valor/google_token.json`
2. Re-run OAuth: `valor-calendar test`
3. Check deps: `.venv/bin/python -c "import google_auth_oauthlib; print('OK')"`

## Wrong projects active (machine identity mismatch)

The bridge derives active projects from `scutil --get ComputerName` matched against the `machine` field in `~/Desktop/Valor/projects.json`. If the wrong projects are active:

1. Check the machine name: `scutil --get ComputerName`
2. Check the config: `python -c "import json; [print(f'{k}: {v.get(\"machine\")}') for k,v in json.load(open('$HOME/Desktop/Valor/projects.json')).get('projects',{}).items()]"`
3. Fix: ensure the `machine` value in projects.json matches the ComputerName exactly (case-insensitive)

## Bridge won't start
```bash
# Check logs
tail -50 ~/src/ai/logs/bridge.error.log

# Manual restart
~/src/ai/scripts/valor-service.sh restart

# Check status
~/src/ai/scripts/valor-service.sh status
```

## Worker won't start
```bash
# Check logs
tail -50 ~/src/ai/logs/worker_error.log

# Manual restart
~/src/ai/scripts/valor-service.sh worker-restart

# Check status
~/src/ai/scripts/valor-service.sh worker-status

# Reinstall plist
~/src/ai/scripts/install_worker.sh
```

## Reflection scheduler dead or stale

The scheduler is its own launchd subprocess (`python -m reflections`, label `com.valor.reflection-worker`) — separate from the worker since issue #1828.

```bash
# Check logs
tail -50 ~/src/ai/logs/reflection_worker.log

# Validate the registry loads (exits 0 on success)
cd ~/src/ai && .venv/bin/python -m reflections --dry-run

# Reinstall/reload plist (worker-role gated, fail-open)
~/src/ai/scripts/install_reflection_worker.sh
```

Health signals on `/dashboard.json`: a stale `data/last_reflection_tick` mtime means the scheduler is dead; `reflection_scheduler_last_start_age_s` pinned near zero means it is crash-looping (launchd keeps respawning it). See `docs/features/reflection-scheduler-subprocess.md`.
