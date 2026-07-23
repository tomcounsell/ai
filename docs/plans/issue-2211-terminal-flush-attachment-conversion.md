---
status: Ready
type: bug
appetite: Medium
owner: Valor
created: 2026-07-23
tracking: https://github.com/tomcounsell/ai/issues/2211
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-23T04:01:34Z
---

# Terminal-Flush Attachment Conversion (validator-aware self-draft flush)

## Problem

A teammate session's **final** message referenced a machine-local path
(`/tmp/eng_review_jul15-22.txt`) instead of attaching the file. The delivery
validator correctly flagged `local_file_path_reference` and injected self-draft
steering telling the agent to re-send via `tools/send_message.py --file`. But the
flagged output was the session's **last** message — the steering landed ~1 s
before the session completed, so the agent never consumed it, and
`_reenqueue_leftover_steering` deliberately drops drafter-fallback steering from
re-enqueue (#1794/#2197). The terminal fallback
(`flush_deferred_self_draft_sync`, `agent/session_health.py`) then delivered the
**exact text the validator had just rejected** — dead `/tmp` path included —
because the sync flush hard-omits attachments.

**Current behavior:**
- On a terminal turn, a wire-format violation can never be self-drafted (the
  turn is already over; the steering is suppressed from re-enqueue). This is the
  *common* case, since delivery validation runs on final outputs.
- The flush delivers the rejected text verbatim with no attachment, so the
  corrective (`send_message.py --file`) never executes. The recipient receives a
  dead local path pointing at a file on a machine they can't reach.

**Desired outcome:**
- When the terminal flush is about to deliver text carrying a
  `local_file_path_reference` violation, it becomes **validator-aware**: it
  extracts the referenced path(s), and for any that exist on disk it attaches
  them to the outbox payload and scrubs the bare path token(s) from the delivered
  text. The recipient gets the real file plus clean prose instead of a dead path.
- Delivery on the terminal path no longer defeats the validator for the
  local-path violation class.

## Freshness Check

**Baseline commit:** 704c28b19eb0934854784d179918f0cad3d187fa
**Issue filed at:** 2026-07-22T10:00:28Z
**Disposition:** Minor drift

**File:line references re-verified (against baseline HEAD):**
- `agent/session_health.py:2089-2243` — `flush_deferred_self_draft_sync` — still present. Telegram branch (2211-2221) builds `build_telegram_outbox_payload(chat_id, message, reply_to, session_id)` and the inline comment at line 2214 confirms "This sync flush never carries attachments, so file_paths is omitted." Confirmed.
- `agent/output_handler.py:668-669` — `_ctx["deferred_self_draft_text"] = text` persists the raw (rejected) text at defer time. Confirmed. Note: only the raw text is persisted; the structured `violations` list is NOT stored in extra_context.
- `agent/output_handler.py:960-1073` — `_inject_self_draft_steering`; local-path addendum appended at 1047-1055, pushed with `sender=DRAFTER_FALLBACK_SENDER`. Confirmed.
- `agent/session_executor.py:802-838` — `_reenqueue_leftover_steering` drops `drafter-fallback` steering from re-enqueue (per #1794/#2197). Confirmed.
- `bridge/message_drafter.py:315-355` — `LOCAL_FILE_PATH_RULE` + `detect_local_file_reference` + `_LOCAL_FILE_PATH_PATTERNS` (`/tmp/\S+`, `/Users/\S+`, `/home/\S+`, `~/\S+`, `open -a ...`). Confirmed — the path-detection primitive already exists.
- `agent/output_handler.py:267-308` — `build_telegram_outbox_payload` already accepts `file_paths` and sets the `file_paths` payload key when truthy (307-308). `build_email_outbox_payload` (168-259) sets an `attachments` key. Confirmed — the wire format already supports attachments.
- `bridge/telegram_relay.py:369,404` — the relay reads `file_paths` from the payload and filters to `os.path.isfile(fp)` before attaching. Confirmed end-to-end.

**Commits on main since issue was filed (touching referenced files):**
- `64bd16e26` "Fix deferred self-draft dedup keys swallowing resumed-session replies (#2211 context)" — the per-run dedup-key hotfix the issue explicitly calls out as already-landed context. It scoped `self_draft_completed_flush_sent` / `self_draft_fallback_sent` per-run (AgentSession record id). It does NOT touch the two design defects in this issue. The code read for this plan is already post-hotfix.

**Cited sibling issues/PRs re-checked:**
- #1794 / #2197 / PR #2198 — merged; established that the terminal flush is the sole owner of the held drafter-fallback content and that re-enqueue must drop it. Still the current design.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** Minor drift only — the dedup hotfix landed but changed neither root cause. Both design defects remain reproducible against baseline.

## Prior Art

- **PR #1796** (`fix(delivery): flush deferred self-draft on completed terminal path (#1794)`): introduced `flush_deferred_self_draft_sync` so a cleanly-completed session that deferred for self-draft and never redrafted no longer silently swallows the reply. Succeeded at *delivering something*, but chose to deliver the raw deferred text with no attachment — the seed of Defect 2.
- **PR #1807** (`fix(delivery): flush deferred self-draft on email-completed path (#1797)`): extended the flush to the email-completed path via `build_email_outbox_payload`. Same no-attachment limitation.
- **PR #2198** (`Suppress drafter-fallback re-enqueue on terminal self-draft deferral`): made `_reenqueue_leftover_steering` drop drafter-fallback steering so the flush is the sole delivery owner (fixing a context-blind re-spawn). Cemented that the flush — not a re-drafted turn — owns terminal delivery, which is precisely why the flush must now become validator-aware.
- **PR #2115** (`Consolidate agent-message-delivery send paths (#1370)`): consolidated the outbox payload builders (`build_telegram_outbox_payload` / `build_email_outbox_payload`) — the shared builders this plan reuses.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|-----------------------|
| PR #1796 / #1807 | Added the terminal flush so deferred replies are never silently swallowed | Solved *silence*, not *correctness*: the flush delivers the raw rejected text and hard-omits attachments, so a `local_file_path_reference` violation on the final message is delivered verbatim with a dead path |
| PR #2198 | Made the flush the sole owner of the held content (dropped re-enqueue) | Correctly removed a context-blind re-spawn, but by making the flush authoritative it also made the flush's no-attachment limitation the *only* outcome — there is no longer any path that could self-correct the violation |

**Root cause pattern:** each fix improved *delivery reliability* while treating
the deferred text as opaque. None made the delivery path *validator-aware*. The
flush is now the authoritative terminal delivery seam, so the fix belongs there:
re-apply the local-path corrective at flush time instead of re-delivering the
rejected text.

## Data Flow

1. **Entry point:** agent emits a final message containing `/tmp/....txt`.
   `TelegramRelayOutputHandler.send()` routes it through the drafter/validator.
2. **Validation:** `detect_local_file_reference` flags `local_file_path_reference`;
   `draft.needs_self_draft` is True.
3. **Deferral:** `_inject_self_draft_steering` pushes drafter-fallback steering;
   `send()` persists `extra_context.deferred_self_draft_pending = True` and
   `deferred_self_draft_text = <raw rejected text>` (`agent/output_handler.py:668-669`)
   and returns `DeliveryOutcome.deferred_self_draft`.
4. **Turn ends** before the steering is consumed. `_reenqueue_leftover_steering`
   drops the drafter-fallback message (no re-draft).
5. **Terminal flush:** `finalize_session` → `flush_deferred_self_draft_sync`
   reads the persisted raw text, narration-gates it, builds a telegram payload
   **without file_paths**, and pushes to `telegram:outbox:{session_id}`.
6. **Relay:** `bridge/telegram_relay.py` sends the text (dead path included), no
   attachment. **← the defect surfaces here.**

**Fix inserts a conversion step between 5 and 6:** before building the payload,
re-detect local paths in the flush `message`, and for existing files populate
`file_paths` and scrub the path tokens from the text.

## Architectural Impact

- **New dependencies:** none. Reuses existing primitives (`detect_local_file_reference` patterns, the outbox payload builders' `file_paths`/`attachments` params, the relay's `os.path.isfile` filter).
- **Interface changes:** one new pure helper in `bridge/message_drafter.py` (e.g. `convert_local_paths_to_attachments(text) -> tuple[str, list[str]]`). No public signature changes to the flush.
- **Coupling:** slightly increases `session_health` → `message_drafter` coupling (already imports `bridge.message_quality`). Acceptable; the detection logic stays owned by `message_drafter`.
- **Data ownership:** unchanged. The flush remains the sole terminal-delivery owner.
- **Reversibility:** high — the conversion is additive and gated on a violation being present + file existing; if it no-ops, behavior is identical to today.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 required (the three prior open questions are resolved with
  documented defaults under **Resolved Decisions** — build proceeds unblocked)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. All touch-points are
internal Python modules.

## Solution

### Key Elements

- **`convert_local_paths_to_attachments(text)` helper** (`bridge/message_drafter.py`):
  a pure function that finds machine-local *file* paths in the text using the
  existing `_LOCAL_FILE_PATH_PATTERNS`, filters to paths that exist on disk
  (`os.path.isfile`), applies the secret-exclusion gate, scrubs **every** matched
  file-path token from the text (dead, secret-excluded, and existing-attachable
  alike — no raw local path ever survives in the returned text), and returns the
  **single canonical return shape** used everywhere in this plan:
  `(scrubbed_text: str, attached_paths: list[str], dead_count: int, skipped_count: int)`
  — a 4-tuple where `attached_paths` is the transport-neutral list of existing,
  non-secret files to attach, `dead_count` is the number of scrubbed
  non-existent paths, and `skipped_count` is the number of scrubbed secret-excluded
  paths. (Tech-debt reconciliation: the Observability and task sections all refer
  to THIS 4-tuple — there is no alternate "result object" shape.) Returns
  `(text, [], 0, 0)` when nothing converts. Does NOT convert the `open -a ...`
  command pattern (that is not a file to attach).
- **Secret-file exclusion gate (BLOCKER fix — exfiltration guard):** the four
  filesystem patterns (`/tmp/\S+`, `/Users/\S+`, `/home/\S+`, `~/\S+`) match
  sensitive paths — `~/Desktop/Valor/.env`, `~/Desktop/Valor/projects.json`,
  SSH keys (`~/.ssh/id_rsa`), cloud creds (`~/.aws/credentials`), PEM/PKCS certs.
  Without a guard, a final message that merely *references* such a path would
  attach and deliver the full secrets file to a Telegram/email recipient.
  **After `os.path.isfile(os.path.expanduser(token))` passes, the token is first
  resolved through `os.path.realpath` (so a symlink into the vault or into a
  dot-directory cannot bypass the gate), then a secret-exclusion check runs
  before appending to `attached`:** skip the token if the resolved real path
  - contains **ANY dot-directory or dotfile component** — compute
    `parts = os.path.normpath(realpath).split(os.sep)` and skip if any component
    starts with `"."` (excluding the `"."` and `".."` navigation components).
    This is component-based, not basename-based, so it catches BOTH a dotfile
    basename (`.env`, `.netrc`, `.pgpass`) AND a secret living in a dot-*directory*
    with an ordinary basename (`~/.ssh/id_rsa`, `~/.aws/credentials`,
    `~/.config/...`) — the case a basename-only `startswith(".")` check silently
    missed,
  - has a sensitive extension — **compared case-insensitively** (lowercase the
    extension before matching so `.ENV`, `.PEM`, `.Key` are all caught): `.env`,
    `.pem`, `.key`, `.p12`, `.pfx`, `.crt`, `.cer`, `.keychain`,
  - has a **known secret basename** regardless of directory: `id_rsa`,
    `id_ed25519`, `id_ecdsa`, `id_dsa`, `credentials`, `known_hosts`,
    `authorized_keys`,
  - or resides anywhere under the secrets vault
    (`os.path.expanduser("~/Desktop/Valor/")` — matched via a normalized
    `os.path.commonpath`/prefix check on the realpath so `projects.json` inside
    the vault is also excluded even though it has no sensitive extension).

  A token **skipped for safety is treated exactly like a dead path**: it is
  scrubbed from the text but NOT appended to `attached`. If scrubbing empties the
  text and nothing else attached, the dead-path-only canned-notice guard (below)
  fires — the recipient gets `"(the referenced file is no longer available)"`,
  never the secret. The excluded-extension set, known-secret-basename set, and
  vault root are named module-level constants (env-overridable per the
  provisional-magic-numbers convention) with a grain-of-salt comment marking them
  tunable.
- **Validator-aware flush — TWO delivery sites, ONLY the sync flush attaches:**
  - **Sync flush that attaches** (`agent/session_health.py::flush_deferred_self_draft_sync`):
    call the helper on `message` before building the payload. The helper returns a
    **transport-neutral** `attached_paths` list; **both builders accept the same
    `file_paths=` parameter** and each maps it internally to its own emitted wire
    key — `build_telegram_outbox_payload(..., file_paths=attached_paths)` emits the
    `file_paths` dict key, and `build_email_outbox_payload(..., file_paths=attached_paths)`
    emits the `attachments` dict key (`agent/output_handler.py:259`). The call-site
    keyword is `file_paths=` for BOTH — `attachments` is NOT a parameter; passing
    `attachments=` raises `TypeError`, which the never-raise flush try/except
    swallows AFTER the dedup SETNX lock is burned for 1h, silently dropping the
    email reply (re-introducing #1796). So attachments ride the existing wire
    format without the flush hard-coding a transport-specific field name. Both
    branches of the sync flush build a real outbox payload, so both attach.
  - **Async fallback that only scrubs** (`agent/session_health.py::_deliver_deferred_self_draft_fallback`,
    email failed/abandoned): call the helper for its **text scrub only**, use
    `scrubbed_text`, and **discard `attached_paths`**. This site delegates delivery
    to `deliver_system_notice(entry, message, *, telemetry_key)`
    (`agent/output_handler.py:312`), which has **no attachment parameter** — it
    calls `send_cb(chat_id, message, telegram_message_id, entry)`. There is no
    attachment channel here, so the fallback cannot deliver a file; it only
    guarantees the recipient never receives a raw local path. **WHY (do not
    re-introduce):** extending `deliver_system_notice` to forward attachments is an
    unscoped signature change to a shared, single-sanctioned delivery seam and is
    deliberately out of scope (see the No-Gos follow-up note).
  - Keep conversion scoped to the local-path token class only at BOTH sites (do NOT
    broaden the attach/scrub path to any other violation class).
- **Empty-text guard (BLOCKER fix — dead-path-only case):** after calling
  `convert_local_paths_to_attachments`, the scrubbed text can be empty in *two*
  ways, and both must be guarded **before** building either outbox payload:
  1. **Scrubbed empty but a file WAS attached** → caption with the basename(s):
     `", ".join(os.path.basename(p) for p in attached)`.
  2. **Scrubbed empty and NOTHING attached** (the dead-path-only case: the text
     was *only* a non-existent local path, so scrubbing empties it and no file
     attaches) → substitute a canned notice, e.g.
     `"(the referenced file is no longer available)"`.
  Without guard (2) the flush would build a payload with neither `text` nor
  `file_paths`, and `bridge/telegram_relay.py` drops it at
  `if not text and not file_paths: return None` (line 394) — silently
  re-introducing the exact swallowed-reply defect PR #1796 fixed. The guard is a
  single check: `if not scrubbed.strip() and not attached: scrubbed = <canned notice>`,
  applied on both the telegram and email-completed branches of the sync flush
  before payload construction. **At the async fallback site** (`_deliver_deferred_self_draft_fallback`)
  nothing ever attaches, so the reduced guard `if not scrubbed.strip(): message = <canned notice>`
  applies after the scrub — the fallback must never pass an empty string to
  `deliver_system_notice`.

### Flow

Final message with `/tmp/report.txt` → validator flags `local_file_path_reference`
→ deferred (steering suppressed on terminal turn) → **terminal flush re-detects
`/tmp/report.txt`, confirms it exists, attaches it, scrubs the token** → relay
delivers the file with clean caption text → recipient gets the real attachment.

### Technical Approach

- **Reuse, don't reinvent detection.** The helper reuses
  `_LOCAL_FILE_PATH_PATTERNS`; extract the *full* matched path substring (not the
  80-char-truncated `Violation.snippet`). Only the four filesystem-path patterns
  are convertible; the `open -a` command pattern is scrubbed-or-left per the same
  rule the addendum already implies (not a file — leave detection to the addendum
  path, do not attempt to attach).
- **Match ALL occurrences, not just the first.** Use `pattern.finditer(text)` (not
  `pattern.search`) so multiple paths on one line — e.g. two `/tmp/...` tokens in a
  single sentence — are each detected, existence-checked, and scrubbed. Using
  `search` would catch only the first and leave a second dead path in the text.
- **Existence is the gate — expand `~` first.** Only attach paths where
  `os.path.isfile(os.path.expanduser(path))` is True at flush time. The `~/\S+`
  pattern yields a tilde-prefixed token; `os.path.isfile("~/foo")` is always False
  because `os.path.isfile` does NOT expand `~`, so tilde paths would never attach
  without the explicit `os.path.expanduser`. Attach the expanded absolute path (so
  the relay's own `os.path.isfile` re-check at send time also passes). `/tmp` files
  may have been reaped; a non-existent path is scrubbed from the text but NOT
  attached (the relay would drop a missing file at line 404 anyway — do the
  filtering flush-side so the caption is honest).
- **Secret exclusion runs after existence, before attach.** The order inside the
  per-token loop is: (1) strip trailing punctuation → (2) `os.path.isfile(expanded)`
  → (3) resolve `realpath = os.path.realpath(expanded)` (defeat symlink bypass) →
  (4) secret-exclusion check on the realpath (ANY dot-component via
  `os.path.normpath(realpath).split(os.sep)` / case-insensitive sensitive-extension
  / known-secret-basename / under the secrets vault) → (5) append to `attached`
  only if it survives all. Steps 2 and 4 both failing route the token to the
  "scrub-only, do-not-attach" arm — identical handling, so a secret path and a
  reaped path are indistinguishable downstream (the recipient never learns a
  secret was referenced). The realpath resolution is stated once here and is the
  single symlink-hardening step: it happens before the dot-component check so a
  symlink whose own name is benign but which points into `~/.ssh/`, a dot-dir, or
  the vault is still caught.
- **Scrub the trimmed token, not `match.group(0)`.** `\S+` greedily captures
  adjacent prose and punctuation: `/tmp/a.txt,and more` captures `,and` into the
  token; `(/tmp/a.txt)` captures the closing paren and leaves the open paren
  orphaned. Scrub the SAME trimmed token that was existence-checked (after trailing
  punctuation is stripped), not the raw `match.group(0)` — so the scrub removes
  exactly the path substring and leaves the surrounding prose/punctuation
  (`,and more`, the `(` and `)`) intact. Strip trailing punctuation
  (`.,;:)]}'"`) from the token BOTH before the `isfile` check AND when computing
  what to remove from the text.
- **Scrub, then guard (two-armed).** Remove the trimmed path token(s) from the
  text; collapse any doubled whitespace left behind. If the result is
  empty/whitespace-only, the guard has two arms and MUST pick per outcome:
  1. **≥1 file attached** → caption with
     `", ".join(os.path.basename(p) for p in attached)` (basename caption).
  2. **nothing attached** (dead-path-only, secret-excluded-only, or both) →
     substitute the canned notice `"(the referenced file is no longer available)"`.

  Arm 2 is the #1796 dead-path guard: without it the payload has neither `text`
  nor `file_paths` and the relay drops it at line 394, re-introducing the swallowed
  reply. This bullet is deliberately two-armed so it cannot be read as
  "always basename-caption," which would crash/empty on the attached==[] case.
- **Convert BEFORE the narration gate (ordering fix).** In both
  `flush_deferred_self_draft_sync` (`agent/session_health.py:2180-2194`, which has
  a telegram branch AND an email-completed branch) and the async fallback
  `_deliver_deferred_self_draft_fallback` (`:2302-2317`, email failed/abandoned),
  the `is_narration_only` check currently replaces `message` with the *pathless*
  `NARRATION_FALLBACK_MESSAGE` BEFORE any conversion could run. For the exact
  motivating incident — a final message that is a short narration sentence plus a
  `/tmp/...` path — running the narration gate first would discard the path and,
  at the sync flush, the real file would be silently lost. **Run
  `convert_local_paths_to_attachments` on the original `deferred_text` FIRST**,
  producing `(scrubbed, attached)`; THEN apply the narration gate to `scrubbed`.
  The two sites diverge in what they do with `attached`:
  - **Sync flush (`flush_deferred_self_draft_sync`) — ATTACH.** Both its branches
    build an outbox payload via a builder that accepts attachments through the
    **same `file_paths=` parameter** (`build_telegram_outbox_payload(..., file_paths=...)`
    / `build_email_outbox_payload(..., file_paths=...)`; the email builder maps
    `file_paths` to the `attachments` dict key internally — `attachments` is NOT a
    call parameter). Attachments survive the
    narration substitution: if `scrubbed` is narration-only, set the text to
    `NARRATION_FALLBACK_MESSAGE` but STILL pass `attached` into the payload
    builder. Only when `attached` is empty AND the text is narration/empty does
    the pathless fallback stand alone.
  - **Async fallback (`_deliver_deferred_self_draft_fallback`) — TEXT-SCRUB-ONLY,
    NO ATTACH.** This site delegates delivery to
    `deliver_system_notice(entry, message, *, telemetry_key)`
    (`agent/output_handler.py:312`), which has **no `file_paths`/`attachments`
    parameter** — it calls `send_cb(chat_id, message, telegram_message_id, entry)`
    and has no attachment channel. So here we run the helper for its **text scrub
    only**: use the returned `scrubbed` text (which has every detected local-path
    token — dead, secret, or existing-and-convertible — removed) and **discard the
    `attached` list**. The recipient never receives a raw local path, but no file
    is attached at this site. If the scrub empties the text, the canned-notice
    fallback (below) applies exactly as on the sync flush. **WHY no attach here:**
    `deliver_system_notice` is the single sanctioned system-notice seam and carries
    no attachment field; forwarding attachments through it would be an unscoped
    signature change to a shared delivery primitive, deliberately out of scope for
    this plan (see the follow-up note under No-Gos). This is the failed/abandoned
    email path, which is rarer than the completed path the sync flush owns.
- **Attachment sites are the two sync-flush branches only.** Apply the
  attach-carrying path identically on the sync flush's telegram and email-completed
  branches. Both builder calls use the **`file_paths=` keyword** (the email builder
  emits an `attachments` dict key from that `file_paths` param; telegram emits a
  `file_paths` key). Never pass `attachments=` as a call argument. The async
  fallback is text-scrub-only per the bullet above.
- **Never raises.** The flush is wrapped in a never-raise try/except already; the
  helper must also be internally defensive (a regex/`os.path` failure returns the
  original text + empty list) so a conversion error can never suppress delivery.
- **Dedup unchanged.** The per-run `self_draft_completed_flush_sent:{sid}:{run_id}`
  SETNX gate is untouched — conversion happens after the gate is acquired.

### Observability

The conversion runs under a never-raise contract (a helper exception is swallowed
and returns `(original, [])`), so silent failures would otherwise be invisible.
Each distinct outcome gets a structured log line AND a best-effort Redis counter
(mirroring the existing `{project_key}:session-health:deferred_self_draft_completed_flush`
counter at `session_health.py:2234`), all inside `try/except: pass` so telemetry
never breaks delivery:

| Outcome | Structured log (INFO/WARNING) | Redis counter (`{project_key}:session-health:...`) |
|---------|-------------------------------|-----------------------------------------------------|
| ≥1 path converted+attached | `INFO [session-health] flush converted N local path(s) to attachments for {sid}` (include attached count) | `deferred_flush_paths_attached` (incr by count) |
| Dead path scrubbed, not attached | `INFO ...scrubbed M dead/non-existent local path(s) for {sid}` | `deferred_flush_dead_paths_scrubbed` |
| Path skipped for safety (secret) | `WARNING [session-health] flush skipped K local path(s) excluded as sensitive for {sid}` (log the COUNT only — never the path/basename, which could leak the secret filename) | `deferred_flush_secret_paths_skipped` |

The secret-skip outcome is a **single** signal (`deferred_flush_secret_paths_skipped`
+ its warning) that fires for a token excluded by **any** arm of the gate —
dot-component (dot-directory or dotfile), sensitive-extension, known-secret-
basename, or vault-prefix. There is no per-arm counter; downstream only needs to
know "N secret paths were withheld," never which arm or which path matched.
| Helper raised (never-raise fallback) | `WARNING [session-health] convert_local_paths_to_attachments raised for {sid}: {err}; delivering unconverted text` | `deferred_flush_conversion_error` |

The helper returns the canonical 4-tuple
`(scrubbed_text, attached_paths, dead_count, skipped_count)` (see the helper Key
Element) — the flush reads `len(attached_paths)`, `dead_count`, and
`skipped_count` directly to emit these outcomes; there is no separate result
object. The secret-skip log line MUST NOT include the path or basename (logs are
lower-trust than the never-delivered payload; a filename like `client-acme.env`
is itself sensitive). Counts only.

These counters live in the existing `{project_key}:session-health:...` Redis
namespace already surfaced by `curl localhost:8500/dashboard.json` — no new
dashboard wiring is in scope; the pointer is noted so a future observer knows
where the new counters appear.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `flush_deferred_self_draft_sync` already wraps its body in
  `except Exception` → `logger.warning` (session_health.py:2238-2243). Add a test
  asserting that when the conversion helper raises, the flush still delivers the
  (unconverted) text rather than swallowing the message — i.e. conversion failure
  degrades to today's behavior, and a warning is logged.
- [ ] The new helper's internal `except` returns `(original_text, [])`; test that
  a malformed input still yields a usable result.

### Empty/Invalid Input Handling
- [ ] Helper on `""`/`None`/whitespace → returns input unchanged, empty list.
- [ ] Text that is ONLY an EXISTING local path → after scrub, empty; flush
  substitutes the basename caption and still attaches (assert payload has
  non-empty text AND file_paths).
- [ ] **Dead-path-only case (BLOCKER guard):** text that is ONLY a NON-existent
  local path → after scrub, empty, and nothing attaches; flush substitutes the
  canned "no longer available" notice so the payload has non-empty text and is NOT
  dropped by the relay's `if not text and not file_paths` guard. Assert the built
  payload has non-empty `text` (the notice) and no `file_paths`.
- [ ] Path matched but file does not exist on disk (alongside other content) → not
  attached; token scrubbed; assert `file_paths` absent/empty and text no longer
  contains the dead path.
- [ ] Tilde path (`~/existing.txt`) that exists after `expanduser` → attached as
  the expanded absolute path; token scrubbed.
- [ ] Two paths on one line (both existing) → both attached and both scrubbed
  (guards the `finditer` vs `search` choice).
- [ ] **Adjacent-prose scrub (concern 2):** `/tmp/a.txt,and more` (existing file)
  → the file attaches, the token is scrubbed, but `,and more` survives intact (no
  `,and` deletion). `(/tmp/a.txt)` → token scrubbed, both parens handled cleanly
  (no orphan `(`). Asserts scrub operates on the trimmed token, not `match.group(0)`.

### Security / Secret-Exclusion Coverage (BLOCKER guard)
- [ ] **`.env` under the secrets vault:** text referencing
  `~/Desktop/Valor/.env` (create a temp file at that resolved path in the test,
  or monkeypatch the vault root to a temp dir) → NOT attached; token scrubbed; if
  it was the only content, the canned "no longer available" notice is delivered.
  Assert the built payload has NO `file_paths`/`attachments` and the secret path
  never appears in the delivered text.
- [ ] **`projects.json` under the vault (no sensitive extension):** referencing a
  file inside `~/Desktop/Valor/` that has an ordinary `.json` extension → still
  excluded by the vault-prefix arm (not just the extension arm). Assert not attached.
- [ ] **Sensitive extensions:** `~/id_rsa.pem`, `/tmp/cert.key`, `/tmp/store.p12`,
  a dotfile basename `/tmp/.netrc` → each existence-passes but is excluded; not
  attached; scrubbed.
- [ ] **Case-insensitive extension:** `/tmp/config.ENV` / `/tmp/server.PEM`
  (uppercase extension) → still excluded (the extension is lowercased before
  matching); not attached.
- [ ] **Dot-DIRECTORY secrets (BLOCKER guard — component-based, not basename):**
  `~/.ssh/id_rsa` and `~/.aws/credentials` — ordinary basenames living in a
  dot-directory → each existence-passes but is excluded by the dot-component arm;
  not attached; scrubbed. (These are the exact paths a basename-only
  `startswith(".")` check missed.)
- [ ] **Known secret basename:** a file named `credentials`/`id_ed25519`/
  `known_hosts` in a non-dot directory → excluded by the known-basename set even
  though neither its extension nor its directory is dotted.
- [ ] **Symlink hardening:** a benignly-named symlink (`/tmp/link.txt`) whose
  `realpath` resolves into `~/.ssh/` (or the vault) → excluded via
  `os.path.realpath` before the component check; not attached. Proves a symlink
  cannot bypass the gate.
- [ ] **Ordinary file is still attached:** control case — a plain `/tmp/report.txt`
  outside the vault with a benign extension → attaches (proves the exclusion gate
  is not over-broad).
- [ ] **Secret-skip telemetry:** assert the skipped-for-safety counter/log fires
  and that the log line does NOT contain the path or basename (count only).

### Error State Rendering
- [ ] Assert the recipient-visible outcome: for an existing file, the outbox
  payload carries `file_paths=[<path>]` and text without the raw path token.
- [ ] Assert no double-send: the existing dedup gate still fires exactly once.

### Narration-Before-Conversion Ordering (concern 1)
- [ ] **Narration sentence + existing `/tmp/...` path** (the motivating incident
  shape): the flush attaches the file EVEN THOUGH the residual text is
  narration-only. Assert the payload has `file_paths=[<path>]` AND the text is the
  pathless `NARRATION_FALLBACK_MESSAGE` (not the raw narration+path). Proves
  conversion ran on the original `deferred_text` before the narration gate, so the
  file is not lost.
- [ ] **Sync flush email-completed branch:** add the same attach-and-scrub ordering
  assertion for the sync flush's email-completed branch (it builds
  `build_email_outbox_payload`, which accepts `attachments`) — narration text +
  existing path → payload has `attachments=[<path>]` AND narration fallback text.
- [ ] **Async fallback site (TEXT-SCRUB-ONLY, no attach):** for
  `_deliver_deferred_self_draft_fallback` (`session_health.py:2302-2317`), a
  narration sentence + existing `/tmp/...` path → assert the message passed to
  `deliver_system_notice` has the path token **scrubbed** and is the pathless
  narration fallback; assert **no attachment is delivered** (this seam has no
  attachment channel — `deliver_system_notice` has no `file_paths`/`attachments`
  param). The invariant here is "no raw local path reaches the recipient," NOT
  attachment delivery.

### End-to-End Send-Path Validation
- [ ] Drive an existing-file payload through the relay's send path
  (`bridge/telegram_relay.py`) — not just the payload-builder unit assertion —
  confirming the file survives the relay's `os.path.isfile` filter (line 404) and
  reaches the file-send branch. This closes the gap between "payload has
  `file_paths`" and "the relay actually attaches it," catching path-shape
  mismatches (e.g. an unexpanded `~` token) that a builder-only test would miss.
- [ ] Drive the dead-path-only payload through the relay and assert it is NOT
  dropped by the line-394 guard (the canned notice keeps `text` non-empty).
- [ ] **Email relay parity (nit):** the same three assertions — converted payload
  attaches, dead-path-only delivers the canned notice, non-local-path deferral
  stays text-only — must also be exercised against the **sync flush's email-completed
  send path** (`build_email_outbox_payload` → the SMTP relay's attachment handling),
  not only telegram. The regression guard is transport-general: both relays must
  preserve the no-attachment behavior for non-local-path deferrals and honor
  attachments for converted ones.
- [ ] **Async fallback send path (text-scrub-only):** exercise
  `_deliver_deferred_self_draft_fallback` → `deliver_system_notice` and assert the
  delivered message has the local-path token scrubbed (or the canned notice when
  empty) and that NO attachment is passed — this seam has no attachment param, so
  the assertion is "no raw local path in the delivered text," never "file
  attached."

## Test Impact

- [ ] `tests/unit/test_deferred_self_draft_completed.py` — UPDATE: existing tests
  assert the flush writes text-only payloads. Add cases for the conversion path;
  update any assertion that hard-codes "no file_paths key" for the local-path
  scenario. Non-local-path deferrals must still produce text-only payloads on BOTH
  the telegram and email-completed branches (transport-general regression guard).
  Add secret-exclusion and narration-before-conversion cases for both branches.
- [ ] `tests/unit/test_output_handler.py` — UPDATE (if any test asserts the flush
  payload shape). The `_inject_self_draft_steering` addendum tests
  (`test_local_file_path_violation_adds_attach_addendum_to_steering`) are
  unaffected — the steering-injection path is unchanged.
- [ ] `tests/unit/test_message_drafter.py` / `tests/unit/test_medium_validators.py`
  — ADD: unit tests for the new `convert_local_paths_to_attachments` helper
  (co-located with the detection tests). No existing cases change; `detect_local_file_reference` is untouched.
- [ ] `tests/unit/test_telegram_relay.py` (or the nearest relay send-path test
  module) — ADD: an end-to-end case driving a converted payload through the relay
  so the file survives the `os.path.isfile` filter, plus a dead-path-only case
  asserting the canned-notice payload is not dropped by the line-394 guard. If no
  relay send-path test module exists, add these cases to the flush test module
  invoking the relay send function directly.

## Rabbit Holes

- **Do NOT build a synchronous pre-finalization re-draft turn (Direction B).**
  Running the full self-draft cycle synchronously before finalization (the
  flush's `completed` path has no running event loop) is a large, invasive change
  to the finalization chokepoint for marginal additional coverage. The concrete,
  common incident is the local-path class; the validator-aware flush neutralizes
  it directly.
- **Do NOT generalize to every wire-format violation class.** Markdown-table and
  other cosmetic violations delivered by the flush are lower-harm (readable, just
  imperfect) than a dead path (actively broken/misleading). Converting arbitrary
  violations flush-side has no bounded corrective. Scope to local-path→attachment.
- **Do NOT try to parse "intent"** (did the agent *mean* to attach?). Existence on
  disk + a detected path is a sufficient, mechanical signal. No LLM call.
- **Do NOT touch the dedup-key logic** (just hotfixed in `64bd16e26`).

## Risks

### Risk 1: Attaching a file the agent referenced only for context, not sharing
**Impact:** A `/tmp` path mentioned illustratively gets attached as a file the
recipient didn't ask for.
**Mitigation:** This is strictly better than delivering a dead path, and the
validator already treats any local path in an outbound message as a wire-format
violation (the agent should not be pasting local paths at all). The scrub-plus-
attach keeps the prose intact minus the token. Acceptable per the validator's
existing contract.

### Risk 2: Path with trailing punctuation captured by `\S+`
**Impact:** `/tmp/report.txt.` (sentence-final period) or `(/tmp/x)` yields a path
string that fails `os.path.isfile`, so nothing attaches.
**Mitigation:** Trim a small set of trailing punctuation (`.,;:)]}'"`) from the
matched token before the existence check; test both `report.txt.` and
`(report.txt)` forms.

### Risk 0 (BLOCKER-class): Secret-file exfiltration via broad path patterns
**Impact:** `/Users/\S+` and `~/\S+` match `~/Desktop/Valor/.env`,
`~/Desktop/Valor/projects.json`, SSH private keys (`~/.ssh/id_rsa`), cloud creds
(`~/.aws/credentials`), and PEM/PKCS certs. A final message merely *referencing*
such a path would, without a guard, attach and deliver the full secrets file to
an external Telegram/email recipient — a credential leak.
**Mitigation:** The secret-exclusion gate runs after `os.path.isfile`, on the
`os.path.realpath`-resolved path (so a symlink cannot bypass it), and before
attach. It is **component-based, not basename-based**: it skips a token if ANY
path component starts with `.` (catching secrets in dot-*directories* with
ordinary basenames — `~/.ssh/id_rsa`, `~/.aws/credentials` — not just dotfile
basenames), plus a case-insensitive sensitive-extension check, a known-secret-
basename set (`id_rsa`/`id_ed25519`/`credentials`/`known_hosts`/`authorized_keys`/
…), and a normalized-prefix check against the secrets vault (`~/Desktop/Valor/`,
so `projects.json` is caught despite its benign `.json` extension). Excluded
tokens are scrubbed-not-attached, identical to a dead path, and the recipient
gets the canned notice. Telemetry logs a count only (never the path/basename,
which is itself sensitive). Tested by the full secret-exclusion matrix in the
Security coverage subsection (including `~/.ssh/id_rsa`, `~/.aws/credentials`,
and a symlink into a dot-dir).

### Risk 3: Multiple paths, some existing some not
**Impact:** Partial conversion.
**Mitigation:** Attach the subset that exists; scrub only the tokens actually
converted OR all detected tokens (decision: scrub all detected file-path tokens
so no dead path survives, attach only existing ones). Unit-test the mixed case.

### Risk 4 (KNOWN LIMITATION — tech-debt, out of scope): Paths containing spaces
**Impact:** The `_LOCAL_FILE_PATH_PATTERNS` use `\S+`, which stops at the first
whitespace. A path with a space in it (e.g. `/tmp/my report.txt`,
`~/Desktop/Valor/some file.json`) is only partially matched — the token captured
ends at the space, so `os.path.isfile` on the truncated token fails and nothing
attaches, while the truncated fragment is scrubbed from the text (leaving the
remainder of the path in prose).
**Disposition:** **Documented known limitation, explicitly out of scope for this
plan.** The existing detection primitive (`detect_local_file_reference`) already
uses `\S+` and shares this boundary; broadening the regex to handle quoted or
space-containing paths is a separate change to the shared detector, not this
flush-conversion fix. We accept the partial-scrub behavior rather than silently
mis-attaching. Noted here so a future builder does not mistake it for a bug
introduced by this work.

### Risk 5 (no new guard needed — stated for completeness): Large-file attachment
**Impact:** A converted path could point at a very large file.
**Disposition:** **No new size guard is added in this plan.** Attachment size
limits are enforced **downstream by the relay** (`bridge/telegram_relay.py` /
the SMTP relay) at send time — the same enforcement any agent-initiated
`send_message.py --file` attachment already passes through. Adding a redundant
flush-side size check would duplicate that authority. Stated explicitly so the
absence of a flush-side guard is a deliberate decision, not an oversight.

## Race Conditions

### Race 1: File reaped between reference and flush
**Location:** `agent/session_health.py` flush, existence check.
**Trigger:** `/tmp` file deleted after the agent referenced it but before the
terminal flush runs.
**Data prerequisite:** the file must exist at flush time to be attached.
**State prerequisite:** none beyond file existence.
**Mitigation:** `os.path.isfile` is checked at flush time (the last possible
moment before the payload is written); the relay re-checks at send time
(`telegram_relay.py:404`). A file that vanishes between flush and send is dropped
by the relay's own filter — no crash, and the text was already scrubbed. This is
the honest, best-effort outcome; documented, not prevented.

No other race conditions — the flush is fully synchronous and the dedup SETNX
already serializes concurrent finalizers.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2211] Synchronous pre-finalization self-draft re-draft turn
  (Direction B) — the general structural-race fix for *all* violation classes is
  a distinct, larger design; this plan deliberately fixes the local-path class at
  the flush seam. (Tracked by this same issue's remaining scope; if pursued it
  warrants its own issue.)
- **[FOLLOW-UP — not in this plan] Attachment delivery at the async fallback
  site.** `_deliver_deferred_self_draft_fallback` delegates to
  `deliver_system_notice(entry, message, *, telemetry_key)`, which has no
  `file_paths`/`attachments` parameter. Extending that shared, single-sanctioned
  system-notice seam to forward attachments — so the email failed/abandoned path
  could also attach converted files — is *possible* future work but is an unscoped
  signature change to a widely-called primitive. It is deliberately excluded here;
  this plan scopes the async site to text-scrub-only. If pursued, it warrants its
  own issue.
- Nothing else deferred — every relevant item for the local-path class is in
  scope for this plan.

<!-- Note: the [SEPARATE-SLUG] entry above references this issue itself as the
     home for the deferred Direction-B design; no separate anti-criterion row is
     required because the No-Go describes an approach not taken, not a forbidden
     code artifact in this PR. -->

## Update System

No update system changes required — this feature is purely internal (a
delivery-path bug fix in `agent/` + `bridge/`). No new deps, config, or
propagation steps.

## Agent Integration

No agent integration required — this is a bridge/worker-internal change on the
terminal delivery path. No new tool or MCP surface; the agent's existing
`tools/send_message.py --file` affordance is unchanged. The fix makes the
*fallback* flush behave correctly when the agent did NOT use that affordance in
time.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/subconscious-memory.md` or the relevant delivery-path
  doc if one exists; otherwise add a short note to
  `docs/features/bridge-worker-architecture.md` describing the validator-aware
  terminal flush (local-path→attachment conversion).
- [ ] If a dedicated self-draft/deferred-delivery doc exists, update it to state
  that the terminal flush now converts local-path references to attachments.

### Inline Documentation
- [ ] Docstring on `convert_local_paths_to_attachments` describing the existence
  gate (with `os.path.expanduser`), the secret-exclusion gate (realpath resolution,
  ANY dot-component / case-insensitive sensitive-extension / known-secret-basename /
  secrets-vault prefix, and that skipped == dead path), `finditer` all-occurrences
  scrub-the-trimmed-token behavior, the telemetry outcomes, and the never-raise
  contract.
- [ ] Correct the stale **inline comment** at `agent/session_health.py:2214`
  ("This sync flush never carries attachments, so file_paths is omitted") — it is
  an inline comment, not the function docstring — to describe the new
  local-path→attachment conversion behavior.

## Success Criteria

- [ ] A terminal-turn deferral whose text references an existing local file
  delivers that file as a real attachment with the path token scrubbed from the
  text.
- [ ] A terminal-turn deferral referencing a non-existent local path scrubs the
  dead path from the delivered text and attaches nothing (no dead path reaches
  the recipient).
- [ ] A terminal-turn deferral whose text is ONLY a non-existent local path
  delivers the canned "no longer available" notice (never an empty payload that
  the relay's line-394 guard would silently drop).
- [ ] A terminal-turn deferral referencing a secret file (`~/Desktop/Valor/.env`,
  vault `projects.json`, a `.pem`/`.key`/`.p12`, any dotfile, a secret in a
  dot-*directory* like `~/.ssh/id_rsa` or `~/.aws/credentials`, a known-secret
  basename, an uppercase-extension variant, or a symlink resolving into any of the
  above) NEVER attaches or delivers that file — the path is scrubbed and treated as
  a dead path (canned notice if it was the only content). The secret path/basename
  never appears in the delivered text OR in any log line.
- [ ] A narration-only final message that also carries an existing local path still
  attaches the file (conversion runs before the narration gate) on the sync flush
  — verified on BOTH the telegram and email-completed branches of
  `flush_deferred_self_draft_sync`. At the async fallback site
  (`_deliver_deferred_self_draft_fallback`) the same input has its path token
  scrubbed and delivers pathless narration text with NO attachment (that seam has
  no attachment channel).
- [ ] Adjacent prose/punctuation around a scrubbed path token survives intact
  (`,and more`, surrounding parens) — no over-capture.
- [ ] Non-local-path deferrals still deliver text-only payloads (no regression) —
  asserted on BOTH the telegram flush and the email-completed relay send path.
- [ ] The per-run dedup gate still fires exactly once (no double-send).
- [ ] Conversion failure degrades to today's behavior with a logged warning
  (never suppresses delivery).
- [ ] Each conversion outcome (attached / dead-scrubbed / secret-skipped /
  helper-error) emits its distinct log + counter; the secret-skip signal carries a
  count only, never the path.
- [ ] The sync flush's email-completed branch has attachment parity with its
  telegram branch (both build real outbox payloads via the attachment-accepting
  builders).
- [ ] **Both delivery sites never deliver a raw local path; only the sync flush
  attaches.** The sync flush (`flush_deferred_self_draft_sync`, both branches)
  attaches converted files; the async fallback
  (`_deliver_deferred_self_draft_fallback`) scrubs the path token and delivers
  clean text (or the canned notice) with NO attachment, because
  `deliver_system_notice` has no attachment parameter. (This replaces the earlier,
  incorrect "both sites attachment parity" claim — the async seam cannot carry an
  attachment.)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep` confirms `flush_deferred_self_draft_sync` references
  `convert_local_paths_to_attachments` (or the shared helper name chosen).
- [ ] **Correct-keyword grep-gate:** `grep -n "build_email_outbox_payload(" agent/session_health.py`
  shows the call using `file_paths=` (or a positional 4th arg), NEVER `attachments=`.
  `attachments` is only the emitted dict key (`agent/output_handler.py:259`), not a
  parameter; passing `attachments=` raises `TypeError` and the never-raise flush
  swallows it after the dedup lock is burned, silently dropping the reply (#1796).

## Team Orchestration

### Team Members

- **Builder (flush-conversion)**
  - Name: flush-builder
  - Role: Implement the `convert_local_paths_to_attachments` helper and wire it
    into both branches of `flush_deferred_self_draft_sync`.
  - Agent Type: builder
  - Domain: async/delivery, untrusted-input (paths)
  - Resume: true

- **Validator (flush-conversion)**
  - Name: flush-validator
  - Role: Verify success criteria, run the targeted unit tests, confirm no
    double-send and no regression on non-local-path deferrals.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: flush-docs
  - Role: Update the delivery-path feature doc and the two docstrings.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Implement conversion helper
- **Task ID**: build-helper
- **Depends On**: none
- **Validates**: tests/unit/test_message_drafter.py (add cases)
- **Assigned To**: flush-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `convert_local_paths_to_attachments(text)` to `bridge/message_drafter.py`,
  reusing the four filesystem `_LOCAL_FILE_PATH_PATTERNS` (exclude the `open -a`
  command pattern). Return the canonical 4-tuple
  `(scrubbed_text, attached_paths, dead_count, skipped_count)` (the single return
  shape referenced by the Observability and flush-wiring sections — no alternate
  result object). `dead_count`/`skipped_count` give the flush enough structure to
  emit telemetry without recomputing.
- Use `pattern.finditer(text)` (not `search`) so ALL occurrences on a line are
  detected, not just the first.
- Per token: (1) strip trailing punctuation (`.,;:)]}'"`); (2) existence-check with
  `os.path.isfile(os.path.expanduser(token))` (expand `~` — `os.path.isfile` does
  not); (3) resolve `realpath = os.path.realpath(expanded)` to defeat symlink
  bypass; (4) **secret-exclusion gate on the realpath** — skip if it has ANY
  dot-directory-or-file component (`os.path.normpath(realpath).split(os.sep)`, any
  component `startswith(".")` other than `.`/`..` — catches `~/.ssh/id_rsa` and
  `~/.aws/credentials` whose basenames are ordinary), OR a sensitive extension
  compared **case-insensitively** (`.env/.pem/.key/.p12/.pfx/.crt/.cer/.keychain`),
  OR a known secret basename (`id_rsa/id_ed25519/id_ecdsa/id_dsa/credentials/
  known_hosts/authorized_keys`), OR lives under the secrets vault
  (`~/Desktop/Valor/`, prefix-matched via normalized `os.path.commonpath`);
  (5) attach the expanded absolute path only if it survives all. Define the
  excluded-extensions set, known-secret-basename set, and vault root as named
  module-level constants (env-overridable; grain-of-salt "tunable" comment).
- **Scrub the trimmed token** (the same string existence-checked), NOT
  `match.group(0)`, so adjacent prose/punctuation is preserved. Both dead paths and
  secret-skipped paths are scrubbed but not attached. Collapse doubled whitespace.
- Internally defensive: any exception returns the original text + empty attach list.
- Unit tests: empty/None, single existing path, non-existent path, multiple mixed,
  two-on-one-line, tilde path, trailing-punctuation, adjacent-prose (`,and more` /
  `(...)`), path-only text, AND the full secret-exclusion matrix — dotfile
  basename, vault `projects.json`, `.pem`/`.key`/`.p12`, case-variant extension
  (`.ENV`/`.PEM`), **dot-directory secrets `~/.ssh/id_rsa` and `~/.aws/credentials`
  (ordinary basenames)**, a known-secret basename, and a **symlink pointing into a
  dot-dir / the vault** (must be caught via `realpath`) — plus a benign-control
  attach.

### 2. Wire helper into the terminal flush
- **Task ID**: build-flush
- **Depends On**: build-helper
- **Validates**: tests/unit/test_deferred_self_draft_completed.py
- **Assigned To**: flush-builder
- **Agent Type**: builder
- **Parallel**: false
- In `flush_deferred_self_draft_sync`, after acquiring the dedup gate, call the
  helper on the ORIGINAL `deferred_text` **before** the `is_narration_only` gate
  (concern 1):
  `scrubbed, attached, dead_count, skipped_count = convert_local_paths_to_attachments(deferred_text)`,
  then apply the narration gate to `scrubbed`. If `scrubbed` is narration-only, use
  `NARRATION_FALLBACK_MESSAGE` for the text but STILL pass `attached` to the builder
  — attachments survive narration substitution. This is the ATTACH site (both its
  branches build real outbox payloads).
  - Telegram branch: pass `file_paths=attached` into `build_telegram_outbox_payload`.
  - Email-completed branch: pass `file_paths=attached` into `build_email_outbox_payload`
    (its parameter is `file_paths`, per `agent/output_handler.py:168`; it maps that
    to the `attachments` dict key internally). Do NOT pass `attachments=` — it is not
    a parameter and raises `TypeError`, silently dropping the reply after the dedup
    lock is burned (#1796).
  - Empty-text-after-scrub guard (two-armed): basename caption when ≥1 file
    attached; canned "no longer available" notice when nothing attached
    (dead-path-only OR secret-excluded-only) — BLOCKER fix preventing the relay
    from dropping a text-and-file-less payload.
- Apply the SAME convert-before-narration reordering at the async fallback site
  `_deliver_deferred_self_draft_fallback` (`session_health.py:2302-2317`) but as
  **TEXT-SCRUB-ONLY**: call the helper on the original `deferred_text` first, use
  `scrubbed`, **discard `attached`** (this site delegates to
  `deliver_system_notice`, which has no attachment parameter), then apply the
  narration gate to `scrubbed`. Reduced empty-text guard: if `scrubbed` is empty,
  substitute the canned notice before passing to `deliver_system_notice` — never
  pass an empty string. **Do NOT attempt to attach or add an attachment param to
  `deliver_system_notice`** — that is out of scope (documented WHY in Key Elements
  + No-Gos follow-up note). The goal at this site is solely that no raw local path
  reaches the recipient.
- Emit the four telemetry outcomes (converted / dead-scrubbed / secret-skipped /
  helper-error) per the Observability spec — structured log + best-effort counter,
  all `try/except: pass`. The secret-skip log carries COUNT only, never the path.
- Correct the stale inline comment at `agent/session_health.py:2214` ("This sync
  flush never carries attachments, so file_paths is omitted") to describe the new
  conversion behavior — it is an inline comment, not the function docstring.

### 3. Validate
- **Task ID**: validate-flush
- **Depends On**: build-flush
- **Assigned To**: flush-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the two targeted unit test modules; verify all Success Criteria.
- Confirm dedup gate unchanged and non-local-path deferrals still text-only.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-flush
- **Assigned To**: flush-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update the delivery-path feature doc and both docstrings.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: flush-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification checks; confirm docs updated; final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Deferred-flush tests pass | `pytest tests/unit/test_deferred_self_draft_completed.py -q` | exit code 0 |
| Drafter/helper tests pass | `pytest tests/unit/test_message_drafter.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Flush references helper | `grep -c "convert_local_paths_to_attachments" agent/session_health.py` | output > 0 |
| Email builder called with correct keyword | `grep -n "build_email_outbox_payload(" agent/session_health.py` | uses `file_paths=` / positional, never `attachments=` |
| Helper exists | `grep -c "def convert_local_paths_to_attachments" bridge/message_drafter.py` | output > 0 |
| No stale xfails | `grep -rn 'xfail' tests/ \| grep -v '# open bug'` | exit code 1 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | critique | Empty-text guard misses dead-path-only case → payload with neither text nor file_paths → relay drops it (line 394), re-introducing the #1796 defect | Key Elements empty-text guard (case 2); build-flush task; dead-path-only test | Add `if not scrubbed.strip() and not attached: scrubbed = "(the referenced file is no longer available)"` before building either payload |
| Concern | critique | Tilde paths never attach (`os.path.isfile` doesn't expand `~`) | Technical Approach "expand `~` first"; build-helper task; tilde unit test | Existence check via `os.path.isfile(os.path.expanduser(token))`; attach expanded absolute path |
| Concern | critique | Duplicate paths on one line — `search` catches only first | Technical Approach "match ALL occurrences"; build-helper task; two-on-one-line test | Use `pattern.finditer(text)` not `pattern.search` |
| Concern | critique | Open Questions 1-2 locked into build spec while pending PM confirmation | Resolved Decisions section (all 3 resolved with documented defaults) | Build proceeds on defaults; no PM gate |
| Concern | critique | No end-to-end send-path validation through the relay's `os.path.isfile` filter | End-to-End Send-Path Validation test subsection; Test Impact relay test module | Drive converted + dead-path-only payloads through `bridge/telegram_relay.py` send path |
| Nit | critique | Task 2/Documentation referenced flush "docstring" but "never carries attachments" is an inline comment (line 2214) | build-flush task + Inline Documentation corrected | Corrected to "inline comment at `agent/session_health.py:2214`" |
| BLOCKER (rev 2) | critique | Secret-file exfiltration: broad `/Users/` + `~/` patterns would attach `.env`/`projects.json`/keys to an external recipient | Solution "Secret-file exclusion gate" Key Element; Risk 0; Technical Approach "Secret exclusion runs after existence"; build-helper step; Security/Secret-Exclusion test subsection; Success Criteria | Post-`isfile` gate skips dotfile / sensitive-extension / under-`~/Desktop/Valor/`; skipped == dead path; log counts only, never the path |
| Concern (rev 2) | critique | Narration gate fires before conversion → real file silently lost for the motivating incident (both `session_health.py:2187-2188` and `:2302-2311`) | Technical Approach "Convert BEFORE the narration gate"; build-flush step (both sites); Narration-Before-Conversion test subsection; Success Criteria | Convert on original `deferred_text` first; attachments survive `NARRATION_FALLBACK_MESSAGE` substitution; applied at both twin sites |
| Concern (rev 2) | critique | Greedy `\S+` scrub over-captures adjacent prose / orphans punctuation | Technical Approach "Scrub the trimmed token, not `match.group(0)`"; build-helper step; adjacent-prose test | Scrub the trimmed, punctuation-stripped token — the same string existence-checked — not `match.group(0)` |
| Concern (rev 2) | critique | Technical Approach scrub-then-guard bullet contradicted the case-2 guard, reintroducing the #1796 dead-path BLOCKER for `attached==[]` | Technical Approach "Scrub, then guard (two-armed)" | Bullet is now explicitly two-armed: basename caption when ≥1 attached, canned notice when nothing attached |
| Concern (rev 2) | critique | No distinct telemetry for conversion / dead-path / secret-skip / helper-error under the never-raise contract | Solution "### Observability" table; build-flush telemetry step; Success Criteria | Four structured logs + best-effort counters, all `try/except: pass`; secret-skip is count-only |
| Nit (rev 2) | critique | Email regression guard phrased telegram-centric | End-to-End "Email relay parity"; Test Impact transport-general guard; Success Criteria "BOTH the telegram flush and the email-completed relay send path" | Regression + attachment assertions exercised against the email send path too |
| BLOCKER (rev 3) | critique | Dot-DIRECTORY secrets slip the gate: basename-only `startswith(".")` misses `~/.ssh/id_rsa` (basename `id_rsa`) and `~/.aws/credentials` (basename `credentials`); Risk 0's SSH-key/`.aws/credentials` claim was false | Solution secret-gate Key Element (component-based); Technical Approach "Secret exclusion runs after existence" (realpath + component check); build-helper step; Security test subsection (dot-dir + symlink cases); Risk 0 rewritten TRUE; Success Criteria | After `os.path.realpath`, skip if ANY `os.path.normpath(realpath).split(os.sep)` component `startswith(".")` (excl. `.`/`..`); plus known-secret-basename set (`id_rsa`/`id_ed25519`/`id_ecdsa`/`id_dsa`/`credentials`/`known_hosts`/`authorized_keys`); skipped == dead path |
| Deferred (rev 3) | critique | Symlink into vault/dot-dir could bypass the gate | Same gate: `os.path.realpath` before the component/prefix checks (stated once in Technical Approach) | Resolve symlinks first; symlink-into-dot-dir test case added |
| Deferred (rev 3) | critique | Extension match was case-sensitive (`.ENV`/`.PEM` slipped) | Solution + build-helper: lowercase the extension before matching; case-variant test | `.env/.pem/.key/…` compared case-insensitively |
| Deferred (rev 3) | critique | Telemetry: single secret-skip signal must cover the new dot-component/known-basename arm too | Observability table note | One `deferred_flush_secret_paths_skipped` fires for ANY exclusion arm |
| Nit (rev 3) | critique | Email wire-key naming was transport-specific in the flush spec | Key Elements "Validator-aware flush": helper returns transport-neutral `attached_paths`; each builder maps to `file_paths`/`attachments` | Flush passes one neutral list; builders own the wire key |
| BLOCKER (rev 4) | critique | Internal contradiction: the async fallback `_deliver_deferred_self_draft_fallback` delegates to `deliver_system_notice`, which has NO attachment param — it cannot carry attachments, so the "both sites attachment parity" criterion + "email fallback payload has file_paths" test referenced an impossible seam | Key Elements "Validator-aware flush" (two sites, only sync flush attaches); Technical Approach "Convert BEFORE the narration gate" (sync=attach / async=text-scrub-only); build-flush task; Narration-Before-Conversion test subsection rewritten; Success Criteria "Both delivery sites never deliver a raw local path; only the sync flush attaches"; No-Gos follow-up note | Async site scoped to TEXT-SCRUB-ONLY (scrub path token, discard `attached`, canned-notice on empty); sync flush (both branches) attaches; "both sites attachment parity" claim removed; WHY documented so a future builder does not re-introduce it |
| Tech-debt (rev 4) | critique | Return-shape contradiction — helper's documented return shape drifted (2-tuple vs "result object with counts") | Helper Key Element pins the canonical 4-tuple `(scrubbed_text, attached_paths, dead_count, skipped_count)`; Observability + build-helper + build-flush all reference it | Single canonical 4-tuple everywhere; no alternate result object |
| Tech-debt (rev 4) | critique | Space-in-path partial scrub — `\S+` cannot capture paths containing spaces | Risk 4 (known limitation, out of scope) | Documented as inherited `\S+` boundary from the shared detector; accepted partial-scrub, not silent mis-attach |
| Tech-debt (rev 4) | critique | File-size guard — is a flush-side attachment size limit needed? | Risk 5 | No new guard: size limits enforced downstream by the relay; absence is deliberate |
| BLOCKER (rev 5) | critique | Plan told the builder to call `build_email_outbox_payload(..., attachments=...)` in three places, but the real signature (`agent/output_handler.py:168`) is `build_email_outbox_payload(session, chat_id, text, file_paths=None)` — `attachments` is only the emitted dict key, not a parameter; passing it raises `TypeError`, which the never-raise flush swallows AFTER the 1h dedup SETNX lock is burned, silently dropping the email reply (re-introducing #1796) | Key Elements "Validator-aware flush"; Technical Approach "Convert BEFORE the narration gate"; "Attachment sites are the two sync-flush branches only"; Task 2 email-completed branch; new Success-Criteria + Verification-table grep-gate | Every call site now uses `file_paths=attached` (both builders share that param; the email builder maps it to the `attachments` dict key internally). Grep-gate asserts `build_email_outbox_payload(` in `session_health.py` never uses `attachments=` |
| Nit (rev 5) | critique | Helper control-flow slightly over-specified in the plan | Left as-is (tech-debt) | Intent is unambiguous; the ordered per-token steps in Technical Approach / build-helper are retained as a builder aid — trimming risks losing the symlink/dot-component ordering guarantees. Deliberately not rewritten to keep this revision surgical |
| Nit (rev 5) | critique | Telemetry dashboard path reference | Observability section pointer | Counters mirror the existing `{project_key}:session-health:...` namespace surfaced by `curl localhost:8500/dashboard.json`; no new dashboard wiring in scope |

---

## Resolved Decisions

All three formerly-open questions are resolved with documented defaults so the
build is unambiguous and does not block on human input. If the PM later prefers a
different call on any of these, it is a one-line change to the helper/guard — but
the build proceeds on these defaults.

1. **Text-scrub behavior — DECIDED: scrub the path token from the delivered
   text** (cleaner UX). The flush attaches the file AND removes the bare path
   token from the prose; if the scrub empties the text, a basename caption
   (existing file) or the canned notice (dead-path-only) fills it. Chosen over
   leaving the raw path in the text, which would defeat the point of the
   validator flag.
2. **Non-existent path at flush time — DECIDED: fall back to the canned notice.**
   When the referenced file no longer exists at flush time, the dead path is
   scrubbed from the text and nothing attaches; if that empties the text, the
   canned `"(the referenced file is no longer available)"` notice is substituted
   (the BLOCKER-fix guard). This guarantees the payload is never text-and-file
   empty, so the relay's line-394 guard never silently drops the reply.
3. **Scope — DECIDED: local-path violation class only (Direction A).** The
   general structural race for other violation classes (Direction B, synchronous
   pre-finalization re-draft) stays out of scope and is captured under No-Gos.
