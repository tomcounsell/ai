---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-09-04
tracking: https://github.com/tomcounsell/ai/issues/2732
last_comment_id: none
revision_applied: true
revision_applied_at: 2026-09-04T11:46:03Z
---

# Reply-Chain Media Renders As The Literal String `[media]`

## Problem

A teammate attaches a document, two text replies follow, and the agent is handed
this:

```
REPLY THREAD CONTEXT (oldest to newest):
Hazem: [media]
Tom: Plausible recommendation, but it brushes over many details and side-effects
Tom: Valor can help you flesh this out with his do-issue and do-plan skills
```

The chain hydrated perfectly. Every hop is present. The one thing the
conversation is *about* is four characters wide. The agent knows a
recommendation exists and that someone thinks it is thin on side-effects, and it
has no way to read a word of it, no filename to name, and no way to tell the
humans what it cannot see. Asking "point me at this" was the only honest answer
available, and it read as inattention.

**Current behavior:** `bridge/context.py:425` builds each chain entry as
`msg.text or "[media]"`. A caption-less attachment collapses to the literal
string. The file's bytes are already on disk — the bridge downloaded them at
intake under #1297 and recorded the absolute path on
`TelegramMessage.media_local_path` — and the renderer throws the reference away.

A second, quieter case: an attachment *with* a caption renders as the caption
alone. `msg.text` is truthy, so the fallback never fires and nothing in the
rendered line indicates a file is attached at all. The agent reads a sentence and
has no idea it is a cover note for something.

**Desired outcome:** Media anywhere in agent-facing bridge context is something
the agent can act on — a readable local path, or a filename plus the exact
retrieval it needs. The literal `[media]` appears nowhere. A file that genuinely
cannot be resolved says so, specifically, so the agent can report the gap instead
of asking the human to repeat themselves.

## Freshness Check

**Baseline commit:** `dcdb0c58b` (main at revision time; the original plan-time baseline was `b3f43656d`). `bridge/context.py` remains untouched since `e1ec8695c`, and every load-bearing file:line reference below was re-verified at the revision baseline.
**Issue filed at:** 2026-08-12T12:06:04Z (23 days before planning)
**Disposition:** Minor drift

**File:line references re-verified:**

- `bridge/context.py:425` — `"content": msg.text or "[media]"` — **still holds, exact line unchanged.** `git log --since` shows zero commits touching `bridge/context.py` since the issue was filed.
- `bridge/enrichment.py:75` — `media_local_path = getattr(telegram_message, "media_local_path", None)` — still holds, exact line.
- `bridge/email_bridge.py:104` — `EMAIL_ATTACHMENT_DIR` definition — still holds, exact line.
- `agent/session_executor.py:1779` — cited as the `trigger_telegram_message` load — **drifted.** The symbol now lives at `agent/session_executor.py:1850-1861`; line 1779 is unrelated outbox-drain code. Claim itself is intact: the load is still `TelegramMessage.query.filter(msg_id=session.telegram_message_key)` and still resolves exactly one record.

**Cited sibling issues/PRs re-checked:**

- #1297 — closed 2026-05-07, fixed by PR #1316. Established the download-at-intake pattern this plan extends. No change since.
- #1215 — closed 2026-04-30. Steering-path attachment fix, still landed, unrelated to the chain renderer.
- #949 / PR #953 — closed and merged 2026-04-14. Built the resume-branch hydration.
- #1064 / PR #1070 — closed and merged 2026-04-20. Built the fresh-session pre-hydration and its 3s budget.

**Commits on main since issue was filed (touching referenced files):** nine
commits touched `agent/session_executor.py`, `bridge/email_bridge.py`, or the two
feature docs (`9fec0698b`, `f6ba598ce`, `2b926acae`, `f4312871a`, `afc10ef93`,
`659f1d0e4`, `1e398e46d`, `ac190fb26`, `0f070970b`). None changed the root cause;
the only material effect is the `session_executor.py` line drift recorded above.
`bridge/context.py` itself is untouched.

**Bug reproduction against current main:** confirmed by reading the code path
rather than by live Telegram traffic (reproduction needs a real chat with a real
attachment, which is production-only). `fetch_reply_chain` still writes the
literal on the caption-less branch, `format_reply_chain` still copies
`msg["content"]` verbatim into the rendered line, and both bridge call sites
(`telegram_bridge.py:2089` resume path, `telegram_bridge.py:2604` fresh path)
still splice that output into `message_text`. The defect is present.

**Active plans in `docs/plans/` overlapping this area:** none.
`docs/plans/simulated-bridge-dispatch-harness.md` mentions `reply_chain` but is a
July harness plan at `status: Ready` with no overlap in the touched files.

**Notes:** One issue premise was **wrong** and the plan corrects it — see
spike-2. The issue speculates that a captioned document loses its caption because
"`msg.text` for a document may be empty while `msg.message` holds the caption."
Telethon's `Message.text` returns `self.message` (parse-mode-unparsed), so the
caption survives. The real caption-case defect is the inverse: the caption
renders and the *attachment* vanishes without trace.

## Prior Art

- **#1297 / PR #1316**: *Image/voice/document enrichment silently dropped: worker has no Telegram client* — closed 2026-05-07. Moved media download to the bridge at intake and had the worker read `media_local_path` off the `TelegramMessage` record. **Succeeded**, and it is the reason this fix is cheap: the bytes and the path already exist for every chain ancestor the bridge saw.
- **#1215**: *Telegram file attachments silently dropped in steering path + auto-ingest to knowledge base* — closed 2026-04-30. Another instance of media surviving intake and dying at one specific handoff. Same family, different seam.
- **#949 / PR #953**: *Reply-to threads don't carry conversation context* — merged 2026-04-14. Built `fetch_reply_chain` / `format_reply_chain` and the resume-branch call site. The `msg.text or "[media]"` line dates from here and has never been revisited.
- **#1064 / PR #1070**: *Reply-to messages creating a new session don't include full thread history* — merged 2026-04-20. Added the fresh-session pre-hydration path and the 3-second `asyncio.wait_for` budget that constrains this fix.

Both hydration PRs asked whether the chain *arrives*. Neither asked what a
non-text message looks like once it does.

## Research

**Queries used:**

- `Telethon Message.file attributes name mime_type size document filename`

**Key findings:**

- Telethon 1.x exposes a `File` helper at `message.file` with `.name`, `.ext`, `.mime_type`, and `.size` — [docs.telethon.dev/en/stable/modules/utils.html](https://docs.telethon.dev/en/stable/modules/utils.html). `message.file` is `None` when the message carries no media, which makes it a clean has-media predicate as well as the filename source. This removes any need to hand-walk `document.attributes` for `DocumentAttributeFilename`; the repo already does that walk in `bridge/media.py::get_media_type` for type classification, and the descriptor builder can reuse that function rather than duplicating it.
- `File.name` is `None` for `MessageMediaPhoto` — photos genuinely have no filename. The descriptor must fall back to a synthetic label (media type plus message id) rather than rendering an empty name.
- Verified against the installed pin rather than the docs alone: `.venv/.../telethon/tl/custom/file.py` defines `name`, `ext`, `mime_type`, and `size` as properties on the `File` class, and `telethon/tl/custom/message.py:385` defines `text` as returning `self.message` (unparsed when the client has no parse mode).

## Spike Results

All spikes resolved by direct code reading against the working tree at
`b3f43656d`. No agents were dispatched; each question was answerable from the
repo or the installed dependency, which is stronger evidence than a prototype.

### spike-1: Can an ancestor's on-disk path be resolved inside the 3-second budget?

- **Assumption**: "Resolving `media_local_path` for a chain ancestor requires network work and will not fit the pre-hydration budget."
- **Method**: code-read
- **Finding**: **False — it needs no network at all.** `TelegramMessage.chat_id` and `TelegramMessage.message_id` are both Popoto `KeyField`s (`models/telegram.py:25-26`), giving an O(1) Redis filter. The exact lookup already exists in the same file: `bridge/context.py:651` runs `TelegramMessage.query.filter(chat_id=str(chat_id), message_id=current_id)` inside `_cache_walk_root`, and two other call sites use the identical shape (`tools/telegram_history/__init__.py:447`, `bridge/read_the_room.py:180`). The 3s budget is dominated by the two Telethon RPCs already in the loop (`client.get_messages` plus `msg.get_sender()`), not by a sub-millisecond Redis read.
- **Confidence**: high
- **Caveat added at revision**: "cheap" is about latency, not about concurrency. The `filter` call is synchronous redis-py and blocks whatever thread runs it, so a *fast* lookup is still a loop-blocking one. The spike settles where resolution happens; it does not license an inline call. See Risk 4 and the `asyncio.to_thread` rule in Technical Approach.
- **Impact on plan**: Settles issue open question 1. Resolution happens at hydration time in `fetch_reply_chain`; no deferral to the worker, no bounding to the nearest N ancestors.

### spike-2: Does a captioned attachment lose its caption today?

- **Assumption**: "`msg.text` is empty for a document, so a caption is dropped along with the media reference."
- **Method**: code-read (installed Telethon pin)
- **Finding**: **False.** `telethon/tl/custom/message.py:385` — `Message.text` returns `self.message`, parse-mode-unparsed. A captioned document has a truthy `msg.text`, so the caption reaches the rendered chain intact. The defect in the captioned case is different and previously unnamed: because the fallback never fires, the rendered line is *pure caption* with nothing signalling that a file is attached.
- **Confidence**: high
- **Impact on plan**: Reframes one acceptance criterion. The work is not "preserve the caption" (already true) but "compose the caption with an attachment descriptor" — a strictly larger change than the issue described, and one a builder working from the issue text alone would have missed.

### spike-3: Are ancestor media files still on disk when the chain is walked?

- **Assumption**: "`data/media/` is swept on a retention window, so older chain hops reference deleted files."
- **Method**: code-read
- **Finding**: **False for files, true for records.** `tools/disk_reclaim.py` sweeps `.worktrees/`, `~/.claude/projects/` transcripts, and `logs/sessions/` — it never touches `data/media/`. Media files are never garbage-collected. The Redis *record* is swept at 90 days by `reflections/housekeeping/redis_ttl_cleanup.py:36` (`TelegramMessage.cleanup_expired(max_age_days=90)`). So the resolution failure mode is a missing record, not a missing file, and only for chains reaching past 90 days.
- **Confidence**: high
- **Impact on plan**: Settles issue open question 3. The unresolvable case is real but rare — a download failure at intake, a pre-#1297 legacy record, or a >90-day hop. It still needs its own explicit rendering, and the resolver must stat the path rather than trusting the record.

### spike-4: Is the email path really "partially ahead", as the issue claims?

- **Assumption**: "`bridge/email_bridge.py` already surfaces attachment state to the agent via `extra_context`, so the fix should generalise that pattern."
- **Method**: code-read
- **Finding**: **False.** `bridge/email_bridge.py:1494-1502` writes `attachments_unrecoverable`, `attachments_truncated`, `attachments_recovered_count`, `attachments_referenced`, and `email_attachments` onto `extra_context`. A repo-wide sweep of every `extra_context` reader outside tests (`agent/`, `worker/`, `tools/`, `reflections/`, `ui/`, `bridge/`) finds **no consumer of any of those five keys**. They are written and never read. The only `extra_context` keys that actually reach the agent are `injection_risk_banner` (`agent/session_executor.py:1932`) and the context-recall advisory (`:1956`).
- **Confidence**: high
- **Impact on plan**: Revises the issue's Solution Sketch bullet 4. There is no email pattern to generalise — generalising it would generalise a no-op. Email attachments are stranded by a *different* mechanism (a write-only context field) and fixing that is a separate seam, filed as its own issue and listed under No-Gos. What this plan can and does cover for email is the guard that stops any medium from reintroducing a bare placeholder.

## Data Flow

1. **Entry point** — a Telegram message arrives at `bridge/telegram_bridge.py` `handler()`. `store_message(chat_id=str(event.chat_id), ...)` (`telegram_bridge.py:1549`) writes a `TelegramMessage` row carrying `has_media`, `media_type`, `reply_to_msg_id`, and returns `stored_msg_id`.
2. **Bridge media download** (`telegram_bridge.py:1645-1715`) — if `message.media`, `_download_media_with_retry` writes the bytes under `data/media/{prefix}_{timestamp}_{message.id}{ext}` and persists the resolved absolute path onto `TelegramMessage.media_local_path`, or the failure string onto `media_download_error`. **This is the step that makes the fix possible: by the time any later reply is walked, the ancestor's bytes and path are already durable.**
3. **A later reply arrives** carrying `reply_to_msg_id`. Two call sites hydrate the chain, both wrapped in `asyncio.wait_for(..., timeout=_REPLY_CHAIN_FETCH_TIMEOUT_S)` where the constant is `3.0` (`telegram_bridge.py:193`):
   - resume-completed branch, `telegram_bridge.py:2089`
   - fresh-session non-Valor reply branch, `telegram_bridge.py:2604`
4. **`fetch_reply_chain`** (`bridge/context.py:379`) walks `reply_to_msg_id` backward up to `max_depth=20`, calling `client.get_messages` and `msg.get_sender()` per hop, and appends `{"sender", "content", "message_id", "date"}`. **This is where the media reference is discarded** (`:425`).
5. **`format_reply_chain`** (`bridge/context.py:446`) renders the dicts under `REPLY_THREAD_CONTEXT_HEADER`, filtering tool logs from Valor's lines and truncating at 2000/500 chars.
6. **`strip_private`** runs on the formatted block at both call sites, then the block is spliced into `AgentSession.message_text`.
7. **Worker** — `agent/session_executor.py:1850-1893` loads only the *trigger* `TelegramMessage` and skips deferred re-hydration when `extra_context["reply_chain_hydrated"]` is set or the canonical header is already present. Ancestors are never enriched by construction.
8. **Output** — `message_text` becomes the agent's turn input. Whatever `format_reply_chain` emitted is the totality of what the agent knows about every ancestor.

The fix lands entirely at steps 4 and 5. Steps 1-3 already carry everything
needed; steps 6-8 are unchanged.

## Why Previous Fixes Failed

No previous fix targeted this defect, so nothing failed. What is worth recording
is the shape the prior work shares, because it predicts where the next instance
will appear.

| Prior Fix | What It Did | Why The Class Of Bug Survived |
|-----------|-------------|-------------------------------|
| PR #953 (#949) | Hydrated the reply thread on the resume-completed branch | Scoped to *whether* the chain arrives. The `msg.text or "[media]"` fallback was written here and never questioned. |
| PR #1070 (#1064) | Pre-hydrated the chain for fresh non-Valor reply sessions | Copied the existing renderer verbatim, duplicating the placeholder to a second call site. |
| PR #1316 (#1297) | Moved media download to intake so the worker could enrich without Telethon | Correctly fixed the *trigger* message. Scoping enrichment to one record was the right call for cost; the unexamined consequence was that ancestors keep whatever the renderer gave them. |

**Root cause pattern:** media is captured correctly at the boundary and then lost
at a specific rendering or handoff seam, because each fix asks "does the payload
arrive?" and never "what does a non-text payload look like once it has?" The bare
placeholder is the tell — a constant string standing in for a value that exists
one lookup away. The guard in this plan exists to make that tell mechanically
detectable rather than relying on somebody noticing it again.

## Architectural Impact

- **New dependencies**: none. `bridge/context.py` already imports `TelegramClient` from Telethon, and the `models.telegram` import is done lazily inside `_cache_walk_root` in the same module — the descriptor resolver follows that established shape.
- **Interface changes**: chain dicts produced by `fetch_reply_chain` gain a `media` key (a small dict, or `None`). `format_reply_chain` reads it. Both are internal to `bridge/context.py` plus its tests; the two bridge call sites pass the value through opaquely and need no edit. The exported `REPLY_THREAD_CONTEXT_HEADER` contract is unchanged, so the worker's idempotency guard keeps working untouched.
- **Coupling**: adds a read-only dependency from `bridge/context.py` onto `models.telegram` — which already exists at line 651 — and onto `bridge/media.py::get_media_type`, which is a pure classifier. No new write paths, no new ownership.
- **Data ownership**: unchanged. `TelegramMessage.media_local_path` remains owned by the bridge intake path. This work only reads it.
- **Reversibility**: high. The change is one function's return shape plus one renderer branch. Reverting restores the placeholder without touching persisted state.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**

- PM check-ins: 1-2 (confirming the path-reference-not-enrichment call and the email scope split)
- Review rounds: 1

The code change is small and confined to one module. The overhead is in agreeing
the boundary — what gets resolved at hydration time versus deferred, and how far
the cross-medium invariant reaches.

## Prerequisites

No prerequisites — this work has no external dependencies. Every input
(`TelegramMessage` records, `data/media/` files, the Telethon `File` helper) is
already present in a working checkout, and the unit tests for the renderer need
neither Redis nor a Telegram client.

## Solution

### Key Elements

- **Media descriptor** — a small structured record (`kind`, `filename`, `media_type`, `local_path`, `reason`) attached to each chain entry that carries media. It is data, not text, so the renderer owns presentation and the tests can assert on structure.
- **Ancestor media resolver** — resolves the descriptor for one chain hop: media type and filename come from the Telethon message already in hand; the on-disk path comes from a chat-scoped `TelegramMessage` lookup. It never performs AI work and never touches the network.
- **Chain renderer composition** — `format_reply_chain` composes a line from the human text and the descriptor. Caption present and file resolved: both. Caption absent: descriptor alone. File unresolvable: an explicit, distinguishable unreadable marker.
- **Bare-placeholder guard** — a test that fails when any bridge module reintroduces a constant-string stand-in for unreadable content, so email and any future medium cannot regress into the same shape.

### Flow

**Attachment arrives** → bridge downloads it and records the path → **later reply
arrives** → chain walk reaches the attachment hop → resolver looks up the record
by `(chat_id, message_id)` → **descriptor built** → renderer composes the line →
**agent's prompt carries filename, type, and a readable path** → agent reads the
file with the tools it already has, or reports precisely what it cannot open.

### Technical Approach

- **Resolve at hydration time, in `fetch_reply_chain`.** Settled by spike-1: the lookup is a Popoto `KeyField` filter, the same call already present at `bridge/context.py:651`, costing well under a millisecond against a 3-second budget that is spent almost entirely on the two Telethon RPCs per hop. Deferring to the worker would mean re-walking a chain the worker has no Telethon client for.
- **Every Redis read goes off-loop through `asyncio.to_thread`.** `fetch_reply_chain` is `async` and runs on the bridge's event loop, but Popoto sits on synchronous redis-py: `TelegramMessage.query.filter(...)` blocks the thread it is called on. Calling it directly would put up to 20 sequential blocking round-trips on the loop, and `asyncio.wait_for` cannot preempt a coroutine that is not at an `await` — a slow or hung Redis would stall the entire bridge process, not merely this hydration. The resolver therefore does:

  ```python
  records = await asyncio.to_thread(
      lambda: list(TelegramMessage.query.filter(chat_id=str(chat_id), message_id=msg.id))
  )
  ```

  which restores a real suspension point per hop so the existing `wait_for` guard can actually fire. `_cache_walk_root` (`bridge/context.py:629-651`) calls `filter` inline on the loop today; that is a pre-existing defect of the same shape, not a precedent for correctness, and it is out of scope here (see No-Gos).
- **Reference, not enrichment.** The descriptor carries a path; it never runs vision, Whisper, or document extraction. Enriching twenty ancestors would blow the budget by orders of magnitude and is usually wasted work — the agent spends a tool call only on the file that matters. This is the answer to issue open question 2, and it is the reason issue open question 1 stops being a dilemma.
- **Two independent sources per hop, degrading separately.** Media type and filename come from the Telethon `Message` already fetched (`msg.file.name`, and `bridge/media.py::get_media_type(msg)` for the type — reuse it, do not re-walk `document.attributes`). The local path comes from Redis. A Redis miss therefore still yields a named attachment, just an unreadable one — strictly better than today's four characters.
- **Three rendering states, all distinguishable.**
  1. *Resolved* — the record has `media_local_path` and the file passes an existence check. Render filename, media type, and the absolute path.
  2. *Referenced but unreadable* — media exists per Telethon but the record is missing, `media_local_path` is unset, `media_download_error` is set, or the path no longer exists on disk. Render the filename and type with an explicit unreadable marker naming the reason. The agent must be able to say "there is a file here I cannot open" without guessing.
  3. *Text only* — no media. Unchanged from today.
- **Caption composes with the descriptor.** Per spike-2, a caption already survives. The change is that a captioned attachment now renders as caption *plus* descriptor, where today it renders as caption alone with the file invisible.
- **Stat the path, do not trust the record.** Spike-3 shows files are never swept but records expire at 90 days; the inverse (record present, file gone) is possible after a manual clean or a failed write, so existence is checked, mirroring `bridge/enrichment.py`'s `path.exists() and os.access(path, os.R_OK)` check.
- **Decided — the descriptor carries the full absolute path, with the basename as the human-readable name.** The absolute path is the thing that makes the attachment actionable in one `Read` call; a filename-plus-retrieval-instruction is one indirection slower and invites the agent to guess. The leak risk is that the agent quotes a path into a group chat, which is disclosure of directory *shape*, not of another tenant's data — Risk 3's `chat_id` scoping is what prevents cross-chat exposure, and it holds independently. Risk 2's mitigation carries the presentation rule: name the file by basename so the natural thing to quote is the filename, and record the disclosure in the feature doc.
- **Decided — the guard hard-fails.** A warning-only lint is noise that accumulates until someone filters it out, and the defect this plan fixes survived three PRs precisely because nothing failed. The friction is bounded by matching on AST shape rather than line numbers, so a legitimate future placeholder is the only thing that can trip it, and the fix in that case is a deliberate, reviewed exemption.
- **Scope every lookup by `chat_id`.** The resolver filters on `chat_id=str(chat_id)` exactly as `_cache_walk_root` does, so a filename or path can only ever come from the chat being walked. This is the answer to issue open question 4: `data/media/` is one flat directory shared across projects, but the resolution key makes a cross-chat file unnameable.
- **Preserve the existing renderer contracts.** `filter_tool_logs` on Valor's lines, the 2000/500-char truncation, and the `strip_private` pass at both call sites all continue to apply. Truncation must never bisect a path, and it must measure the human-authored text so a long descriptor cannot push a real message under the truncation limit.
- **Filter first, then compose — the descriptor is never fed to `filter_tool_logs`.** `bridge/context.py:486-487` runs `content = filter_tool_logs(content)` and then `if not content: continue` for `sender == "Valor"`, and `filter_tool_logs` returns `""` whenever its result is under 5 characters (`bridge/response.py:353`). Today a caption-less Valor media hop carries the 7-character `"[media]"` and survives that gate; once the placeholder is deleted, `content` is empty and the entire entry — descriptor included — is dropped. So the drop condition must become:

  ```python
  content = filter_tool_logs(content)
  if not content and not msg.get("media"):
      continue
  ```

  with the descriptor appended *after* the filter runs. Feeding a descriptor into `filter_tool_logs` would let its 5-character floor and tool-log heuristics mangle machine-facing text they were never written for.
- **Valor media hops resolve *unreadable* by construction, and that is correct.** `media_local_path` is written only at `bridge/telegram_bridge.py:1694`, inside the inbound download block; outbound stores (`bridge/telegram_relay.py`, `bridge/telegram_bridge.py:3205`) never set it. A file Valor sent is therefore never on disk under `data/media/`. Rendering it as a named-but-unreadable attachment is honest and still strictly better than dropping the hop. Do not add an outbound download to "fix" this — see No-Gos.
- **Fail quiet, per hop.** The chain walk's existing `except Exception: break` must not become a way to lose the whole chain over one bad record. Descriptor resolution is wrapped per hop; a failure degrades that hop to *referenced but unreadable* and the walk continues.
- **The guard is two lint-shaped tests, and each claims only what it detects.** A true runtime invariant across mediums would require unifying the Telegram and email context-rendering paths, which is a different and much larger change (see No-Gos). What is achievable now is two static scans over `bridge/`:
  1. **Placeholder-shape scan.** Parse each module with `ast` and flag any `BoolOp(op=Or)` whose right-hand operand is a bracketed string constant — the `X or "[literal]"` shape. This is the exact shape of the defect being fixed and of PR #953's original line.
  2. **Write-only-context scan.** Assert every `extra_context["<key>"] = ...` writer has at least one reader outside `tests/`. This is the shape spike-4 found on the email side, where five keys (`attachments_unrecoverable`, `attachments_truncated`, `attachments_recovered_count`, `attachments_referenced`, `email_attachments`, all at `bridge/email_bridge.py:1494-1502`) are stamped and never read.
- **Match on AST shape, never on line numbers.** An allow-list pinned to positions in a 2600-line, heavily-edited file fires on unrelated edits, and a guard that cries wolf gets deleted — which is the very outcome Risk 5 warns about. The AST predicate needs no allow-list at all: the seven `[media]` strings in `bridge/telegram_bridge.py` (786, 794, 802, 1654, 1682, 1708, 1715) are all f-string arguments to `logger.info`/`logger.warning` calls, which are `JoinedStr` nodes inside a `Call`, not `BoolOp` operands. They are structurally invisible to the scan rather than excused by it.
- **Be precise about what the guard does not cover.** Scan 1 detects one syntactic shape. Of the three prior fixes tabulated in *Why Previous Fixes Failed*, only PR #953's is that shape — PR #1070's was renderer duplication to a second call site and PR #1316's was enrichment scoped to a single record, and neither is expressible as a literal-shape scan. Scan 2 generalises one step further by catching context written for the agent and never delivered. Together they cover two of the four historical instances. The remaining class, "a payload arrives and nobody asks what the non-text case renders as", is a review question, not a lint. Claiming otherwise is how the placeholder survived three PRs.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `bridge/context.py::fetch_reply_chain` has a broad `except Exception` at the loop tail (`:437`) that logs at debug and breaks. A test must assert that a per-hop descriptor-resolution failure does **not** reach it: the hop degrades to the unreadable rendering and the walk continues to the remaining ancestors.
- [ ] The new resolver's own `except` path must log at warning and return an unreadable descriptor, never `None` and never a bare re-raise. A test asserts the observable warning and the returned state.
- [ ] `except Exception: pass` blocks: none exist in `bridge/context.py`, and none are introduced.

### Empty/Invalid Input Handling

- [ ] `format_reply_chain([])` returns `""` today; assert this is preserved once entries carry a `media` key.
- [ ] A chain entry with `media=None` renders exactly as it does today — this is the regression fence for text-only chains.
- [ ] A descriptor whose `filename` is `None` (the photo case, per Research) renders a synthetic label from media type and message id, never an empty name or a dangling separator.
- [ ] A `media_local_path` that is an empty string, whitespace, or a path outside `data/media/` resolves to *unreadable* rather than being rendered as a path.
- [ ] A record whose `media_type` is `None` while `has_media` is true renders with a generic type label rather than crashing the renderer.

### Error State Rendering

- [ ] The unreadable rendering is user-visible output and is tested directly, for each distinct reason: no record, no path, `media_download_error` set, path absent from disk.
- [ ] The unreadable marker is asserted to be textually distinguishable from the resolved rendering, so an agent (and a test) can tell the two apart without heuristics.
- [ ] A test asserts the literal string `[media]` appears in no rendered chain output under any of the above states.

## Test Impact

- [ ] `tests/unit/test_context_helpers.py::TestReplyThreadContextHeader::test_format_reply_chain_uses_the_constant` — UPDATE: chain dicts gain a `media` key; the fixture must keep passing with and without it.
- [ ] `tests/unit/test_context_helpers.py::test_format_reply_chain_drops_variation_selector_and_backtick_echo` — UPDATE: confirm sanitisation still applies to the composed caption-plus-descriptor line.
- [ ] `tests/unit/test_context_helpers.py::test_format_reply_chain_omits_messages_below_length_floor` — UPDATE: this test must keep passing unchanged for its own case (a Valor hop with **no** media, whose post-filter remainder is under the `<5` floor in `filter_tool_logs`, is still omitted). Add a sibling case proving the complement: a Valor hop that is below the floor **and** carries media is now *retained*, rendered as its descriptor. The floor lives in `bridge/response.py:353` and applies only to Valor lines and only to text; it is never applied to a descriptor.
- [ ] `tests/integration/test_steering.py` (reply-chain timeout guards, ~lines 45-80, 1290-1310) — UPDATE: the two `asyncio.wait_for(fetch_reply_chain(...))` guards keep the same 3.0s constant; assertions must survive the signature change.
- [ ] `tests/integration/test_private_tag_ingestion.py` (lines 130, 171) — UPDATE: `strip_private` must still cover the descriptor text spliced into the chain block.

## Rabbit Holes

- **Enriching ancestors.** Running vision, Whisper, or document extraction over a 20-deep chain is the obvious "complete" fix and it is a trap: it cannot fit the 3-second budget, it burns model spend on files nobody asks about, and it re-solves what a tool call already solves on demand. The issue's own recon dropped it. Keep it dropped.
- **Unifying the Telegram and email context renderers.** Tempting while both are in view, and a genuinely larger project — email's problem is a missing delivery seam, not a bad renderer. #3136 owns it.
- **Re-walking `document.attributes` for filenames.** `bridge/media.py::get_media_type` already does this walk, and Telethon's `msg.file.name` covers the rest. Writing a third parser is duplicated logic that will drift.
- **Redesigning `data/media/` retention.** Spike-3 shows files are never swept while records expire at 90 days. That asymmetry is worth knowing and is not this plan's problem; the unreadable rendering covers the consequence.
- **Widening the guard into a general "no magic strings in bridge/" lint.** The guard has one job: catch a constant standing in for unreadable content. A broad string-literal lint will drown in false positives and get disabled.
- **Chasing the dead `enrich_reply_to_msg_id` variable.** `agent/session_executor.py:1888-1893` computes it, uses it only as a gate condition, and never passes it to `enrich_message` — the "deferred reply-chain fetch" it guards no longer exists. Real cruft, unrelated to this defect, and touching it changes the worker's enrichment gate. Leave it.

## Risks

### Risk 1: Descriptor text inflates the chain block and crowds the prompt

**Impact:** A 20-deep chain where every hop carries media adds twenty descriptor
lines. If a descriptor is verbose, the reply-chain block grows enough to push out
the context that matters.
**Mitigation:** Keep the descriptor to a single compact line per hop — filename,
type, path. Measure the worst case (20 hops, media at every hop) in the same
regression test that covers the budget, and assert a ceiling on the rendered
block size, not just on elapsed time.

### Risk 2: A resolved path leaks into an outbound message

**Impact:** The agent sees absolute filesystem paths and may echo one back into a
group chat, exposing directory structure to everyone in the room.
**Mitigation:** Render the basename as the human-readable name and the absolute
path as an explicitly machine-facing affordance, so the natural thing to quote is
the filename. Note in the feature doc that chain descriptors carry paths. This is
a disclosure-of-shape risk, not a cross-tenant one — see Risk 3.

### Risk 3: Cross-chat or cross-project file exposure

**Impact:** `data/media/` is a single flat directory shared by every project on
the machine. A resolver that keyed only on `message_id` could surface a file from
another chat.
**Mitigation:** Every lookup filters `chat_id=str(chat_id)` alongside
`message_id`, exactly as `bridge/context.py:651` does. A test asserts that a
record with a matching `message_id` in a *different* chat is not resolved. This
closes issue open question 4.

### Risk 4: Blocking Redis reads on the bridge event loop

**Impact:** Popoto is synchronous redis-py. Twenty `TelegramMessage.query.filter()`
calls issued inline from `async def fetch_reply_chain` are twenty blocking
round-trips executed *on the bridge's event loop*. `asyncio.wait_for` bounds a
coroutine only by cancelling it at an `await`; a coroutine that never yields
cannot be cancelled. So a slow or hung Redis is precisely the case the existing
3-second guard does **not** cover, and the consequence is not a lost reply chain
but a stalled bridge process — every other handler, the nudge loop, and the
output callbacks freeze with it.
**Mitigation:** Wrap each hop's lookup in `await asyncio.to_thread(...)` per the
Technical Approach. This moves the blocking call to a worker thread and puts a
genuine suspension point in the coroutine, which is what makes the `wait_for`
guard at both call sites real rather than nominal. Layered on top: the resolver
does one `filter` per hop with no fan-out, the per-hop try/except degrades a slow
or failing lookup to *referenced but unreadable* instead of losing the walk, and
the 20-hop regression test asserts the worst case completes inside the budget.
**Verification:** the budget regression test must drive its 20 hops against a
*stalled* lookup, not just a fast one — a test that only exercises a healthy
Redis cannot distinguish the correct implementation from the broken one.

### Risk 5: The guard test is written to pass rather than to bite

**Impact:** A guard that never fails is worse than no guard — it advertises
coverage that does not exist, which is exactly how the placeholder survived three
prior PRs.
**Mitigation:** Prove the red state mechanically, in the suite. The scanner is a
pure function over source text, so a positive fixture
(`'content = msg.text or "[media]"'` → exactly one finding) and a negative fixture
(`'logger.info(f"[media] download failed {e}")'` → zero findings) assert both
directions on every run. A PR-description paste would have been an honour-system
criterion on an otherwise mechanical checklist — no Verification row could check
it and no SDLC gate reads a PR description, which is exactly the hollow assurance
this risk is about. Demonstrating the red state by hand remains useful reviewer
practice; it is not the guarantee.

## Race Conditions

### Race 1: A reply arrives before the ancestor's media download has persisted

**Location:** `bridge/telegram_bridge.py:1549` (`store_message`) through
`:1645-1715` (download and persist), against `bridge/context.py` chain walk.
**Trigger:** The `TelegramMessage` row is written before the download starts.
`_download_media_with_retry` can take up to 120 seconds for a large file. A reply
sent inside that window walks a chain whose ancestor record exists with
`media_local_path` unset.
**Data prerequisite:** `media_local_path` must be persisted before the resolver
reads it.
**State prerequisite:** none beyond the record existing.
**Mitigation:** No synchronisation and no recovery attempt — this is a legitimate
transient, and the correct behavior is to render *referenced but unreadable*,
naming the file that is still arriving. The agent can say "there is a file here
that has not finished downloading" and a later reply resolves it normally.

**Explicitly rejected: the `bridge/enrichment.py` self-heal glob.** Enrichment
recovers an unpersisted path with `MEDIA_DIR.glob(f"*_{msg_id}.*")`
(`bridge/enrichment.py:96`). That is unsafe here. `MEDIA_DIR` is a single flat
repo-root directory (`bridge/media.py:23`) shared by every chat and every project
on the machine, and the download filename pattern
`{prefix}_{timestamp}_{message.id}{ext}` carries no chat id. Telegram message ids
are per-chat sequences, so a collision across chats is ordinary rather than
exotic, and "require exactly one match" only proves the match is unambiguous —
never that it belongs to the chat being walked. Adopting the glob would hand back
exactly the cross-chat exposure Risk 3 exists to prevent. No glob over
`MEDIA_DIR` can be made chat-scoped without a filename change, which is out of
scope. The only sound confirmation would be a `TelegramMessage` record in this
chat, and that record is the very thing missing in the race being recovered from.
Enrichment's use of the glob is scoped to a single known trigger message and is
not a precedent for a 20-hop walk across arbitrary ancestors.

### Race 2: Popoto stale-index miss on the ancestor lookup

**Location:** `bridge/context.py`, the new resolver.
**Trigger:** The same transient stale-index condition documented at
`bridge/telegram_bridge.py:1663-1670`, where `TelegramMessage.query.get` returns
`None` for a record that exists.
**Data prerequisite:** the index must reflect the written record.
**State prerequisite:** none.
**Mitigation:** Treat a miss as unreadable rather than retrying — the chain walk
is on a 3-second clock and a bounded re-query per hop would multiply the cost by
the depth. Do **not** call `keys(clean=True)`; it is heavy and index-mutating, and
the bridge code already declines it for the same reason.

### Race 3: The file is deleted between the existence check and the agent's read

**Location:** resolver existence check, versus the worker turn that follows.
**Trigger:** A manual clean or an operator action between hydration and the
agent's tool call.
**Data prerequisite:** the file must exist when the agent reads it, which no
check at hydration time can guarantee.
**State prerequisite:** none.
**Mitigation:** Accepted and unmitigated. The check is a best-effort signal, not
a lock. The agent's own read failure is the backstop, and the descriptor's
wording should not promise more certainty than a stat can give.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #3136] Delivering email attachment paths and the unrecoverable signal to the agent. Spike-4 established that email's `extra_context` attachment keys have no reader at all; that is a missing delivery seam in `agent/session_executor.py`, not a renderer defect, and fixing it here would double this plan's blast radius. Filed as #3136 with its own recon.
- [SEPARATE-SLUG #3136] Unifying the Telegram and email context-rendering paths behind one renderer. Scoped into #3136's follow-on discussion rather than attempted here.

- **Fixing `_cache_walk_root`'s inline blocking `filter`.** `bridge/context.py:629-651` calls `TelegramMessage.query.filter` directly on the event loop — the same defect this plan avoids in new code. It is pre-existing, it is on a different code path, and moving it off-loop changes the behavior of the root-resolution cache under load. Worth its own issue; not worth widening this diff.
- **Downloading Valor's outbound media so ancestor hops resolve.** Outbound stores never set `media_local_path`, so a Valor media hop is always *unreadable*. Adding an outbound download path means new writes to `data/media/`, new retention pressure, and a new failure mode on every message the agent sends — a real feature, not a rendering fix.

Everything else the issue asks for is in scope: the chain renderer, the caption
composition, the explicit unreadable state, the worst-case budget regression
test, the cross-medium scans, and both doc updates.

## Update System

No update system changes required. This is a bridge-internal rendering change:
no new dependencies, no new config files, no new secrets, no `.env` keys, no
Popoto schema change (the plan only *reads* existing `TelegramMessage` fields, so
`scripts/update/migrations.py` is untouched), and no changes to
`scripts/remote-update.sh` or the `update` skill.

One operational note for `/do-deploy` rather than `/update`: the bridge must be
restarted for the new renderer to take effect
(`./scripts/valor-service.sh restart`), since `bridge/context.py` is imported by
the long-lived bridge process.

## Agent Integration

No new CLI entry point and no new MCP tool. The change is inside a module the
bridge already imports (`bridge/telegram_bridge.py:116-117` imports both
`fetch_reply_chain` and `format_reply_chain`), and both call sites pass the chain
through opaquely — neither needs an edit.

The agent reaches the newly-surfaced media with tools it already has: the path in
the descriptor is readable by the `Read` tool, and the existing `valor-ingest`
entry point covers binaries that need extraction. Nothing new is wired.

What does need an integration-level test is the end of the chain, not the
beginning: an assertion that the rendered block reaching `AgentSession.message_text`
actually contains a resolvable path for an ancestor attachment. Stamping a
descriptor that never arrives is precisely the failure mode spike-4 found on the
email side, and this plan should not reproduce it.

## Documentation

- [ ] Update `docs/features/reply-thread-context-hydration.md` with a section describing how a chain ancestor carrying media is rendered (resolved / unresolved / caption-plus-attachment).
- [ ] Update `docs/features/media-enrichment.md` to state that chain ancestors get a path reference, never an AI enrichment pass, and why.
- [ ] Add inline docstrings on the new descriptor builder and renderer in `bridge/context.py`.

## Success Criteria

- [ ] `bridge/context.py::fetch_reply_chain` emits the literal string `[media]` under no input. A caption-less attachment in a chain renders with its filename, media type, and a readable path.
- [ ] Replaying the reported exchange shape (attachment, then two text replies) produces agent turn input containing a path sufficient to read the attachment without asking the human.
- [ ] A caption on a chain-ancestor attachment renders together with the attachment descriptor, so the agent sees both the note and the file.
- [ ] An unresolvable attachment renders as an explicit unreadable marker naming the file and the reason, textually distinguishable from the resolved rendering, for each of: no record, no path, download error, file absent from disk.
- [ ] A `chat_id`-scoped resolution test proves a same-`message_id` record in a different chat is never resolved.
- [ ] A 20-deep chain with media at every hop completes inside the 3-second `_REPLY_CHAIN_FETCH_TIMEOUT_S` budget, and the rendered block stays under an asserted size ceiling.
- [ ] The bare-placeholder guard covers `bridge/` including `bridge/email_bridge.py`, and its red state is proven by an in-suite positive fixture plus a negative fixture that keeps a `logger.` line unflagged.
- [ ] The write-only-context scan passes with its known-gap allow-list holding exactly the five `#3136` email keys, and asserts each is still unread so the allow-list can only shrink.
- [ ] The 20-hop budget test exercises a *stalled* lookup, proving `asyncio.wait_for` can actually interrupt the walk — an inline blocking `filter` fails it.
- [ ] `docs/features/reply-thread-context-hydration.md` and `docs/features/media-enrichment.md` both describe how chain-ancestor media is represented.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] No xfail conversions required — the repo currently contains zero `pytest.mark.xfail` or runtime `pytest.xfail()` markers in `tests/`, verified at plan time.

## Team Orchestration

### Team Members

- **Builder (chain renderer)**
  - Name: `chain-renderer-builder`
  - Role: The descriptor record, the ancestor resolver, and the renderer composition in `bridge/context.py`
  - Agent Type: builder
  - Domain: Redis/Popoto data access, async
  - Resume: true

- **Builder (guard and budget tests)**
  - Name: `guard-test-builder`
  - Role: The cross-medium bare-placeholder guard and the 20-hop budget regression test
  - Agent Type: test-engineer
  - Resume: true

- **Validator (renderer)**
  - Name: `chain-renderer-validator`
  - Role: Verifies the three rendering states, the `chat_id` scoping, and that no existing renderer contract regressed
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `chain-media-documentarian`
  - Role: Both feature doc updates
  - Agent Type: documentarian
  - Resume: true

- **Validator (final)**
  - Name: `final-validator`
  - Role: Runs the full Verification table and confirms every Success Criterion
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Build the media descriptor and ancestor resolver

- **Task ID**: build-resolver
- **Depends On**: none
- **Validates**: `tests/unit/test_context_helpers.py` (extend)
- **Informed By**: spike-1 (Popoto `KeyField` lookup is O(1) and needs no network; the identical call already exists at `bridge/context.py:651`), spike-3 (files are never swept, records expire at 90 days, so stat the path)
- **Assigned To**: `chain-renderer-builder`
- **Agent Type**: builder
- **Parallel**: true
- Add the descriptor record to `bridge/context.py` with fields `kind` (`resolved` | `unreadable`), `filename`, `media_type`, `local_path`, `reason`.
- Add the per-hop resolver. Media type via `bridge.media.get_media_type(msg)`; filename via `msg.file.name` with a synthetic `{media_type}-{message_id}` fallback for photos, which have no filename. Path via a `chat_id`-scoped `TelegramMessage` lookup, importing `models.telegram` lazily inside the function as `_cache_walk_root` does.
- **Issue that lookup off the event loop**: `records = await asyncio.to_thread(lambda: list(TelegramMessage.query.filter(chat_id=str(chat_id), message_id=msg.id)))`. Popoto is blocking redis-py; an inline call would freeze the bridge and make the `asyncio.wait_for` guard unenforceable (Risk 4). A direct `TelegramMessage.query.filter(...)` inside this coroutine is a review blocker.
- Gate on `msg.file` / `msg.media` truthiness, not on `msg.text` falsiness — a captioned attachment must produce a descriptor too.
- Stat the resolved path (`exists()` and `os.access(..., os.R_OK)`), mirroring `bridge/enrichment.py`, and downgrade to `unreadable` with a specific reason when it fails.
- **Do not glob `data/media/`.** `MEDIA_DIR` is flat and shared across chats and projects, and the filename pattern carries no chat id, so the `bridge/enrichment.py:96` self-heal recovery cannot be chat-scoped and would reintroduce the cross-chat exposure Risk 3 closes. An unpersisted path renders *unreadable*; see Race 1.
- Wrap resolution per hop in try/except so a failure yields an `unreadable` descriptor and the walk continues. Log at warning.
- Attach the descriptor to each chain dict under a `media` key; entries with no media carry `None`.

### 2. Compose the renderer

- **Task ID**: build-renderer
- **Depends On**: build-resolver
- **Validates**: `tests/unit/test_context_helpers.py` (extend)
- **Informed By**: spike-2 (the caption already survives; the attachment is what vanishes, so this is composition rather than preservation)
- **Assigned To**: `chain-renderer-builder`
- **Agent Type**: builder
- **Parallel**: false
- Teach `format_reply_chain` to read `msg["media"]` and compose the line: text plus descriptor when both exist, descriptor alone when the text is empty, text alone when `media` is `None`.
- Keep the existing contracts intact — `filter_tool_logs` on Valor's lines, the 2000/500 truncation, and the caller-side `strip_private`.
- **Change the Valor drop condition at `bridge/context.py:487` from `if not content: continue` to `if not content and not msg.get("media"): continue`, and append the descriptor after `filter_tool_logs` has run.** Without this, deleting the placeholder makes `filter_tool_logs` return `""` for a caption-less Valor media hop and the whole entry vanishes — a regression the current 7-character `"[media]"` string is accidentally preventing.
- Apply truncation to the human-authored text only, and never let truncation bisect a path.
- Delete the `msg.text or "[media]"` fallback outright. No commented-out remnant.

### 3. Unit-test the three rendering states

- **Task ID**: test-states
- **Depends On**: build-renderer
- **Validates**: `tests/unit/test_context_helpers.py`
- **Assigned To**: `guard-test-builder`
- **Agent Type**: test-engineer
- **Parallel**: false
- Cover: resolved with caption; resolved without caption; text-only unchanged; and each unreadable reason (no record, no path, `media_download_error` set, file absent).
- Cover the Valor composition-order case both ways: a below-floor Valor hop with no media is still omitted, and a below-floor Valor hop *with* media is retained and renders its descriptor.
- Cover the photo case where `msg.file.name` is `None`, and the `media_type is None` case.
- Assert the resolved and unreadable renderings are textually distinguishable.
- Assert the literal `[media]` appears in no output across every case.
- Add the `chat_id` scoping test: a record with the same `message_id` in a different chat must not resolve.
- Update the three existing `test_context_helpers.py` cases per **Test Impact**.

### 4. Budget regression test

- **Task ID**: test-budget
- **Depends On**: build-renderer
- **Validates**: `tests/integration/test_steering.py` (extend)
- **Assigned To**: `guard-test-builder`
- **Agent Type**: test-engineer
- **Parallel**: true
- Drive a 20-deep chain with media at every hop and assert completion inside `_REPLY_CHAIN_FETCH_TIMEOUT_S`.
- **Add the stalled-lookup case, which is the one that actually discriminates.** Patch the resolver's Redis lookup to sleep well past the 3-second budget, then assert `asyncio.wait_for` raises `TimeoutError` and the bridge coroutine yields control. With the lookup off-loop via `asyncio.to_thread` this passes; with an inline blocking `filter` the loop cannot be preempted and the test hangs past the budget. A healthy-Redis-only test cannot tell the two implementations apart (Risk 4).
- Assert a ceiling on the rendered block size so a verbose descriptor cannot silently crowd the prompt (Risk 1).
- Leave the two existing timeout-guard assertions (`test_steering.py` ~lines 45-80 and ~1290-1310) passing: both call sites keep the same 3.0s constant.

### 5. Cross-medium static guards (placeholder shape + write-only context)

- **Task ID**: build-guard
- **Depends On**: none
- **Validates**: `tests/unit/` (new test module)
- **Informed By**: spike-4 (there is no email pattern to generalise; the guard is the only cross-medium mechanism in scope)
- **Assigned To**: `guard-test-builder`
- **Agent Type**: test-engineer
- **Parallel**: true
- Write the scanner as a **pure function over source text** — `find_placeholder_fallbacks(source: str) -> list[Finding]` — so it can be unit-tested on fixtures without touching the filesystem. The test module then applies it across `bridge/*.py`.
- Scan 1, placeholder shape: parse with `ast` and flag any `BoolOp(op=Or)` whose right-hand operand is a `Constant` string matching a bracketed-placeholder pattern (`[media]`, `[image]`, `[attachment]`, `[document]`, `[file]`, and the general `^\[[a-z_ ]+\]$` shape). **No line-number allow-list.** The seven `[media]` log strings in `bridge/telegram_bridge.py` are `JoinedStr` arguments to `logger.*` calls and do not match the `BoolOp` shape, so they need no exemption (Concern 4).
- **Prove the guard red in-suite, not in a PR description.** Add two fixture tests against the pure function: a positive fixture, `'content = msg.text or "[media]"'`, asserting exactly one finding; and a negative fixture, `'logger.info(f"[media] download failed {e}")'`, asserting zero. These run on every invocation and are what actually satisfies Risk 5. If any allow-list predicate is introduced later, pair it with an assertion that it still matches at least one real site, so a stale exemption fails loudly instead of silently widening the guard.
- Scan 2, write-only agent context: collect every `extra_context["<key>"] = ...` assignment across `bridge/` and `agent/`, then assert each key is read somewhere outside `tests/`. Seed the known-gap allow-list with exactly the five email keys spike-4 identified (`bridge/email_bridge.py:1494-1502`), each annotated `# known gap: #3136`. Assert that every allow-listed key is *still* unread, so when #3136 lands a reader the test fails and forces the entry's removal. The allow-list ratchets shut; it cannot quietly grow stale.
- Scan 2 changes no production code and does not fix email — it makes the existing gap visible and prevents a sixth write-only key. Fixing the delivery seam remains #3136's job (No-Gos).

### 6. Validate the renderer

- **Task ID**: validate-renderer
- **Depends On**: build-renderer, test-states, test-budget, build-guard
- **Assigned To**: `chain-renderer-validator`
- **Agent Type**: validator
- **Parallel**: false
- Confirm all three rendering states behave as specified and that text-only chains are byte-identical to today.
- Confirm the guard fails on an injected placeholder and passes on `main` plus this branch.
- Confirm no `[media]` literal survives in agent-facing code.

### 7. Documentation

- **Task ID**: document-feature
- **Depends On**: validate-renderer
- **Assigned To**: `chain-media-documentarian`
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/reply-thread-context-hydration.md` with the chain-ancestor media rendering (three states, the `chat_id` scoping, the path disclosure note from Risk 2).
- Update `docs/features/media-enrichment.md` to state that ancestors receive a path reference and never an enrichment pass, and why.
- Describe only the new status quo. No before/after narration, no historical artifacts.

### 8. Final validation

- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: `final-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run every row of the Verification table.
- Confirm every Success Criterion, including both guard red-state fixtures and the stalled-lookup budget case.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Renderer unit tests pass | `./scripts/pytest-clean.sh tests/unit/test_context_helpers.py -q` | exit code 0 |
| Reply-chain integration tests pass | `./scripts/pytest-clean.sh tests/integration/test_steering.py -q -k reply_chain` | exit code 0 |
| Bare placeholder gone from the chain renderer | `grep -c '\[media\]' bridge/context.py` | match count == 0 |
| No bare placeholder in agent-facing bridge context | `grep -rn 'or "\[media\]"\|or "\[image\]"\|or "\[attachment\]"\|or "\[document\]"\|or "\[file\]"' bridge/ \| wc -l` | match count == 0 |
| Guard test exists and passes, including both red-state fixtures | `./scripts/pytest-clean.sh tests/unit -q -k placeholder` | exit code 0 |
| Write-only-context scan passes | `./scripts/pytest-clean.sh tests/unit -q -k write_only_context` | exit code 0 |
| Ancestor Redis lookup is off-loop | `grep -c 'asyncio.to_thread' bridge/context.py` | output > 0 |
| No inline blocking filter in the chain walk | `python - <<'PY'` — parse `bridge/context.py`, assert no `TelegramMessage.query.filter` call inside `fetch_reply_chain` lacks an enclosing `to_thread` | exit code 0 |
| Both timeout guards still use the shared 3.0s constant | `grep -c '_REPLY_CHAIN_FETCH_TIMEOUT_S' bridge/telegram_bridge.py` | output > 2 |
| Ancestor lookup is chat-scoped | `grep -c 'chat_id=str(chat_id)' bridge/context.py` | output > 1 |
| Hydration path runs no AI enrichment (anti-criterion for the dropped scope) | `grep -c 'process_downloaded_media\|describe_image\|transcribe_voice\|extract_document_text' bridge/context.py` | match count == 0 |
| Email delivery seam untouched (anti-criterion for No-Go #3136) | `git diff --name-only main...HEAD -- bridge/email_bridge.py agent/session_executor.py \| wc -l` | match count == 0 |
| Feature docs updated | `grep -l 'chain-ancestor media\|ancestor media' docs/features/reply-thread-context-hydration.md docs/features/media-enrichment.md \| wc -l` | output > 1 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

**Depth:** FULL (3 lenses) · **Verdict:** NEEDS REVISION (2 blockers) · **Run:** 2026-09-04
**Revision applied:** 2026-09-04 · all 8 findings addressed; every citation independently re-verified against source at `dcdb0c58b` before acting on it.

**Dispatch caveat:** the critique session had no subagent-spawn tool available, so the three FULL lenses (Risk & Robustness, Scope & Value, History & Consistency) were applied sequentially by one agent against verified source reads rather than by three independent critics. Every finding below carries a file:line citation re-verified at `dcbf4a019`. The independence property of the war room was not obtained; a re-run from a session with the Agent tool would add cross-checking, not new ground truth.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|----------|---------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | Risk 4's mitigation is false. `fetch_reply_chain` is `async` but `TelegramMessage.query.filter()` is a blocking redis-py call, so the plan adds up to 20 sequential synchronous round-trips onto the bridge event loop inside the `asyncio.wait_for` guard. `wait_for` can only preempt at an `await` point, so a slow or hung Redis is exactly the case the timeout cannot bound, and the stall takes the whole bridge process with it, not just this hydration. | **Risk 4** (rewritten), **Technical Approach** (off-loop rule), **Task 1**, **Task 4** (stalled-lookup case), **spike-1** caveat, Verification rows | Do the lookup off-loop: `records = await asyncio.to_thread(lambda: list(TelegramMessage.query.filter(chat_id=str(chat_id), message_id=msg.id)))`. That restores a real `await` point per hop so `wait_for` can actually fire. Then rewrite Risk 4's mitigation text, which currently claims the existing `wait_for` already bounds this. `_cache_walk_root` (`bridge/context.py:629-651`) has the same shape today and is not a precedent for correctness. |
| BLOCKER | Risk & Robustness | Race 1's proposed `data/media/` self-heal glob defeats Risk 3's cross-chat guarantee. `bridge/enrichment.py:96` globs `MEDIA_DIR.glob(f"*_{msg_id}.*")` keyed on `message_id` alone, and `MEDIA_DIR` (`bridge/media.py:23`) is one flat repo-root directory shared by every chat and project. Telegram message ids are per-chat sequences, so id collisions across chats are ordinary. "Require exactly one match" does not make the single match the right chat's file. | **Race 1** (glob explicitly rejected with rationale), **Task 1** (do-not-glob bullet) | The download filename pattern is `{prefix}_{timestamp}_{message.id}{ext}` and carries no chat id, so no glob over `MEDIA_DIR` can be chat-scoped. Delete the glob bullet from Race 1 and from Task 1's optional step; render the transient as *referenced but unreadable*. If the recovery is kept, it must be confirmed by a `TelegramMessage` record in this chat, which is the record that is missing in the race being recovered from. |
| CONCERN | Risk & Robustness, History & Consistency | Composition order around `filter_tool_logs` silently deletes Valor media hops, and the Test Impact item that should catch it names a floor that does not exist. `bridge/context.py:486-487` runs `content = filter_tool_logs(content)` then `if not content: continue` for `sender == "Valor"`. `filter_tool_logs` returns `""` for any result under 5 chars (`bridge/response.py:353`). Today a caption-less Valor media hop carries `"[media]"` (7 chars) and survives; with `content` empty the whole entry, descriptor included, is dropped. Test Impact says the "length floor must measure the human-authored text" but that floor is inside `filter_tool_logs` and applies only to Valor lines, never to a descriptor. | **Technical Approach** (filter-then-compose + Valor-unreadable-by-construction), **Task 2**, **Task 3**, **Test Impact** item restated | Filter first, then compose. Change the drop condition at `bridge/context.py:487` from `if not content: continue` to `if not content and not entry.get("media"): continue`, and append the descriptor after the filter so it is never fed to `filter_tool_logs`. Restate the Test Impact item accordingly. Also note that outbound Valor media is never downloaded, so every Valor media hop resolves *unreadable* by construction. |
| CONCERN | Scope & Value | Task 5 pins the guard's allow-list to line numbers (786, 794, 802, 1654, 1682, 1708, 1715 in `bridge/telegram_bridge.py`). All seven are accurate at `dcbf4a019`, but that file is 2600+ lines and among the most-edited in the repo. The plan's own Freshness Check records `agent/session_executor.py:1779` drifting to `:1850` in 23 days. A line-pinned allow-list fires on unrelated edits, which is how a guard gets disabled. Risk 5 names that outcome without noticing the plan creates it. | **Technical Approach** (AST-shape rule), **Task 5** (allow-list deleted entirely) | Key the allow-list on matched source text, not position: exclude any hit whose source line contains `logger.` or sits inside an f-string argument to a logger call. All seven current entries are `logger.warning`/`logger.info` f-strings, so one predicate replaces the list. Add an assertion that each allow-list predicate still matches at least one site, so a stale entry fails loudly instead of silently widening the guard. |
| CONCERN | Scope & Value | The guard's red-state proof is an honour-system criterion on an otherwise mechanical checklist. Success Criterion 7 requires the red state to be "demonstrated and pasted into the PR description before merge", but no row of the Verification table checks it and no SDLC gate reads a PR description. It is exactly the shape of assurance Risk 5 says is worthless. | **Task 5** (positive/negative fixtures), **Risk 5** (rewritten), **Success Criteria** (PR-description clause dropped) | Make it bite in-suite. The guard's scanner is a pure function over source text, so add a unit test that feeds it the literal fixture `'content = msg.text or "[media]"'` and asserts a hit, plus a negative fixture asserting a `logger.` line is not flagged. That runs on every invocation and satisfies Risk 5 mechanically. Then drop the PR-description clause from Success Criteria and leave it as reviewer guidance. |
| CONCERN | Scope & Value | Three of the four items under `## Open Questions` are already decided elsewhere in the plan, which is marked `status: Ready`. Q2 (how much path to expose) is settled by Risk 2's mitigation. Q3 (guard hard-fail vs warn) is settled by Task 5 and Success Criterion 7. Q1 (email split) is settled by the No-Gos and by #3136 being filed and open. A builder reading top-down implements the decisions; a human reading bottom-up believes the plan is blocked on three answers. | **Technical Approach** (Q2/Q3 as stated decisions), **Open Questions** (reduced to a decision index) | Move Q2 and Q3 into `## Solution → Technical Approach` as stated decisions carrying their current rationale, and reduce Q1 to one line noting the split is recorded in No-Gos against #3136. Leave `## Open Questions` empty or delete it rather than restating settled decisions as questions. |
| CONCERN | History & Consistency | The guard does not detect the root-cause class the plan diagnoses. "Why Previous Fixes Failed" concludes each fix asked whether the payload arrives and never what a non-text payload looks like, then asserts the guard makes that tell mechanically detectable. The guard detects one syntactic shape, `X or "[literal]"`. Of the three tabulated prior fixes only PR #953's is that shape: PR #1070's was renderer duplication to a second call site, PR #1316's was scoping enrichment to one record, and spike-4's email defect is a write-only `extra_context` key. All three are invisible to a literal-shape scan. | **Technical Approach** (coverage narrowed and stated honestly), **Task 5** scan 2 (write-only `extra_context` ratchet) | Either narrow the prose so it claims only what the guard does (literal placeholders in `bridge/`), or add the one check that generalises and that spike-4 already proved fires: a static scan asserting every `extra_context["<key>"] =` writer has at least one reader outside `tests/`. That is expressible as a test and covers the email seam the plan otherwise only claims to cover. |
| NIT | History & Consistency | Two citation drifts, neither material. `models/telegram.py:24-25` is cited for the `chat_id` and `message_id` KeyFields; at `dcbf4a019` they are at `:25-26`. The baseline `b3f43656d` has moved to `dcbf4a019`, though `bridge/context.py` remains untouched since `e1ec8695c` and every load-bearing line reference re-verified exactly. Separately, `bridge/enrichment.py`'s self-heal comment says the glob searches `bridge/data/media/` while `MEDIA_DIR` resolves to repo-root `data/media/`; the plan copies the correct path and the source comment is the stale one. | **Spike Results** (`models/telegram.py:25-26`), **Freshness Check** (baseline `dcdb0c58b`) | Update the `models/telegram.py` citation to `:25-26` and the baseline to the commit at build start. The `bridge/enrichment.py` comment drift is out of scope here; leave it. |

---

## Open Questions

The issue carried four open questions. Three are now settled by evidence and are
recorded in Spike Results rather than left for a human:

- **Q1, where resolution happens** — settled by spike-1. At hydration time, in `fetch_reply_chain`. The lookup is a Popoto `KeyField` filter with no network cost, so the 3-second budget was never actually the constraint it appeared to be.
- **Q2, path reference versus full enrichment** — settled. Path reference. Enrichment of 20 ancestors cannot fit the budget and is usually wasted; the agent spends a tool call only on the file that matters.
- **Q3, are ancestor files still on disk** — settled by spike-3. Files are never swept. Records expire at 90 days, so the miss is a missing record, and the resolver stats the path anyway.
- **Q4, cross-chat safety** — settled by Risk 3. Every lookup filters on `chat_id`, so a file from another chat is unnameable. The residual concern is disclosure of path *shape* into a group chat, handled as Risk 2.

The three questions this section previously carried are all decided, and the
decisions live where a builder will actually read them rather than being restated
here as though the plan were blocked:

- **How much path the descriptor exposes** — decided in *Technical Approach*: full absolute path, basename as the display name, with Risk 2 carrying the presentation rule.
- **Whether the guard hard-fails or warns** — decided in *Technical Approach*: hard fail, with AST-shape matching keeping the friction bounded.
- **Where the email seam splits** — decided in *No-Gos*: split, against #3136, which is filed and open. This plan ships the scans that make an email regression visible without taking on email's delivery seam.

Nothing is blocking. Raise an objection to any of the three above if the calls
read wrong; otherwise this is ready to build.
