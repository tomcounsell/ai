---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-06-04
tracking: https://github.com/tomcounsell/ai/issues/1567
last_comment_id:
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

**Baseline commit:** `ce1c852d` (`git rev-parse HEAD`)
**Issue filed at:** 2026-06-04T14:35:22Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `bridge/email_bridge.py:139-172` — `_extract_body()` skips `attachment` parts — still holds.
- `bridge/email_bridge.py:175-215` — `parse_email_message()` returns no attachments field — still holds.
- `bridge/email_bridge.py:334-392` — `_record_history()` blob has a fixed field set, no attachments — still holds.
- `bridge/email_bridge.py:937-948` — inbound `extra_context` has no `email_attachments` — still holds.
- `tools/email_history/__init__.py:135-150` — `get_recent_emails()` projects a fixed field set — still holds.
- `tools/valor_email.py:343-362` — outgoing `--file` is a single arg (multi-file does NOT work today) — still holds.
- `bridge/media.py:285-322` / `bridge/telegram_bridge.py:799-843` — Telegram persists inbound files to the filesystem (`data/media/`, then `~/work-vault/telegram-attachments/`) — still holds; this is the storage precedent.

**Cited sibling issues/PRs re-checked:**
- #1067 (valor-email CLI) — closed/merged; established the outbox + history-cache architecture this plan extends.
- #1297 (Telegram media enrichment) — closed; confirms the filesystem-storage + work-vault-ingest pattern for inbound media.
- #1161 (markitdown ingestion) — closed; `valor-ingest` + KnowledgeWatcher auto-index anything dropped under `~/work-vault/`, which the vault-mirror step reuses.

**Commits on main since issue was filed (touching referenced files):** None. The
most recent email-bridge commits (`#1093` customer resolver, `bfa0b09c` IMAP
timeout, `#1095/#1144` relay shim removal) all predate the issue.

**Active plans in `docs/plans/` overlapping this area:** None touching the email
attachment path.

**Notes:** All premises hold against `ce1c852d`. No drift.

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

1. **Entry point**: `_poll_imap()` fetches raw RFC822 bytes for an unseen email.
2. **Parse + extract**: `_email_inbox_loop` calls `parse_email_message(raw_bytes)`.
   Within it, a new `_extract_attachments(msg, message_id)` walks the MIME tree,
   and for each `attachment`-disposition part: sanitizes the filename, enforces
   the cumulative size cap, writes the decoded bytes to
   `data/media/email-attachments/{msgid_hash}/{sanitized_name}`, and collects a
   metadata dict `{filename, content_type, size, path}`. The list is attached to
   the parsed dict as `parsed["attachments"]`.
3. **History cache**: `_record_history(parsed)` includes `attachments` (metadata
   only — no bytes) in the JSON blob at `email:history:msg:{message_id}`.
4. **Vault mirror (consistency with Telegram)**: a fire-and-forget copy of each
   stored file into `~/work-vault/email-attachments/` so the KnowledgeWatcher
   indexes it, mirroring `_ingest_attachments`. Failures are logged, never fatal.
5. **Session context**: `_process_inbound_email(parsed, ...)` reads
   `parsed["attachments"]` and sets `extra_context["email_attachments"]` to the
   list of readable stored paths (plus metadata).
6. **Output**: `valor-email read --json` surfaces the `attachments` metadata array
   per message; the agent session opens the files via the paths in `extra_context`.

## Architectural Impact

- **New dependencies**: None — stdlib `email`, `hashlib`, `shutil` only.
- **Interface changes**: `parse_email_message()` return dict gains an
  `attachments` key (additive). `_record_history` blob gains an `attachments`
  field (additive — old blobs without it read as `[]`). `extra_context` gains
  `email_attachments` (additive). `--file` becomes repeatable (additive — a
  single `--file` still works identically).
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

- **`_extract_attachments(msg, message_id)`** (new, `bridge/email_bridge.py`):
  Walks the MIME tree, sanitizes filenames, enforces the size cap, decodes and
  writes attachment bytes to the filesystem, returns a metadata list. Each part
  is wrapped in its own try/except so one malformed part never aborts the rest.
- **`parse_email_message()`** (modified): Calls the extractor and adds
  `attachments` to the returned dict. An email with attachments but an empty body
  must NOT be dropped (the current empty-body guard at line 197 would discard it).
- **`_record_history()`** (modified): Adds the `attachments` metadata array to the
  JSON blob — metadata only, never bytes.
- **`get_recent_emails()` / `search_history()` / `_hydrate()`** (modified,
  `tools/email_history/`): Project `attachments` into read output.
- **`_process_inbound_email()`** (modified): Sets
  `extra_context["email_attachments"]` to the readable stored paths.
- **Vault mirror** (new, fire-and-forget): Copies stored files into
  `~/work-vault/email-attachments/` for KnowledgeWatcher indexing, mirroring
  `_ingest_attachments`.
- **Outgoing `--file`** (modified, `tools/valor_email.py`): `action="append"` +
  per-file validation, so multiple `--file` flags attach multiple files.

### Flow

Email with PDF arrives → IMAP poll fetches it → `parse_email_message` extracts +
persists the PDF to `data/media/email-attachments/{hash}/report.pdf` → metadata
recorded in history cache + mirrored to vault → session `extra_context` carries
`email_attachments=[".../report.pdf"]` → agent opens the file and acts on it →
`valor-email read --json` shows the attachment metadata.

### Technical Approach

- **Storage decision: filesystem, matching Telegram.** Inbound bytes are written
  to `data/media/email-attachments/{msgid_hash}/{sanitized_name}` (parallel to
  Telegram's `data/media/`). Redis carries metadata + paths only, never bytes.
  Rationale: (1) consistency with the established Telegram inbound pattern; (2)
  the history blob has a 7-day TTL and a 500-entry cap — base64-encoding
  multi-megabyte files into it would blow the cap and bloat Redis; (3)
  filesystem paths are directly readable by the agent, which is exactly what
  `extra_context` needs to hand off.
- **Single extraction point.** Extraction + persistence happens once, inside
  `parse_email_message`, before either `_record_history` or
  `_process_inbound_email` runs. Both consumers read the same `parsed["attachments"]`
  list — no double-read, no ordering hazard.
- **Filename sanitization.** Reduce to `Path(raw).name`, then allow only
  `[alnum, -, _, .]`, collapse the rest to `_`; empty/`.`/`..` results fall back
  to `attachment_{index}{guessed_ext}`. The `{msgid_hash}` subdir guarantees
  uniqueness across messages and contains traversal to a single directory.
- **Size cap.** A module constant (env-overridable,
  `EMAIL_ATTACHMENT_MAX_TOTAL_BYTES`) bounds the cumulative bytes persisted per
  email. Once exceeded, remaining parts are skipped and a `truncated: true`
  marker is recorded in the metadata so the agent knows files were dropped.
- **Empty-body emails with attachments.** Adjust the line-197 guard so an email
  with attachments but no text body is still processed (body may legitimately be
  empty when the message is just a file).
- **Vault mirror.** Reuse the `_ingest_attachments` shape: fire-and-forget copy,
  sanitized target name keyed by `(timestamp, sender, message_id, filename)`,
  every failure caught and logged.
- **Outgoing multi-file.** `_build_reply_mime` and the relay already iterate an
  attachments list — only the CLI arg parsing needs `action="append"` plus a
  per-file existence/readability loop.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_extract_attachments` wraps each part in try/except — add a test feeding a
  part with undecodable payload / missing filename and assert the function logs a
  warning, skips that part, and returns the remaining valid attachments.
- [ ] The vault-mirror copy is fire-and-forget with a top-level catch — add a test
  that points the vault dir at an unwritable path and asserts the inbound flow
  still completes (attachment still persisted to `data/media/`, session enqueued).
- [ ] `_record_history` already swallows-and-logs; assert the attachments field is
  present on success and its absence (old blob) hydrates as `[]`.

### Empty/Invalid Input Handling
- [ ] Email with attachments but empty text body → still parsed and processed
  (not dropped by the line-197 guard).
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
  `parse_email_message` returning attachments (single + multiple), filename
  sanitization, size cap, malformed part, empty-body-with-attachment.
- [ ] `tests/unit/test_email_history.py` — UPDATE: assert `_record_history` blob
  and `get_recent_emails`/`search_history` projections include `attachments`;
  back-compat blob without the field hydrates as `[]`.
- [ ] `tests/integration/test_email_bridge.py` — UPDATE/REPLACE relevant inbound
  cases: full multipart email → attachments parsed, persisted to disk, exposed in
  read output and `extra_context["email_attachments"]`.
- [ ] `tests/unit/test_email_relay.py` — no change needed for inbound; existing
  `TestProcessOutboxAttachment` cases stay green (outgoing list path unchanged).
- [ ] `tests/unit/test_valor_email.py` (or the send test module) — UPDATE: add a
  multi-`--file` outgoing case asserting both files land in the outbox payload's
  `attachments` list.

No existing inbound-attachment tests exist to break — the inbound work is purely
additive. The only existing-behavior change is the line-197 empty-body guard,
covered by an updated unit test.

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

### Race 1: Metadata and session-context reads of the same attachment list
**Location:** `bridge/email_bridge.py` `_email_inbox_loop` → `_record_history`
then `_process_inbound_email`.
**Trigger:** Both functions need the attachment data for the same email.
**Data prerequisite:** `parsed["attachments"]` must be populated before either runs.
**State prerequisite:** Files must be on disk before paths are handed to the agent.
**Mitigation:** Extraction + disk persistence happens synchronously inside
`parse_email_message`, which returns before the loop calls either consumer. Single
producer, sequential consumers, same in-memory list — no race.

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
- [ ] Docstrings on `_extract_attachments` (sanitization + size-cap contract) and
  the updated `parse_email_message` / `_record_history` describing the
  `attachments` field shape.

## Success Criteria

- [ ] `parse_email_message()` returns an `attachments` list with
  `{filename, content_type, size, path}` for each inbound attachment part.
- [ ] Attachment bytes are persisted under `data/media/email-attachments/` with
  sanitized, collision-safe filenames; no path traversal possible.
- [ ] The history cache blob and `valor-email read --json` expose attachment
  metadata; messages without attachments render `attachments: []`.
- [ ] Inbound session `extra_context["email_attachments"]` carries readable paths.
- [ ] Size cap, filename sanitization, and per-part malformed-input handling are
  enforced and tested.
- [ ] Empty-body emails that carry attachments are processed, not dropped.
- [ ] Outgoing multiple `--file` flags attach multiple files (CLI fix + test).
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

### Team Members

- **Builder (inbound-parse)**
  - Name: `inbound-parse-builder`
  - Role: `_extract_attachments`, `parse_email_message` changes, empty-body guard, persistence + vault mirror
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

### 1. Inbound extraction + persistence
- **Task ID**: build-inbound-parse
- **Depends On**: none
- **Validates**: tests/unit/test_email_bridge.py, tests/integration/test_email_bridge.py
- **Assigned To**: inbound-parse-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_extract_attachments(msg, message_id)`: walk MIME, sanitize filenames, enforce `EMAIL_ATTACHMENT_MAX_TOTAL_BYTES`, write bytes to `data/media/email-attachments/{msgid_hash}/`, per-part try/except.
- Wire it into `parse_email_message`; add `attachments` to the returned dict; relax the empty-body guard for attachment-only emails.
- Add the fire-and-forget vault mirror to `~/work-vault/email-attachments/`.

### 2. History cache + read output + session context
- **Task ID**: build-history-context
- **Depends On**: build-inbound-parse
- **Validates**: tests/unit/test_email_history.py, tests/integration/test_email_bridge.py
- **Assigned To**: history-context-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `attachments` to the `_record_history` blob (metadata only); back-compat read as `[]`.
- Project `attachments` in `get_recent_emails`/`search_history`/`_hydrate`.
- Set `extra_context["email_attachments"]` in `_process_inbound_email`.

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
| Extractor exists | `grep -n "_extract_attachments" bridge/email_bridge.py` | output contains _extract_attachments |
| Multi-file outgoing | `grep -n "action=\"append\"" tools/valor_email.py` | output contains action="append" |

## Critique Results

**Verdict:** READY TO BUILD (with concerns) — 0 blockers, 5 concerns, 2 nits.
Critics: Skeptic, Operator, Archaeologist, Adversary, Simplifier, User, Consistency Auditor.
Run 2026-06-04 against baseline `ce1c852d`. Concerns C1–C5 carry implementation
notes that BUILD must honor. Nits N1–N2 are optional polish.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Operator, Adversary | **C1: `read`-time disk writes.** Persistence + vault mirror live inside `parse_email_message`, which is also called by `tools/valor_email.py:_imap_fallback_fetch` on a cache-miss `valor-email read`. A read-only CLI read would write bytes to disk and fire the vault mirror — a write side-effect on a read path, possibly double-persisting. | Task 1 (inbound-parse) | Split into `_extract_attachment_metadata(msg)` (pure: filename/content-type/size, called inside `parse_email_message`) and `_persist_attachments(parsed, message_id)` (writes bytes + vault mirror). Call `_persist_attachments` ONLY from `_email_inbox_loop` (~`bridge/email_bridge.py:1085`) after parse returns, gated on `if parsed and parsed.get("attachments")`. On the fallback read path, `path` is `None` (bytes never persisted) — document that `path` is populated only for poll-loop-ingested messages. |
| CONCERN | Skeptic, User | **C2: Read-output misses the IMAP fallback path.** Attachment exposure is scoped to `tools/email_history/` projections, but `valor-email read` falls back to `_imap_fallback_fetch` (re-parsing raw IMAP) when the cache is empty. Its result dicts are hand-shaped in the CLI, so attachments would be absent there. | Task 2 (history-context) | In `_imap_fallback_fetch`, the `results.append({...})` block must add `"attachments": parsed.get("attachments", [])`. Test BOTH cache-hit (`get_recent_emails`) and cache-miss (`_imap_fallback_fetch`) read paths project the field. |
| CONCERN | Adversary | **C3: Empty/missing `Message-ID` breaks the storage subdir key.** `message_id` can be empty (providers omit it; `_record_history` guards `if not message_id`). With an empty Message-ID, `{msgid_hash}` collapses to a constant, so attachments from ALL Message-ID-less emails collide into one subdir and overwrite across messages. | Task 1 (inbound-parse) | Compute `key = message_id or f"{from_addr}:{subject}:{int(timestamp)}"` before hashing, OR generate a `uuid4().hex` subdir when `message_id` is falsy. Distinct from Risk 3 (same-message dupes via index-suffix); this is cross-message collision. Apply the same fallback to the vault target name (see C5). |
| CONCERN | Adversary, Operator | **C4: Size cap is post-decode only.** `EMAIL_ATTACHMENT_MAX_TOTAL_BYTES` bounds persisted bytes, but each part is `get_payload(decode=True)`'d into RAM before the cap rejects it. A multipart bomb forces full base64-decode of every part; `IMAP_MAX_BATCH=20` compounds it. | Task 1 (inbound-parse) | Keep the SINGLE cumulative knob (per PM Open Q1 — no per-file cap). But make the cap short-circuit the `walk()` loop: maintain a running total, and once it would exceed `EMAIL_ATTACHMENT_MAX_TOTAL_BYTES`, stop decoding further parts (mark `truncated: true`) rather than decoding-then-skipping. Estimate from encoded `len(part.get_payload())` before `decode=True` where possible so an oversized part is rejected pre-decode. Add `EMAIL_ATTACHMENT_MAX_PARTS` count cap as a cheap bomb guard (default generous). |
| CONCERN | Archaeologist | **C5: Vault-mirror naming key must include filename + handle empty msgid.** Reusing `_ingest_attachments`' shape, two different files named `report.pdf` from two senderless emails could collide in the flat vault dir. | Task 1 (inbound-parse) | Reuse the exact Telegram formula `f"{date_part}_{safe_sender}_{msg_id}_{src.name}"`, substituting the C3 fallback for `msg_id` when Message-ID is empty. Content-hash idempotency makes a genuine duplicate overwrite the prior sidecar — accepted (matches Telegram). Gated on Open Q2 = YES (resolved). |
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
