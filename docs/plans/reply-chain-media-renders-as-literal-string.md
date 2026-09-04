---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-09-04
tracking: https://github.com/tomcounsell/ai/issues/2732
last_comment_id: none
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

**Baseline commit:** `b3f43656d` (main at plan time)
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
- **Finding**: **False — it needs no network at all.** `TelegramMessage.chat_id` and `TelegramMessage.message_id` are both Popoto `KeyField`s (`models/telegram.py:24-25`), giving an O(1) Redis filter. The exact lookup already exists in the same file: `bridge/context.py:651` runs `TelegramMessage.query.filter(chat_id=str(chat_id), message_id=current_id)` inside `_cache_walk_root`, and two other call sites use the identical shape (`tools/telegram_history/__init__.py:447`, `bridge/read_the_room.py:180`). The 3s budget is dominated by the two Telethon RPCs already in the loop (`client.get_messages` plus `msg.get_sender()`), not by a sub-millisecond Redis read.
- **Confidence**: high
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

<!-- skeleton -->

## Why Previous Fixes Failed

<!-- skeleton -->

## Architectural Impact

<!-- skeleton -->

## Appetite

<!-- skeleton -->

## Prerequisites

<!-- skeleton -->

## Solution

<!-- skeleton -->

## Failure Path Test Strategy

<!-- skeleton -->

## Test Impact

- [ ] `tests/unit/test_context_helpers.py::TestReplyThreadContextHeader::test_format_reply_chain_uses_the_constant` — UPDATE: chain dicts gain a `media` key; the fixture must keep passing with and without it.
- [ ] `tests/unit/test_context_helpers.py::test_format_reply_chain_drops_variation_selector_and_backtick_echo` — UPDATE: confirm sanitisation still applies to the composed caption-plus-descriptor line.
- [ ] `tests/unit/test_context_helpers.py::test_format_reply_chain_omits_messages_below_length_floor` — UPDATE: the length floor must measure the human-authored text, not the synthetic descriptor.
- [ ] `tests/integration/test_steering.py` (reply-chain timeout guards, ~lines 45-80, 1290-1310) — UPDATE: the two `asyncio.wait_for(fetch_reply_chain(...))` guards keep the same 3.0s constant; assertions must survive the signature change.
- [ ] `tests/integration/test_private_tag_ingestion.py` (lines 130, 171) — UPDATE: `strip_private` must still cover the descriptor text spliced into the chain block.

## Rabbit Holes

<!-- skeleton -->

## Risks

<!-- skeleton -->

## Race Conditions

<!-- skeleton -->

## No-Gos (Out of Scope)

<!-- skeleton -->

## Update System

<!-- skeleton -->

## Agent Integration

<!-- skeleton -->

## Documentation

- [ ] Update `docs/features/reply-thread-context-hydration.md` with a section describing how a chain ancestor carrying media is rendered (resolved / unresolved / caption-plus-attachment).
- [ ] Update `docs/features/media-enrichment.md` to state that chain ancestors get a path reference, never an AI enrichment pass, and why.
- [ ] Add inline docstrings on the new descriptor builder and renderer in `bridge/context.py`.

## Success Criteria

<!-- skeleton -->

## Team Orchestration

<!-- skeleton -->

## Step by Step Tasks

<!-- skeleton -->

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `.venv/bin/python -m pytest tests/unit/test_context_helpers.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

<!-- skeleton -->
