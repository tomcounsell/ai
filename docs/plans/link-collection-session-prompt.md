# Session Prompt: Telegram History & Link Collection

Copy this prompt to start a new Claude Code session:

---

## Task: Implement Telegram History & Link Collection

Read the build plan at `docs/plans/link-collection-rebuild.md` and implement it fully.

### Summary

1. **Wire up message storage** - The `tools/telegram_history/` tool exists but isn't connected to the bridge. Every incoming Telegram message should be stored in SQLite at `~/.valor/telegram_history.db`.

2. **Add links table** - Extend `tools/telegram_history/__init__.py` with a `links` table and functions: `store_link()`, `search_links()`, `list_links()`, `update_link()`.

3. **Bridge integration** - Modify `bridge/telegram_bridge.py` to:
   - Store ALL messages via `store_message()`
   - Extract URLs from messages sent by whitelisted users (see `TELEGRAM_LINK_COLLECTORS` in .env)
   - Store links with metadata via `store_link()`

4. **Test everything** - Add unit tests, restart the bridge, verify messages and links are being stored.

### Key Files

- `docs/plans/link-collection-rebuild.md` - Full build plan with schema, API design, code snippets
- `tools/telegram_history/__init__.py` - Add links table and functions here
- `tools/link_analysis/__init__.py` - Use `extract_urls()` for URL detection
- `bridge/telegram_bridge.py` - Wire up storage calls in message handler
- `.env` - Add `TELEGRAM_LINK_COLLECTORS=tomcounsell`

### Definition of Done

Per CLAUDE.md, a task is only "done" when:
- [ ] Code implemented and working
- [ ] Unit tests passing
- [ ] Bridge restarted and tested end-to-end
- [ ] Plan document moved to `docs/features/telegram-history.md`

### Commands

```bash
# Run tests
pytest tests/ -v

# Restart bridge after changes
./scripts/valor-service.sh restart

# Check if messages are being stored
python3 -c "from tools.telegram_history import get_chat_stats; print(get_chat_stats('test'))"
```

---

Start by reading the full plan, then implement phase by phase.
