---
status: Ready
type: feature
appetite: Small
owner: Valor Engels
created: 2026-04-03
tracking: https://github.com/tomcounsell/ai/issues/644
---

# Telegram Multi-File Album Sends

## Problem

When the agent needs to send multiple screenshots or files (e.g., a PR review with before/after screenshots), it must make separate `send_telegram.py` calls. Each call produces an individual Telegram message rather than a grouped album.

**Current behavior:**
```bash
# 3 calls = 3 separate messages cluttering the chat
python tools/send_telegram.py "Before" --file before.png
python tools/send_telegram.py "During" --file during.png
python tools/send_telegram.py "After" --file after.png
```

**Desired outcome:**
```bash
# 1 call = 1 album message with caption
python tools/send_telegram.py "PR review screenshots" --file before.png --file during.png --file after.png
```

Telethon's `send_file()` natively accepts a list of files to create an album. The plumbing already exists -- this work threads multi-file support through the CLI, Redis payload, and relay.

## Prior Art

- **PR #642**: Add --file support to PM send tool -- merged 2026-04-03. Added single-file support via `--file`. This issue extends that to multiple files.
- **PR #527**: PM Telegram tool: ChatSession composes its own messages -- merged 2026-03-25. Original PM self-messaging tool (text only).
- **Issue #641**: Unify telegram send interface -- closed by PR #642.

## Data Flow

1. **Entry point**: Agent calls `python tools/send_telegram.py "caption" --file a.png --file b.png --file c.png`
2. **CLI parsing** (`tools/send_telegram.py`): argparse collects `--file` args into a list via `action="append"`. `send_message()` validates each file exists, resolves to absolute paths.
3. **Redis queue**: Payload pushed to `telegram:outbox:{session_id}` with `file_paths: ["/abs/a.png", "/abs/b.png", "/abs/c.png"]` (list, not string).
4. **Relay** (`bridge/telegram_relay.py`): `_send_queued_message()` detects `file_paths` list, passes to `client.send_file([paths], caption=text)`. Telethon groups them as an album.
5. **Output**: Single Telegram album message with caption on first image. Returns list of Message objects; first ID is recorded on AgentSession.

## Architectural Impact

- **Interface changes**: `send_message()` signature changes from `file_path: str | None` to `file_paths: list[str] | None`. Redis payload key changes from `file_path` to `file_paths`.
- **Backward compat**: Relay must handle both old `file_path` (string) and new `file_paths` (list) payloads during rolling deployments.
- **Coupling**: No new coupling. Same components, same flow, just wider pipe.
- **Reversibility**: Easy to revert -- the list-of-one case is identical to the current single-file behavior.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- PR #642 is already merged and this builds directly on it.

## Solution

### Key Elements

- **CLI multi-file collection**: `--file` uses `action="append"` to collect multiple paths into a list
- **Payload migration**: Redis payload uses `file_paths` (list) instead of `file_path` (string)
- **Relay album send**: Pass file list to Telethon's `send_file()` which natively creates albums
- **Backward-compatible relay**: Handle both `file_path` and `file_paths` in incoming payloads

### Flow

**CLI** → validate all files exist → build payload with `file_paths: list` → **Redis queue** → relay pops → detect multi-file → **Telethon** `send_file([paths])` → album delivered

### Technical Approach

- `argparse` change: `--file` gets `action="append"`, `dest="file_paths"`, producing `list[str] | None`
- `send_message(file_paths: list[str] | None)`: validate each file, normalize to absolute paths, store as `file_paths` list in payload
- Relay normalization: if payload has `file_path` (string), wrap to `[file_path]`. If `file_paths` (list), use directly. Filter out any files missing at send time.
- Telethon: `client.send_file(chat_id, file_list, caption=text, reply_to=reply_to_id)`. Returns list of Message objects for albums, single Message for single file.
- Telegram album limit: max 10 files. Validate at CLI time, exit with clear error if exceeded.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Relay's file-missing fallback: when some files in the list are missing at send time, log warning and send remaining files. If all missing and no text, return None.
- [ ] Redis push failure: existing behavior, exit code 1 with error message.

### Empty/Invalid Input Handling
- [ ] Empty `--file` list with no text: argparse error (existing behavior preserved)
- [ ] `--file ""` (empty string path): validation catches and exits with error
- [ ] `--file` with mix of existing and nonexistent files: exit with error listing missing files

### Error State Rendering
- [ ] CLI prints clear error listing which specific files were not found
- [ ] CLI prints clear error if more than 10 files specified

## Test Impact

- [ ] `tests/unit/test_send_telegram.py::TestSendTelegramFileSupport::test_queues_file_payload` -- UPDATE: change `file_path=tmp_path` to `file_paths=[tmp_path]`, assert `file_paths` key in payload
- [ ] `tests/unit/test_send_telegram.py::TestSendTelegramFileSupport::test_file_only_send` -- UPDATE: change to use `file_paths=[tmp_path]`, assert `file_paths` key
- [ ] `tests/unit/test_send_telegram.py::TestSendTelegramFileSupport::test_file_not_found_exits` -- UPDATE: change to use `file_paths=["/nonexistent/file.png"]`
- [ ] `tests/unit/test_send_telegram.py::TestSendTelegramFileSupport::test_empty_file_path_exits` -- UPDATE: change to use `file_paths=[""]`
- [ ] `tests/unit/test_send_telegram.py::TestSendTelegramFileSupport::test_file_path_normalized_to_absolute` -- UPDATE: change to use `file_paths`, assert `file_paths` key
- [ ] `tests/unit/test_send_telegram.py::TestSendTelegramCli::test_main_with_file_flag` -- UPDATE: adjust for multi-file CLI parsing
- [ ] `tests/unit/test_bridge_relay.py::TestSendQueuedMessage::test_sends_file_via_send_file` -- UPDATE: payload uses `file_paths` list
- [ ] `tests/unit/test_bridge_relay.py::TestSendQueuedMessage::test_file_only_send_no_caption` -- UPDATE: payload uses `file_paths` list
- [ ] `tests/unit/test_bridge_relay.py::TestSendQueuedMessage::test_missing_file_falls_back_to_text` -- UPDATE: payload uses `file_paths` list
- [ ] `tests/unit/test_bridge_relay.py::TestSendQueuedMessage::test_missing_file_no_text_returns_none` -- UPDATE: payload uses `file_paths` list
- [ ] `tests/unit/test_send_telegram.py::TestSendTelegramQueueing::test_queues_message_to_redis` -- UPDATE: assert `file_paths` not in payload (text-only)

## Rabbit Holes

- **Mixed media type handling**: Telegram albums require homogeneous media types (all photos or all documents). Do not try to auto-detect and split into multiple albums. If Telethon raises an error for mixed types, let it propagate. This can be addressed later if it becomes a real problem.
- **Album caption on all items**: Telegram only supports caption on the first album item. Do not try to work around this with multiple sends or caption duplication.
- **Streaming/chunked uploads**: Not needed. Files are local on disk, Telethon handles upload internally.

## Risks

### Risk 1: Backward compatibility during rolling deployment
**Impact:** Old bridge instances receive new `file_paths` payloads and ignore the field, sending text-only messages.
**Mitigation:** Relay normalizes both formats. During transition, old relays will see no `file_path` key and fall through to text-only send. This is acceptable for the brief deployment window.

## Race Conditions

No race conditions identified -- the Redis queue is atomic (LPOP/RPUSH) and each message is processed by exactly one relay instance.

## No-Gos (Out of Scope)

- Auto-splitting albums exceeding 10 files into multiple sends
- Mixed media type detection and smart grouping
- Per-file captions (Telegram limitation, not addressable)
- Changing `valor-telegram send` CLI (DevSession tool) -- only `tools/send_telegram.py` (PM tool) is in scope

## Update System

No update system changes required -- this modifies existing Python files with no new dependencies or config. Standard `git pull` and bridge restart is sufficient.

## Agent Integration

The agent already calls `tools/send_telegram.py` via Bash. This change is purely additive (new `--file` flag repetition). No MCP server changes, no `.mcp.json` changes, no bridge import changes needed.

The PM prompt in `agent/sdk_client.py` needs updating to show multi-file syntax. The skill doc `.claude/skills/telegram/SKILL.md` needs a multi-file example added.

## Documentation

- [ ] Update inline docstrings in `tools/send_telegram.py` (usage examples, function signature)
- [ ] Update inline docstrings in `bridge/telegram_relay.py` (Redis queue contract)
- [ ] Update PM prompt in `agent/sdk_client.py` with multi-file `--file` example
- [ ] Update `.claude/skills/telegram/SKILL.md` with multi-file example

No new feature doc needed -- this is a small extension of existing documented functionality.

## Success Criteria

- [ ] `python tools/send_telegram.py "caption" --file a.png --file b.png --file c.png` queues a single payload with `file_paths: [list]`
- [ ] Single `--file` still works identically (backward compatible)
- [ ] Bridge relay sends multi-file payloads as a Telegram album via `send_file([paths])`
- [ ] File-only album (no caption) works
- [ ] Missing files at queue time: exit with error listing which files don't exist
- [ ] Missing files at relay time: send available files, skip missing with warning log
- [ ] More than 10 files: exit with clear error
- [ ] Relay handles old `file_path` (string) payloads during rolling deployment
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (multi-file)**
  - Name: multi-file-builder
  - Role: Implement CLI, payload, and relay changes plus test updates
  - Agent Type: builder
  - Resume: true

- **Validator (multi-file)**
  - Name: multi-file-validator
  - Role: Verify all success criteria, run tests, check backward compat
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Update send_telegram.py for multi-file support
- **Task ID**: build-send-tool
- **Depends On**: none
- **Validates**: tests/unit/test_send_telegram.py
- **Assigned To**: multi-file-builder
- **Agent Type**: builder
- **Parallel**: true
- Change `--file` argparse to `action="append"`, `dest="file_paths"`
- Change `send_message()` signature: `file_path: str | None` to `file_paths: list[str] | None`
- Validate each file in list exists, normalize to absolute paths
- Validate list length <= 10 (Telegram album limit)
- Store as `file_paths` (list) in Redis payload
- Update CLI entry point to pass `file_paths` list
- Update all existing tests for new signature and payload format
- Add new tests: multi-file queueing, >10 files error, partial missing files error

### 2. Update telegram_relay.py for album sends
- **Task ID**: build-relay
- **Depends On**: none
- **Validates**: tests/unit/test_bridge_relay.py
- **Assigned To**: multi-file-builder
- **Agent Type**: builder
- **Parallel**: true
- Normalize incoming payloads: `file_path` (string) wrapped to list, `file_paths` (list) used directly
- Filter missing files at send time with warning log
- Pass file list to `client.send_file()` for album sends
- Handle Telethon return value: list of Messages for albums, single Message for single file. Record first message ID.
- Update existing tests for new payload format
- Add new tests: multi-file album send, partial file missing at relay time, backward compat with old `file_path` payloads

### 3. Update agent-facing documentation
- **Task ID**: build-docs
- **Depends On**: build-send-tool, build-relay
- **Assigned To**: multi-file-builder
- **Agent Type**: builder
- **Parallel**: false
- Update PM prompt in `agent/sdk_client.py` with multi-file `--file` examples
- Update `.claude/skills/telegram/SKILL.md` with multi-file usage
- Update module docstrings in `tools/send_telegram.py` and `bridge/telegram_relay.py`

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-send-tool, build-relay, build-docs
- **Assigned To**: multi-file-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/unit/test_send_telegram.py tests/unit/test_bridge_relay.py -v`
- Verify backward compatibility: old `file_path` payloads still work in relay
- Verify lint/format: `python -m ruff check . && python -m ruff format --check .`
- Confirm all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_send_telegram.py tests/unit/test_bridge_relay.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check tools/send_telegram.py bridge/telegram_relay.py` | exit code 0 |
| Format clean | `python -m ruff format --check tools/send_telegram.py bridge/telegram_relay.py` | exit code 0 |
| Multi-file payload key | `grep -n "file_paths" tools/send_telegram.py` | output contains file_paths |
| Backward compat in relay | `grep -n "file_path" bridge/telegram_relay.py` | output contains file_path |
| Album limit validation | `grep -n "10" tools/send_telegram.py` | output contains 10 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

No open questions -- the scope is narrow and all technical decisions are straightforward based on Telethon's existing album API and the patterns established in PR #642.
