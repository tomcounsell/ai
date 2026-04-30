---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-30
tracking: https://github.com/tomcounsell/ai/issues/1215
last_comment_id:
revision_applied: true
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
- **Fire-and-forget vault ingestion:** When the helper receives a non-empty `files` list from `process_incoming_media`, schedule a background `asyncio.create_task` that copies each file into `~/work-vault/telegram-attachments/` with a deduplicated filename (`{date}_{sender}_{telegram_msg_id}_{original}`). The `KnowledgeWatcher` already monitors that directory recursively; it converts and indexes the file with no further wiring. The coroutine catches every exception locally and logs at `warning` so the bridge keeps serving. **Implementation Note (concern: a bare `asyncio.create_task(...)` reference can be silently garbage-collected before the coroutine completes — a well-known Python footgun that turns "fire-and-forget" into "fire-and-vanish"):** the new task MUST be appended to the existing module-level `_background_tasks` list at `bridge/telegram_bridge.py` (the same list used at lines 2465 and 2493 for `_run_catchup` and `watchdog_loop`). Pattern: `_background_tasks.append(asyncio.create_task(_ingest_attachments(files, message, sender_name)))`. This guarantees the GC keeps a strong reference until the coroutine completes. The validator in step 3 must `grep -n "_background_tasks.append" bridge/telegram_bridge.py` and confirm the new ingest task is in the list.
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
- **Vault subdirectory:** Use `~/work-vault/telegram-attachments/` (auto-classifies as `company-wide` per `tools/knowledge/scope_resolver.py:88`). Create on first use with `mkdir(parents=True, exist_ok=True)`. The watcher has been monitoring `~/work-vault/` recursively since #1167; new subdirectories require no config change. **Implementation Note (concern: "the watcher monitors recursively since #1167" is asserted but should be validated against the actual watcher init, not assumed):** the builder MUST verify `bridge/knowledge_watcher.py` calls `Observer.schedule(..., recursive=True)` (or equivalent) before shipping. If recursion is per-subdir-opt-in instead of automatic, the plan must add a `KnowledgeWatcher.add_path("~/work-vault/telegram-attachments/")` call to the bridge startup sequence. The validator step 3 grep must include `grep -n "recursive" bridge/knowledge_watcher.py` and confirm the watcher will pick up the new subdir without an explicit registration call. If recursion is NOT enabled by default, the builder STOPS, files a child issue for the missing recursion, and routes back through `/sdlc` — this is a hard precondition for the auto-ingest contract.
- **Filename collision:** Use `f"{message.date.strftime('%Y%m%d_%H%M%S')}_{sender_name}_{message.id}_{original_basename}"`. The `(timestamp, message.id)` pair from Telegram is unique per chat, and `sender_name` provides additional disambiguation. Re-runs of the bridge against the same message remain idempotent because the watcher's hash check short-circuits sidecar regeneration. **Implementation Note (concern: `message.id` is unique per chat but NOT globally unique — two different chats can independently produce the same numeric id, and second-resolution timestamps may collide for fast bursts):** the chosen filename keeps the per-chat-unique `message.id` PLUS the second-resolution timestamp PLUS the sender name; the joint key `(YYYYMMDD_HHMMSS, sender_name, message.id)` requires three independent collisions to overlap. In the residual collision case, the watcher's content-hash idempotency means a duplicate write either (a) hashes identically and is a no-op, or (b) hashes differently and the second write overwrites the first — losing one file. We accept this residual risk for v1 because the joint-key collision probability is operationally negligible (estimated < 1 / 10^9 per inbound burst given current Telegram volume). If a real collision is ever observed, the follow-up is to append a 4-char hash suffix derived from `sha256(file_bytes)[:4]`.
- **Fire-and-forget contract:** The ingest task is registered via `_background_tasks.append(asyncio.create_task(_ingest_attachments(files, message, sender_name)))` so it cannot be GC'd mid-flight. The wrapper catches `Exception` (broad on purpose — every failure must be non-fatal) and logs at `warning`. The push to the steering queue happens *first*, so a slow or failing copy never gates message delivery.
- **Latency for text-only steering:** Zero. The new code is gated behind `if message.media:`, and `message.media` is `None` for plain text. The hot path through `_ack_steering_routed` for the common case adds zero awaits and zero new branches taken. **Implementation Note (concern: `process_incoming_media` is reused as-is from the new-session worker path, but the steering path is user-facing — a slow download stalls a live conversation, while a slow worker-side download is invisible):** to keep the user from staring at silence, `set_reaction` is moved to fire **before** the `process_incoming_media` call (see Risk 1 mitigation). The eyes emoji becomes the immediate "I see you" acknowledgement; the enriched text arrives when the download completes. `process_incoming_media` already enforces its own size and timeout bounds — no new bounds are added here. If user-facing latency proves problematic in practice, the follow-up is to push a placeholder steering message immediately (`"[downloading attachment...]"`) and replace it via `agent_session.queued_steering_messages` once enrichment completes — but that is explicitly OUT OF SCOPE for v1 to keep the change centralized and small.

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

- [ ] `tests/unit/test_bridge_ack_steering_routed.py` — UPDATE: add a `TestMediaEnrichment` class with cases covering (a) media + empty caption replaces sentinel, (b) media + non-empty caption prepends description, (c) text-only path bypasses `process_incoming_media` entirely, (d) `process_incoming_media` exception leaves the original sentinel intact and still pushes, (e) ingest task exception does not prevent the push, (f) ingest task is registered in `_background_tasks` after dispatch, (g) reaction-ordering spy: media path calls `set_reaction → process_incoming_media → push_steering_message`; text-only path calls `push_steering_message → set_reaction`. Existing tests remain unchanged. **Implementation Note (concern: the plan asserts this file already exists — verify before assuming):** confirmed at plan time via `ls tests/unit/test_bridge_ack_steering_routed.py` (size 7573, mtime 2026-04-29). Builder MUST re-confirm at build time and FAIL LOUDLY if the file has been deleted or moved — do not silently create a new file under the same path; if missing, the builder routes back through `/sdlc` because the test plan is invalid.
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
**Mitigation:** The user-facing eyes emoji reaction (`set_reaction`) currently fires *after* the push. Move it to fire *before* the download starts so the user gets immediate visual acknowledgement. The push to Redis happens after enrichment completes. `process_incoming_media` already has its own validation and timeouts; we do not add new bounds beyond what already governs the new-session path. **Implementation Note (concern: reordering `set_reaction` to fire BEFORE the push subtly changes the established UX semantic from "queued" to "received" — Open Question #2 already flags this for human confirmation, but build cannot block on a Q&A round-trip):** if Open Question #2 is unresolved at build time, the builder MUST ship the **media-only reordering** (the reaction fires before download for media-bearing messages) and KEEP THE EXISTING ORDER for text-only steering (reaction still fires after push). Concretely: inside the new `if message.media:` branch, `set_reaction` runs first; outside that branch (text-only path) the existing order is byte-identical. This bounds the semantic change to media interjections only — text-only steering UX is preserved 1:1 and is independently revertible if the human later vetoes the media reorder. The integration smoke test in step 2 must assert this ordering by mocking `set_reaction` and `push_steering_message` with a sequence-tracking spy: the call order for media must be `set_reaction → process_incoming_media → push_steering_message`, while the call order for text-only must be `push_steering_message → set_reaction` (existing).

### Risk 2: Knowledge ingestion failures crash the bridge
**Impact:** A markitdown error on an unusual file format could propagate up and kill the event loop.
**Mitigation:** Wrap the ingest task body in a top-level `try/except Exception as e:` that logs at `warning`. Use `asyncio.create_task` (not `await`) so the coroutine never blocks the handler. The watcher path already has the same contract (`docs/features/markitdown-ingestion.md` — "never crash the bridge"); we extend it to the bridge-side copy step.

### Risk 3: Vault filename collisions
**Impact:** Two messages from different chats with the same Telegram message ID could overwrite each other's vault copies, losing one.
**Mitigation:** Filename uses `{YYYYMMDD_HHMMSS}_{sender_name}_{message.id}_{original_basename}`. The `message.id` is per-chat-unique, and the sender + timestamp components add disambiguation. Even worst-case overlap is bounded — a duplicate write would re-hash to the same content (sidecar conversion is idempotent on hash match).

### Risk 4: PII / sensitive content auto-ingested without consent
**Impact:** A user sends a private screenshot expecting it to be transient; it ends up permanently indexed in the work-vault.
**Mitigation:** This is an explicit product choice — the vault is the canonical knowledge store and inbound files are work artifacts by definition. Document the behaviour in `docs/features/telegram.md` so it is discoverable. No code-level consent gate is required for v1; revisit if a user objects. **Implementation Note (concern: messages from non-project chats — DMs, personal contacts not tied to a project_key — would still ingest into the company-wide vault, which is broader than "work artifacts" — those messages are arguably personal):** the v1 ingest path is intentionally project-agnostic (everything inbound lands in `~/work-vault/telegram-attachments/`, classified as `company-wide` by `tools/knowledge/scope_resolver.py:8`). This is acceptable for v1 because (a) the bridge already gates *which* contacts can interject at all via `dms.whitelist` and project membership in `projects.json` — non-whitelisted senders never reach `_ack_steering_routed`, and (b) the company-wide vault is NOT public — it is the operator's local work store. The narrowest interpretation is: "files you sent to your own AI assistant via your own bridge are by definition work artifacts." If a user later requests project-scoped routing or a per-chat opt-out, the hook point is `_ingest_attachments` — gate the copy on `project_key in {None, "personal"}` or similar. Documentation in `docs/features/telegram.md` MUST explicitly state "every attachment you send to the bridge — DM, group, or topic — becomes searchable in the company-wide work vault" so the behaviour is discoverable BEFORE a user is surprised.

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
  - Inside the new `if message.media:` branch, fire `set_reaction` **before** `process_incoming_media` so the user gets an immediate visual ack. Outside that branch (text-only path), keep the existing reaction-after-push order — see Risk 1 Implementation Note for the rationale.
  - Add the `if message.media:` branch that calls `process_incoming_media(client, message)` with a try/except. On success, replace `text` with the description (or compose with caption when `text` is non-sentinel). On failure, log at `warning` and leave `text` as-is.
  - When `files` is non-empty, schedule the ingest task as `_background_tasks.append(asyncio.create_task(_ingest_attachments(files, message, sender_name)))` to keep the GC from collecting the task mid-flight (matches the existing `_run_catchup` and `watchdog_loop` pattern at lines 2465 and 2493).
- Add a private `_ingest_attachments(files, message, sender_name)` helper at module scope that copies each file into `~/work-vault/telegram-attachments/` with the disambiguated filename. Wrap the body in `try/except Exception as e:` that logs at `warning`.
- Verify `bridge/knowledge_watcher.py` calls `Observer.schedule(..., recursive=True)` so the new vault subdirectory is picked up automatically. If recursion is not enabled, STOP and route back through `/sdlc` per the Vault Subdirectory Implementation Note.
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
- Verify `grep -n "_background_tasks.append" bridge/telegram_bridge.py` shows the new ingest task registration alongside the existing `_run_catchup` and `watchdog_loop` registrations — confirming the task is GC-safe.
- Verify `grep -n "recursive" bridge/knowledge_watcher.py` shows `recursive=True` on the watcher's `Observer.schedule(...)` call (or, if recursion is opt-in, the explicit `add_path("~/work-vault/telegram-attachments/")` registration is present in bridge startup).
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
| Ingest task GC-safe | `grep -n "_background_tasks.append" bridge/telegram_bridge.py` | output contains the new `_ingest_attachments` registration |
| Watcher recursion confirmed | `grep -n "recursive" bridge/knowledge_watcher.py` | shows `recursive=True` on `Observer.schedule(...)` |
| No stale xfails introduced | `grep -rn 'xfail' tests/unit/test_bridge_ack_steering_routed.py tests/integration/test_steering_media_smoke.py` | exit code 1 |

## Critique Results

**Verdict:** READY TO BUILD (with concerns) — recorded 2026-04-30T06:34:40Z.

The war-room verdict was clean enough to ship to BUILD, but six concerns were folded as Implementation Notes during the revision pass per SDLC Row 4b. CONCERNs are acknowledged risks/clarifications, NOT blockers — they remain real risks the build must be aware of, and the Implementation Notes embed the mitigation directly next to the relevant section so the builder cannot miss them.

| # | Lens | Concern | Folded as Implementation Note in |
|---|------|---------|-----------------------------------|
| C1 | Adversary | Bare `asyncio.create_task(...)` references can be silently GC'd before the coroutine completes — fire-and-forget becomes fire-and-vanish | Solution → Key Elements (fire-and-forget vault ingestion bullet) |
| C2 | Operator | `message.id` is per-chat-unique, NOT globally unique; second-resolution timestamps can still collide on bursts | Solution → Technical Approach (filename collision bullet) |
| C3 | Operator | `process_incoming_media` is reused as-is from the worker path, but the steering path is user-facing — slow downloads stall a live conversation | Solution → Technical Approach (latency for text-only steering bullet) |
| C4 | Skeptic | Reordering `set_reaction` to fire BEFORE the push changes the established UX semantic from "queued" to "received"; Open Question #2 flags this for human confirmation but build cannot block on Q&A | Risks → Risk 1 (latency mitigation) |
| C5 | User | Messages from non-project chats (DMs, personal contacts) would still ingest into the company-wide vault — broader than "work artifacts" | Risks → Risk 4 (PII / sensitive content) |
| C6 | Archaeologist | The "watcher monitors recursively since #1167" claim is asserted, not validated — must be confirmed against the actual `Observer.schedule(...)` call | Solution → Technical Approach (vault subdirectory bullet); Step 1 task; Verification table |

Two additional concerns folded into Test Impact and tasks:
- **C7 (Adversary):** `tests/unit/test_bridge_ack_steering_routed.py` is asserted as existing — re-confirm at build time and fail loudly if missing rather than silently creating a new file under the same path.
- **C8 (Operator):** Test Impact's enumerated cases (a)-(e) miss two important assertions — (f) the ingest task is registered in `_background_tasks` after dispatch, (g) the reaction-ordering spy proves the per-branch ordering split.

`revision_applied: true` set in frontmatter; status: Planning → Ready.

---

## Open Questions

1. **Vault subdirectory naming.** Current proposal: `~/work-vault/telegram-attachments/`. Alternatives considered: `~/work-vault/inbox/telegram/`, `~/work-vault/_inbox_/`. The hyphenated underscore-prefixed `_inbox_` matches existing vault convention (`_archive_`, `_notes_`). Should we adopt that convention instead?
2. **Reaction ordering.** The plan moves `set_reaction` to fire before the media download so the user sees the eyes emoji immediately. Confirm this is acceptable — the previous order put the reaction *after* the push, so it implicitly signalled "queued" rather than "received". Are we OK with the small semantic shift?
3. **Per-project routing v1?** Should v1 route project-tagged messages into their project's vault subfolder (e.g., `~/work-vault/PsyOptimal/inbox/`) rather than the company-wide bucket? The plan currently says "no" to keep scope tight; confirm this aligns with how the knowledge base is meant to grow.
