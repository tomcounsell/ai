---
status: docs_complete
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-06-16
tracking: https://github.com/tomcounsell/ai/issues/1709
last_comment_id:
---

# Agent-Judgment `/catchup` Layer (Recover Sessioned-But-Unanswered Messages)

## Problem

The bridge fires a **mechanical catchup** (`bridge/catchup.py::scan_for_missed_messages`) on every connect and runs a periodic **reconciler** (`bridge/reconciler.py::reconcile_once`). Both key recovery on **"did a session get enqueued"** — gated by `is_duplicate_message()` (a `DedupRecord` set of ~50 recent processed IDs per chat) plus the `LastProcessedRecord` cursor — **not on "did a reply actually reach the chat."**

So a message whose session hung or was killed *without replying* is dedup-marked **processed** and skipped **forever** by both scanners. It is bookkeeping-indistinguishable from a message that was answered correctly.

**Current behavior:** Recovery of a silently-failed session requires **manual ORM surgery** — clear the message's `DedupRecord` entry, rewind the `LastProcessedRecord` cursor, restart — to force a re-ingest and a single reply.

**Desired outcome:** An invokable, agent-judgment `/catchup` that **reads the actual chat thread** (including Valor's own prior replies), decides with LLM judgment which messages are genuinely unanswered, and responds — with a strong **conservative bias toward NOT replying**. It recovers **response failures**, not just ingestion gaps, and removes the manual-surgery requirement. `/update` invokes it as its final, best-effort, non-blocking step after bridge+worker restart report healthy.

### Motivating incident (2026-06-16)

PR #1694 switched granite to `/granite:prime-pm-role` slash commands under `.claude/commands/granite/`, but `_sync_commands` globbed only top-level `*.md` and never recursed, so namespaced commands never reached `~/.claude/commands/granite/`. Result per worktree: `Unknown command` → no persona → no real turn → 600s startup ceiling → `startup_unresolved` → silent hang, `communicated=False`, **no reply**. Messages in the Cyndra Dev Team chat were enqueued fine (each got a `DedupRecord`), so the mechanical catchup returned "already processed" and could not recover them. One had to be re-armed by hand. (Root cause fixed separately by commit `3a3ff1ab` — `rglob` recursion.)

## Freshness Check

**Baseline commit:** `e691f2ab` (`git rev-parse HEAD` at plan time)
**Issue filed at:** 2026-06-16T04:39:26Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `bridge/catchup.py::scan_for_missed_messages` — exists, signature confirmed (catchup.py:23-292). Skips via `is_duplicate_message()` (catchup.py:176) + per-chat cursor cutoff (catchup.py:117-137). Has an existing `_check_if_handled()` (catchup.py:295-318) that only matches **threaded** replies (`reply.reply_to_msg_id == message.id`) — does NOT catch non-threaded replies. **Still holds.**
- `bridge/telegram_bridge.py::_run_catchup()` — claimed "around 2914"; **actual line 2863**, fires on connect as a background task (telegram_bridge.py:2911). Minor line drift only.
- `models/dedup.py::DedupRecord` — confirmed; `_MAX_IDS = 50`, `SetField`, `ttl=7200`. "~50 recent" holds.
- `models/last_processed.py::LastProcessedRecord` — confirmed; `last_message_id`, `last_message_ts`, `updated_at`, `ttl=2592000` (30d).
- `agent/granite_container/` — confirmed; PTY-driven interactive TUI runner.
- Single-machine ownership enforced in `bridge/config_validation.py` (validate_dm_whitelist, validate_telegram_groups). `ALL_MONITORED_GROUPS` (telegram_bridge.py:584) is already scoped to this machine's owned groups via `ACTIVE_PROJECTS` (telegram_bridge.py:453-490).
- Persona resolution: `bridge/routing.py::resolve_persona` (339-394) + `persona_to_session_type` (397-415). Confirmed.
- `valor-telegram read` — `pyproject.toml:77` → `tools.valor_telegram:main`; reads thread incl. Valor's own replies (`m.out` → "Valor", valor_telegram.py:283); `--json` flag present.
- Telegram relay + dead-letter: `bridge/telegram_relay.py` (`telegram:outbox:*`), `bridge/dead_letters.py` + `models/dead_letter.py`. Dead letter persists after `MAX_RELAY_RETRIES` failures.

**Cited sibling issues/PRs re-checked:**
- #1408 — CLOSED (PR #1559). Built the per-chat `LastProcessedRecord` cursor + extended reconciler lookback. **Ingestion side.** Still the complement to this issue.
- #948 — CLOSED (PR #952). Centralized dedup recording. Context for `DedupRecord`.
- #588/#590 — CLOSED. Built the periodic reconciler.

**Commits on main since issue was filed (touching referenced files):**
- `fc3e3acc` fix(granite): catchup/reconciler persona resolution (#1708/#1713) — **partially relevant (improves premise).** Added `resolve_persona() → persona_to_session_type()` to BOTH catchup and reconciler scanners, with a narrow per-message try/except falling back to eng. This is the exact persona-correctness primitive the `/catchup` layer must reuse. Does not change the root problem (dedup-based skip).

**Active plans in `docs/plans/` overlapping this area:** none (`ls docs/plans/` shows no catchup/reconciler plan in flight).

**Notes:** All issue claims hold. Only drift is the `_run_catchup()` line number (2863, not 2914) and the new persona-resolution helpers — both improvements to the plan's foundation, not contradictions.

## Prior Art

- **#1408 / PR #1559**: *Messages permanently lost in Telethon update gap.* Built `LastProcessedRecord` per-chat cursor and extended reconciler lookback to close the **ingestion** dead zone (message received but never enqueued). This issue is the **response-failure** complement (message enqueued, session died, no reply). Same family, opposite failure stage.
- **#588 / PR #590**: *Bridge misses messages during live connection.* Added the periodic reconciler (`bridge/reconciler.py`). The reconciler is the architectural sibling `/catchup` composes alongside — both are owner-scoped dialog scanners.
- **#948 / PR #952**: *Centralize dedup recording.* Established `record_message_processed` / `record_last_processed` as the single dedup-write path. `/catchup` must NOT add a parallel watermark; it reads the thread and (optionally) the existing dedup/cursor, never a new store.

## Research

No relevant external findings — this is purely internal (composing `valor-telegram read`, the existing dedup/reconciler primitives, and the in-repo Ollama-first/Haiku-fallback LLM judgment pattern). No external libraries or APIs involved.

## Data Flow

1. **Entry point**: Operator runs `valor-catchup` (CLI) directly, OR `/update` Step (new, final) invokes it after `service.restart_service()` + `service.restart_worker()` report `get_service_status(...).running` and `get_worker_status(...).running`.
2. **Owner scoping**: Resolve this machine's owned chats from `config/projects.json` via the same logic as `_get_active_projects()` / `build_group_to_project_map()` → list of `(chat_id, chat_title, project_config)`.
3. **Thread read**: For each owned chat, read the last N messages (incl. Valor's own `out` replies) via the same Redis-first/Telethon-fallback path that backs `valor-telegram read` (`tools/valor_telegram.py`).
4. **Judgment**: An LLM judge (Ollama-first, Haiku fallback — same pattern as `classify_conversation_terminus`) reads the thread transcript and returns, per recent inbound human message: `ANSWERED` | `UNANSWERED_NEEDS_REPLY` | `UNANSWERED_NO_REPLY_NEEDED`. **Conservative default: any ambiguity or judge error → ANSWERED (no reply).**
5. **Recovery enqueue**: For each `UNANSWERED_NEEDS_REPLY`, resolve persona (`resolve_persona → persona_to_session_type`) and enqueue an `AgentSession` exactly as catchup/reconciler do — same `session_id` shape (`tg_{project_key}_{chat_id}_{message_id}`), same `enqueue_agent_session` signature, same dedup write afterward. The enqueued session then replies through the normal relay → outbox path.
6. **Output**: A persona-correct reply reaches the chat exactly once, OR no reply (conservative default). The CLI prints a summary; `/update` ignores its exit code (best-effort).

## Architectural Impact

- **New dependencies**: None. Composes `valor-telegram` read path, `bridge/routing.py` persona helpers, `bridge/dedup.py` write path, and the existing Ollama/Haiku LLM-judgment helper pattern.
- **Interface changes**: New CLI entry point `valor-catchup` in `pyproject.toml [project.scripts]`. New module `bridge/agent_catchup.py` (judgment scanner). No change to existing scanner signatures.
- **Coupling**: Adds one more owner-scoped scanner that reuses `enqueue_agent_session` + dedup writes. No new storage, no new watermark (per #948 constraint).
- **Data ownership**: No new data store. Reads thread (source of truth) + optionally the existing `DedupRecord`/`LastProcessedRecord`. Writes only through the established `record_message_processed`/`record_last_processed` path on actual recovery.
- **Reversibility**: High. Drop the CLI script + module + the `/update` final step; nothing else depends on it.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1-2 (judge prompt design, double-reply guard)
- Review rounds: 1 (the LLM-judgment correctness and the conservative-default proof are the review focus)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Telegram session present | `python -c "import os; assert os.path.exists(os.path.expanduser('~/Desktop/Valor/.env'))"` | `valor-telegram` read path needs Telethon creds |
| Ollama OR Anthropic key for judge | `python -c "from dotenv import dotenv_values; v=dotenv_values('.env'); assert v.get('ANTHROPIC_API_KEY') or True"` | LLM judge fallback (Ollama-first, Haiku fallback) |

Run all checks: `python scripts/check_prerequisites.py docs/plans/agent_judgment_catchup.md`

## Solution

### Key Elements

- **`bridge/agent_catchup.py`** — Owner-scoped judgment scanner. For each owned chat: read recent thread (incl. Valor replies), run the LLM judge, enqueue recovery sessions for genuinely-unanswered messages.
- **Thread-aware "answered" judge** — LLM classifier (Ollama-first, Haiku fallback) that reads the transcript and classifies each recent inbound human message as ANSWERED / UNANSWERED_NEEDS_REPLY / UNANSWERED_NO_REPLY_NEEDED. Conservative default to ANSWERED on any error/ambiguity.
- **`valor-catchup` CLI** — `pyproject.toml [project.scripts]` entry; manually invokable, prints a per-chat summary, exits 0 even on partial failure (best-effort contract).
- **`/update` final step** — Invokes `valor-catchup` after bridge+worker restart report healthy; wrapped so failure/timeout never blocks `/update` completion.
- **Double-reply guard** — Before enqueuing, re-check `is_duplicate_message`-adjacent state AND re-read for a fresh Valor reply since the judge ran; enqueue at most once per message (dedup write immediately after enqueue, same as existing scanners).

### Flow

`valor-catchup` (or `/update` final step) → resolve this machine's owned chats → for each chat: read recent thread → LLM judge classifies each inbound message → for UNANSWERED_NEEDS_REPLY: resolve persona, enqueue one recovery session, write dedup → worker runs session → persona-correct reply reaches chat (or, conservatively, no reply) → CLI prints summary, exits 0.

### Technical Approach

- **Detecting "answered" keys on the thread, not the session.** The crux. The judge reads the actual transcript (Valor's `out` messages are the ground truth for what's been said) rather than inferring from `DedupRecord`. This dissolves the "failed-silently vs. correctly-silent" ambiguity that a mechanical replay fights (and that motivated removing stdout-silence as a kill signal in #1172). The existing `_check_if_handled` (threaded-reply-only) is insufficient precisely because most replies aren't threaded — the judge supersedes it for this layer.
- **Reuse, don't reinvent.** Persona via `resolve_persona → persona_to_session_type` (the #1708 helpers). Enqueue via the same `enqueue_agent_session` callable and `session_id` shape as catchup/reconciler. Dedup write via `record_message_processed`/`record_last_processed`. Thread read via the `valor-telegram` Redis-first/Telethon-fallback path.
- **Conservative judge contract.** Modeled on `classify_conversation_terminus`: fast-path obvious cases, LLM for the rest, and **any error or ambiguity → ANSWERED (no reply).** The acceptance bar is "a thread whose recent messages were already answered produces NO reply."
- **Interaction with mechanical scanners.** `/catchup` runs *after* `/update`'s restart (which already fires the mechanical catchup). The mechanical layer handles ingestion gaps (un-sessioned messages); `/catchup` handles only messages that DID get a session but no reply. The dedup write after a `/catchup` enqueue prevents the next mechanical scan from double-enqueuing. Ordering boundary: `/catchup` is strictly last, so the mechanical scan has already claimed all ingestion-gap messages.
- **Dedup interaction.** `/catchup` does NOT bypass `DedupRecord` blindly. It reads the thread (which is dedup-independent) for judgment, but on recovery it WRITES dedup so the recovered message is not re-opened. The manual-surgery pattern is eliminated because the judge re-examines dedup-marked messages by reading the thread, something the mechanical scanners structurally cannot do.
- **Granularity.** One judgment pass per owned chat (persona is per-chat, so per-chat sessions are persona-correct by construction). Not one sweeping session — that would muddy persona boundaries.
- **Lookback window.** Bounded read (e.g. last 20 messages or last 2h, whichever is smaller) per chat, capped, mirroring the reconciler's bounded `get_messages` call. Resolved concretely in spike/build.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The per-chat scan loop and the judge call each get a NARROW try/except (mirror catchup/reconciler): on failure, log a greppable WARNING and continue to the next chat — never abort the sweep, never crash `/update`. Test asserts the WARNING fires and the loop continues (via `caplog`).
- [ ] The `/update` invocation wrapper swallows non-zero exit / timeout and logs; test asserts `/update` completion is unaffected by a failing `valor-catchup`.

### Empty/Invalid Input Handling
- [ ] Empty thread (no messages) → judge not called, zero enqueues, exit 0. Test added.
- [ ] Whitespace-only / empty inbound text → skipped before judge (same as scanners skip `not text.strip()`). Test added.
- [ ] Judge returns empty/garbage/None → conservative default ANSWERED (no reply). Test added with a stubbed judge returning junk.

### Error State Rendering
- [ ] CLI prints a per-chat summary including chats that errored (not silently dropped). Test asserts errored chats appear in summary output.
- [ ] No raw error/system string is ever enqueued as a reply — only the original inbound message text is enqueued; the worker session produces the persona-correct reply. Test asserts enqueued `message_text` equals the inbound text, never an error string.

## Test Impact

- [ ] `tests/unit/test_catchup*.py` (existing catchup tests) — No change expected; `/catchup` is a NEW module (`bridge/agent_catchup.py`) and does not modify `scan_for_missed_messages`. Verify by running them post-build; if any shared helper is refactored, UPDATE accordingly.
- [ ] `tests/unit/test_reconciler*.py` — Same: no change unless a shared persona/enqueue helper is extracted. If extracted, UPDATE the reconciler tests to import from the shared location.

No existing tests are modified by design — this is an additive layer composing existing primitives. New tests live in `tests/unit/test_agent_catchup.py` and `tests/integration/test_agent_catchup_recovery.py`. (Justification: the feature adds a new owner-scoped scanner + CLI without altering existing scanner signatures or the dedup/persona helpers it reuses.)

## Rabbit Holes

- **Building a new "answered-ness" watermark / store.** Explicitly forbidden by #948 — read the thread, don't add a parallel store. The thread IS the source of truth.
- **Trying to detect "delivered reply" purely from relay delivery logs + dead-letter queue.** Tempting precision, but the thread read already shows whether Valor's reply landed. Use relay/dead-letter only as a secondary signal if cheap; do not build a delivery-tracking subsystem.
- **Generalizing into a scheduled reflection now.** The issue scopes `/catchup` as standalone + `/update`-invoked. Reflection-callable is "later"; do not wire a scheduler in this plan.
- **Reworking the mechanical catchup/reconciler.** They handle ingestion gaps correctly. Do not refactor them; compose alongside.
- **Making `_check_if_handled` thread-aware.** Tempting to fix the threaded-reply-only limitation in place, but that's the mechanical layer; the judgment layer supersedes it. Leave the mechanical primitive alone.

## Risks

### Risk 1: Double-reply (judge says unanswered, but a reply was in flight)
**Impact:** Customer gets two replies to one message — worse than the bug being fixed.
**Mitigation:** Re-read the thread for a fresh Valor `out` reply immediately before enqueue; write dedup right after enqueue (same idempotency as existing scanners); one enqueue per message id max. Conservative default biases toward NOT replying.

### Risk 2: Judge false-positive recovers an already-answered message
**Impact:** Unwanted reply, possibly to a customer.
**Mitigation:** Acceptance criterion proves the conservative default — an already-answered thread produces zero replies. Judge prompt biased to ANSWERED; any error/ambiguity → ANSWERED.

### Risk 3: `/catchup` blocks or slows `/update`
**Impact:** `/update` hangs on a slow Telethon/LLM call.
**Mitigation:** Tight per-invocation timeout; best-effort wrapper swallows failure/timeout; runs strictly last after health checks. `/update` exit is independent of `/catchup` exit.

### Risk 4: Raw error/system string leaks to a customer chat
**Impact:** A customer sees an internal error.
**Mitigation:** `/catchup` only enqueues the original inbound message text; the persona-correct worker session produces the reply through the normal pipeline (same path as live messages). `/catchup` never composes reply text itself.

## Race Conditions

### Race 1: Reply in flight while `/catchup` judges
**Location:** `bridge/agent_catchup.py` judge → enqueue gap.
**Trigger:** A worker session for the same message is mid-flight (about to send its reply) when `/catchup` reads the thread and the reply hasn't landed yet.
**Data prerequisite:** The thread read must reflect the latest Valor `out` messages; the dedup state must be current.
**State prerequisite:** No concurrent enqueue for the same `session_id`.
**Mitigation:** Re-read the thread for a fresh Valor reply immediately before enqueue (narrowing the window); `is_duplicate_message` + dedup-write idempotency makes a duplicate enqueue a no-op on the next pass; `/catchup` runs *after* worker restart reports healthy, so most in-flight sessions have settled. Residual window accepted as small, mitigated by the conservative default.

### Race 2: Mechanical catchup and `/catchup` both fire on the same `/update`
**Location:** `/update` restart (fires mechanical catchup) → `/update` final step (`valor-catchup`).
**Trigger:** Both scanners read overlapping chats.
**Data prerequisite:** Mechanical catchup's dedup writes must be visible to `/catchup`.
**State prerequisite:** `/catchup` runs strictly after the restart-triggered mechanical catchup.
**Mitigation:** Ordering — `/catchup` is the LAST `/update` step, after health checks; the mechanical catchup (fired by restart) has already claimed ingestion-gap messages and written dedup. `/catchup` only acts on messages that DID get a session but no reply, which the mechanical layer ignores. No double-enqueue because both write/read the same `DedupRecord`.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1536] Startup-phase telemetry (#1538) and don't-auto-resume-deterministic-never-started (#1539) amendments — part of epic #1536, tracked separately.
- Granite adaptive TUI frame-recovery design — separate effort, not part of this judgment layer.
- Scheduled-reflection invocation of `/catchup` — the issue explicitly scopes this as "later, reflection-callable"; this plan ships standalone + `/update`-invoked only. (Additive future work, no blocker; deliberately left for a follow-up once the standalone layer is proven in production.)

## Update System

**Yes — the `/update` skill needs a new final step.**

- Add a final step to `scripts/update/run.py` (after Step 5 service management and its health checks) that invokes `valor-catchup`, gated on `get_service_status(...).running` AND `get_worker_status(...).running`. Wrap in a best-effort try/except with a tight timeout so failure/timeout never changes `/update`'s result.
- Add a corresponding entry to `.claude/skills/update/SKILL.md` documenting the new final step and its best-effort contract.
- The new CLI entry `valor-catchup` is propagated automatically via `pip install -e .` during the existing dependency-sync step (`scripts/update/deps.py`) — no extra propagation wiring needed because it's a `[project.scripts]` entry in `pyproject.toml`.
- No new config files. No migration steps for existing installs beyond the normal `git pull` + dep sync.

## Agent Integration

- **New CLI entry point required:** `valor-catchup = "bridge.agent_catchup:main"` (or `tools.agent_catchup:main`) in `pyproject.toml [project.scripts]`. This is the agent-reachable surface (invocable via Bash) and the `/update` invocation target.
- **Bridge import:** The bridge does NOT need to import `/catchup` directly — it's invoked out-of-band (CLI / `/update`), not on the live message path. The mechanical catchup/reconciler stay as the bridge's in-process scanners.
- **Integration tests:** `tests/integration/test_agent_catchup_recovery.py` verifies the agent can invoke `valor-catchup` end-to-end and that a simulated hung/killed session is recovered exactly once with no manual dedup surgery.
- No new MCP server needed — this is a CLI + `/update` integration, not an in-session agent tool.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/agent-judgment-catchup.md` describing the judgment layer, how it differs from mechanical catchup/reconciler, the conservative-default contract, and the `/update` final-step integration.
- [ ] Add entry to `docs/features/README.md` index table.
- [ ] Cross-link from `docs/features/bridge-worker-architecture.md` (the catchup/reconciler section) to the new doc.

### External Documentation Site
- [ ] N/A — repo has no external docs site.

### Inline Documentation
- [ ] Module docstring on `bridge/agent_catchup.py` explaining the "answered keys on thread, not session" principle and the conservative default.
- [ ] Docstring on the judge function documenting the three return classes and the error → ANSWERED contract.
- [ ] Update `.claude/skills/update/SKILL.md` with the new final step.

## Success Criteria

- [ ] `valor-catchup` exists as a standalone, invokable CLI scoped to this machine's owned chats (`pyproject.toml [project.scripts]`).
- [ ] `/update` invokes `valor-catchup` as its final step, only after bridge+worker restart report healthy; `/catchup` failure/timeout never blocks `/update`.
- [ ] Conservative default proven: a thread whose recent messages were already answered produces **no** reply (unit + integration test).
- [ ] Recovers a response-failure case the mechanical dedup skips: a simulated hung/killed session is detected and answered **exactly once**, with **no** manual `DedupRecord` surgery (integration test).
- [ ] Replies are persona-correct for the chat (reuses `resolve_persona → persona_to_session_type`); no raw error/system string ever enqueued as reply text.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] grep confirms `scripts/run.py` (or `scripts/update/run.py`) references `valor-catchup` in its final step.

## Team Orchestration

The lead agent orchestrates; it never builds directly.

### Team Members

- **Builder (judgment scanner)**
  - Name: catchup-builder
  - Role: Implement `bridge/agent_catchup.py` (owner-scoping, thread read, judge, enqueue, dedup write) + the LLM judge + `valor-catchup` CLI entry.
  - Agent Type: builder
  - Resume: true

- **Builder (update integration)**
  - Name: update-builder
  - Role: Wire the best-effort final `/update` step in `scripts/update/run.py` + SKILL.md.
  - Agent Type: builder
  - Resume: true

- **Test Engineer**
  - Name: catchup-tester
  - Role: Unit tests (conservative default, empty/garbage judge, narrow except + continue) + integration test (recover-once-no-surgery).
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: catchup-validator
  - Role: Verify all success criteria, run validation commands, confirm no double-reply / no raw-error-leak.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: catchup-documentarian
  - Role: Feature doc + README index + SKILL.md + cross-links.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Build judgment scanner + judge + CLI
- **Task ID**: build-scanner
- **Depends On**: none
- **Validates**: tests/unit/test_agent_catchup.py (create), tests/integration/test_agent_catchup_recovery.py (create)
- **Assigned To**: catchup-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `bridge/agent_catchup.py`: resolve owned chats (reuse `_get_active_projects` / `build_group_to_project_map` logic), per-chat thread read (reuse `valor-telegram` read path), LLM judge (Ollama-first, Haiku fallback; conservative ANSWERED default), enqueue recovery via existing `enqueue_agent_session` + persona helpers + dedup write.
- Add narrow per-chat and per-judge try/except: log greppable WARNING, continue.
- Add double-reply guard: re-read for fresh Valor `out` reply before enqueue; one enqueue per message id.
- Add `valor-catchup` to `pyproject.toml [project.scripts]`; `main()` prints per-chat summary, exits 0 on partial failure.

### 2. Build `/update` final-step integration
- **Task ID**: build-update-step
- **Depends On**: build-scanner
- **Validates**: tests/unit/test_update_catchup_step.py (create)
- **Assigned To**: update-builder
- **Agent Type**: builder
- **Parallel**: false
- Add final step in `scripts/update/run.py` after service management, gated on `get_service_status(...).running` and `get_worker_status(...).running`.
- Best-effort wrapper: tight timeout, swallow failure/timeout, log, never change `/update` result.
- Update `.claude/skills/update/SKILL.md`.

### 3. Tests
- **Task ID**: test-catchup
- **Depends On**: build-scanner, build-update-step
- **Assigned To**: catchup-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Unit: conservative-default (answered thread → no reply), empty thread, garbage judge → ANSWERED, narrow-except-continues (caplog WARNING), enqueued message_text == inbound (no error string).
- Integration: simulate hung/killed session (dedup-marked, no reply) → `/catchup` recovers exactly once, no manual surgery; second run → no duplicate.
- Unit: `/update` final step swallows a failing `valor-catchup`.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: test-catchup
- **Assigned To**: catchup-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/agent-judgment-catchup.md`; add README index entry; cross-link from `bridge-worker-architecture.md`; confirm SKILL.md updated.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-scanner, build-update-step, test-catchup, document-feature
- **Assigned To**: catchup-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands; verify every success criterion (incl. conservative-default proof, recover-once-no-surgery, persona-correctness, no raw-error-leak); generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_agent_catchup.py tests/integration/test_agent_catchup_recovery.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| CLI registered | `grep -n 'valor-catchup' pyproject.toml` | output contains valor-catchup |
| Update step wired | `grep -rn 'valor-catchup\|agent_catchup' scripts/update/run.py` | output contains agent_catchup |
| Feature doc exists | `test -f docs/features/agent-judgment-catchup.md` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Judge backend & lookback bounds.** Ollama-first/Haiku-fallback mirrors `classify_conversation_terminus` — confirm that's the desired judge, and confirm the lookback bound (proposed: min(last 20 messages, last 2h) per chat). Acceptable, or tighter?
2. **`/update` invocation shape.** Should the final step invoke the `valor-catchup` CLI as a subprocess (clean isolation, easy timeout) or call the module function in-process? Subprocess is proposed for the best-effort/timeout contract — confirm.
3. **Relay/dead-letter as secondary signal.** The plan treats thread-read as the sole source of truth for "answered" and uses relay delivery logs / dead-letter only opportunistically. Is that acceptable, or do you want dead-letter-absence as a hard input to the judgment?
