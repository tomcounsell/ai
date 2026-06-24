---
status: Ready
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-06-04
tracking: https://github.com/tomcounsell/ai/issues/1567
last_comment_id:
revision_applied: true
---

# Incoming Email Attachment Support

## Problem

Someone emails the agent a PDF, a spreadsheet, or a screenshot and writes "take a
look at the attached." Today the agent never sees the file. The IMAP poll loop
reads the email, extracts the text body, and **silently discards every
attachment part**. The agent replies as if no file was sent — confusing for the
sender and a hard wall for any workflow that depends on emailed documents.

Outgoing attachments already work (`valor-email send --file`), so the experience
is lopsided: the agent can send files but cannot receive them.

**Current behavior:**
- `_extract_body()` (`bridge/email_bridge.py:139-172`) walks the MIME tree and
  explicitly skips any part whose `Content-Disposition` contains `attachment`.
- `parse_email_message()` (`bridge/email_bridge.py:175-215`) returns no
  attachments field.
- The history cache blob, `valor-email read --json` output, and the inbound
  session `extra_context` all carry zero attachment data.
- The agent processing the email has no way to know a file arrived, let alone
  open it.

**Desired outcome:**
- When an email with attachments arrives, the bytes are persisted to the
  filesystem, attachment metadata (filename, content-type, size, stored path) is
  recorded in the history cache and exposed via `valor-email read --json`, and
  the agent's session `extra_context` carries an `email_attachments` list of
  readable paths so the agent can open and act on the files.
- The behavior mirrors how the Telegram bridge already handles inbound media, so
  the two transports stay consistent.

## Freshness Check

**Original baseline commit:** `ce1c852d`
**Refresh baseline commit:** `b045c361` (re-verified 2026-06-24)
**Issue filed at:** 2026-06-04T14:35:22Z
**Disposition:** Minor drift — line numbers moved; all claims still hold.

**File:line references re-verified (current line numbers at `b045c361`):**
- `bridge/email_bridge.py:139-172` — `_extract_body()` walks the MIME tree and at
  line 149 skips any non-`text/plain` part / `attachment`-disposition part — still
  holds. Inbound attachment bytes are still read and discarded.
- `bridge/email_bridge.py:175-216` — `parse_email_message()` returns no attachments
  field — still holds. The empty-body guard that would drop attachment-only emails
  is now at **lines 197-199** (was "line 197"); update task references accordingly.
- `bridge/email_bridge.py:334` — `_record_history()` blob has a fixed field set, no
  attachments — still holds (function start drifted from 334 region; signature
  unchanged).
- `bridge/email_bridge.py:965-975` — inbound `extra_context` build (was cited
  `937-948`). The customer-service auto-reply layer (#1575) added `customer_id` and
  a few `email_*` fields to this dict, but the insertion point for
  `email_attachments` is unchanged and the addition remains purely additive.
- `bridge/email_bridge.py:1112-1119` — `_email_inbox_loop` calls
  `parse_email_message` then `_record_history`/`_record_thread` then
  `_process_inbound_email` (critique C1's `_persist_attachments` insertion point; was
  cited `~1085`). Structure intact.
- `tools/email_history/__init__.py:55-216` — `_hydrate` / `get_recent_emails` (94) /
  `search_history` (155) project a fixed field set — still holds.
- `tools/valor_email.py:522` — outgoing `--file` is a single arg `add_argument`
  (multi-file does NOT work today) — still holds.
- `tools/valor_email.py:106` — `_imap_fallback_fetch` re-parses raw IMAP and
  hand-shapes result dicts (critique C1/C2 concern) — still holds; the fallback path
  still bypasses the `email_history` projections.
- `bridge/media.py` / `bridge/telegram_bridge.py` — Telegram persists inbound files
  to the filesystem (`data/media/`, then `~/work-vault/telegram-attachments/`) — still
  holds; this is the storage precedent.

**Cited sibling issues/PRs re-checked:**
- #1067 (valor-email CLI) — closed/merged; established the outbox + history-cache architecture this plan extends.
- #1297 (Telegram media enrichment) — closed; confirms the filesystem-storage + work-vault-ingest pattern for inbound media.
- #1161 (markitdown ingestion) — closed; `valor-ingest` + KnowledgeWatcher auto-index anything dropped under `~/work-vault/`, which the vault-mirror step reuses.

**Commits on main since the original baseline (touching referenced files):** Two,
both additive and non-conflicting:
- `0065527c` — Email customer-service auto-reply layer (#1575, shadow-mode Phase 1).
  Added the `customer_id` resolver and extra `email_*` fields to the inbound
  `extra_context` block (now lines 965-975) and BYOB-inference branches before it.
  Does NOT touch the attachment-discard behavior, the parse/record/context shape, or
  the storage strategy. The plan's `email_attachments` addition slots in cleanly
  alongside `customer_id`.
- `dd926192` — PM/Dev → Eng role merge (#1633/#1691). Mechanical `session_type`
  refactor; no change to attachment-relevant code paths.

**Active plans in `docs/plans/` overlapping this area:** None touching the email
attachment path.

**Notes:** All premises hold. The only material change since the original critique
is line-number drift from the customer-service layer; the design, integration points,
and all seven critique concerns (C1-C5, N1-N2) remain valid and unaddressed-by-others.
No major drift; no scope change; the issue is NOT already fixed.

## Prior Art

- **#1067** (valor-email CLI): Built the read/send/threads CLI, the
  `email:outbox:*` relay, the `email:history:*` cache, and the outgoing
  `_build_reply_mime` attachment path. This plan extends that architecture on
  the inbound side.
- **#1297** (Telegram media enrichment): Established that inbound binary media is
  downloaded to the filesystem (`bridge/media.py:download_media`) and copied into
  `~/work-vault/telegram-attachments/` for KnowledgeWatcher indexing
  (`bridge/telegram_bridge.py:_ingest_attachments`). This is the storage pattern
  we deliberately match for consistency across transports.
- **#1161** (markitdown ingestion): `valor-ingest` + the KnowledgeWatcher turn
  any binary dropped under `~/work-vault/` into an indexed `.md` sidecar. The
  vault-mirror step reuses this for free — no new ingestion wiring needed.

No prior attempt to read inbound email attachments exists. This is greenfield on
the inbound side; the outgoing side is untouched except for the multi-file CLI fix.

## Research

No relevant external findings needed — the work uses the Python stdlib `email`
package (already in use throughout `bridge/email_bridge.py`) and matches an
existing in-repo pattern (Telegram media). Proceeding with codebase context.

## Data Flow

The pipeline deliberately separates **pure metadata extraction** (safe on any read)
from **byte persistence** (a write side-effect that only the poll loop may trigger).
This is the C1 split: `parse_email_message` never writes to disk, so a read-only
`valor-email read` cache-miss (which re-calls `parse_email_message` via
`_imap_fallback_fetch`) produces attachment metadata with `path: None` and never
touches the filesystem.

**Stable-key invariant (C6 — the carried blocker).** Every consumer that needs a
timestamp or a stable storage/history key — `_persist_attachments`, the vault
mirror, `_record_history`, and `_record_thread` — must read **one** `timestamp`
and **one** `stable_key` that `parse_email_message` computes **once** and writes
into the returned dict. No call site may re-evaluate `time.time()` or re-derive the
`message_id or "{from}:{subj}:{int(ts)}"` `or`-chain locally. Today
`_record_history:355` and `_record_thread:410` each independently fall back to
`float(parsed.get("timestamp") or time.time())`, and `parse_email_message` never
writes a `timestamp` — so for an empty-`Message-ID` email each call site computes a
**different** `int(time.time())`, making the on-disk subdir key, the vault target
name, and the history Redis key all diverge. That silently breaks the C3 (cross-message
collision) and C5 (vault naming) and empty-`Message-ID` (C-history) fixes, all of which
assume a shared key. The fix closes the root cause: `parse_email_message` stamps
`timestamp` and `stable_key` before returning, and every downstream site reads them.

1. **Entry point**: `_poll_imap()` fetches raw RFC822 bytes for an unseen email.
2. **Parse + extract metadata + stamp key (pure, no disk write)**: `_email_inbox_loop`
   calls `parse_email_message(raw_bytes)`. Within it: (a) a new
   `_extract_attachment_metadata(msg)` walks the MIME tree, and for each
   `attachment`-disposition part collects a metadata dict
   `{filename, content_type, size, path}` — where `filename` is sanitized,
   `size` is computed without persisting, and **`path` is `None`** at this stage;
   the list is attached as `parsed["attachments"]`; (b) **before returning**, it sets
   `parsed["timestamp"] = ts` (a single `float`, parsed from the `Date` header when
   present, else `time.time()`) and
   `parsed["stable_key"] = message_id or f"{from_addr}:{subject}:{int(ts)}"`
   (falling back to `uuid4().hex` when even those are empty). **No bytes are written
   to disk and no vault mirror fires here** — `parse_email_message` stays pure so the
   IMAP-fallback read path has no write side-effect, but the timestamp and stable key
   are now fixed exactly once for the lifetime of this `parsed` dict.
3. **Persist bytes (write side-effect, poll-loop only)**: back in
   `_email_inbox_loop`, gated on `if parsed and parsed.get("attachments")`, a new
   `_persist_attachments(parsed)` re-walks the message (re-derived from
   `parsed["raw_bytes"]`, see Technical Approach), sanitizes filenames,
   enforces the cumulative size cap, writes the decoded bytes to
   `data/media/email-attachments/{key_hash}/{sanitized_name}` — where `key_hash` is
   the hash of `parsed["stable_key"]`, **never re-derived locally** — and **mutates each
   metadata dict's `path` in place** to the stored location. It also fires the
   fire-and-forget vault mirror, named with the same `parsed["stable_key"]` and
   `parsed["timestamp"]`. This runs **before** `_record_history` so the history blob
   records the real `path`, not `None`.
4. **History cache**: `_record_history(parsed)` includes `attachments` (metadata
   only — no bytes, but `path` now populated) in the JSON blob. It reads
   `parsed["timestamp"]` for the `ts` field/ZADD score (never `time.time()`) and,
   when `message_id` is falsy, keys the blob/sorted-set under `parsed["stable_key"]`
   (never re-deriving the `or`-chain) so the Redis key matches the on-disk subdir key
   and the vault name byte-for-byte. `_record_thread(parsed)` likewise reads
   `parsed["timestamp"]`.
5. **Session context**: `_process_inbound_email(parsed, ...)` reads
   `parsed["attachments"]` and sets `extra_context["email_attachments"]` to the
   list of readable stored paths (plus metadata).
6. **Output**: `valor-email read --json` surfaces the `attachments` metadata array
   per message; the agent session opens the files via the paths in `extra_context`.
   On the cache-miss fallback path, the array is present with `path: None` (metadata
   only — bytes were never persisted because no poll-loop ingest occurred).

## Architectural Impact

- **New dependencies**: None — stdlib `email`, `hashlib`, `shutil` only.
- **Interface changes**: `parse_email_message()` return dict gains four additive
  keys: `attachments`, `timestamp` (a single authoritative `float`), `stable_key`
  (the shared storage/history key), and `raw_bytes` (the original message bytes,
  so `_persist_attachments` can re-walk without re-fetching). `_record_history` blob
  gains an `attachments` field (additive — old blobs without it read as `[]`).
  `extra_context` gains `email_attachments` (additive). `--file` becomes repeatable
  (additive — a single `--file` still works identically). The `timestamp`/`stable_key`
  keys make `_record_history` and `_record_thread` read a shared value instead of each
  calling `time.time()` — behavior-preserving for the common (non-empty `Message-ID`)
  case, and the fix for the empty-`Message-ID` divergence.
- **Coupling**: Low. Extraction is a self-contained helper; consumers read one
  new dict key.
- **Data ownership**: A new filesystem location, `data/media/email-attachments/`,
  owned by the email bridge — parallel to `data/media/` owned by the Telegram media path.
- **Reversibility**: High. All changes are additive; removing the feature means
  deleting the extraction call and the new dict keys.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1-2 (storage-location confirmation, size-cap default)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| IMAP configured | `python -c "from bridge.email_bridge import _get_imap_config; assert _get_imap_config()"` | Inbound email polling already runs |
| Filesystem write to data dir | `python -c "import os; os.makedirs('data/media/email-attachments', exist_ok=True)"` | Persist attachment bytes |

Run all checks: `python scripts/check_prerequisites.py docs/plans/incoming_email_attachments.md`

## Solution

### Key Elements

- **`_extract_attachment_metadata(msg)`** (new, pure, `bridge/email_bridge.py`):
  Walks the MIME tree and, for each `attachment`-disposition part, returns a
  metadata dict `{filename, content_type, size, path}` with the filename
  sanitized and `path` set to `None` (no disk write). Each part is wrapped in its
  own try/except so one malformed part never aborts the rest. **This is called
  inside `parse_email_message` and has no write side-effect**, so the IMAP read
  fallback can call it safely.
- **`_persist_attachments(parsed)`** (new, side-effecting, `bridge/email_bridge.py`):
  Re-walks the message — re-parsed from `parsed["raw_bytes"]` (the original message
  bytes `parse_email_message` stored on the dict), since `parsed` carries only
  metadata, not the live `Message` object or any payload to re-walk. Enforces the
  size cap, writes decoded bytes to
  `data/media/email-attachments/{key_hash}/{sanitized_name}` where `key_hash =
  sha256(parsed["stable_key"])` (read, never re-derived). Writes each file as a temp
  file then `os.replace()`s it into final position, so a mid-write failure leaves no
  partial file the agent could be handed; only attachments that reach a real on-disk
  path get their `path` filled. Mutates each persisted metadata dict's `path` in place,
  and fires the fire-and-forget vault mirror named with `parsed["stable_key"]` /
  `parsed["timestamp"]`. **Called ONLY from `_email_inbox_loop`** (the poll loop),
  gated on `if parsed and parsed.get("attachments")`, and **before** `_record_history`
  so the recorded blob carries the real `path`.
- **`parse_email_message()`** (modified): Calls `_extract_attachment_metadata` and
  adds `attachments` (metadata-only, `path=None`) to the returned dict. **Before
  returning, stamps the dict once** with `timestamp` (single `float`, from the `Date`
  header or `time.time()`), `stable_key` (`message_id or
  f"{from_addr}:{subject}:{int(ts)}"`, else `uuid4().hex` — synthesized **only** when
  the email actually has attachments; see Empty-body / gated-synthesis note), and
  `raw_bytes` (so `_persist_attachments` can re-walk). Stays pure — it never writes
  bytes or fires the vault mirror. An email with attachments but an empty body must
  NOT be dropped (the current empty-body guard at lines 197-199 would discard it).
- **`_record_history()` / `_record_thread()`** (modified): `_record_history` adds the
  `attachments` metadata array to the JSON blob — metadata only, never bytes; it runs
  after `_persist_attachments`, so `path` is populated for poll-loop-ingested messages.
  Both functions replace their local `float(parsed.get("timestamp") or time.time())`
  with a plain read of `parsed["timestamp"]` (now always present). When `message_id`
  is falsy, `_record_history` keys the blob/sorted-set under `parsed["stable_key"]`
  instead of hard-returning — the one place the empty-`Message-ID` visibility fix lands.
- **`get_recent_emails()` / `search_history()` / `_hydrate()`** (modified,
  `tools/email_history/`): Project `attachments` into read output.
- **`_imap_fallback_fetch()`** (modified, `tools/valor_email.py`): Add
  `"attachments": parsed.get("attachments", [])` to its hand-shaped result dicts
  so the cache-miss read path also surfaces attachment metadata (with `path=None`,
  since bytes were never persisted on a read).
- **`_process_inbound_email()`** (modified): Sets
  `extra_context["email_attachments"]` to the readable stored paths.
- **Vault mirror** (new, fire-and-forget, inside `_persist_attachments`): Copies
  stored files into `~/work-vault/email-attachments/` for KnowledgeWatcher
  indexing, mirroring `_ingest_attachments`.
- **Outgoing `--file`** (modified, `tools/valor_email.py`): `action="append"` +
  per-file validation, so multiple `--file` flags attach multiple files.

### Flow

Email with PDF arrives → IMAP poll fetches it → `parse_email_message` extracts
attachment **metadata** (pure, `path=None`) → poll loop calls
`_persist_attachments`, which writes the PDF to
`data/media/email-attachments/{key_hash}/report.pdf`, sets `path`, and mirrors to
vault → `_record_history` records the metadata (with real `path`) → session
`extra_context` carries `email_attachments=[".../report.pdf"]` → agent opens the
file and acts on it → `valor-email read --json` shows the attachment metadata.
(On a cache-miss `valor-email read`, `_imap_fallback_fetch` calls
`parse_email_message` only — metadata appears with `path=None`, no bytes written.)

### Technical Approach

- **Single source of truth for `timestamp` + `stable_key` (C6 — closes the carried
  blocker).** `parse_email_message` computes both **exactly once** before returning:
  `ts = <Date-header epoch or time.time()>` written as `parsed["timestamp"]`, and
  `parsed["stable_key"] = message_id or f"{from_addr}:{subject}:{int(ts)}"` (else
  `uuid4().hex`). `_persist_attachments` (subdir key), the vault mirror (target name),
  `_record_history` (Redis key + `ts` score when `message_id` is empty), and
  `_record_thread` (`ts`) all **read** `parsed["timestamp"]` / `parsed["stable_key"]`
  and **never** call `time.time()` or re-evaluate the `or`-chain locally. Because the
  value is fixed once, the on-disk subdir, the vault filename, and the history key are
  byte-identical for every email — including an empty-`Message-ID` email, where the
  prior independent `int(time.time())` fallbacks at `_record_history:355` and
  `_record_thread:410` diverged and silently broke the C3/C5/empty-`Message-ID` fixes.
  An integration test asserts the three derived keys are byte-identical for an
  empty-`Message-ID` email.
- **Storage decision: filesystem, matching Telegram.** Inbound bytes are written
  by `_persist_attachments` to
  `data/media/email-attachments/{key_hash}/{sanitized_name}` (parallel to
  Telegram's `data/media/`). `{key_hash}` is `sha256(parsed["stable_key"])` — the
  shared key computed once in `parse_email_message` (see above), not the raw
  Message-ID — so Message-ID-less emails never collide into a single subdir. Redis
  carries metadata + paths only, never bytes.
  Rationale: (1) consistency with the established Telegram inbound pattern; (2)
  the history blob has a 7-day TTL and a 500-entry cap — base64-encoding
  multi-megabyte files into it would blow the cap and bloat Redis; (3)
  filesystem paths are directly readable by the agent, which is exactly what
  `extra_context` needs to hand off.
- **Metadata/persist split (C1).** Metadata extraction
  (`_extract_attachment_metadata`) happens once inside `parse_email_message` and is
  **pure** — no disk write, no vault mirror — so the IMAP read fallback can call it
  with zero side-effects. Byte persistence (`_persist_attachments`) is a separate,
  side-effecting helper called **only** from the poll loop (`_email_inbox_loop`),
  gated on `if parsed and parsed.get("attachments")`, **before** `_record_history`.
  Because `parsed` carries metadata, not the live `Message` object, `_persist_attachments`
  re-parses `parsed["raw_bytes"]` (stored by `parse_email_message`) to re-walk the
  parts and reach the real payload bytes — without this thread, every `path` would
  stay `None` because there is nothing to re-walk (non-blocking concern 1, resolved).
  It mutates the same `parsed["attachments"]` dicts in place (filling `path` only for
  files that successfully reach disk), so all downstream consumers (`_record_history`,
  `_process_inbound_email`) read one list — no double-read, no ordering hazard. On the
  read path, `path` stays `None`.
- **Partial-write safety (non-blocking concern 3, resolved).** Each attachment is
  written to a temp file in the target subdir and then `os.replace()`d into its final
  name (atomic on the same filesystem). If the decode or write raises mid-way, the
  metadata dict's `path` is left `None` and that part is skipped — the agent is never
  handed a path to a non-existent or half-written file. `_process_inbound_email` builds
  `extra_context["email_attachments"]` from only the entries whose `path` is non-`None`,
  so a partial-persist failure surfaces fewer files, never a dangling path.
- **Filename sanitization.** Reduce to `Path(raw).name`, then allow only
  `[alnum, -, _, .]`, collapse the rest to `_`; empty/`.`/`..` results fall back
  to `attachment_{index}{guessed_ext}`. The `{key_hash}` subdir guarantees
  uniqueness across messages and contains traversal to a single directory.
- **Size cap (enforced in `_persist_attachments`).** A module constant
  (env-overridable, `EMAIL_ATTACHMENT_MAX_TOTAL_BYTES`) bounds the cumulative bytes
  persisted per email. The cap lives in `_persist_attachments` (the only place bytes
  are decoded), short-circuits the `walk()` loop once the running total would exceed
  the cap (C4 — stop decoding rather than decode-then-skip), and records a
  `truncated: true` marker in the metadata so the agent knows files were dropped.
  `_extract_attachment_metadata` reports declared `size` without decoding, so the
  pure read path never inflates RAM.
- **Empty-body emails with attachments.** Adjust the lines-197-199 guard so an email
  with attachments but no text body is still processed (body may legitimately be
  empty when the message is just a file). Because `_extract_attachment_metadata`
  runs inside `parse_email_message` before the guard, the guard can check
  `if (not body or not body.strip()) and not parsed_attachments` and only drop the
  email when there is neither body nor attachment.
- **Gated `stable_key` synthesis (non-blocking concern 2, resolved).** The synthesized
  `stable_key` is computed for **every** parsed email (it is the storage/history key),
  but the **empty-`Message-ID` history-key synthesis** in `_record_history` — keying the
  Redis blob/sorted-set under `stable_key` instead of hard-returning — fires **only when
  the email has attachments** (`parsed.get("attachments")`). A plain-text, no-attachment,
  no-`Message-ID` email keeps today's behavior: `_record_history` still hard-returns, so
  it does NOT newly surface in `valor-email read`. This confines the visibility change to
  exactly the attachment-only emails the feature targets and avoids a broad,
  unintended behavior shift for all `Message-ID`-less plain mail.
- **Empty-`Message-ID` history visibility (carried concern).** `_record_history`
  hard-returns at line 353 when `message_id` is empty (`if not message_id: return`),
  so an attachment-only email from a provider that omits `Message-ID` would be
  persisted to disk and handed to the session but never surface in
  `valor-email read --json`. When `message_id` is falsy **and** the email has
  attachments, `_record_history` keys the history blob/sorted-set under the shared
  `parsed["stable_key"]` (computed once in `parse_email_message`, **not** re-derived
  here) instead of returning, so the message is observable in read output. Because the
  subdir key, the vault name, and this Redis key all read the one `parsed["stable_key"]`,
  they are byte-identical — the divergence the carried blocker called out is gone. The
  blob still records the real (possibly empty) `message_id` field for reply threading;
  only the Redis key is the synthesized `stable_key`.
- **Vault mirror.** Reuse the `_ingest_attachments` shape: fire-and-forget copy,
  sanitized target name keyed by `(parsed["timestamp"], sender, parsed["stable_key"],
  filename)` — both read from the parsed dict, never recomputed — every failure caught
  and logged.
- **Outgoing multi-file.** `_build_reply_mime` and the relay already iterate an
  attachments list — only the CLI arg parsing needs `action="append"` plus a
  per-file existence/readability loop.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_extract_attachment_metadata` and `_persist_attachments` each wrap every part
  in try/except — add a test feeding a part with undecodable payload / missing
  filename and assert the function logs a warning, skips that part, and returns (or
  persists) the remaining valid attachments.
- [ ] The vault-mirror copy (inside `_persist_attachments`) is fire-and-forget with a
  top-level catch — add a test that points the vault dir at an unwritable path and
  asserts the inbound flow still completes (attachment still persisted to
  `data/media/`, session enqueued).
- [ ] `_record_history` already swallows-and-logs; assert the attachments field is
  present on success and its absence (old blob) hydrates as `[]`.

### Empty/Invalid Input Handling
- [ ] Email with attachments but empty text body → still parsed and processed
  (not dropped by the lines-197-199 guard).
- [ ] Attachment-only email with an empty `Message-ID` → persisted, processed, AND
  visible in `valor-email read --json` (history recorded under the synthesized
  stable key). Assert the on-disk subdir key, the vault target name, and the history
  Redis key are **byte-identical** (all read the one `parsed["stable_key"]`) — the
  regression guard for the carried timestamp-divergence blocker.
- [ ] Plain-text email with an empty `Message-ID` and NO attachments → behavior
  unchanged: `_record_history` still hard-returns, so it does NOT newly appear in
  `valor-email read` (gated-synthesis guard, non-blocking concern 2).
- [ ] Partial-persist failure (second attachment's write raises) → the first file is
  present with a real `path`, the failed entry's `path` stays `None`, and
  `extra_context["email_attachments"]` contains only the real path — no dangling path
  (non-blocking concern 3).
- [ ] Attachment part with no `filename` → falls back to `attachment_{i}{ext}`.
- [ ] Filename containing `../` or absolute path → sanitized to a safe basename
  written inside the message subdir (assert no escape).
- [ ] Cumulative size over the cap → later parts skipped, `truncated: true`
  recorded, no exception.

### Error State Rendering
- [ ] `valor-email read --json` for a message with attachments renders the
  `attachments` array; for a message without, renders `[]` (not missing key).

## Test Impact

- [ ] `tests/unit/test_email_bridge.py` — UPDATE: add cases for
  `parse_email_message` returning attachment metadata with `path=None` and writing
  NO files (C1 purity), `parse_email_message` stamping `timestamp` + `stable_key` +
  `raw_bytes` exactly once (C6), `_persist_attachments` writing bytes via temp-then-
  `os.replace` + filling `path` only for files that reach disk, partial-write leaving
  `path=None`, `_persist_attachments` re-walking from `parsed["raw_bytes"]`,
  filename sanitization, size-cap decode short-circuit, malformed part,
  empty-body-with-attachment, and empty-`Message-ID` stable-key. Add a focused case
  asserting `_record_history` and `_record_thread` read `parsed["timestamp"]` rather
  than calling `time.time()` (monkeypatch `time.time` to a sentinel and assert the
  stored `ts` equals `parsed["timestamp"]`, not the sentinel).
- [ ] `tests/unit/test_email_history.py` — UPDATE: assert `_record_history` blob
  and `get_recent_emails`/`search_history` projections include `attachments`;
  back-compat blob without the field hydrates as `[]`; **add a case asserting an
  attachment-only email with an empty `Message-ID` is recorded and retrievable**
  (synthesized-key fix).
- [ ] `tests/integration/test_email_bridge.py` — UPDATE/REPLACE relevant inbound
  cases: full multipart email → metadata parsed, persisted to disk by the poll
  loop, exposed in read output and `extra_context["email_attachments"]`; **add the
  outcome test** that reads the persisted file's bytes from the context path and
  asserts a known marker string is present; **add the key-identity test** that drives
  an empty-`Message-ID` attachment email through the real poll-loop sequence
  (`parse_email_message` → `_persist_attachments` → `_record_history`) and asserts the
  on-disk subdir name, the vault target name, and the history Redis key are
  byte-identical; assert the cache-miss `_imap_fallback_fetch` read path surfaces
  `attachments` with `path=None` and writes no files.
- [ ] `tests/unit/test_email_relay.py` — no change needed for inbound; existing
  `TestProcessOutboxAttachment` cases stay green (outgoing list path unchanged).
- [ ] `tests/unit/test_valor_email.py` (or the send test module) — UPDATE: add a
  multi-`--file` outgoing case asserting both files land in the outbox payload's
  `attachments` list; assert `_imap_fallback_fetch` projects the `attachments` key.

No existing inbound-attachment tests exist to break — the inbound work is purely
additive. The two existing-behavior changes are (1) the lines-197-199 empty-body
guard and (2) the `_record_history` empty-`Message-ID` synthesized-key fix, both
covered by updated unit tests.

## Rabbit Holes

- **Inline/CID image extraction and rendering.** Treat `inline`/CID parts as
  ordinary attachments at most; do not build an HTML-inlining or image-rendering
  pipeline.
- **Attachment garbage collection / TTL on disk.** Telegram's `data/media/` files
  are not GC'd either. Do not build a reaper in this plan — note it as future work.
- **Virus/malware scanning.** Out of scope; senders are already allow-listed by
  the routing layer.
- **markitdown conversion of attachments at parse time.** The vault mirror already
  hands files to `valor-ingest`/KnowledgeWatcher asynchronously — do not invoke
  conversion synchronously in the poll loop.
- **Re-architecting the history blob into a separate attachments namespace.**
  Metadata inline in the existing blob is sufficient; a new Redis namespace is
  over-engineering.

## Risks

### Risk 1: Disk growth from persisted attachments
**Impact:** `data/media/email-attachments/` grows unbounded over time.
**Mitigation:** Accepted as residual risk — matches the existing Telegram
`data/media/` behavior. The per-email size cap bounds the worst case per message.
A disk reaper is explicitly noted as future work (Rabbit Holes), not this plan.

### Risk 2: History blob eviction orphans the on-disk files
**Impact:** After the 7-day TTL or 500-entry trim, the metadata blob is gone but
the files remain on disk with no index entry.
**Mitigation:** Files are independently readable (full paths in `extra_context`
during the live session, plus the vault mirror is indexed by the KnowledgeWatcher
independent of the email history blob). Orphaned working-dir files are the same
accepted tradeoff as Telegram media; the vault copy is the durable record.

### Risk 3: Filename collision within a single email
**Impact:** Two attachments named `image.png` overwrite each other.
**Mitigation:** Disambiguate by index within the message subdir
(`image.png`, `image_1.png`) — the extractor checks for an existing target and
suffixes an index before writing.

## Race Conditions

### Race 1: Persistence vs. history/context reads of the same attachment list
**Location:** `bridge/email_bridge.py` `_email_inbox_loop` →
`_persist_attachments` → `_record_history` then `_process_inbound_email`.
**Trigger:** `_record_history` and `_process_inbound_email` both read the attachment
metadata (including the on-disk `path`) for the same email.
**Data prerequisite:** `parsed["attachments"]` `path` fields must be populated
before either consumer runs.
**State prerequisite:** Files must be on disk before paths are handed to the agent.
**Mitigation:** The poll loop runs strictly sequentially:
`parse_email_message` (pure metadata; stamps the one `timestamp` + `stable_key`) →
`_persist_attachments` (writes bytes, fills `path` in the same in-memory list) →
`_record_history` / `_record_thread` → `_process_inbound_email`.
All later steps read the one list that `_persist_attachments` already mutated and the
one `timestamp`/`stable_key` that `parse_email_message` already stamped — no consumer
recomputes a timestamp or re-derives the key, so every derived key is identical.
Single producer, sequential consumers, no concurrency — no race. (`_record_history`'s
internal blob+ZADD atomicity is provided by its existing `r.pipeline()` MULTI/EXEC,
already in place — not introduced here; see N1.)

### Race 2: Vault mirror vs. session start
**Location:** Fire-and-forget vault copy vs. `enqueue_agent_session`.
**Trigger:** The agent session may start before the vault copy finishes.
**Data prerequisite:** The agent only needs the `data/media/...` working paths in
`extra_context`, which exist before enqueue.
**State prerequisite:** None — the vault copy is for offline knowledge indexing,
not for the live session.
**Mitigation:** The agent reads the working-dir paths (already written). The vault
mirror is decoupled and indexed asynchronously by the KnowledgeWatcher; its
completion is not on the session's critical path.

## No-Gos (Out of Scope)

- [EXTERNAL] No virus/malware scanning of inbound files — would need a third-party
  scanner the agent cannot provision; senders are already allow-listed upstream.

(Disk reaper / on-disk TTL cleanup is covered as accepted residual risk under
Risk 1 and as future work under Rabbit Holes — not deferred scope here. Inline/CID
rendering and synchronous markitdown conversion are also covered under Rabbit
Holes — they are deliberately never built.)

**Vault mirror scope note (carried concern):** The vault mirror to
`~/work-vault/email-attachments/` is **not required by the issue's acceptance
criteria** — the issue only asks that the agent can read incoming attachments,
which the `data/media/` persistence + `extra_context` paths fully satisfy. The
mirror is an **intentional in-scope extension**, decided by the PM in Open Q2
(✅ YES) for consistency with the Telegram inbound-media path and durable
KnowledgeWatcher indexing. It is decoupled and fire-and-forget, so it can be
dropped without affecting any AC item if review prefers a tighter first cut. It is
explicitly labeled here so the scope boundary is unambiguous: AC-critical work is
the persistence + read-output + context exposure; the vault mirror is the
consistency-with-Telegram bonus.

## Update System

No update system changes required. The feature is internal to the email bridge
and the `valor-email` CLI (already installed). The new directory
`data/media/email-attachments/` is created on first use (`mkdir(parents=True,
exist_ok=True)`), and `~/work-vault/email-attachments/` is auto-watched by the
existing KnowledgeWatcher — no new config files, dependencies, or migration steps
to propagate across machines. `EMAIL_ATTACHMENT_MAX_TOTAL_BYTES` has a sane
default and is optional in `.env`.

## Agent Integration

The agent reaches inbound attachments through two existing surfaces, no new MCP
wiring:
- **Session `extra_context`**: `_process_inbound_email` already flows
  `extra_context` into the enqueued `AgentSession`; adding `email_attachments`
  (readable paths) means the agent processing the email sees the files in its
  prompt context automatically.
- **`valor-email read --json` CLI**: already declared in
  `pyproject.toml [project.scripts]` and invoked via the agent's Bash tool;
  surfacing the `attachments` metadata array requires no new entry point.
- Integration test: assert that an inbound multipart email produces an
  `AgentSession` whose `extra_context["email_attachments"]` contains the readable
  stored paths, and that `valor-email read --json` lists the attachment metadata.

No new CLI entry point and no bridge-internal import changes beyond the email
bridge module itself.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/email-bridge.md` — add an "Incoming attachments"
  section (storage location, metadata shape, `email_attachments` context field,
  size cap, sanitization) alongside the existing outgoing-attachments docs.
- [ ] Update the outgoing-attachments note in `docs/features/email-bridge.md` to
  document multi-`--file` support.

### Inline Documentation
- [ ] Docstrings on `_extract_attachment_metadata` (pure, no-disk-write contract +
  `path=None`) and `_persist_attachments` (poll-loop-only side-effect, re-walks
  `parsed["raw_bytes"]`, reads `parsed["stable_key"]`, temp-then-`os.replace` write,
  sanitization + size-cap contract, runs before `_record_history`), plus the updated
  `parse_email_message` (documents that it stamps `timestamp`/`stable_key`/`raw_bytes`
  once as the single source of truth) and `_record_history` / `_record_thread`
  (document that they READ `parsed["timestamp"]`/`parsed["stable_key"]` and must never
  call `time.time()` or re-derive the key) describing the `attachments` field shape.

## Success Criteria

- [ ] `parse_email_message()` is **pure** — it returns an `attachments` metadata
  list with `{filename, content_type, size, path}` (with `path=None`) for each
  inbound attachment part and writes **nothing** to disk. A unit test asserts that
  calling `parse_email_message` on a multipart email creates no files under
  `data/media/email-attachments/` and leaves every `path` as `None` (proves the C1
  read-path side-effect is gone).
- [ ] `_persist_attachments()` (called only from the poll loop) writes attachment
  bytes under `data/media/email-attachments/` with sanitized, collision-safe
  filenames, populates each `path` in place, and runs **before** `_record_history`;
  no path traversal possible. A test asserts the cache-miss `valor-email read`
  fallback (`_imap_fallback_fetch`) surfaces attachment metadata with `path=None`
  and writes no files.
- [ ] The history cache blob and `valor-email read --json` expose attachment
  metadata (with populated `path` for poll-loop messages); messages without
  attachments render `attachments: []`. **Attachment-only emails with an empty
  `Message-ID` still surface in `valor-email read --json`** (history records under a
  synthesized stable key — carried concern).
- [ ] **Single timestamp / stable key (carried blocker).** `parse_email_message`
  stamps `timestamp` and `stable_key` once; `_persist_attachments`, the vault mirror,
  `_record_history`, and `_record_thread` all read them and never call `time.time()`
  or re-derive the `or`-chain. An integration test on an empty-`Message-ID` attachment
  email asserts the on-disk subdir key, the vault target name, and the history Redis
  key are byte-identical. A unit test asserts a no-attachment, no-`Message-ID`
  plain-text email is still NOT recorded (gated synthesis).
- [ ] **No dangling paths.** A partial-persist write failure leaves the failed entry's
  `path=None` and excludes it from `extra_context["email_attachments"]`; the agent
  never receives a path to a missing file.
- [ ] Inbound session `extra_context["email_attachments"]` carries readable paths.
- [ ] **Outcome: the agent can act on the attached file's content.** An integration
  test delivers a multipart email carrying a small text/CSV attachment with a known
  marker string, runs the inbound flow, and asserts that the persisted file at the
  `extra_context["email_attachments"]` path is readable and its bytes contain the
  marker — proving the end-to-end path (persist → context → readable content), not
  just the plumbing.
- [ ] Size cap (short-circuits the decode loop), filename sanitization, and per-part
  malformed-input handling are enforced and tested.
- [ ] Empty-body emails that carry attachments are processed, not dropped.
- [ ] Outgoing multiple `--file` flags attach multiple files (CLI fix + test).
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

### Team Members

- **Builder (inbound-parse)**
  - Name: `inbound-parse-builder`
  - Role: `_extract_attachment_metadata` (pure) + `_persist_attachments` (poll-loop-only), `parse_email_message` changes, empty-body guard, vault mirror
  - Agent Type: builder
  - Resume: true

- **Builder (history-and-context)**
  - Name: `history-context-builder`
  - Role: `_record_history` + `tools/email_history/` projections + `extra_context["email_attachments"]`
  - Agent Type: builder
  - Resume: true

- **Builder (outgoing-cli)**
  - Name: `outgoing-cli-builder`
  - Role: multi-`--file` `action="append"` + per-file validation in `tools/valor_email.py`
  - Agent Type: builder
  - Resume: true

- **Validator (attachments)**
  - Name: `attachments-validator`
  - Role: verify persistence, sanitization, size cap, read-output + context exposure, multi-file outgoing
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `email-docs`
  - Role: update `docs/features/email-bridge.md`
  - Agent Type: documentarian
  - Resume: true

### 1. Inbound metadata extraction (pure) + persistence (poll-loop-only)
- **Task ID**: build-inbound-parse
- **Depends On**: none
- **Validates**: tests/unit/test_email_bridge.py, tests/integration/test_email_bridge.py
- **Assigned To**: inbound-parse-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_extract_attachment_metadata(msg)` (PURE — no disk write): walk MIME, sanitize filenames, report declared `size`, return metadata dicts `{filename, content_type, size, path=None}`, per-part try/except. Call it inside `parse_email_message` and add `attachments` to the returned dict.
- In `parse_email_message`, **before returning**, stamp the dict ONCE (C6 — the carried blocker): `parsed["timestamp"] = <Date-header epoch or time.time()>` (single `float`), `parsed["stable_key"] = message_id or f"{from_addr}:{subject}:{int(ts)}"` (else `uuid4().hex`), and `parsed["raw_bytes"] = raw_bytes`. These are the single source of truth for every downstream timestamp/key read.
- Add `_persist_attachments(parsed)` (SIDE-EFFECTING): re-parse `parsed["raw_bytes"]` to re-walk the parts (concern 1 — `parsed` has no payload to re-walk otherwise), read `key_hash = sha256(parsed["stable_key"])` (NEVER re-derive the key locally), enforce `EMAIL_ATTACHMENT_MAX_TOTAL_BYTES` by short-circuiting the decode loop (+`EMAIL_ATTACHMENT_MAX_PARTS`), write each file as a temp file then `os.replace()` into `data/media/email-attachments/{key_hash}/` (concern 3 — no partial files), and mutate each metadata dict's `path` in place ONLY for files that reach disk. Include the fire-and-forget vault mirror to `~/work-vault/email-attachments/` named with `parsed["stable_key"]` + `parsed["timestamp"]`.
- Wire `_persist_attachments(parsed)` into `_email_inbox_loop` gated on `if parsed and parsed.get("attachments")`, placed **after** `parse_email_message` and **before** `_record_history` (so the recorded blob carries the real `path`). Do NOT call `_persist_attachments` from `parse_email_message` or any read path.
- Relax the lines-197-199 empty-body guard so an email with attachments but no text body is still returned (drop only when there is neither body nor attachment).
- Test that `parse_email_message` writes no files (paths stay `None`) — the C1 read-safety assertion — and that it stamps `timestamp`/`stable_key`/`raw_bytes` exactly once (C6).

### 2. History cache + read output + session context
- **Task ID**: build-history-context
- **Depends On**: build-inbound-parse
- **Validates**: tests/unit/test_email_history.py, tests/integration/test_email_bridge.py
- **Assigned To**: history-context-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `attachments` to the `_record_history` blob (metadata only, `path` populated by Task 1's `_persist_attachments` which already ran); back-compat read as `[]`.
- Replace the local `float(parsed.get("timestamp") or time.time())` in BOTH `_record_history` (line ~355) and `_record_thread` (line ~410) with a plain read of `parsed["timestamp"]` (Task 1 guarantees it is set). This is the C6 fix — both functions must read the one shared timestamp, never call `time.time()`.
- Fix the empty-`Message-ID` history gap: when `message_id` is falsy **and** `parsed.get("attachments")` is truthy (gated synthesis — concern 2: do NOT change behavior for plain no-attachment mail), key the Redis blob/sorted-set under `parsed["stable_key"]` (read, NEVER re-derive) so attachment-only emails surface in read output. The blob still stores the real `message_id` field.
- Project `attachments` in `get_recent_emails`/`search_history`/`_hydrate`.
- Add `"attachments": parsed.get("attachments", [])` to `_imap_fallback_fetch`'s result dicts (C2 — cache-miss read path), so `path=None` metadata still appears.
- Set `extra_context["email_attachments"]` in `_process_inbound_email`, including ONLY entries whose `path` is non-`None` (concern 3 — no dangling paths).

### 3. Outgoing multi-file CLI fix
- **Task ID**: build-outgoing-cli
- **Depends On**: none
- **Validates**: tests/unit/test_valor_email.py (or send test module)
- **Assigned To**: outgoing-cli-builder
- **Agent Type**: builder
- **Parallel**: true
- Change `--file` to `action="append"`; validate each path; build the absolute-path attachments list.
- Add a multi-file outgoing test.

### 4. Validation
- **Task ID**: validate-attachments
- **Depends On**: build-inbound-parse, build-history-context, build-outgoing-cli
- **Assigned To**: attachments-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all success criteria, run the full email test suite, confirm no traversal / no byte-bloat in Redis.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-attachments
- **Assigned To**: email-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/email-bridge.md` (incoming attachments + multi-file outgoing).

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: attachments-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all verification checks; confirm docs updated; final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Email tests pass | `pytest tests/unit/test_email_bridge.py tests/unit/test_email_history.py tests/integration/test_email_bridge.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Attachments wired into context | `grep -n "email_attachments" bridge/email_bridge.py` | output contains email_attachments |
| Metadata extractor exists (pure) | `grep -n "_extract_attachment_metadata" bridge/email_bridge.py` | output contains _extract_attachment_metadata |
| Persist helper exists + poll-loop-gated | `grep -n "_persist_attachments" bridge/email_bridge.py` | appears in both the def and the `_email_inbox_loop` call site |
| parse_email_message stays pure | `grep -n "_persist_attachments\|vault" bridge/email_bridge.py` | no `_persist_attachments` / vault-mirror call inside `parse_email_message` (manual confirm of the C1 split) |
| Shared timestamp/stable_key | `grep -n "stable_key\|parsed\[.timestamp.\]" bridge/email_bridge.py` | `parse_email_message` sets both; `_persist_attachments`/`_record_history`/`_record_thread`/vault read them |
| No re-evaluated time.time() in record helpers | `grep -n "time.time()" bridge/email_bridge.py` | `time.time()` no longer appears inside `_record_history` / `_record_thread` (C6 — manual confirm; only `parse_email_message` and the poll health-timestamp use it) |
| Fallback read projects attachments | `grep -n "attachments" tools/valor_email.py` | `_imap_fallback_fetch` result dict includes attachments |
| Multi-file outgoing | `grep -n "action=\"append\"" tools/valor_email.py` | output contains action="append" |

## Critique Results

**Verdict:** READY TO BUILD (with concerns) — original run 0 blockers, 5 concerns, 2 nits;
re-critique surfaced one root-cause blocker (C6), now resolved in this revision.
Critics: Skeptic, Operator, Archaeologist, Adversary, Simplifier, User, Consistency Auditor.
Run 2026-06-04 against baseline `ce1c852d`; revised 2026-06-24 against `b045c361`.
Concerns C1–C5 carry implementation notes that BUILD must honor; C6 (and non-blocking
concerns 1–3) are resolved below. Nits N1–N2 are optional polish.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Operator, Adversary | **C1: `read`-time disk writes.** Persistence + vault mirror live inside `parse_email_message`, which is also called by `tools/valor_email.py:_imap_fallback_fetch` on a cache-miss `valor-email read`. A read-only CLI read would write bytes to disk and fire the vault mirror — a write side-effect on a read path, possibly double-persisting. | Task 1 (inbound-parse) | Split into `_extract_attachment_metadata(msg)` (pure: filename/content-type/size, called inside `parse_email_message`) and `_persist_attachments(parsed, message_id)` (writes bytes + vault mirror). Call `_persist_attachments` ONLY from `_email_inbox_loop` (~`bridge/email_bridge.py:1113-1119`, the `parse_email_message` → `_record_history` block) after parse returns, gated on `if parsed and parsed.get("attachments")`. On the fallback read path, `path` is `None` (bytes never persisted) — document that `path` is populated only for poll-loop-ingested messages. |
| CONCERN | Skeptic, User | **C2: Read-output misses the IMAP fallback path.** Attachment exposure is scoped to `tools/email_history/` projections, but `valor-email read` falls back to `_imap_fallback_fetch` (re-parsing raw IMAP) when the cache is empty. Its result dicts are hand-shaped in the CLI, so attachments would be absent there. | Task 2 (history-context) | In `_imap_fallback_fetch`, the `results.append({...})` block must add `"attachments": parsed.get("attachments", [])`. Test BOTH cache-hit (`get_recent_emails`) and cache-miss (`_imap_fallback_fetch`) read paths project the field. |
| CONCERN | Adversary | **C3: Empty/missing `Message-ID` breaks the storage subdir key.** `message_id` can be empty (providers omit it; `_record_history` guards `if not message_id`). With an empty Message-ID, `{msgid_hash}` collapses to a constant, so attachments from ALL Message-ID-less emails collide into one subdir and overwrite across messages. | Task 1 (inbound-parse) | Compute `key = message_id or f"{from_addr}:{subject}:{int(timestamp)}"` before hashing, OR generate a `uuid4().hex` subdir when `message_id` is falsy. Distinct from Risk 3 (same-message dupes via index-suffix); this is cross-message collision. Apply the same fallback to the vault target name (see C5). |
| CONCERN | Adversary, Operator | **C4: Size cap is post-decode only.** `EMAIL_ATTACHMENT_MAX_TOTAL_BYTES` bounds persisted bytes, but each part is `get_payload(decode=True)`'d into RAM before the cap rejects it. A multipart bomb forces full base64-decode of every part; `IMAP_MAX_BATCH=20` compounds it. | Task 1 (inbound-parse) | Keep the SINGLE cumulative knob (per PM Open Q1 — no per-file cap). But make the cap short-circuit the `walk()` loop: maintain a running total, and once it would exceed `EMAIL_ATTACHMENT_MAX_TOTAL_BYTES`, stop decoding further parts (mark `truncated: true`) rather than decoding-then-skipping. Estimate from encoded `len(part.get_payload())` before `decode=True` where possible so an oversized part is rejected pre-decode. Add `EMAIL_ATTACHMENT_MAX_PARTS` count cap as a cheap bomb guard (default generous). |
| CONCERN | Archaeologist | **C5: Vault-mirror naming key must include filename + handle empty msgid.** Reusing `_ingest_attachments`' shape, two different files named `report.pdf` from two senderless emails could collide in the flat vault dir. | Task 1 (inbound-parse) | Reuse the exact Telegram formula `f"{date_part}_{safe_sender}_{msg_id}_{src.name}"`, substituting the C3 fallback for `msg_id` when Message-ID is empty. Content-hash idempotency makes a genuine duplicate overwrite the prior sidecar — accepted (matches Telegram). Gated on Open Q2 = YES (resolved). |
| BLOCKER (resolved) | Re-critique | **C6: Stable-key timestamp divergence.** The stable key `message_id or f"{from_addr}:{subject}:{int(timestamp)}"` assumes a shared `timestamp`, but `parse_email_message` never wrote one — `_record_history:355` and `_record_thread:410` each fell back to their own `int(time.time())`. For an empty-`Message-ID` email, `_persist_attachments`, `_record_history`, and the vault mirror computed different `int(time.time())` values, so the on-disk subdir key, the vault name, and the history key all diverged — silently breaking the C3/C5/empty-`Message-ID` fixes. | Task 1 + Task 2 | `parse_email_message` sets `parsed["timestamp"]` (single `float`, from `Date` header or `time.time()`) and `parsed["stable_key"]` (the `or`-chain, else `uuid4().hex`) once before returning. `_persist_attachments` (subdir + vault), `_record_history` (Redis key + score), and `_record_thread` (score) all READ those keys; none re-evaluates `time.time()` or the `or`-chain. Integration test asserts the three derived keys are byte-identical for an empty-`Message-ID` email. Also resolved: (1) `_persist_attachments` re-walks `parsed["raw_bytes"]` so `path` is actually filled; (2) empty-`Message-ID` history synthesis is gated on `parsed.get("attachments")` so plain mail behavior is unchanged; (3) temp-then-`os.replace` writes + filtering `email_attachments` to non-`None` paths prevents dangling paths. |
| NIT | Consistency Auditor | **N1: Race 1 prose drift.** `_record_history` already uses a `r.pipeline()` MULTI/EXEC + orphan-DEL design; Race 1 reads as if introducing the pipeline. | Task 2 (history-context) | Trim Race 1 prose to acknowledge the existing atomic pipeline rather than presenting it as new mitigation. Cosmetic. |
| NIT | User | **N2: `--file` help text not updated.** Switching to `action="append"` without updating the help string ("File to attach…") to indicate it's repeatable. | Task 3 (outgoing-cli) | Update help to "File to attach; repeat for multiple files". |

### Structural Checks
All PASS: required sections present/non-empty; tasks 1–6 sequential, deps acyclic;
13/13 referenced source/test files exist (`data/media/email-attachments/` intentionally
new); cross-references sound (every Success Criterion maps to a task; No-Gos and Rabbit
Holes absent from tasks).

---

## Open Questions

All three resolved by PM (2026-06-04) with pattern-matching defaults — consistency
with the existing Telegram inbound-media path.

1. **Size cap default.** ✅ RESOLVED — `EMAIL_ATTACHMENT_MAX_TOTAL_BYTES = 25 MiB`
   cumulative total per email. Single knob; **no separate per-file cap.** (Critique
   C4 still applies: the single cumulative cap must short-circuit the decode loop
   rather than decode-then-skip, and a generous `EMAIL_ATTACHMENT_MAX_PARTS` count
   cap guards against multipart bombs — neither introduces a per-file *size* cap.)
2. **Vault mirror.** ✅ RESOLVED — YES. Mirror inbound attachments into
   `~/work-vault/email-attachments/` for KnowledgeWatcher indexing, matching the
   Telegram pattern. Unblocks the C5 vault-mirror code path.
3. **Disk cleanup.** ✅ RESOLVED — ship WITHOUT a reaper. On-disk retention is
   accepted residual risk (matches Telegram `data/media/`); a reaper is future work
   (see Rabbit Holes / Risk 1).
