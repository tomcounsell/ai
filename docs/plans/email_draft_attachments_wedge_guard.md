---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-06-24
tracking: https://github.com/tomcounsell/ai/issues/1775
last_comment_id:
---

# Email Draft Attachments + Attachment-Wedge Hardening (gws adoption decision)

## Problem

Valor receives and sends email through the email bridge. Two real failures motivate this work, and a third sub-question (whether to adopt `gws` as a bridge mechanism) must be answered formally so it stops resurfacing.

**Failure 1 — attachment-referencing emails can still wedge a session.** An email from a real sender asked Valor to "analyze all three attached reports." The attachments were dropped, the spawned session had nothing to act on, produced no output (`communicated=False`), and a phantom watchdog logged a heartbeat for ~4.5 days. #1567 (merged `80802492`) now *extracts and persists* inbound attachments — but the bridge still enqueues a session **unconditionally** (`bridge/email_bridge.py:1287`). If an email references attachments and **zero** files are recoverable (decode failure, size-cap rejection, or genuinely absent), the agent still receives "analyze the attached reports" with nothing attached and can wedge the same way.

**Failure 2 — there is no way to attach a file to an email *draft*.** When drafting a reply, the Gmail MCP `create_draft` tool returns "Creating drafts with attachments is not supported yet," so a polished PDF/.docx must be inlined into the body. The only outbound-attachment path that exists (`valor-email send --file`) **sends immediately** — there is no draft-with-attachment path for the human-review-before-send workflow that this repo's `google-workspace` skill mandates ("Draft-first rule: all outbound composition must produce a draft … never call a send tool without explicit user instruction to send").

**Current behavior:**
- Inbound: attachments extracted/persisted by #1567, but an attachment-referencing email that recovers no files still spawns a silent-wedge-prone session.
- Outbound send: `valor-email send --file` works (immediate send).
- Outbound draft: no attachment support anywhere (Gmail MCP can't; `valor-email` has no draft mode).

**Desired outcome:**
- An attachment-referencing email that recovers **no** files produces a session that responds gracefully (asks the sender to resend / proceeds explicitly) instead of wedging silently.
- Valor can create a **real Gmail draft with a file attached** for human review-before-send, via a single `valor-email draft` command, with a Drive-link fallback above Gmail's 25 MB inline limit.
- A documented, durable decision on whether `gws` is adopted as a *bridge* mechanism (it is **not** — inbound stays IMAP; `gws` is adopted only for the agent-invoked outbound draft path).

## Freshness Check

**Baseline commit:** `d706d8a9` (`git rev-parse HEAD` at plan time)
**Issue filed at:** 2026-06-23T09:37:35Z
**Disposition:** **Major drift (anticipated)** — the issue explicitly asked for reconciliation with #1567, which merged after filing.

**File:line references re-verified:**
- `bridge/email_bridge.py:149` (`_extract_body` skips attachment parts) — **drifted**: #1567 reworked extraction into `_extract_attachment_metadata()` (pure MIME walk) + `_persist_attachments()`. The original drop-on-floor behavior is gone for the *poll loop*.
- `bridge/email_bridge.py:1287` (`enqueue_agent_session(...)`) — **still holds**: the session is enqueued unconditionally; no fail-fast on unrecoverable attachments. This is the live wedge vector.
- `bridge/email_bridge.py:516-609` (`_build_reply_mime`) — **confirmed present**: builds `multipart/mixed` with base64-encoded attachments; reusable for the draft path.
- `tools/valor_email.py` subparsers (`read`/`send`/`threads` only; no `draft`) — **still holds**.

**Cited sibling issues/PRs re-checked:**
- **#1567** — CLOSED 2026-06-24T09:31:57Z. Shipped inbound extraction/persistence, storage (`data/media/email-attachments/{msgid_hash}/`), vault mirror, size caps (25 MiB / 50 parts), sanitization, `extra_context["email_attachments"]`, multi-`--file` send. Completed plan: `docs/plans/completed/incoming_email_attachments.md`.
- **#1630** "pre-execution prompt-injection inspection on untrusted bridge input" — **OPEN / not built**. Coordinate, do not block.
- **#1297** "worker has no Telegram client" — CLOSED. The inbound-media persistence pattern #1567 mirrored (filesystem bytes + Redis metadata).

**Commits on main since issue was filed (touching referenced files):**
- `80802492` feat(email): read incoming attachments + fix multi-file outgoing — **partially addresses** this issue (the inbound-read half). Remaining scope reconciled below.

**Active plans in `docs/plans/` overlapping this area:** none (worker-lifecycle plans are unrelated; the only attachment plan is `completed/incoming_email_attachments.md`).

**Notes:** Inbound extraction is fully shipped — do NOT rebuild it. Remaining scope is the draft-with-attachment path, the wedge guard, and the formal `gws` decision.

## Prior Art

- **#1567 / PR #1780** "Email bridge: read incoming attachments": shipped the inbound half. **Relevance: highest** — this plan reuses its `extra_context["email_attachments"]` contract and its `_build_reply_mime()` MIME builder; it must not duplicate the IMAP walk.
- **#1297** "Image/voice/document enrichment silently dropped: worker has no Telegram client": established filesystem-bytes + Redis-metadata persistence for inbound media. **Relevance: pattern source** for #1567; nothing further to do here.
- **#1630** "pre-execution prompt-injection inspection on untrusted bridge input": OPEN. **Relevance: security dependency** for the untrusted inbound attachment surface (Open Q6).
- Outbound `valor-email send --file` (#1567 N-series): repeatable `--file` send path. **Relevance: the immediate-send counterpart** that the new draft path deliberately does NOT replace.

## Research

**Queries used:**
- "Google Workspace CLI gws gmail drafts create attachment support multipart upload"
- "Gmail API users.drafts.create draft with attachment 25MB limit Drive link large file"

**Key findings:**
- The Gmail **API** fully supports drafts with attachments via `users.drafts.create` (a `Message` with a base64url-encoded `raw` RFC822 body). The "not supported yet" error is a limitation of the **MCP `create_draft` tool wrapper**, not the API — confirmed by [gemini-cli-extensions/workspace #359](https://github.com/gemini-cli-extensions/workspace/issues/359) ("Add Attachment Support to gmail.createDraft Tool") and [Gmail drafts guide](https://developers.google.com/workspace/gmail/api/guides/drafts). **Informs:** the draft path uses `gws gmail users drafts create` with a locally-built MIME message, bypassing the MCP limitation entirely.
- `gws schema gmail.users.drafts.create` (probed, no auth needed): accepts `{message: {$ref: Message}}` at `POST gmail/v1/users/{userId}/drafts`. The `Message` schema's `raw` field carries a base64url RFC822 message. **Informs:** we serialize `_build_reply_mime(...).as_bytes()` → `base64.urlsafe_b64encode` → `message.raw`.
- Gmail's 25 MB inline attachment limit ([uploads guide](https://developers.google.com/workspace/gmail/api/guides/uploads)): files above it are uploaded to Drive and shared as a link. **Informs:** Open Q4 resolution — inline ≤ 25 MB, Drive-link fallback above.

## Spike Results

### spike-1: Does `gws gmail users drafts create` support attachments at the API layer?
- **Assumption:** "The Gmail draft-with-attachment limitation is a tool-wrapper gap, not an API gap, so `gws` can create real drafts with attachments."
- **Method:** web-research + `gws schema gmail.users.drafts.create` (non-interactive, no auth)
- **Finding:** Confirmed. The API takes a raw RFC822 `Message`; attachments are encoded in the MIME body. MCP `create_draft` is the only layer that rejects attachments.
- **Confidence:** high
- **Impact on plan:** Draft path is `gws gmail users drafts create` with a locally-built MIME message; no dependence on the MCP wrapper.

### spike-2: Is the relay's MIME builder reusable for the draft path?
- **Assumption:** "`_build_reply_mime()` can build the draft MIME so we don't hand-roll a second MIME encoder."
- **Method:** code-read (`bridge/email_bridge.py:516-609`)
- **Finding:** Confirmed. It returns a `MIMEText` (no attachments) or `MIMEMultipart('mixed')` with base64-encoded parts and full RFC threading headers. Reusable as-is; the draft path serializes its output into `message.raw`.
- **Confidence:** high
- **Impact on plan:** One MIME builder, two consumers (relay send + draft create). No duplicate encoder.

### spike-3: Does `gws` auth work headless?
- **Assumption:** "`gws` can run unattended from the bridge."
- **Method:** issue evidence + `CLAUDE.md` tool-ladder; live probe deferred (interactive OAuth risk).
- **Finding:** `gws` returns `invalid_grant` and re-auth needs interactive `gws auth login`. The headless bridge cannot perform OAuth.
- **Confidence:** high
- **Impact on plan:** The bridge NEVER calls `gws`. The draft command is **agent/operator-invoked** (a Bash-tool surface), runs where auth can exist, and fails fast with an actionable message when `gws` is unauthenticated. Inbound stays IMAP. (Resolves Open Q1/Q2.)

## Data Flow

**Wedge guard (inbound):**
1. **Entry point:** IMAP poll loop receives a message → `parse_email_message()` → `_extract_attachment_metadata()` → `_persist_attachments()`.
2. **`_process_inbound_email()`** builds `extra_context`, including `email_attachments` (only persisted paths).
3. **New guard (before line 1287):** if the body references attachments (regex heuristic) AND `email_attachments` is empty → set `extra_context["attachments_unrecoverable"] = True` and `extra_context["attachments_referenced_count"]` so the agent is told plainly the files didn't arrive.
4. **`enqueue_agent_session(...)`** spawns the session, which now has explicit context to respond ("the sender referenced N files but none arrived — ask them to resend") instead of wedging on a no-op.
5. **Output:** a graceful reply / clarifying request, not a 4.5-day phantom.

**Draft with attachment (outbound):**
1. **Entry point:** agent or operator runs `valor-email draft --to X --subject Y --file report.pdf "body"`.
2. **`cmd_draft()`** validates recipients + each file (existence, readability, total size).
3. **Size routing:** files whose combined size ≤ 25 MiB are attached inline; any file above the per-file inline limit is uploaded via `gws drive files create --upload` and a share link is appended to the body.
4. **MIME build:** reuse `_build_reply_mime(...)`; serialize to bytes → `base64.urlsafe_b64encode` → `message.raw`.
5. **Draft create:** `gws gmail users drafts create --json '{"message":{"raw":"…"}}'`. On `invalid_grant` / non-zero exit → fail fast with an actionable error (run `gws auth login`; or use `valor-email send --file` to send immediately).
6. **Output:** a real Gmail draft visible in the user's Drafts folder for review-before-send.

## Architectural Impact

- **New dependencies:** `gws` becomes a *runtime* dependency of the new `valor-email draft` path only (agent/operator surface, not the bridge). Already on PATH per `CLAUDE.md`.
- **Interface changes:** new `valor-email draft` subcommand; new `extra_context` keys (`attachments_unrecoverable`, `attachments_referenced_count`) — additive, healed generically by `AgentSession` field handling (cf. #1099/#1172).
- **Coupling:** the draft path reuses `_build_reply_mime()` (shared with the relay), increasing intentional reuse, not coupling. The bridge gains no `gws` dependency.
- **Data ownership:** unchanged. Inbound bytes remain owned by the #1567 persistence layer; drafts live in Gmail (Google-owned), not in our Redis/outbox.
- **Reversibility:** high. The draft subcommand is additive; the wedge guard is a single pre-enqueue branch that defaults to current behavior when no attachment reference is detected.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm draft-path product shape; confirm wedge-guard policy = inform-agent vs hard-block)
- Review rounds: 1 (code review + PR review gate)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `gws` on PATH | `command -v gws` | Draft path shells to `gws` |
| Redis reachable | `python -c "import redis,os; redis.Redis.from_url(os.environ.get('REDIS_URL','redis://localhost:6379/0')).ping()"` | Wedge-guard tests touch the session/enqueue path |
| `gws` authenticated (runtime-only, EXTERNAL) | `gws gmail users getProfile --params '{"userId":"me"}'` | Live draft creation; **not** required for unit tests (gws is mocked) |

Run all checks: `python scripts/check_prerequisites.py docs/plans/email_draft_attachments_wedge_guard.md`

## Solution

### Key Elements

- **Wedge guard (`bridge/email_bridge.py`)**: a pre-enqueue detection that an attachment-referencing email recovered zero files, surfaced to the agent via `extra_context` so it responds instead of wedging.
- **`valor-email draft` subcommand (`tools/valor_email.py`)**: creates a real Gmail draft with attachments via `gws gmail users drafts create`, reusing `_build_reply_mime()`, with Drive-link fallback above 25 MiB.
- **gws adoption decision (docs)**: a durable, written decision — inbound stays IMAP (do not route inbound through `gws`); `gws` is adopted only for the agent-invoked outbound draft path; auth ladder documented.

### Flow

**Inbound wedge guard:**
Email arrives → attachments extracted/persisted (#1567) → guard checks "body references attachments AND none recovered" → if true, tag `extra_context` → session spawned with explicit "attachments didn't arrive" context → agent asks sender to resend (no wedge).

**Outbound draft:**
Agent composes reply → `valor-email draft --to … --file report.pdf "body"` → MIME built (inline ≤25 MiB, Drive-link above) → `gws gmail users drafts create` → real Gmail draft → human reviews in Gmail and sends.

### Technical Approach

- **Wedge guard** (resolves Open Q5): add `_body_references_attachments(text) -> bool` (a small, conservative regex over a fixed phrase set: "attached", "attachment", "see attached", "enclosed", "find attached" — anchored to avoid over-matching). In `_process_inbound_email`, after building `email_attachments`, if the body references attachments and `email_attachments` is empty, set `extra_context["attachments_unrecoverable"] = True` and `extra_context["attachments_referenced_count"] = <regex hit count or 1>`. The session prompt/persona consumes these to respond gracefully. **Policy choice for PM:** default is *inform the agent* (spawn with context) rather than *hard-block* (refuse to spawn) — informing preserves the email and lets the agent ask the sender to resend; hard-block risks dropping legitimate mail whose body merely says "attached" colloquially. (Open Question 1 below.)
- **Draft subcommand** (resolves Open Q4): `cmd_draft(args)` mirrors `cmd_send` validation, then builds MIME via `_build_reply_mime()`, serializes to `message.raw` (base64url), and runs `gws gmail users drafts create`. Per-file size routing: inline if total ≤ `EMAIL_ATTACHMENT_MAX_TOTAL_BYTES` (reuse the #1567 constant, 25 MiB); otherwise upload via `gws drive files create --upload` and append the returned `webViewLink` to the body. **Degradation (resolves Open Q2):** detect `gws` non-zero exit / `invalid_grant` in stderr and emit a single actionable error (`gws auth login` needed; or `valor-email send --file` to send now) with a non-zero exit code. No silent fallback to immediate send.
- **gws adoption decision** (resolves Open Q1): inbound stays on the merged IMAP walk — routing inbound through `gws` would duplicate IMAP polling, fight the history cache, and is impossible headless (broken auth). Document this in `docs/features/email-bridge.md` and a short decision note. `gws` is adopted **only** for the outbound draft path.
- **Security** (Open Q6): the wedge-guard directive text is a **static template** — it must NOT echo untrusted body text or unsanitized filenames (filenames from #1567 are already sanitized; the count is an integer). Note the #1630 dependency for full pre-execution inspection; do not build a competing inspector here.
- **Integration point:** `valor-email draft` is a new `[project.scripts]`-reachable subcommand (the CLI is already `valor-email`), so the agent reaches it via the Bash tool with no new MCP wiring.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `gws` subprocess call in `cmd_draft` must NOT `except Exception: pass` — every failure path returns a non-zero exit with a stderr message. Test asserts the actionable error text and exit code on simulated `invalid_grant`.
- [ ] The wedge-guard regex helper is pure and total (no exceptions on empty/None/odd input); test covers empty string and None.
- [ ] Drive-upload fallback failure (gws drive create non-zero) returns a clear error rather than producing a draft with a dangling/empty link.

### Empty/Invalid Input Handling
- [ ] `valor-email draft` with no body and no `--file` → error (mirror `cmd_send`).
- [ ] `_body_references_attachments("")` and `(None)` → `False`, no crash.
- [ ] Wedge guard: body references attachments + `email_attachments` empty → tags set; body references attachments + files present → tags absent; body with no reference + no files → tags absent (no false positive).

### Error State Rendering
- [ ] `valor-email draft` surfaces the `gws` auth error to stderr (operator-visible), not swallowed.
- [ ] The wedge-guard context flows into the spawned session so the agent's reply (the user-visible output) reflects "attachments didn't arrive," verified in an integration test on the spawn path.

## Test Impact

- [ ] `tests/unit/test_valor_email.py` — UPDATE: add `cmd_draft` coverage (validation, MIME→raw serialization, size routing, gws invocation mocked, auth-failure exit). Existing `send`/`read` tests unchanged.
- [ ] `tests/unit/test_email_bridge.py` — UPDATE: add wedge-guard tests (`_body_references_attachments` truth table; `extra_context` tagging in `_process_inbound_email` with/without recovered files). Existing extraction/persistence tests unchanged.
- [ ] `tests/integration/test_email_bridge.py` — UPDATE: add an inbound flow where an attachment-referencing email recovers zero files and assert `attachments_unrecoverable` reaches `extra_context` on the enqueued session. Existing single/multi-attachment flows unchanged.
- [ ] No existing test asserts "no draft subcommand exists," so nothing to DELETE/REPLACE — changes are additive.

## Rabbit Holes

- **Re-routing inbound through `gws`.** Tempting ("adopt gws everywhere") but duplicates IMAP polling, fights the history cache, and is impossible headless. Inbound stays IMAP — this is a documented decision, not a code change.
- **Building a local Redis "draft hold queue" + review UI.** A real Gmail draft (visible in the user's Drafts folder) is the review surface; do not build a parallel review system in Redis/outbox.
- **A general-purpose prompt-injection inspector.** That is #1630. Here we only ensure our injected directive is a static template; full inspection is out of scope.
- **NLP-grade attachment-reference detection.** A small conservative regex over a fixed phrase set is enough; do not train/import a classifier.
- **Drive-link sharing-permission management.** Use the default share behavior of `gws drive files create`; do not build ACL management.

## Risks

### Risk 1: `gws` unauthenticated at runtime makes live draft creation fail
**Impact:** `valor-email draft` cannot create the Gmail draft on a machine where `gws auth login` hasn't been run.
**Mitigation:** Fail fast with an actionable message; unit tests mock `gws` so the code path is fully covered regardless of live auth. Live verification is an `[EXTERNAL]` step.

### Risk 2: Wedge-guard false positives drop or misroute legitimate mail
**Impact:** An email whose body says "attached" colloquially but carries no real attachment gets the "unrecoverable" tag.
**Mitigation:** Default policy is *inform the agent*, not *hard-block* — the email is still processed; the agent uses judgment. The regex is conservative and unit-tested against a truth table.

### Risk 3: Untrusted inbound content reaches the agent via the new context
**Impact:** Prompt-injection surface (Open Q6).
**Mitigation:** The injected directive is a static template (no untrusted echo); filenames are already sanitized by #1567; the referenced-count is an integer. Full inspection deferred to #1630 (dependency noted, not blocking).

## Race Conditions

### Race 1: File deleted between `valor-email draft` validation and MIME read
**Location:** `tools/valor_email.py` `cmd_draft` (file validation → MIME build).
**Trigger:** A file passed to `--file` is removed after the existence check but before bytes are read.
**Data prerequisite:** The file must exist and be readable at MIME-build time.
**State prerequisite:** None cross-process.
**Mitigation:** Read bytes immediately after validation within the same call (no enqueue/drain gap as the relay has); a read failure returns a clear error and non-zero exit. Unlike the relay's enqueue→drain window, draft creation is synchronous, so the window is negligible.

## No-Gos (Out of Scope)

- `[SEPARATE-SLUG #1630]` Pre-execution prompt-injection inspection of untrusted inbound attachment content — owned by #1630; this plan only guarantees its own injected directive is a static template.
- `[EXTERNAL]` Running `gws auth login` to restore Gmail OAuth on each machine — interactive OAuth the agent cannot perform; required only for *live* draft creation, not for shipping/testing the code.
- `[SEPARATE-SLUG #1567]` Inbound attachment extraction/persistence — already shipped; not rebuilt here.

## Update System

- **No update-script changes required for the code.** `gws` is already installed on every machine by `/update` (per `CLAUDE.md`), and the new `valor-email draft` subcommand ships inside the existing `valor-email` entry point — no new `[project.scripts]` line, no new dependency to propagate.
- **Operator note (docs only):** machines that will create live drafts need a one-time `gws auth login`. This is documented in `docs/features/email-bridge.md`, not enforced by the update script (it is interactive and machine-specific).

## Agent Integration

- **CLI surface:** `valor-email draft` is added to the existing `valor-email` CLI (`tools/valor_email.py`), already declared in `pyproject.toml [project.scripts]`. The agent invokes it via the Bash tool — **no new MCP server and no `.mcp.json` change**.
- **Bridge import:** the wedge guard lives inside `bridge/email_bridge.py`'s existing `_process_inbound_email` path; no new cross-module wiring beyond the local helper.
- **Integration test:** `tests/integration/test_email_bridge.py` verifies the wedge-guard context reaches the enqueued session; a unit test verifies `valor-email draft` shells to `gws` with a correctly base64url-encoded `message.raw` (gws mocked).
- The `google-workspace` skill already documents `gws gmail users drafts create` as the draft mechanism; we add a pointer to `valor-email draft` as the attachment-capable convenience wrapper.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/email-bridge.md`: add a "Draft with attachment" subsection under the CLI section documenting `valor-email draft`, the 25 MiB inline / Drive-link behavior, and the `gws auth` requirement; add a "Attachment-wedge guard" note under inbound handling; add the **gws adoption decision** under Design Decisions (inbound = IMAP, outbound draft = gws).
- [ ] Update `CLAUDE.md` "Reading Email" / `valor-email` usage block to list the new `draft` subcommand.

### External Documentation Site
- [ ] Not applicable — this repo has no Sphinx/MkDocs site; `docs/features/` is the canonical surface.

### Inline Documentation
- [ ] Docstring on `cmd_draft` covering the gws path, size routing, and degradation.
- [ ] Comment on the wedge-guard branch explaining the inform-vs-block policy and the #1630 dependency.

## Success Criteria

- [ ] `valor-email draft --to X --subject Y --file <file> "body"` creates a real Gmail draft with the file attached (verified live where gws is authed; unit-verified via mocked gws everywhere).
- [ ] Files above 25 MiB are uploaded to Drive and a share link is appended to the draft body instead of inlining.
- [ ] `valor-email draft` fails fast with an actionable message (not a swallowed exception) when `gws` is unauthenticated.
- [ ] An attachment-referencing email recovering zero files spawns a session whose `extra_context` carries `attachments_unrecoverable=True`, and the agent's reply asks the sender to resend rather than producing no output.
- [ ] An attachment-referencing email **with** recovered files, and a non-referencing email, do NOT get the tag (no false positives).
- [ ] The gws adoption decision is documented in `docs/features/email-bridge.md`.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] grep confirms `tools/valor_email.py` references `gws gmail users drafts create` and `bridge/email_bridge.py` references `attachments_unrecoverable`.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools and never builds directly.

### Team Members

- **Builder (draft-cli)**
  - Name: draft-cli-builder
  - Role: Implement `valor-email draft` + size routing + gws invocation + degradation
  - Agent Type: builder
  - Resume: true

- **Builder (wedge-guard)**
  - Name: wedge-guard-builder
  - Role: Implement `_body_references_attachments` + `extra_context` tagging in `_process_inbound_email`
  - Agent Type: builder
  - Resume: true

- **Validator (email-feature)**
  - Name: email-validator
  - Role: Verify both deliverables against success criteria, run scoped tests
  - Agent Type: validator
  - Resume: true

- **Documentarian (email-docs)**
  - Name: email-documentarian
  - Role: Update `docs/features/email-bridge.md` + `CLAUDE.md`
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

(Standard tiers as per template — builder/validator/documentarian used here.)

## Step by Step Tasks

### 1. Wedge guard
- **Task ID**: build-wedge-guard
- **Depends On**: none
- **Validates**: tests/unit/test_email_bridge.py, tests/integration/test_email_bridge.py
- **Informed By**: spike-2 (MIME builder reuse not needed here), Data Flow (inbound)
- **Assigned To**: wedge-guard-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_body_references_attachments(text) -> bool` (conservative regex, total over empty/None).
- In `_process_inbound_email`, after building `email_attachments`, tag `extra_context["attachments_unrecoverable"]` + `["attachments_referenced_count"]` when referenced-but-empty.
- Keep the directive static (no untrusted echo) — Open Q6 safety.

### 2. Draft subcommand
- **Task ID**: build-draft-cli
- **Depends On**: none
- **Validates**: tests/unit/test_valor_email.py
- **Informed By**: spike-1 (gws drafts API), spike-2 (reuse `_build_reply_mime`), spike-3 (auth degradation)
- **Assigned To**: draft-cli-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `cmd_draft` + `draft` subparser (`--to`, `--subject`, `--file` repeatable, `--reply-to`, `--json`).
- Build MIME via `_build_reply_mime()`; serialize to `message.raw` (base64url); call `gws gmail users drafts create`.
- Size routing: inline ≤ 25 MiB; Drive-upload + link append above.
- Degradation: detect `invalid_grant`/non-zero gws exit → actionable error + non-zero exit.

### 3. Validate
- **Task ID**: validate-email-feature
- **Depends On**: build-wedge-guard, build-draft-cli
- **Assigned To**: email-validator
- **Agent Type**: validator
- **Parallel**: false
- Run scoped unit + integration tests; verify success criteria and grep checks.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-wedge-guard, build-draft-cli
- **Assigned To**: email-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/email-bridge.md` (draft subsection, wedge-guard note, gws decision) + `CLAUDE.md`.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: validate-email-feature, document-feature
- **Assigned To**: email-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification commands; confirm all success criteria including docs.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Draft unit tests pass | `pytest tests/unit/test_valor_email.py -q` | exit code 0 |
| Wedge-guard unit tests pass | `pytest tests/unit/test_email_bridge.py -q` | exit code 0 |
| Inbound integration tests pass | `pytest tests/integration/test_email_bridge.py -q` | exit code 0 |
| Format clean | `python -m ruff format --check tools/valor_email.py bridge/email_bridge.py` | exit code 0 |
| Draft subcommand wired | `grep -c "drafts create" tools/valor_email.py` | output > 0 |
| Wedge tag present | `grep -c "attachments_unrecoverable" bridge/email_bridge.py` | output > 0 |
| gws decision documented | `grep -c "adoption" docs/features/email-bridge.md` | output > 0 |
| No competing injection inspector | `grep -rc "prompt.injection" tools/valor_email.py` | match count == 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Wedge-guard policy — inform vs hard-block?** Default in this plan is *inform the agent* (spawn the session with `attachments_unrecoverable` context so it asks the sender to resend). The alternative is *hard-block* (refuse to spawn and auto-reply "your attachments didn't arrive"). Inform preserves the email and uses agent judgment; hard-block is more deterministic but risks dropping mail whose body only colloquially says "attached." Which posture do you want?
2. **Draft surface — real Gmail draft only, or also a `--send-link` Drive convenience?** This plan creates a real Gmail draft via `gws` (review-in-Gmail). Should it also support a "draft as Drive link in body" mode independent of the 25 MiB threshold (e.g., always link, never inline), or is size-based routing sufficient?
