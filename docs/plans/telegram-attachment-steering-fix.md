---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-30
tracking: https://github.com/tomcounsell/ai/issues/1215
last_comment_id:
---

# Telegram Attachment Steering Fix + Auto-Ingest

## Problem

File attachments sent over Telegram are silently dropped when the receiving session is already running, active, or pending. The agent receives the literal sentinel `"--file attachment only--"` instead of the actual file content. The user thinks they shared a `.txt`, screenshot, or document; the agent sees a four-word string and proceeds without it.

**Current behavior:**

1. User sends a file (`.txt`, photo, document) into a chat that already has a live session.
2. The bridge runs `clean_message(text, project)` (`bridge/telegram_bridge.py:1025`). For a media-only message, `text` is empty, so the result is empty too.
3. The bridge falls through to `clean_text = "--file attachment only--" if message.media else "--empty message--"` (line 1027).
4. The bridge then routes the message to one of five steering branches that all call `_ack_steering_routed(...)` with that sentinel (lines 1081, 1292, 1325, 1358, 1542, 1639).
5. `_ack_steering_routed` pushes the sentinel into the Redis steering queue. No media download is attempted on this path.
6. The deferred-enrichment path (`bridge/enrichment.py:enrich_message`) — which *does* download media — is only invoked by the worker for new sessions, never for steering-routed messages.

Confirmed in production logs (Chris X, 2026-04-30 03:44 UTC):

```
[steering] Pushed message to steering:tg_pba_-5238229534_9440: '--file attachment only--' (from Chris X)
```

No download attempt appears anywhere in the surrounding log lines.

**Desired outcome:**

- File attachments reach the agent as enriched text (document content, image description, voice transcription) regardless of whether the target session is new or already running.
- The fix is centralized so all five `_ack_steering_routed` call sites benefit at once — no scattered patches at each branch.
- Every downloaded attachment is automatically ingested into the work-vault knowledge base so it is permanently searchable via `python -m tools.memory_search search`.
- Knowledge ingestion is fire-and-forget: a failure there must never block steering delivery or crash the bridge.
- Text-only steering messages remain on their existing fast path — no new latency for the common case.

## Freshness Check

**Baseline commit:** `fbcaceb845115f577607f7104557824e68c11e7f`
**Issue filed at:** 2026-04-30T05:51:35Z
**Plan time:** 2026-04-30T05:54Z (~3 minutes after issue creation)
**Disposition:** Unchanged

**Skip rationale:** The issue was filed within the last hour AND no commits have landed on main since (`git log --since=2026-04-30T05:50:00Z main` returns empty). Per `/do-plan` Phase 0.5, this satisfies the skip condition. Even so, the four cited file:line references were re-verified against current main:

- `bridge/telegram_bridge.py:690` — `_ack_steering_routed` definition — **still holds**.
- `bridge/telegram_bridge.py:1027` — sentinel injection (`"--file attachment only--"`) — **still holds**.
- `bridge/telegram_bridge.py:1292` — primary steering call site — **still holds**.
- `bridge/media.py:388` — `process_incoming_media(client, message)` signature — **still holds**, returns `tuple[str, list[Path]]`.
- `tools/knowledge/converter.py:423` — `convert_to_sidecar(source_path, *, force=False)` — **still holds**.

Additional discovery: there are **five** `_ack_steering_routed` call sites, not the two implied by the issue body's "call sites" sentence. Lines 1081 (semantic-routed active session), 1292 (reply-to running/active), 1325 (reply-to pending), 1358 (reply-to-completed re-routed to live), 1542 (in-memory coalescing guard), and 1639 (intake classifier interjection). All six paths must benefit from the fix; placing the enrichment inside `_ack_steering_routed` itself satisfies this with one change.

**Cited sibling issues/PRs re-checked:** None of the references (`#642`, `#726`, commit `ddc33e49` / PR #1167) require revisiting — they describe orthogonal send-side and ingestion plumbing.

**Active plans in `docs/plans/` overlapping this area:** None — `grep -r "_ack_steering_routed\|process_incoming_media" docs/plans/` returns no matches.

## Prior Art

- **PR #1167** (`feat(#1161): markitdown integration for knowledge pipeline`, merged 2026-04-26): introduced `valor-ingest`, `tools/knowledge/converter.py:convert_to_sidecar`, and `bridge/knowledge_watcher.py`. **Did not** wire incoming Telegram attachments into the ingest pipeline. This plan closes that gap.
- **#642**: Add `--file` support to PM send tool — outbound only, no overlap with the inbound steering path.
- **#726**: `valor-telegram` send broken — same outbound plumbing, also no overlap.
- No closed issues match `"steering attachment media"` (`gh issue list --state closed --search "steering attachment media"` returns `[]`). This is a fresh defect, not a regression of a prior fix. **No `## Why Previous Fixes Failed` section needed.**
- No xfail tests in `tests/` cover steering+media (`grep "pytest.mark.xfail\|pytest.xfail(" tests/ | grep -i "steering\|attachment\|media"` returns empty). Nothing to convert.

## Research

No relevant external findings — proceeding with codebase context. The change is internal: it composes two functions that already exist (`process_incoming_media`, `convert_to_sidecar`) inside one helper that already exists (`_ack_steering_routed`). No new libraries, APIs, or ecosystem patterns are introduced.

## Data Flow

The full inbound path for a Telegram message that arrives during a live session:

1. **Entry point:** Telegram delivers a new message; Telethon `event` reaches the bridge handler in `bridge/telegram_bridge.py`.
2. **Project resolution + cleaning:** `clean_message(text, project)` returns text. For media-only messages this is empty, and the bridge substitutes the `"--file attachment only--"` sentinel (line 1027).
3. **Routing decision:** One of six branches resolves a `session_id` (reply-to, semantic-router match, intake-classifier interjection, in-memory coalescing guard, etc.). If the resolved session is `running`/`active`/`pending`, the branch calls `_ack_steering_routed(client, event, message, session_id, sender_name, text=clean_text, log_context=..., agent_session=...)`.
4. **Steering helper (current):** `_ack_steering_routed` writes `text` (the sentinel for media-only) to `agent_session.queued_steering_messages` (when `agent_session` is given), pushes the same `text` into Redis via `push_steering_message`, reacts with the eyes emoji, and records the message handled.
5. **Worker drains queue:** The worker reads the steering queue at the next turn boundary and feeds the sentinel into the agent's input. The file is never seen.

After the fix:

3.5. **NEW — media enrichment inside `_ack_steering_routed`:** If `message.media` is present, the helper calls `process_incoming_media(client, message)` *before* the push. The returned `description` (document content, image description, voice transcription, or graceful failure string) replaces the sentinel as the actual text pushed to the steering queue. Text-only messages skip this branch entirely.
3.6. **NEW — fire-and-forget knowledge ingestion:** When `process_incoming_media` returns a non-empty `files` list, the helper schedules a background coroutine that copies each file into `~/work-vault/telegram-attachments/` (creating the directory on first use). The existing `KnowledgeWatcher` picks up the new file via watchdog, the 2-second debounce coalesces it, and the indexer routes it through `convert_to_sidecar` to produce a searchable `.md` sidecar. The coroutine is wrapped in a top-level `try/except` and dispatched via `asyncio.create_task` so its failure cannot propagate.

## Architectural Impact

- **New dependencies:** None at the package level. `process_incoming_media` and `convert_to_sidecar` are already wired into the bridge process; this plan composes them.
- **Interface changes:** `_ack_steering_routed` gains internal logic but keeps its existing signature (callers pass the same arguments). No public interface moves.
- **Coupling:** Slightly increases coupling inside `bridge/telegram_bridge.py` between steering and media — acceptable because both already live in the same module and the alternative (touching every call site) is worse.
- **Data ownership:** The work-vault remains the canonical knowledge store; the fix only adds a new write path under `~/work-vault/telegram-attachments/`. The knowledge watcher already owns scanning and indexing.
- **Reversibility:** Trivially reversible — revert restores the sentinel behaviour; the new vault subdirectory is harmless if left in place.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (sanity-check the file landing location and ingestion fire-and-forget contract)
- Review rounds: 1

This is a tightly-scoped centralized fix plus a small additive feature. Most of the surface area is already in place — the work is composing existing functions and adding a vault subdirectory.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `~/work-vault` exists and is the configured knowledge root | `python -c "import os; assert os.path.isdir(os.path.expanduser('~/work-vault')), 'work-vault missing'"` | Auto-ingest target directory must exist |
| `markitdown` extras installed | `python -c "import markitdown"` | Required for sidecar conversion of `.docx`, `.pdf`, etc. (installed by `uv pip install -e '.[knowledge]'`) |
| `process_incoming_media` is importable from the bridge process | `python -c "from bridge.media import process_incoming_media"` | Centralized media handler used by the new steering enrichment |

## Solution

### Key Elements

- **Centralized steering enrichment:** Add a media-detection branch at the top of `_ack_steering_routed`. If `message.media` is truthy, call `process_incoming_media(client, message)`, replace the sentinel `text` with the returned description (or compose with existing text if a caption was present), and proceed with the normal push/react/record sequence. All six call sites benefit automatically.
- **Fire-and-forget vault ingestion:** When the helper receives a non-empty `files` list from `process_incoming_media`, schedule a background `asyncio.create_task` that copies each file into `~/work-vault/telegram-attachments/` with a deduplicated filename (`{date}_{sender}_{telegram_msg_id}_{original}`). The `KnowledgeWatcher` already monitors that directory recursively; it converts and indexes the file with no further wiring. The coroutine catches every exception locally and logs at `warning` so the bridge keeps serving.
- **Text-only fast path:** Gate every new operation behind `if message.media:` so the existing zero-cost path for text-only steering is untouched. No new awaits, no new logging, no new branches for plain text.

### Flow

User sends a `.txt` to a live session →
Bridge handler routes through `_ack_steering_routed` →
**NEW:** helper detects `message.media`, awaits `process_incoming_media`, replaces sentinel with extracted document text →
**NEW:** helper schedules ingestion task that copies file into `~/work-vault/telegram-attachments/` →
Existing `push_steering_message` writes enriched text to Redis →
Existing reaction + log + record-handled sequence completes →
Worker drains queue at next turn boundary, agent reads document content →
(Asynchronously) `KnowledgeWatcher` picks up the new vault file → `convert_to_sidecar` writes `.md` → indexer adds the document to `KnowledgeDocument` →
The same content is now retrievable via `python -m tools.memory_search search "..."`.

### Technical Approach

- **Single change site:** All enrichment logic lives inside `_ack_steering_routed`. No call-site edits — the six branches at lines 1081/1292/1325/1358/1542/1639 stay byte-identical.
- **Caption composition:** When a media message has a non-empty caption (`text` arrives as the cleaned caption rather than the sentinel), prepend the media description: `"{description}\n\n{text}"`. When the caption is the sentinel, replace it outright. This mirrors the composition pattern already in `bridge/enrichment.py:enrich_message` (lines 89-92) so behaviour matches the new-session deferred path.
- **Vault subdirectory:** Use `~/work-vault/telegram-attachments/` (auto-classifies as `company-wide` per `tools/knowledge/scope_resolver.py:88`). Create on first use with `mkdir(parents=True, exist_ok=True)`. The watcher has been monitoring `~/work-vault/` recursively since #1167; new subdirectories require no config change.
- **Filename collision:** Use `f"{message.date.strftime('%Y%m%d_%H%M%S')}_{sender_name}_{message.id}_{original_basename}"`. The `(timestamp, message.id)` pair from Telegram is unique per chat, and `sender_name` provides additional disambiguation. Re-runs of the bridge against the same message remain idempotent because the watcher's hash check short-circuits sidecar regeneration.
- **Fire-and-forget contract:** The ingest task runs as `asyncio.create_task(_ingest_attachments(files))`. The wrapper catches `Exception` (broad on purpose — every failure must be non-fatal) and logs at `warning`. The push to the steering queue happens *first*, so a slow or failing copy never gates message delivery.
- **Latency for text-only steering:** Zero. The new code is gated behind `if message.media:`, and `message.media` is `None` for plain text. The hot path through `_ack_steering_routed` for the common case adds zero awaits and zero new branches taken.

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] Identify `except Exception: pass` blocks in touched files. The existing `_ack_steering_routed` already has one around `set_reaction` (line 726-727); no new bare `pass` is added. The new ingest wrapper logs at `warning` rather than swallowing silently. Test asserts that a forced ingest exception still results in a successful steering push.
- [x] No new exception handlers swallow without logging.

### Empty/Invalid Input Handling
- [x] `process_incoming_media` already returns `("[User sent a {type} but the file is invalid/corrupted: ...]", [])` for invalid files. The fix surfaces that string to the agent unchanged — no further handling required.
- [x] When `process_incoming_media` returns an empty description (no media or unhandled type), fall back to the existing sentinel/text behaviour rather than pushing an empty string.
- [x] Empty `files` list from `process_incoming_media` skips the ingest task entirely — no zero-byte copies into the vault.

### Error State Rendering
- [x] Failure to download media surfaces as `"[User sent a {type} but download failed]"` (existing `process_incoming_media` behaviour) — the agent sees the failure string instead of silently missing the file.
- [x] Failure to ingest into the vault is logged at `warning` and never reaches the user; the steering message itself still arrives enriched. This is the correct trade-off (delivery > durable indexing).

## Test Impact

- [ ] `tests/unit/test_bridge_ack_steering_routed.py` — UPDATE: add a `TestMediaEnrichment` class with cases covering (a) media + empty caption replaces sentinel, (b) media + non-empty caption prepends description, (c) text-only path bypasses `process_incoming_media` entirely, (d) `process_incoming_media` exception leaves the original sentinel intact and still pushes, (e) ingest task exception does not prevent the push. Existing tests remain unchanged.
- [ ] `tests/unit/test_media_handling.py` — no changes; `process_incoming_media` itself is untouched.
- [ ] No integration tests currently cover the steering+media combination — a new smoke test (`tests/integration/test_steering_media_smoke.py`) is added under the `bridge` marker that drives the helper end-to-end with a temp file and verifies the vault copy lands.

No existing tests need to be deleted or replaced — the change is purely additive at the helper level and the existing suite continues to pass unmodified.

## Rabbit Holes

- **Refactoring the sentinel out of `bridge/telegram_bridge.py:1027`.** Tempting, because the sentinel feels redundant once the helper enriches. Resist — the sentinel is still useful as a defensive fallback when `process_incoming_media` returns an empty description (e.g., unknown media type). Touching it ripples into every steering branch.
- **Project-aware vault routing.** The issue suggests `~/work-vault/telegram-attachments/`. A more refined version would route into the project's own knowledge_base subdir (e.g., `~/work-vault/PsyOptimal/inbox/`). This is a real follow-up but doubles the surface area. Stay company-wide for v1; revisit only if users request per-project routing.
- **Re-using `enrich_message` for the steering path.** It is tempting because it already composes media + URL + reply-chain enrichment. But `enrich_message` is async-friendly with the worker's enrichment phase and expects deferred metadata (`raw_media_message_id`, `youtube_urls` JSON, etc.). Re-fitting it for inline use in the bridge handler is more work than calling `process_incoming_media` directly.
- **Symlinking instead of copying.** Symlinking the `data/media/` file into `~/work-vault/` avoids a duplicate write. But the existing media file is short-lived and its path is part of the bridge's temp scratch space — a symlink would break when the underlying file is rotated or cleaned. Copy is simpler and durable.
- **Reacting differently for media interjections.** The eyes emoji is fine and consistent; resist any urge to invent a new "📎" reaction for attachments — that is a UX change that belongs in a separate issue.

## Risks

### Risk 1: Latency added to media-bearing steering messages
**Impact:** A large image or document download could delay the steering push by seconds, making the user wonder if the message was received.
**Mitigation:** The user-facing eyes emoji reaction (`set_reaction`) currently fires *after* the push. Move it to fire *before* the download starts so the user gets immediate visual acknowledgement. The push to Redis happens after enrichment completes. `process_incoming_media` already has its own validation and timeouts; we do not add new bounds beyond what already governs the new-session path.

### Risk 2: Knowledge ingestion failures crash the bridge
**Impact:** A markitdown error on an unusual file format could propagate up and kill the event loop.
**Mitigation:** Wrap the ingest task body in a top-level `try/except Exception as e:` that logs at `warning`. Use `asyncio.create_task` (not `await`) so the coroutine never blocks the handler. The watcher path already has the same contract (`docs/features/markitdown-ingestion.md` — "never crash the bridge"); we extend it to the bridge-side copy step.

### Risk 3: Vault filename collisions
**Impact:** Two messages from different chats with the same Telegram message ID could overwrite each other's vault copies, losing one.
**Mitigation:** Filename uses `{YYYYMMDD_HHMMSS}_{sender_name}_{message.id}_{original_basename}`. The `message.id` is per-chat-unique, and the sender + timestamp components add disambiguation. Even worst-case overlap is bounded — a duplicate write would re-hash to the same content (sidecar conversion is idempotent on hash match).

### Risk 4: PII / sensitive content auto-ingested without consent
**Impact:** A user sends a private screenshot expecting it to be transient; it ends up permanently indexed in the work-vault.
**Mitigation:** This is an explicit product choice — the vault is the canonical knowledge store and inbound files are work artifacts by definition. Document the behaviour in `docs/features/telegram.md` so it is discoverable. No code-level consent gate is required for v1; revisit if a user objects.

## Race Conditions

### Race 1: Concurrent steering pushes mutating `queued_steering_messages`
**Location:** `bridge/telegram_bridge.py:_ack_steering_routed`, lines 713-718
**Trigger:** Two attachments arrive simultaneously and both branches reach the dual-push at the same time.
**Data prerequisite:** None — `agent_session.push_steering_message` writes through the Popoto ORM which serialises field updates per session.
**State prerequisite:** AgentSession exists in Redis.
**Mitigation:** Existing — `agent_session.push_steering_message` is the only writer; the underlying Popoto field update is atomic per session. The fix does not change the order of these operations; it only changes the value of `text`. No new race introduced.

### Race 2: Vault file copy completes after the `KnowledgeWatcher` debounce window
**Location:** New `_ingest_attachments` task; `bridge/knowledge_watcher.py:_DebouncedHandler` (2-second debounce).
**Trigger:** A burst of attachments arrives in <2 s; the watcher's debounce coalesces them.
**Data prerequisite:** The vault directory must exist before the file copy.
**State prerequisite:** Watcher is running.
**Mitigation:** This is the watcher's intended behaviour, not a hazard — coalescing is the *point* of the 2-second debounce. Each file is processed inside the same `_flush()` iteration that writes the sidecars. No additional coordination needed.

### Race 3: `process_incoming_media` and the steering push observe different message states
**Location:** `_ack_steering_routed` between the new media call and `push_steering_message`.
**Trigger:** Telegram delivers an edit to the message between download and push.
**Data prerequisite:** None — Telethon's `message` object is a snapshot.
**State prerequisite:** None.
**Mitigation:** Edits are handled by a separate code path (`bridge/telegram_message_edit.py`); the `message` object captured by the handler is a snapshot at receive time. We push the snapshot's enrichment, which is the same contract as the rest of the bridge.

## No-Gos (Out of Scope)

- **Not** adding media handling to the new-session deferred enrichment path beyond the auto-ingest hook — that path already enriches via `enrich_message`. The auto-ingest hook is added for both paths so the vault is consistent regardless of which path served the message.
- **Not** routing files into project-specific vault subdirectories. Company-wide `~/work-vault/telegram-attachments/` for v1; project routing is a separate issue.
- **Not** adding new media types to `process_incoming_media`. We use it as-is. Voice/photo/document/audio coverage is already in place; expanding to new types is unrelated.
- **Not** changing the `--file attachment only--` sentinel itself. The string remains as a defensive fallback when `process_incoming_media` returns an empty description.
- **Not** adding consent prompts before ingestion. Auto-ingest is the default; a consent gate is a future product decision.
- **Not** introducing a new emoji reaction for attachments. The existing eyes reaction is the steering acknowledgement contract.
- **Not** modifying `bridge/enrichment.py:enrich_message`. The new-session path already downloads media correctly; we extend it only with the auto-ingest hook (one new call) and leave its existing behaviour untouched.

## Update System

No update system changes required. The fix is purely internal to the bridge process. The vault subdirectory `~/work-vault/telegram-attachments/` is created on first use by the bridge itself; no migration step is needed on existing machines. The `markitdown` extras and `valor-ingest` CLI are already deployed via the prior `/update` flow established in `docs/infra/markitdown-ingestion.md`.

## Agent Integration

No new agent integration required. Existing surfaces continue to work:

- The agent receives the enriched text (document content, image description, voice transcription) via the steering queue — same channel as before, with richer content.
- Auto-ingested attachments become discoverable via the existing `python -m tools.memory_search search` CLI, which is already a registered console script.
- The bridge itself imports `process_incoming_media` (already imported elsewhere in `bridge/`) and the new `_ingest_attachments` helper (a private function inside `bridge/telegram_bridge.py`). No `pyproject.toml [project.scripts]` entries change.
- Integration test (`tests/integration/test_steering_media_smoke.py`) verifies the round-trip from an inbound `.txt` to enriched steering content + vault landing.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/telegram.md` with a new subsection: "Inbound attachments — steering enrichment + auto-ingest" describing the centralized helper change, the vault subdirectory, and the fire-and-forget contract.
- [ ] Add a one-line entry to `docs/features/README.md` index pointing at the new subsection (or refresh the existing telegram entry's blurb).
- [ ] Update `docs/features/markitdown-ingestion.md` to mention the new auto-ingest source (Telegram steering path), so future readers see the full set of vault writers.

### External Documentation Site
This repo has no Sphinx/Read the Docs site; nothing else to update.

### Inline Documentation
- [ ] Update the docstring of `_ack_steering_routed` to describe the new media branch and the fire-and-forget ingest task.
- [ ] Add a brief comment at the top of the new `_ingest_attachments` helper documenting the never-block contract.

## Success Criteria

- [ ] A `.txt` file sent to an active session arrives as its text content in the steering message — no `"--file attachment only--"` reaches the agent for media-bearing messages.
- [ ] A photo sent to an active session arrives as an image description in the steering message.
- [ ] A document with a caption arrives as `description + "\n\n" + caption` (composition behaviour matches the new-session enrichment path).
- [ ] A file sent when **no** active session exists continues to work via the existing new-session deferred enrichment path — that path is unchanged and tests covering it stay green.
- [ ] Every downloaded attachment (steering and new-session) lands under `~/work-vault/telegram-attachments/` with a unique filename and is discoverable via `python -m tools.memory_search search` once the watcher's debounce window elapses.
- [ ] Forced exceptions inside `process_incoming_media` or the ingest task do not crash the bridge — the steering push still arrives (sentinel as fallback for media path, enriched text for ingest-only failures).
- [ ] Unit tests in `tests/unit/test_bridge_ack_steering_routed.py` cover the five new behaviours listed in **Test Impact**.
- [ ] Integration smoke test sends a `.txt` to a running session, asserts the agent receives the file contents, and asserts the vault sidecar exists after the watcher debounce.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] Verification grep: `grep -n "process_incoming_media" bridge/telegram_bridge.py` shows the new call inside `_ack_steering_routed`.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly — they deploy team members and coordinate.

### Team Members

- **Builder (steering-media-enrichment)**
  - Name: `steering-builder`
  - Role: Add the media-detection branch and fire-and-forget ingest helper to `_ack_steering_routed`.
  - Agent Type: builder
  - Resume: true

- **Test Engineer (steering-media-tests)**
  - Name: `steering-tester`
  - Role: Add the five `TestMediaEnrichment` unit cases and the integration smoke test.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (steering-media)**
  - Name: `steering-validator`
  - Role: Run `pytest tests/unit/test_bridge_ack_steering_routed.py tests/integration/test_steering_media_smoke.py` and confirm all success criteria.
  - Agent Type: validator
  - Resume: true

- **Documentarian (steering-media-docs)**
  - Name: `steering-docs`
  - Role: Update `docs/features/telegram.md`, `docs/features/markitdown-ingestion.md`, and `docs/features/README.md`.
  - Agent Type: documentarian
  - Resume: true

### Step by Step Tasks

#### 1. Build the centralized media enrichment + ingest helper
- **Task ID**: build-steering-media
- **Depends On**: none
- **Validates**: tests/unit/test_bridge_ack_steering_routed.py, tests/integration/test_steering_media_smoke.py (both updated/created in step 2)
- **Informed By**: Freshness Check (5 call sites confirmed), Data Flow (composition with caption is the v1 contract)
- **Assigned To**: steering-builder
- **Agent Type**: builder
- **Parallel**: false
- Edit `bridge/telegram_bridge.py:_ack_steering_routed`:
  - Move the existing `set_reaction` call to fire **before** the media download so the user gets an immediate visual ack.
  - Add a `if message.media:` branch that calls `process_incoming_media(client, message)` with a try/except. On success, replace `text` with the description (or compose with caption when `text` is non-sentinel). On failure, log at `warning` and leave `text` as-is.
  - When `files` is non-empty, schedule `asyncio.create_task(_ingest_attachments(files, message, sender_name))`.
- Add a private `_ingest_attachments(files, message, sender_name)` helper at module scope that copies each file into `~/work-vault/telegram-attachments/` with the disambiguated filename. Wrap the body in `try/except Exception as e:` that logs at `warning`.
- Update the `_ack_steering_routed` docstring to describe the new media branch and the fire-and-forget contract.

#### 2. Build the test coverage
- **Task ID**: build-steering-media-tests
- **Depends On**: build-steering-media
- **Validates**: own assertions
- **Assigned To**: steering-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Add a `TestMediaEnrichment` class to `tests/unit/test_bridge_ack_steering_routed.py` with the five cases listed in **Test Impact**. Mock `process_incoming_media` and `asyncio.create_task` to assert dispatch order without doing real I/O.
- Create `tests/integration/test_steering_media_smoke.py` under the `bridge` marker:
  - Drive `_ack_steering_routed` with a fake message carrying a real temp `.txt`.
  - Assert the steering queue receives the file contents.
  - Assert a copy lands under `~/work-vault/telegram-attachments/` with the disambiguated name.
  - Use `tmp_path` for the vault root via monkeypatch so the test does not pollute the real vault.

#### 3. Validate end-to-end
- **Task ID**: validate-steering-media
- **Depends On**: build-steering-media-tests
- **Assigned To**: steering-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_bridge_ack_steering_routed.py tests/integration/test_steering_media_smoke.py -v`.
- Run `python -m ruff check bridge/telegram_bridge.py tests/unit/test_bridge_ack_steering_routed.py tests/integration/test_steering_media_smoke.py`.
- Verify `grep -n "process_incoming_media" bridge/telegram_bridge.py` shows the new call inside the helper, not at the existing call sites.
- Confirm all Success Criteria are met. Report pass/fail.

#### 4. Documentation
- **Task ID**: document-steering-media
- **Depends On**: validate-steering-media
- **Assigned To**: steering-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Add the "Inbound attachments — steering enrichment + auto-ingest" subsection to `docs/features/telegram.md`.
- Update `docs/features/markitdown-ingestion.md` to mention Telegram steering as a vault writer.
- Refresh `docs/features/README.md` if the telegram entry needs a new keyword for the index.

#### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: document-steering-media
- **Assigned To**: steering-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the full Verification table below.
- Confirm all Success Criteria checkboxes are reachable from the diff.
- Report final pass/fail.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_bridge_ack_steering_routed.py -q` | exit code 0 |
| Integration smoke passes | `pytest tests/integration/test_steering_media_smoke.py -q` | exit code 0 |
| Lint clean | `python -m ruff check bridge/telegram_bridge.py tests/` | exit code 0 |
| Format clean | `python -m ruff format --check bridge/telegram_bridge.py tests/` | exit code 0 |
| Helper composes media | `grep -n "process_incoming_media" bridge/telegram_bridge.py` | output contains `_ack_steering_routed` (in surrounding context) |
| Vault subdirectory referenced | `grep -n "telegram-attachments" bridge/telegram_bridge.py` | exit code 0 |
| No stale xfails introduced | `grep -rn 'xfail' tests/unit/test_bridge_ack_steering_routed.py tests/integration/test_steering_media_smoke.py` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique. Leave empty until critique is run. -->

---

## Open Questions

1. **Vault subdirectory naming.** Current proposal: `~/work-vault/telegram-attachments/`. Alternatives considered: `~/work-vault/inbox/telegram/`, `~/work-vault/_inbox_/`. The hyphenated underscore-prefixed `_inbox_` matches existing vault convention (`_archive_`, `_notes_`). Should we adopt that convention instead?
2. **Reaction ordering.** The plan moves `set_reaction` to fire before the media download so the user sees the eyes emoji immediately. Confirm this is acceptable — the previous order put the reaction *after* the push, so it implicitly signalled "queued" rather than "received". Are we OK with the small semantic shift?
3. **Per-project routing v1?** Should v1 route project-tagged messages into their project's vault subfolder (e.g., `~/work-vault/PsyOptimal/inbox/`) rather than the company-wide bucket? The plan currently says "no" to keep scope tight; confirm this aligns with how the knowledge base is meant to grow.
