---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-02-12
tracking: https://github.com/tomcounsell/ai/issues/29
---

# Reactivate Daydream Mode with Self-Reflection

## Problem

The Daydream system (`scripts/daydream.py`) was built as an autonomous daily maintenance process but is currently inactive. The existing code has broken references (`clawdbot` CLI doesn't exist), no scheduling, no Telegram reporting, and no ability to learn from past mistakes.

Meanwhile, session logs (`logs/sessions/`), conversation history (SQLite), bridge logs (`logs/bridge.log`), and crash history (`data/crash_history.jsonl`) accumulate daily with no automated review. Mistakes repeat because there's no feedback loop — each session starts from the same baseline.

**Current behavior:**
- Daydream script exists but is never run
- References non-existent `clawdbot` CLI for Sentry and Linear
- No scheduling mechanism (no launchd plist, no cron)
- No conversation analysis or mistake detection
- No institutional memory — lessons are lost between sessions
- Daily reports written to disk but never sent anywhere

**Desired outcome:**
- Daydream runs daily via launchd (like the existing watchdog pattern)
- Analyzes bridge logs, session snapshots, and conversation history for mistakes
- Uses LLM intelligence to extract lessons learned
- Persists institutional memory in a structured, retrievable format
- Sends a concise daily digest to Telegram
- Mistakes identified in week 1 are avoided in week 2

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (confirm report format and memory storage approach)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Bridge running | `pgrep -f telegram_bridge` | Session logs and message history require active bridge |
| Sentry MCP available | `grep -q sentry .claude/settings.local.json` | Error log analysis |
| SQLite message history | `test -f data/telegram_history.db` | Conversation analysis source |
| Claude API key | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('ANTHROPIC_API_KEY')"` | LLM-powered analysis |

## Solution

### Key Elements

- **Modernized daydream runner**: Replace broken `clawdbot` calls with direct API/SDK usage; add session analysis and self-reflection as new steps
- **LLM-powered analysis**: Use Claude (via SDK) to review logs and sessions, identify mistakes, and extract lessons — not keyword matching
- **Institutional memory**: Structured `data/lessons_learned.jsonl` file that accumulates insights over time and is loadable as context
- **Daily scheduling**: launchd plist (same pattern as `com.valor.bridge-watchdog`) running at 6 AM Pacific
- **Telegram digest**: Concise daily report sent to the primary chat via the bridge's existing `send_response_with_files()` machinery

### Flow

**6 AM trigger** → Run maintenance steps → Analyze yesterday's sessions → LLM reflection on mistakes → Append lessons → Generate report → **Send to Telegram**

### Technical Approach

- Rewrite `scripts/daydream.py` to remove `clawdbot` dependency. Sentry checks use the Sentry MCP tools or direct API. Linear checks replaced with GitHub issue scanning via `gh` CLI.
- Add Step 7 (Session Analysis): Read session snapshots from `logs/sessions/`, parse `chat.json` and `tool_use.jsonl`, identify high-thrash sessions (many tool calls, low success), user corrections, and error patterns.
- Add Step 8 (Self-Reflection): Feed the analysis output to Claude Haiku with a structured prompt requesting mistake categorization, root cause, and prevention rules. Output as JSON.
- Add Step 9 (Memory Consolidation): Append reflection output to `data/lessons_learned.jsonl`. Deduplicate by pattern similarity. Prune entries older than 90 days.
- Report step enhanced to format findings as a Telegram-friendly message and send via `bridge/response.py`.
- Scheduling via `com.valor.daydream.plist` in `~/Library/LaunchAgents/`.

### Institutional Memory Format

```jsonl
{"date": "2026-02-12", "category": "misunderstanding", "summary": "Built OAuth when user asked for simple API key auth", "pattern": "minimizing qualifier + complex domain", "prevention": "When 'simple/basic/quick' precedes a complex domain, clarify scope first", "source_session": "tg_valor_-123_456", "validated": 0}
```

Categories: `misunderstanding`, `code_bug`, `poor_planning`, `tool_misuse`, `scope_creep`, `integration_failure`

### Report Format (Telegram)

```
Daily Daydream - Feb 12

Health: 92/100
Errors (Sentry): 2 new, 1 recurring
Sessions reviewed: 8
Lessons extracted: 2

Top finding: Agent built full OAuth flow when user wanted API key auth. Added prevention rule for ambiguous auth requests.

Full report: logs/daydream/report_2026-02-12.md
```

## Rabbit Holes

- **Real-time analysis during sessions**: Don't try to intercept live sessions — batch analysis of yesterday's data is sufficient and much simpler
- **Complex NLP on conversation tone**: Don't build sentiment analysis — just detect explicit corrections ("No, I meant...", "That's wrong", etc.) and high tool-call thrash
- **Auto-fixing code from lessons**: Daydream should observe and report, not autonomously modify production code based on its reflections
- **Building a custom vector store for lessons**: JSONL with text search is enough for v1 — don't add embeddings or a vector DB

## Risks

### Risk 1: LLM analysis cost
**Impact:** Daily Claude API calls for session analysis could add up
**Mitigation:** Use Haiku for analysis (cheap, fast). Cap input to 10 most interesting sessions per day. Skip days with no sessions.

### Risk 2: Session snapshot availability
**Impact:** Snapshots auto-delete after 7 days — might miss older patterns
**Mitigation:** Daydream runs daily so this is fine. Extracted lessons persist independently of source snapshots.

### Risk 3: Noisy reports
**Impact:** Sending Telegram messages every day could become annoying if nothing interesting happened
**Mitigation:** Only send detailed reports when there are real findings. Silent days get a one-line "All clear" or skip entirely.

## No-Gos (Out of Scope)

- Hormesis / calibrated stress testing (see #89)
- Pain scoring formulas and scar tissue persistence (see #89)
- Auto-fixing code based on lessons learned
- Real-time / live session interception
- Vector embeddings or semantic search over lessons
- Multi-machine daydream coordination

## Update System

- New launchd plist (`com.valor.daydream.plist`) must be installed on each machine
- Add to `scripts/remote-update.sh`: install/reload the daydream plist
- Add to `/update` skill: check daydream service status after update
- New file `data/lessons_learned.jsonl` — propagated naturally via git (committed periodically)

## Agent Integration

No direct agent integration required for v1. The daydream runs as a standalone scheduled process, not as an agent-invokable tool.

Future consideration: the agent could load `data/lessons_learned.jsonl` into context at session start (similar to how CLAUDE.md is loaded). This is a v2 enhancement, not part of this plan.

## Documentation

- [ ] Create `docs/features/daydream-reactivation.md` describing the reactivated system
- [ ] Update `docs/features/README.md` index table
- [ ] Update `docs/operations/daydream-system.md` to reflect new steps and scheduling
- [ ] Update `CLAUDE.md` quick reference with daydream commands

## Success Criteria

- [ ] `python scripts/daydream.py` runs all steps without errors
- [ ] Sentry check uses direct API (no `clawdbot` dependency)
- [ ] Session analysis step reviews yesterday's `logs/sessions/` snapshots
- [ ] LLM reflection produces categorized lessons from session analysis
- [ ] Lessons appended to `data/lessons_learned.jsonl`
- [ ] Daily report sent to Telegram with health score and findings
- [ ] `com.valor.daydream.plist` installed and running on schedule
- [ ] Documentation updated and indexed

## Team Orchestration

### Team Members

- **Builder (daydream core)**
  - Name: daydream-builder
  - Role: Rewrite daydream.py with modernized steps, session analysis, and LLM reflection
  - Agent Type: builder
  - Resume: true

- **Builder (scheduling)**
  - Name: schedule-builder
  - Role: Create launchd plist, update scripts, integrate Telegram reporting
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: daydream-validator
  - Role: Run daydream end-to-end, verify reports and lesson storage
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Rewrite daydream core
- **Task ID**: build-daydream
- **Depends On**: none
- **Assigned To**: daydream-builder
- **Agent Type**: builder
- **Parallel**: true
- Rewrite `scripts/daydream.py`:
  - Step 1 (legacy cleanup): Keep as-is, works fine
  - Step 2 (log review): Keep, enhance with structured error extraction
  - Step 3 (Sentry): Replace `clawdbot` with direct Sentry API call using `SENTRY_AUTH_TOKEN` from `.env` and the `requests` library (query `https://sentry.io/api/0/projects/{org}/{project}/issues/?query=is:unresolved`)
  - Step 4 (task cleanup): Replace `clawdbot linear` with `gh issue list --state open --label bug` via subprocess
  - Step 5 (docs): Keep as-is
  - Step 6 (report): Enhance to include session analysis findings
- Add Step 7 (session analysis): Read `logs/sessions/*/chat.json` and `tool_use.jsonl`, compute thrash ratio (tool calls / successful outcomes), detect user corrections in conversation text
- Add Step 8 (LLM reflection): Call Claude Haiku via `anthropic` SDK with structured prompt, output categorized lessons as JSON
- Add Step 9 (memory consolidation): Append to `data/lessons_learned.jsonl`, deduplicate by pattern similarity

### 2. Build scheduling and reporting
- **Task ID**: build-scheduling
- **Depends On**: none
- **Assigned To**: schedule-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `com.valor.daydream.plist` in project (modeled on `com.valor.bridge-watchdog.plist`)
  - Runs at 6 AM Pacific daily
  - Points to `scripts/daydream.py`
  - Logs to `logs/daydream.log`
- Create `scripts/install_daydream.sh` to symlink plist and `launchctl load`
- Add Telegram report sending: after report generation, use `bridge/response.py:send_response_with_files()` or a standalone Telethon call to send digest to primary chat
- Update `scripts/remote-update.sh` to install/reload daydream plist

### 3. Validate
- **Task ID**: validate-daydream
- **Depends On**: build-daydream, build-scheduling
- **Assigned To**: daydream-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `python scripts/daydream.py` end-to-end
- Verify all 9 steps complete without error
- Verify `data/lessons_learned.jsonl` has entries
- Verify report file created in `logs/daydream/`
- Verify Telegram message would be sent (check output/logs)
- Verify launchd plist is syntactically valid (`plutil -lint`)

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-daydream
- **Assigned To**: documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/daydream-reactivation.md`
- Update `docs/features/README.md` index
- Update `docs/operations/daydream-system.md` with new steps
- Update `CLAUDE.md` quick reference

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: daydream-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all success criteria met
- Verify documentation exists and is indexed
- Run full daydream one more time to confirm stability

## Validation Commands

- `python scripts/daydream.py` - Full run completes without error
- `test -f data/lessons_learned.jsonl` - Institutional memory file created
- `test -f logs/daydream/report_$(date +%Y-%m-%d).md` - Today's report exists
- `plutil -lint com.valor.daydream.plist` - Plist is valid
- `test -f docs/features/daydream-reactivation.md` - Feature doc exists
- `grep -q daydream docs/features/README.md` - Indexed in feature list
