---
status: Done
type: bug
appetite: Medium
owner: Valor
created: 2026-04-03
tracking: https://github.com/tomcounsell/ai/issues/641
last_comment_id:
---

# Unify Telegram Send Interface

## Problem

**Current behavior:**
The agent has two tools for sending Telegram messages -- `tools/send_telegram.py` (text-only, Redis queue path) and `valor-telegram send` (full-featured CLI with `--file`/`--image`/`--audio`, direct Telethon). The PM prompt only teaches the text-only tool. When the agent needs to send screenshots or files, it confuses the two tools, invents nonexistent parameters (`--photo`, `--project`), and leaks CLI syntax into user-facing responses. This happened 6 times in a single day on the PsyOPTIMAL project, requiring 3+ human corrections.

The root cause is architectural: three conflicting prompt signals (PM prompt teaches text-only tool, Telegram skill documents full CLI, persona warning says "don't expose CLI syntax") force the agent to reconcile incompatible information, producing invented hybrid params.

**Desired outcome:**
A single PM send tool that supports both text and file attachments, routed through the existing Redis queue + relay pipeline. The agent should never leak CLI syntax into responses, and file attachments should work without human intervention.

## Prior Art

- **[PR #527](https://github.com/tomcounsell/ai/pull/527)**: "PM Telegram tool: PM session composes its own messages" -- Created `send_telegram.py` as the PM's text tool. Intentionally text-only to keep scope small. Merged 2026-03-25.
- **[Issue #589](https://github.com/tomcounsell/ai/issues/589)**: QA humility -- Added "TOOL USAGE ONLY" warnings to persona prompt after CLI syntax leaked into responses. Addressed the symptom (leaking) but not the root cause (no file-send path for the PM).
- **[Issue #71](https://github.com/tomcounsell/ai/issues/71)**: Consolidated multiple Telegram skills into `valor-telegram`. Predates the PM tool; established the CLI interface.
- **[Issue #497](https://github.com/tomcounsell/ai/issues/497)**: Created the PM self-messaging architecture (Redis queue, relay, summarizer bypass). The design deliberately separated PM sends from the direct Telethon path.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #527 | Created `send_telegram.py` as PM text tool | Intentionally omitted file support to keep scope small. Left the agent without any file-send path through the PM tool. |
| Issue #589 | Added "TOOL USAGE ONLY" warning to persona prompt | Addressed the symptom (CLI syntax leaking) but not the root cause (PM tool lacks file support). Agent still has no valid path for file sends. |

**Root cause pattern:** Each fix narrowed what the agent could do or say, but never gave it a working file-send capability through its documented tool. The agent is forced to improvise because no correct path exists.

## Data Flow

### Current text-only flow (working)

1. **Entry point**: PM session calls `python tools/send_telegram.py "message text"`
2. **send_telegram.py**: Validates env vars, linkifies text, builds JSON payload `{chat_id, reply_to, text, session_id, timestamp}`, pushes to Redis queue `telegram:outbox:{session_id}`
3. **telegram_relay.py**: Polls Redis, pops message, calls `send_markdown(client, chat_id, text, reply_to=reply_to)`
4. **Output**: Telegram message delivered. `msg_id` recorded on `AgentSession.pm_sent_message_ids` for summarizer bypass.

### Proposed file-send flow (new)

1. **Entry point**: PM session calls `python tools/send_telegram.py "caption text" --file /path/to/file.png`
2. **send_telegram.py**: Validates env vars AND file existence, builds JSON payload `{chat_id, reply_to, text, file_path, session_id, timestamp}`, pushes to Redis queue
3. **telegram_relay.py**: Pops message, detects `file_path` field, calls `client.send_file(entity, file_path, caption=text, reply_to=reply_to)` instead of `send_markdown()`
4. **Output**: Telegram file message delivered. `msg_id` recorded on `AgentSession.pm_sent_message_ids` for summarizer bypass.

Key constraint: the file must exist on the same machine as the bridge relay (which it always does -- same machine). No file transfer needed.

## Architectural Impact

- **New dependencies**: None -- `client.send_file()` is already available via Telethon (used by `bridge/response.py` for `<<FILE:>>` markers)
- **Interface changes**: `send_telegram.py` gains `--file` argument. Redis queue payload gains optional `file_path` field. Both are backward-compatible additions.
- **Coupling**: No change -- same Redis queue contract, same relay consumer
- **Data ownership**: No change -- relay still owns the send, session still records message IDs
- **Reversibility**: High -- both changes are additive. Removing `--file` support is trivial.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (scope fully defined by issue evidence)
- Review rounds: 1 (final validation)

## Prerequisites

No prerequisites -- this work modifies existing tools and bridge components using dependencies already in the project (Telethon, Redis).

## Solution

### Key Elements

- **send_telegram.py extension**: Add `--file` argument for file attachments, validate file existence, include `file_path` in Redis queue payload
- **telegram_relay.py extension**: Detect `file_path` in queue payload, route to `client.send_file()` for file messages vs `send_markdown()` for text-only
- **PM prompt update**: Document the file syntax in `sdk_client.py` PM prompt injection
- **Prompt surface cleanup**: Ensure no PM-facing prompt references `valor-telegram send`

### Flow

**PM needs to send file** -> calls `python tools/send_telegram.py "caption" --file /path/to/file` -> queued in Redis -> relay detects `file_path` -> `client.send_file()` -> Telegram delivery -> msg_id recorded for summarizer bypass

### Technical Approach

#### 1. Extend `tools/send_telegram.py`

Add argparse-based CLI with `--file` argument. The `send_message()` function gains an optional `file_path` parameter.

- Use `argparse` instead of raw `sys.argv` joining to properly handle `--file` flag
- Validate file exists before queuing (fail fast with clear error)
- Serialize `file_path` as absolute path string in the JSON payload
- Text-only messages continue to work identically (backward compatible)
- Allow file-only messages (no text/caption) by relaxing the "text required" validation when a file is present

#### 2. Extend `bridge/telegram_relay.py`

Modify `_send_queued_message()` to handle file payloads:

- Check for `file_path` in the message dict
- If present and file exists: use `client.send_file(entity, file_path, caption=text, reply_to=reply_to)`
- If present but file missing: log warning, fall back to text-only send with a note about the missing file
- If absent: use existing `send_markdown()` path (no change)
- Return `msg_id` from the send_file result for recording on AgentSession

#### 3. Update PM prompt in `sdk_client.py`

Replace the single-line tool documentation with:

```
python tools/send_telegram.py "Your message here"
python tools/send_telegram.py "Caption for file" --file /path/to/screenshot.png
```

Add explicit note: "Use --file to attach screenshots, images, or documents."

#### 4. Prompt surface audit

- Verify `config/personas/_base.md` "TOOL USAGE ONLY" warning remains (it prevents `valor-telegram send` leaking, which is correct behavior)
- Verify `.claude/skills/telegram/SKILL.md` is NOT loaded in PM session context (it's `user-invocable: false`, so only Dev sessions get it)
- No changes needed to `valor-telegram` itself -- it remains the Dev session/CLI tool

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `send_telegram.py`: file not found should exit with code 1 and clear error message
- [ ] `telegram_relay.py`: file missing at send time (deleted between queue and send) should log warning and fall back to text-only
- [ ] `telegram_relay.py`: `client.send_file()` failure should follow existing re-queue pattern (re-push to queue tail)

### Empty/Invalid Input Handling
- [ ] `send_telegram.py`: `--file` with empty string should exit with error
- [ ] `send_telegram.py`: `--file` with nonexistent path should exit with error
- [ ] `send_telegram.py`: file-only (no text) should succeed (caption is optional for file sends)
- [ ] `send_telegram.py`: text-only (no --file) should work identically to current behavior

### Error State Rendering
- [ ] When file send fails, the error message should indicate what file failed and why
- [ ] When file is missing at relay time, log warning includes the path for debugging

## Test Impact

- [ ] `tests/unit/test_send_telegram.py::TestSendTelegramQueueing::test_queues_message_to_redis` -- UPDATE: payload assertions should still pass (text-only path unchanged), but add new test for file payload
- [ ] `tests/unit/test_send_telegram.py::TestSendTelegramCli::test_main_with_args` -- UPDATE: argparse migration may change how args are joined; verify backward compatibility
- [ ] `tests/unit/test_send_telegram.py::TestSendTelegramCli::test_main_no_args_exits` -- UPDATE: argparse changes may alter error behavior; verify exit code 1 still holds
- [ ] `tests/unit/test_bridge_relay.py::TestSendQueuedMessage::test_sends_via_send_markdown` -- no change (text-only path unchanged)
- [ ] `tests/unit/test_bridge_relay.py::TestProcessOutbox::test_processes_queued_messages` -- no change (text-only path unchanged)

## Rabbit Holes

- Making `valor-telegram send` the PM tool -- it bypasses the Redis queue/relay/summarizer-bypass pipeline, which would break `has_pm_messages()` tracking. The queue path is load-bearing.
- Adding `--image` and `--audio` as separate flags to `send_telegram.py` -- Telethon's `send_file()` auto-detects media type. One `--file` flag covers all types.
- Building a file transfer protocol for remote bridge deployments -- the bridge and agent run on the same machine. File paths work directly.
- Adding a sanitizer/guardrail to strip `--` prefixes from response text -- this is a symptom fix. Once the PM has a working file-send path, it won't invent params.

## Risks

### Risk 1: argparse migration breaks text-only sends
**Impact:** Existing PM text messages stop working
**Mitigation:** Extensive backward-compatibility tests. The argparse parser uses `nargs="*"` for positional message args to preserve the `sys.argv[1:]` join behavior. Run existing test suite before and after.

### Risk 2: File path serialization in Redis
**Impact:** Paths with special characters (spaces, unicode) may corrupt in JSON
**Mitigation:** Use `os.path.abspath()` to normalize paths. JSON handles unicode natively. Test with paths containing spaces.

## Race Conditions

### Race 1: File deleted between queue and relay send
**Location:** `tools/send_telegram.py` (queue) and `bridge/telegram_relay.py` (send)
**Trigger:** File is queued, then deleted before the relay processes it (100ms poll interval makes this unlikely but possible)
**Data prerequisite:** File must exist at the path stored in `file_path`
**State prerequisite:** None beyond file existence
**Mitigation:** Relay checks file existence before `send_file()`. If missing, falls back to text-only send with a log warning. Agent-generated files (screenshots, etc.) are typically in `/tmp/` with long lifetimes.

## No-Gos (Out of Scope)

- Replacing `valor-telegram send` -- it remains the Dev session and manual CLI tool
- Adding media-type-specific flags (`--image`, `--audio`) to `send_telegram.py` -- `--file` covers all types
- Modifying the summarizer bypass logic -- the existing `has_pm_messages()` mechanism works correctly
- File transfer for remote/multi-machine deployments
- Response text sanitization (stripping `--` prefixes) -- this is a symptom, not the root cause
- Changing how `bridge/response.py` handles `<<FILE:>>` markers -- that's the Dev session file path, orthogonal to PM sends

## Update System

No update system changes required -- this modifies `tools/send_telegram.py` and `bridge/telegram_relay.py`, both of which are synced via normal `git pull` during updates. No new dependencies, config files, or migration steps.

## Agent Integration

No MCP server changes required. The PM tool (`tools/send_telegram.py`) is invoked via Bash by the PM session, not through MCP. The change is to the tool's CLI interface and the bridge relay's message handling.

The PM prompt injection in `agent/sdk_client.py` must be updated to document the `--file` flag -- this is the only "agent integration" surface, and it's covered in the Solution section.

## Documentation

- [ ] Update `docs/features/pm-self-messaging.md` (if it exists) or create it to describe the PM send tool including file support
- [ ] Update `.claude/skills/telegram/SKILL.md` to add a note distinguishing "PM tool" (`send_telegram.py`) from "CLI tool" (`valor-telegram send`) and when each is used
- [ ] Update `CLAUDE.md` Quick Commands table if `send_telegram.py` usage is listed there

## Success Criteria

- [ ] `python tools/send_telegram.py "caption" --file /path/to/image.png` queues a file payload in Redis
- [ ] `bridge/telegram_relay.py` sends file messages using `client.send_file()` when `file_path` is in the payload
- [ ] Text-only sends continue working identically (backward compatible)
- [ ] PM prompt in `sdk_client.py` documents the `--file` syntax
- [ ] No references to `valor-telegram send` in any PM-facing prompt surface
- [ ] `--photo` and `--project` are not valid params (regression guard via argparse strict parsing)
- [ ] File-only sends (no caption text) work correctly
- [ ] Missing file at queue time exits with error; missing file at relay time falls back to text-only
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (send-tool)**
  - Name: send-tool-builder
  - Role: Extend send_telegram.py with --file support and argparse migration
  - Agent Type: builder
  - Resume: true

- **Builder (relay)**
  - Name: relay-builder
  - Role: Extend telegram_relay.py to handle file payloads
  - Agent Type: builder
  - Resume: true

- **Builder (prompt)**
  - Name: prompt-builder
  - Role: Update PM prompt in sdk_client.py and audit prompt surfaces
  - Agent Type: builder
  - Resume: true

- **Validator (final)**
  - Name: final-validator
  - Role: Verify all send paths work and no prompt surface references valor-telegram send
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Extend send_telegram.py
- **Task ID**: build-send-tool
- **Depends On**: none
- **Validates**: `tests/unit/test_send_telegram.py`
- **Assigned To**: send-tool-builder
- **Agent Type**: builder
- **Parallel**: true
- Migrate CLI from raw `sys.argv` to `argparse` with positional `message` (nargs="*") and optional `--file` flag
- Add `file_path` parameter to `send_message()` function
- Validate file existence when `--file` is provided (exit code 1 with clear error if not found)
- Allow file-only sends (relax "text required" when file_path is present)
- Include `file_path` (as absolute path string) in the Redis queue JSON payload
- Preserve backward compatibility: `python tools/send_telegram.py "Hello World"` must work identically
- Add unit tests for: file payload queuing, file-not-found error, file-only send, argparse backward compat

### 2. Extend telegram_relay.py
- **Task ID**: build-relay
- **Depends On**: build-send-tool
- **Validates**: `tests/unit/test_bridge_relay.py`
- **Assigned To**: relay-builder
- **Agent Type**: builder
- **Parallel**: false
- Modify `_send_queued_message()` to check for `file_path` in the message dict
- When `file_path` is present and file exists: use `telegram_client.send_file(int(chat_id), file_path, caption=text, reply_to=int(reply_to))` and return the `msg_id`
- When `file_path` is present but file missing: log warning with path, fall back to text-only send via `send_markdown()`
- When `file_path` is absent: no change (existing `send_markdown()` path)
- Add unit tests for: file send dispatch, missing file fallback, file-only (no caption) send

### 3. Update PM prompt
- **Task ID**: build-prompt
- **Depends On**: build-send-tool
- **Validates**: grep for `--file` in sdk_client.py, grep for `valor-telegram send` in PM prompt surfaces
- **Assigned To**: prompt-builder
- **Agent Type**: builder
- **Parallel**: true (independent of relay work)
- Update `agent/sdk_client.py` PM prompt injection (~line 1627) to document file send syntax
- Verify `config/personas/_base.md` "TOOL USAGE ONLY" warning remains intact
- Verify `.claude/skills/telegram/SKILL.md` has `user-invocable: false` (not loaded for PM)
- Add a note to SKILL.md distinguishing PM tool from CLI tool

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-relay, build-prompt
- **Assigned To**: prompt-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create or update `docs/features/pm-self-messaging.md` with file attachment documentation
- Update feature index in `docs/features/README.md`
- Update SKILL.md with PM vs CLI distinction

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite for send_telegram and bridge_relay tests
- Grep PM prompt surfaces for `valor-telegram send` references (must find none)
- Verify argparse rejects `--photo` and `--project` (strict parsing)
- Verify backward compatibility: text-only sends produce identical payloads
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_send_telegram.py tests/unit/test_bridge_relay.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check tools/send_telegram.py bridge/telegram_relay.py` | exit code 0 |
| Format clean | `python -m ruff format --check tools/send_telegram.py bridge/telegram_relay.py` | exit code 0 |
| PM prompt has --file | `grep -c '\-\-file' agent/sdk_client.py` | output > 0 |
| No valor-telegram in PM prompt | `python -c "import ast; tree=ast.parse(open('agent/sdk_client.py').read()); [print('FOUND') for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str) and 'valor-telegram send' in node.value]"` | exit code 0 |
| argparse rejects --photo | `TELEGRAM_CHAT_ID=x VALOR_SESSION_ID=x python tools/send_telegram.py --photo foo "test" 2>&1; echo $?` | output contains 2 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

No open questions -- the issue provides complete evidence, root cause analysis, and a validated solution sketch. The implementation path is straightforward: additive changes to two existing files plus a prompt update.
