---
status: Ready
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-06-24
tracking: https://github.com/tomcounsell/ai/issues/1775
last_comment_id: 4787902324
revision_applied: true
---

# Email Draft Attachments + Attachment-Wedge Hardening (gws adoption decision)

## Problem

Valor receives and sends email through the email bridge. Two real failures motivate this work, and a third sub-question (whether to adopt `gws` as a bridge mechanism) must be answered formally so it stops resurfacing.

**Failure 1 — attachment-referencing emails can still wedge a session.** An email from a real sender asked Valor to "analyze all three attached reports." The attachments were dropped, the spawned session had nothing to act on, produced no output (`communicated=False`), and a phantom watchdog logged a heartbeat for ~4.5 days. #1567 (merged `80802492`) now *extracts and persists* inbound attachments — but the bridge still enqueues a session **unconditionally** (`bridge/email_bridge.py:1287`). The wedge has **two** shapes:

- **Zero-recovery:** an email references attachments and **zero** files are recoverable (every part fails to decode, the size cap rejects all, or the files are genuinely absent). `extra_context["email_attachments"]` ends up empty and the agent receives "analyze the attached reports" with nothing attached.
- **Partial-recovery (truncation):** #1567's `_extract_attachment_metadata()` sets `parsed["attachments_truncated"] = True` and **stops the walk** when the 25 MiB cumulative cap or the 50-part cap is hit (`bridge/email_bridge.py:302-322`), and **separately** skips any individual undecodable part with a bare `continue` (`bridge/email_bridge.py:315-317`, `335-337`) **without** setting `truncated`. Either path leaves a **non-empty** `email_attachments` list that is missing files the body references — the body says "all five reports," only two decoded. An emptiness-only guard never trips here, yet this is exactly the wedge the feature targets: the agent confidently acts on a partial set or stalls looking for the rest.

A guard keyed on emptiness alone is therefore insufficient. The guard must fire when the body references attachments AND (`email_attachments` is empty **OR** `parsed["attachments_truncated"]` is True). The truncation field name is verified against the merged #1567 / PR #1780 code — `parsed["attachments_truncated"]`, set in `parse_email_message` at `bridge/email_bridge.py:507` and already surfaced to history at `:662`.

**Failure 2 — there is no way to attach a file to an email *draft*.** When drafting a reply, the Gmail MCP `create_draft` tool returns "Creating drafts with attachments is not supported yet," so a polished PDF/.docx must be inlined into the body. The only outbound-attachment path that exists (`valor-email send --file`) **sends immediately** — there is no draft-with-attachment path for the human-review-before-send workflow that this repo's `google-workspace` skill mandates ("Draft-first rule: all outbound composition must produce a draft … never call a send tool without explicit user instruction to send").

**Current behavior:**
- Inbound: attachments extracted/persisted by #1567, but an attachment-referencing email that recovers no files still spawns a silent-wedge-prone session.
- Outbound send: `valor-email send --file` works (immediate send).
- Outbound draft: no attachment support anywhere (Gmail MCP can't; `valor-email` has no draft mode).

**Desired outcome:**
- An attachment-referencing email that recovers **no** files — **or only some** of them (truncation / partial-decode) — produces a session that responds gracefully (asks the sender to resend / proceeds explicitly) instead of wedging silently.
- Valor can create a **real Gmail draft with a file attached** for human review-before-send, via a single `valor-email draft` command, with a Drive-link fallback above Gmail's 25 MB inline limit.
- A documented, durable decision on whether `gws` is adopted as a *bridge* mechanism (it is **not** — inbound stays IMAP; `gws` is adopted only for the agent-invoked outbound draft path).

## Freshness Check

**Baseline commit:** `d706d8a9` (`git rev-parse HEAD` at plan time)
**Issue filed at:** 2026-06-23T09:37:35Z
**Disposition:** **Major drift (anticipated)** — the issue explicitly asked for reconciliation with #1567, which merged after filing.

**File:line references re-verified:**
- `bridge/email_bridge.py:149` (`_extract_body` skips attachment parts) — **drifted**: #1567 reworked extraction into `_extract_attachment_metadata()` (pure MIME walk) + `_persist_attachments()`. The original drop-on-floor behavior is gone for the *poll loop*.
- `bridge/email_bridge.py:1287` (`enqueue_agent_session(...)`) — **still holds**: the session is enqueued unconditionally; no fail-fast on unrecoverable attachments. This is the live wedge vector.
- `bridge/email_bridge.py:507` (`parsed["attachments_truncated"]` populated in `parse_email_message`) — **confirmed present** (re-verified for this revision). Also surfaced to history at `:662`. This is the field the guard keys on for the partial-recovery (truncation) case.
- `bridge/email_bridge.py:302-322` (cap-driven truncation sets `truncated=True` and `break`s) and `:315-317`, `:335-337` (per-part undecodable/malformed → bare `continue`, does NOT set `truncated`) — **confirmed present**. The second path is a *silent* partial drop: a non-empty list with `attachments_truncated=False`. The guard's emptiness check covers the all-parts-skipped variant; a build-time follow-up (Task 1) sets `truncated=True` on the cap path coverage and documents the per-part-skip residual.
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
3. **New guard (before line 1287):** if the body references attachments (regex heuristic) AND (`email_attachments` is empty **OR** `parsed.get("attachments_truncated")` is True) → set `extra_context["attachments_unrecoverable"] = True`, `extra_context["attachments_truncated"] = bool(parsed.get("attachments_truncated"))`, `extra_context["attachments_recovered_count"] = len(email_attachments)`, and `extra_context["attachments_referenced_count"]` so the agent is told plainly whether **none** or **only some** of the referenced files arrived.
4. **`enqueue_agent_session(...)`** spawns the session, which now has explicit context to respond ("the sender referenced N files; M arrived, the rest were too large / failed to decode — ask them to resend") instead of wedging on a no-op or acting on a partial set.
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

- **Wedge guard** (resolves Open Q5): add `_body_references_attachments(text) -> bool` (a small, conservative regex over a fixed phrase set: "attached", "attachment", "see attached", "enclosed", "find attached" — anchored to avoid over-matching). In `_process_inbound_email`, after building `email_attachments`, the guard fires when the body references attachments AND **either** `email_attachments` is empty **OR** `parsed.get("attachments_truncated")` is truthy. The truncation arm is the critical addition: #1567 marks a message truncated when the 25 MiB / 50-part cap trips (`bridge/email_bridge.py:302-322`), leaving a **non-empty** list that is still missing referenced files — an emptiness-only guard misses the exact partial-recovery wedge this feature targets. On fire, set `extra_context["attachments_unrecoverable"] = True`, `extra_context["attachments_truncated"] = bool(parsed.get("attachments_truncated"))`, `extra_context["attachments_recovered_count"] = len(email_attachments)`, and `extra_context["attachments_referenced_count"] = <regex hit count or 1>`. The session prompt/persona consumes these to respond gracefully (distinguishing "none arrived" from "only M of N arrived"). Note the residual per-part silent-skip path (`:315-317`, `:335-337` `continue` without setting `truncated`): the emptiness arm catches the all-parts-skipped case; a partial silent skip (some decode, some don't, no cap hit) is a narrower residual that the build should at minimum surface by setting `truncated=True` when any part is skipped — verify and tighten in Task 1. **Policy choice for PM:** default is *inform the agent* (spawn with context) rather than *hard-block* (refuse to spawn) — informing preserves the email and lets the agent ask the sender to resend; hard-block risks dropping legitimate mail whose body merely says "attached" colloquially. (Open Question 1 below.)
- **Draft subcommand** (resolves Open Q4): `cmd_draft(args)` mirrors `cmd_send` validation exactly (same empty-body/`--file` guard, same per-file existence + readability check, same `to_addrs` comma-flatten, same `_build_session_id()`), then builds MIME via `_build_reply_mime()`, serializes to `message.raw` (base64url), and runs `gws gmail users drafts create`.
  - **MIME-build alignment with the relay (addresses critique concern: CLI MIME-build divergence).** `cmd_send` does NOT build MIME locally — it enqueues an outbox payload that the **relay** (`bridge/email_relay.py:187`) turns into MIME via the single shared `_build_reply_mime()` and ships with `mime_msg.as_string()`. The draft path has no relay hop (it is synchronous), so it calls `_build_reply_mime()` **directly in `cmd_draft`** and serializes the *same* builder's output with `.as_bytes()` → `base64.urlsafe_b64encode`. There is exactly **one** MIME builder in the codebase; the draft path is a second *consumer* of it, not a second *encoder*. The plan does NOT hand-roll `MIMEMultipart` in `valor_email.py`. This keeps the draft, the relay send, and the inbound reply path all funnelled through `_build_reply_mime()` — divergence is structurally prevented.
  - **Promise gate (addresses critique concern: omitted `cli_check_or_exit`).** `cmd_draft` calls `cli_check_or_exit(body, transport="email", session_id=session_id)` at the same point `cmd_send` does (`tools/valor_email.py:395`), BEFORE shelling to `gws`. A Gmail draft is outbound composition the human will review and send; it belongs behind the same promise-gate audit surface as `send`. (The gate routes synthetic `cli-{epoch}` session IDs to the audit JSONL — see `docs/features/promise-gate.md`.)
  - **Size routing:** inline if total ≤ `EMAIL_DRAFT_INLINE_MAX_TOTAL_BYTES` — a **new, draft-specific** constant defaulting to 25 MiB (Gmail's inline cap), env-overridable, with a "provisional / tunable" comment. See "Constant separation" below for why this is NOT the #1567 inbound cap. Above the threshold: upload via `gws drive files create --upload` and append the returned `webViewLink` to the body.
  - **Degradation (resolves Open Q2):** detect `gws` non-zero exit / `invalid_grant` in stderr and emit a single actionable error (`gws auth login` needed; or `valor-email send --file` to send now) with a non-zero exit code. No silent fallback to immediate send.
  - **`send --file` error-path reuse (addresses critique concern: duplicated error path).** The per-file existence/readability validation is **factored into a shared helper** `_validate_attachment_files(files) -> list[str] | None` (returns resolved absolute paths, or `None` after printing the error). `cmd_send` is refactored to call it too, so the "File not found" / "Cannot read file" error path lives in **one** place, not copy-pasted into `cmd_draft`. This is a small, in-scope refactor of `cmd_send` (no behavior change; covered by existing `cmd_send` tests).
- **Constant separation: inbound cap vs. outbound inline threshold (addresses critique concern: reused constant).** The earlier draft reused #1567's `EMAIL_ATTACHMENT_MAX_TOTAL_BYTES` for outbound inline routing. That coupling is **rejected as unintentional**: the inbound constant is a *defensive ceiling* on untrusted bytes we accept and decode into RAM (a security/DoS knob — could legitimately be lowered to, say, 10 MiB without touching outbound behavior). The outbound inline threshold is a *Gmail product limit* (25 MiB inline, Drive above) that we do not control. Tying them means a future security-driven reduction of the inbound cap would silently start pushing 11 MiB outbound attachments to Drive — a surprising, wrong coupling. The plan introduces a **separate** `EMAIL_DRAFT_INLINE_MAX_TOTAL_BYTES` constant (named, env-overridable, provisional-comment per the magic-numbers convention), defaulting to 25 MiB. The two constants happen to share a default today; they are conceptually independent and must not be aliased.
- **Drive-upload orphan risk (addresses critique concern: non-atomic Drive upload).** The Drive-link fallback is a two-step, non-atomic sequence: (1) `gws drive files create --upload` then (2) `gws gmail users drafts create` with the link in the body. If step 2 fails, the uploaded Drive file is **orphaned** (a stray file in the user's Drive with no referencing draft). Mitigations, in order of preference:
  - **Order of operations:** upload to Drive **first**, build the body with the link, then create the draft. If the draft create fails, attempt a **best-effort cleanup** — `gws drive files delete --params '{"fileId": "<id>"}'` — and report both the draft failure AND whether cleanup succeeded. If cleanup also fails, the error message names the orphaned `fileId` so the operator can remove it manually. No silent orphan.
  - **No partial draft:** never create a draft that references a Drive file that failed to upload — the upload must return a usable `webViewLink` before the draft body is finalized.
  - This keeps the failure observable and recoverable; full transactional atomicity across two Google APIs is not achievable and is explicitly out of scope (see No-Gos).
- **gws adoption decision** (resolves Open Q1): inbound stays on the merged IMAP walk — routing inbound through `gws` would duplicate IMAP polling, fight the history cache, and is impossible headless (broken auth). Document this in `docs/features/email-bridge.md` and a short decision note. `gws` is adopted **only** for the outbound draft path.
- **Security** (Open Q6): the wedge-guard directive text is a **static template** — it must NOT echo untrusted body text or unsanitized filenames (filenames from #1567 are already sanitized; the count is an integer). Note the #1630 dependency for full pre-execution inspection; do not build a competing inspector here.
- **Integration point:** `valor-email draft` is a new `[project.scripts]`-reachable subcommand (the CLI is already `valor-email`), so the agent reaches it via the Bash tool with no new MCP wiring.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `gws` subprocess call in `cmd_draft` must NOT `except Exception: pass` — every failure path returns a non-zero exit with a stderr message. Test asserts the actionable error text and exit code on simulated `invalid_grant`.
- [ ] The wedge-guard regex helper is pure and total (no exceptions on empty/None/odd input); test covers empty string and None.
- [ ] Drive-upload fallback failure (gws drive create non-zero) returns a clear error rather than producing a draft with a dangling/empty link.
- [ ] Drive-upload **success** followed by draft-create **failure** triggers best-effort cleanup; the test asserts the command either confirms deletion or names the orphaned `fileId` (never silently orphans).

### Empty/Invalid Input Handling
- [ ] `valor-email draft` with no body and no `--file` → error (mirror `cmd_send`).
- [ ] `_body_references_attachments("")` and `(None)` → `False`, no crash.
- [ ] Wedge guard truth matrix: (a) references + empty → tagged; (b) references + non-empty + truncated → tagged with `truncated=True`; (c) references + non-empty + not truncated → tags absent; (d) no reference → tags absent (no false positive).

### Error State Rendering
- [ ] `valor-email draft` surfaces the `gws` auth error to stderr (operator-visible), not swallowed.
- [ ] The wedge-guard context flows into the spawned session so the agent's reply (the user-visible output) reflects "attachments didn't arrive," verified in an integration test on the spawn path.

## Test Impact

- [ ] `tests/unit/test_valor_email.py` — UPDATE: add `cmd_draft` coverage (validation via shared helper, MIME→raw base64url round-trip, size routing with the new `EMAIL_DRAFT_INLINE_MAX_TOTAL_BYTES` constant, gws invocation mocked, auth-failure non-zero exit + remedy text, Drive-upload-then-draft-fail orphan-cleanup path, promise-gate `cli_check_or_exit` called). Add one test asserting `cmd_send` and `cmd_draft` emit identical bad-`--file` error text (shared helper). Existing `send`/`read` tests: `cmd_send` is refactored to call `_validate_attachment_files` — UPDATE the existing `cmd_send` bad-file tests only if they assert exact internals; the stderr text is unchanged so they should pass as-is.
- [ ] `tests/unit/test_email_bridge.py` — UPDATE: add wedge-guard tests. `_body_references_attachments` truth table. `extra_context` tagging in `_process_inbound_email` across the full matrix: (a) referenced + empty list → tagged, `recovered_count=0`; (b) **referenced + non-empty + `attachments_truncated=True` → tagged, `truncated=True`, `recovered_count=M` (THE BLOCKER REGRESSION TEST)**; (c) referenced + non-empty + not truncated → NOT tagged; (d) not referenced → NOT tagged.
- [ ] `tests/integration/test_email_bridge.py` — UPDATE: add an inbound flow where an attachment-referencing email recovers zero files AND a second flow where it recovers a truncated/partial set; assert `attachments_unrecoverable` (and `attachments_truncated` for the partial case) reach `extra_context` on the enqueued session. Existing single/multi-attachment flows unchanged.
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

### Risk 3: Drive upload orphaned when draft create fails
**Impact:** A file uploaded to Drive but never referenced by a created draft is a stray file in the user's Drive.
**Mitigation:** Upload-first ordering + best-effort `gws drive files delete` cleanup on draft-create failure; if cleanup fails, the error names the orphaned `fileId`. Non-atomicity is acknowledged and explicitly out of scope for full transactional handling (No-Gos).

### Risk 4: Untrusted inbound content reaches the agent via the new context
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
- `[OUT-OF-SCOPE]` Transactional atomicity across the Drive-upload + draft-create pair. Two independent Google APIs cannot be made atomic; the plan handles the failure window with best-effort cleanup + observable orphan reporting (see Technical Approach), not a distributed transaction.

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

Each criterion states the **observable outcome** (what a human or test can witness), not just the mechanism.

### Outbound draft
- [ ] **Observable:** after `valor-email draft --to X --subject Y --file <file> "body"`, the file **appears in the user's Gmail Drafts folder** as a real draft with the attachment present and openable (verified live where gws is authed via `gws gmail users drafts list` showing the new draft ID; unit-verified via mocked gws asserting a correctly base64url-decoded `message.raw` round-trips to a `multipart/mixed` MIME with the attachment part).
- [ ] **Observable:** a file above the inline threshold does **not** appear as an inline MIME part — instead the draft body contains a Drive `webViewLink` that resolves to the uploaded file. (Unit: assert no attachment MIME part + link substring in body; live: open the draft, confirm the link.)
- [ ] **Observable:** when `gws` is unauthenticated, `valor-email draft` **exits non-zero and prints an actionable message to stderr** naming the remedy (`gws auth login`) — the command does NOT hang, does NOT silently send, and does NOT swallow the error. (Test asserts exit code ≠ 0 and the remedy substring on simulated `invalid_grant`.)
- [ ] **Observable:** if the Drive upload succeeds but the draft create fails, the command reports the failure AND either confirms cleanup of the uploaded file or names the orphaned `fileId` — no silent Drive orphan. (Test simulates step-2 failure and asserts the cleanup-or-name behavior.)
- [ ] **Observable:** `cmd_send` and `cmd_draft` produce **identical** "File not found" / "Cannot read file" stderr text for the same bad `--file` (proves the shared validation helper, not a divergent copy).

### Inbound wedge guard
- [ ] **Observable:** an attachment-referencing email recovering **zero** files spawns a session whose `extra_context` carries `attachments_unrecoverable=True` and `attachments_recovered_count=0`; the agent's **reply asks the sender to resend** rather than producing no output / wedging. (Integration test asserts the context on the enqueued session; behavioral assertion on the reply content.)
- [ ] **Observable (the blocker fix):** an attachment-referencing email whose attachments are **truncated** (cap hit → non-empty list, `parsed["attachments_truncated"]=True`) spawns a session carrying `attachments_unrecoverable=True`, `attachments_truncated=True`, and `attachments_recovered_count=<M>` where `M < referenced`; the agent's reply distinguishes "only M of N arrived" from "none arrived." A guard keyed on emptiness alone would **fail** this criterion — it is the regression-proof for the partial-recovery wedge.
- [ ] **Observable:** an attachment-referencing email **with all files recovered** (non-empty list, `attachments_truncated=False`), and a non-referencing email, do **NOT** get the `attachments_unrecoverable` tag (no false positives). (Truth-table unit test.)

### Decision + gates
- [ ] The gws adoption decision (inbound=IMAP, outbound draft=gws) is documented in `docs/features/email-bridge.md` under Design Decisions.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] grep confirms `tools/valor_email.py` references `gws gmail users drafts create` and `cli_check_or_exit`, and `bridge/email_bridge.py` references both `attachments_unrecoverable` and `attachments_truncated` in the guard.

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
- In `_process_inbound_email`, after building `email_attachments`, tag `extra_context["attachments_unrecoverable"]` + `["attachments_truncated"]` + `["attachments_recovered_count"]` + `["attachments_referenced_count"]` when the body references attachments AND (`email_attachments` empty **OR** `parsed.get("attachments_truncated")`). The truncation arm is the blocker fix — emptiness alone misses partial recovery.
- Tighten the residual silent per-part skip: when a part is skipped at `:315-317`/`:335-337`, set `truncated=True` so a partial-decode (no cap hit) still surfaces. Verify against current code.
- Keep the directive static (no untrusted echo) — Open Q6 safety.

### 2. Draft subcommand
- **Task ID**: build-draft-cli
- **Depends On**: none
- **Validates**: tests/unit/test_valor_email.py
- **Informed By**: spike-1 (gws drafts API), spike-2 (reuse `_build_reply_mime`), spike-3 (auth degradation)
- **Assigned To**: draft-cli-builder
- **Agent Type**: builder
- **Parallel**: true
- Factor `_validate_attachment_files(files)` shared helper out of `cmd_send`; refactor `cmd_send` to call it (no behavior change).
- Add `cmd_draft` + `draft` subparser (`--to`, `--subject`, `--file` repeatable, `--reply-to`, `--json`). Mirror `cmd_send` validation via the shared helper.
- Call `cli_check_or_exit(body, transport="email", session_id=session_id)` before shelling to `gws` (same as `cmd_send`).
- Build MIME via `_build_reply_mime()` (the single shared builder); serialize `.as_bytes()` → base64url → `message.raw`; call `gws gmail users drafts create`.
- Add `EMAIL_DRAFT_INLINE_MAX_TOTAL_BYTES` (new, env-overridable, provisional comment; default 25 MiB) — do NOT reuse the inbound `EMAIL_ATTACHMENT_MAX_TOTAL_BYTES`. Size routing: inline ≤ threshold; Drive-upload + link append above.
- Drive fallback: upload FIRST, then create draft; on draft-create failure attempt `gws drive files delete` cleanup, and if cleanup fails, name the orphaned `fileId` in the error.
- Degradation: detect `invalid_grant`/non-zero gws exit → actionable error + non-zero exit. No silent send fallback.

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
| Truncation arm in guard | `grep -c "attachments_truncated" bridge/email_bridge.py` | output ≥ 2 (parse + guard) |
| Promise gate wired in draft | `grep -c "cli_check_or_exit" tools/valor_email.py` | output ≥ 2 (send + draft) |
| Separate draft constant | `grep -c "EMAIL_DRAFT_INLINE_MAX_TOTAL_BYTES" tools/valor_email.py` | output > 0 |
| Shared file-validation helper | `grep -c "_validate_attachment_files" tools/valor_email.py` | output ≥ 2 (def + send + draft) |
| gws decision documented | `grep -c "adoption" docs/features/email-bridge.md` | output > 0 |
| No competing injection inspector | `grep -rc "prompt.injection" tools/valor_email.py` | match count == 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | critique | Wedge guard misses partial-recovery (truncation) drops — keyed on emptiness only; #1567 leaves a non-empty list when the 25 MiB / 50-part cap trips | Guard now fires on `email_attachments` empty **OR** `parsed["attachments_truncated"]` (field verified at `email_bridge.py:507`/`:662`) | Problem, Freshness, Data Flow, Technical Approach, Success Criteria (blocker regression test), Test Impact, Step 1 |
| Non-blocking | critique | CLI MIME-build divergence from relay (`email_relay.py`) | Draft path calls the single shared `_build_reply_mime()` directly; no second encoder | Technical Approach §MIME-build alignment |
| Non-blocking | critique | Omitted `cli_check_or_exit` promise gate | Added to `cmd_draft` at same point as `cmd_send` (`valor_email.py:395`) | Technical Approach §Promise gate; Step 2; Verification |
| Non-blocking | critique | Inbound-cap constant reused for outbound routing | Rejected the coupling; new `EMAIL_DRAFT_INLINE_MAX_TOTAL_BYTES` (separate, env-overridable) | Technical Approach §Constant separation; Step 2; Verification |
| Non-blocking | critique | Non-atomic Drive-upload orphan risk | Upload-first ordering + best-effort cleanup + named-orphan reporting; full atomicity out of scope | Technical Approach §Drive-upload orphan risk; Risk 3; No-Gos; Step 2 |
| Non-blocking | critique | Mechanism-only success criteria | Rewrote Success Criteria as observable outcomes (Drafts-folder visibility, exit codes, reply content) | Success Criteria |
| Non-blocking | critique | Possible duplication of `send --file` error path | Factored `_validate_attachment_files` shared by `cmd_send` + `cmd_draft` | Technical Approach §`send --file` error-path reuse; Step 2; Verification |

---

## Open Questions

1. **Wedge-guard policy — inform vs hard-block?** Default in this plan is *inform the agent* (spawn the session with `attachments_unrecoverable` context so it asks the sender to resend). The alternative is *hard-block* (refuse to spawn and auto-reply "your attachments didn't arrive"). Inform preserves the email and uses agent judgment; hard-block is more deterministic but risks dropping mail whose body only colloquially says "attached." Which posture do you want?
2. **Draft surface — real Gmail draft only, or also a `--send-link` Drive convenience?** This plan creates a real Gmail draft via `gws` (review-in-Gmail). Should it also support a "draft as Drive link in body" mode independent of the 25 MiB threshold (e.g., always link, never inline), or is size-based routing sufficient?
