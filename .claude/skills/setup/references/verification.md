# Phase 6: Start the Bridge, Verify, and Confirm

Load this when starting services and running final verification (Steps 9-11).

## Step 9: Start the Bridge

Ensure the logs directory exists, then start the bridge as a background process:

```bash
mkdir -p logs
```

Start the bridge using the service script:

```bash
./scripts/valor-service.sh start
```

Wait a few seconds, then verify it started:

```bash
sleep 4 && tail -20 logs/bridge.log 2>/dev/null
```

Check for these indicators in the logs:
- `Agent backend: Claude Agent SDK` -- correct backend
- `Active projects: [...]` -- the projects you configured
- `Monitored groups: [...]` -- the Telegram groups
- `Connected to Telegram` -- successful connection

Also verify the process is running:

```bash
pgrep -f telegram_bridge.py
```

## Step 10: Final Verification

Run a comprehensive health check:

```bash
cd ~/src/ai

echo "=== System Tools ==="
claude --version
gh --version
git --version
uv --version

echo ""
echo "=== Python Environment ==="
.venv/bin/python --version
.venv/bin/python -c "import telethon; import anthropic; import google_auth_oauthlib; print('Dependencies OK')"

echo ""
echo "=== CLI Tools ==="
.venv/bin/valor-calendar --version 2>/dev/null || echo "valor-calendar: Not found (run 'uv pip install -e .' again)"
.venv/bin/python -m tools.sms_reader.cli recent --limit 1 | grep -q "rowid" && echo "SMS reader: OK" || echo "SMS reader: FAIL"

echo ""
echo "=== Bridge Status ==="
./scripts/valor-service.sh status
```

## Step 11: Confirm to User

Report the final status to the user with:

- **Bridge status**: Running (with PID)
- **Agent backend**: Claude Agent SDK
- **Active projects**: Which projects are configured
- **Monitored groups**: Which Telegram groups are being watched
- **Next steps**:
  1. Send a test message in the Telegram group to verify it responds
  2. Run `/update` to generate calendar config

## Troubleshooting

### Bridge won't start
1. Check logs: `tail -50 logs/bridge.log`
2. Verify Telegram session: `ls data/*.session`
3. Test imports: `.venv/bin/python -c "import telethon; print('OK')"`
