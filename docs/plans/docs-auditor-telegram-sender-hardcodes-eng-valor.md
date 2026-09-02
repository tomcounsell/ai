---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-09-02
tracking: https://github.com/tomcounsell/ai/issues/2754
last_comment_id: none
---

# docs-auditor Telegram sender hardcodes "Eng: Valor" instead of using resolve_eng_group

## Problem

The docs-auditor sends two Telegram notifications per run — a withheld-fixes alert on
the zero-diff path, and a pass summary after it opens a rotation PR. Both go through
`reflections/docs_auditor.py::_send_telegram_notification`, whose argv pins the
destination chat to a string literal:

```python
["valor-telegram", "send", "--chat", "Eng: Valor", message]
```

Meanwhile `audit()` already accepts a `project_key`, and `reflections/utilities.py`
already exposes `resolve_eng_group(project)` — the per-project resolver that
`reflections/sdlc_upvote_lanes.py:441` uses to find a project's `Eng: X` group and
numeric chat id.

**Current behavior:**

Every docs-auditor notification lands in `Eng: Valor` regardless of which project's
docs were audited. Today that is accidentally correct, because the scheduled auditor
only ever runs with `VALOR_PROJECT_KEY` unset (defaulting to `valor`). It becomes wrong
the first time the auditor is pointed at a second project: `popoto`'s withheld-fix alert
would page the `valor` engineer group. Live resolution across the current
`projects.json` confirms the destinations diverge —
`valor -> Eng: Valor`, `popoto -> Eng: Popoto`, `cuttlefish -> Eng: Cuttlefish`,
`psyoptimal -> Eng: PsyOPTIMAL`, `royop -> None`.

**Desired outcome:**

`_send_telegram_notification` resolves its destination from the auditor's `project_key`
through `resolve_eng_group`, sends to the resolved numeric `chat_id`, and — when the
project has no properly-configured `Eng:` group — declines to send rather than
misrouting. The `valor` project keeps a literal `Eng: Valor` fallback so today's
behavior is bit-for-bit preserved on the only path that runs in production.

## Freshness Check

**Baseline commit:** `3b6eb651b78fb77b295b7e9a4741b1f614876f1b`
**Issue filed at:** 2026-08-13T05:15:58Z
**Disposition:** Minor drift

**File:line references re-verified:**

- `reflections/docs_auditor.py:1023` — issue claimed the hardcoded argv — **drifted to
  `:1490`**. Literal is byte-identical.
- `reflections/docs_auditor.py:1018` — issue claimed `_send_telegram_notification` —
  **drifted to `:1486`**. Signature still `(message: str) -> None`.
- Call site 1 (zero-diff withheld alert) — **now `:2434`**, inside `audit()`'s step-6
  zero-diff gate. Still present.
- Call site 2 (step-9 pass summary) — **now `:2521`**. Still present.
- `reflections/utilities.py:310` — `resolve_eng_group` — **unchanged**, same line, same
  `(project: dict) -> tuple[str, int] | None` signature.
- `reflections/sdlc_upvote_lanes.py:429` — issue claimed the consuming call site —
  **drifted to `:441`**. Claim holds: `eng_group = resolve_eng_group(project)`.
- `tests/unit/test_docs_auditor_substrate.py` — the `notify.call_count` /
  `notify.call_args.args[0]` assertions the issue predicted would survive are at
  `:1376`, `:1413`, `:1453`, `:1463-1464`, `:1487`, `:1784`. All patch the function
  object wholesale; none inspect the chat argument. Claim holds.

**Cited sibling issues/PRs re-checked:**

- PR **#2728** — merged 2026-08-13T05:24:24Z, eight minutes after this issue was filed.
  It is the PR that added the second call site. `git log` confirms it touched
  `reflections/docs_auditor.py` (`45d0961f9`). It did not touch the sender's argv.
- Issue **#2711** — CLOSED. The docs-auditor invented-rename incident that spawned the
  #2728 review. Resolved by `a9205b065` (deleting the rename channel, #2741/#2842).
  Irrelevant to the routing target.

**Commits on main since issue was filed (touching referenced files):**

Seven commits touched `reflections/docs_auditor.py`:

- `7ccd27d5d` fix(docs-auditor): review-gate every write, report broken .md links —
  *irrelevant to routing*; reshaped `audit()`'s write/commit contract, which is why the
  call sites moved ~1400 lines down.
- `97672207d` Wave 1: batched hotfix sweep — *irrelevant*.
- `659f1d0e4` Move completed plans to docs/archive/plans-completed/ — *irrelevant*.
- `15023ee97` fix(reflections): resolve package-relative doc refs — *irrelevant*.
- `a9205b065` Delete the docs-auditor rename channel (#2741) — *irrelevant*.
- `ffbae5b1d` fix(docs-auditor): migration-context hatch — *irrelevant*.
- `45d0961f9` fix(docs-auditor): word-anchor stale terms (#2728) — *the PR that added
  call site 1*. Already accounted for by the issue.

One commit touched `reflections/utilities.py`: `049914b32` (deleting
`bridge/session_logs.py` / `models/reflections.py` shims) — did not touch
`resolve_eng_group`.

None of the seven changed the root cause. Disposition stands at **Minor drift**: line
numbers moved, claims hold.

**Active plans in `docs/plans/` overlapping this area:** none. The most recent plans
(`overclaim-guard-greps-whole-worktree`, `promise-gate-recorded-obligations`,
`wave4-hooks-guards-gates`) touch guards, expectations, and hooks — no overlap with
reflections Telegram routing.

**Bug still reproducible:** yes, by inspection and by execution. `resolve_eng_group`
returns distinct chats per project (verified live against `projects.json`, results
above), while the sender's argv is a constant. A `VALOR_PROJECT_KEY=popoto` run would
notify `Eng: Valor`.

## Prior Art

- **PR #2721**: *Autonomous SDLC pickup on upvote-labeled issues (#2717)* — merged
  2026-08-12. This is the PR that **introduced** `resolve_eng_group` and its first
  consumer in `sdlc_upvote_lanes.py`. It is the pattern this fix copies, including the
  decision to route by numeric `chat_id` rather than group name. Successful; not a
  failed prior attempt.
- **PR #2728**: *fix(docs-auditor): word-anchor stale terms and enforce a path-existence
  invariant* — merged 2026-08-13. Added the second hardcoded call site (the zero-diff
  withheld alert). The reviewer flagged the hardcode during the cycle-6 consensus review
  and severed it into this issue rather than blocking the merge. Not a failed fix — a
  deliberate deferral.
- **Issue #2629** (CLOSED): *react() cannot derive a transport: reflection emoji
  reactions still RPUSH to `telegram:outbox:0`* — the same defect class one layer down
  (a reflection hardcoding a Telegram destination instead of deriving it). Its
  resolution is prior evidence that hardcoded reflection transports get fixed by
  deriving from project/session context, not by adding a second constant.
- No prior attempt to change `docs_auditor`'s sender exists. `gh issue list --search
  "resolve_eng_group"` over closed issues returns empty.

**No "Why Previous Fixes Failed" section** — there are no prior failed fixes for this
defect.

## Research

No relevant external findings — proceeding with codebase context. This is a purely
internal routing change: no new libraries, no external APIs, no ecosystem patterns.
`valor-telegram` is a first-party CLI in this repo. Phase 0.7 skipped per the skill's
"purely internal work" exemption.

## Data Flow

1. **Entry point**: the reflection scheduler invokes `run_docs_auditor()`
   (`reflections/docs_auditor.py:2263`).
2. **Project identity**: `run_docs_auditor` reads
   `project_key = os.environ.get("VALOR_PROJECT_KEY", "valor")` (`:2299`) and passes it
   into `audit(..., project_key=project_key)` (`:2364`). The alternate caller at `:2794`
   does the same.
3. **`audit()`**: runs the docs pass. On the zero-diff-with-withheld-fixes path (`:2431`)
   it calls `_send_telegram_notification(msg)`; on the PR-opened path (`:2521`) it calls
   it again with the pass summary. `project_key` is a live local at both points.
4. **`_send_telegram_notification`** (`:1486`): **today** discards all project context and
   shells out with a constant `--chat "Eng: Valor"`. **After this change** it resolves
   `project_key -> project dict -> (group_name, chat_id)` and shells out with the numeric
   id.
5. **Resolution**: `reflections.utilities.load_local_projects()` reads
   `~/Desktop/Valor/projects.json` and returns dicts each carrying a `slug` key;
   `resolve_eng_group(project)` scans `project["telegram"]["groups"]` for the first key
   with the literal `Eng:` prefix and returns `(group_name, chat_id)` or `None`.
6. **Output**: `valor-telegram send --chat <chat_id> <message>`.
   `tools/valor_telegram.py::cmd_send` tries `resolve_chat(args.chat)` first and falls
   back to accepting the raw value when `args.chat.lstrip("-").isdigit()`
   (`tools/valor_telegram.py:836`) — so a numeric id is accepted and bypasses the
   ambiguity-tolerant name cascade entirely.

## Architectural Impact

- **New dependencies**: none external. One new intra-package import edge:
  `reflections.docs_auditor -> reflections.utilities`. `docs_auditor` currently imports
  only `config.machine`, `config.settings`, and stdlib. `utilities` imports only
  `config.settings` at module scope, so **no import cycle** is created. The import is
  placed at module scope (not function-local) — there is no cycle to defend against and
  a function-local import would be cargo-culted defensiveness.
- **Interface changes**: `_send_telegram_notification(message)` gains a keyword-only
  parameter with a default: `_send_telegram_notification(message, *,
  project_key=DEFAULT_PROJECT_KEY)`. Private function, two call sites, both updated.
  The default keeps every existing `patch(...)`-style test target valid.
- **Coupling**: increases coupling `docs_auditor -> utilities` by one function pair,
  and *decreases* coupling to a hardcoded deployment fact. Net positive.
- **Data ownership**: unchanged. `projects.json` remains the single source of truth for
  chat routing; this change makes one more consumer read it instead of duplicating a
  value from it.
- **Reversibility**: trivial. Revert the commit; the argv literal returns.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

One private function, two call sites, three or four new tests, one doc line. The only
genuine decision (the fallback rule, below) is settled in this plan.

## Prerequisites

No prerequisites — this work has no external dependencies. `projects.json` is read at
runtime and already required by the reflections package; the new tests stub
`load_local_projects` rather than depending on the real file.

## Solution

### Key Elements

- **`_resolve_notify_chat(project_key) -> str | None`** (new, private, in
  `reflections/docs_auditor.py`): maps a project key to the string the `--chat` flag
  should carry, or `None` meaning *do not send*. Owns the entire resolution + fallback
  policy so the sender stays a thin subprocess wrapper.
- **`_send_telegram_notification(message, *, project_key=DEFAULT_PROJECT_KEY)`**
  (modified): asks `_resolve_notify_chat` for a destination; on `None`, logs a warning
  naming the project key and returns without shelling out; otherwise sends as before.
- **Two updated call sites**: both pass `project_key=project_key`, which is already in
  scope at each.
- **Named constants** replacing the literal: `DEFAULT_PROJECT_KEY = "valor"` and
  `FALLBACK_ENG_CHAT = "Eng: Valor"`. The string survives in exactly one place, as a
  named fallback rather than an inline argv element.

### Flow

Scheduled docs-auditor run → `audit(project_key=...)` reaches a notify point →
`_resolve_notify_chat(project_key)` → **resolved** → `valor-telegram send --chat <id>` →
alert lands in that project's own `Eng:` group.

Alternate branch: → `_resolve_notify_chat` returns `None` → `logger.warning` naming the
project → **no message sent** → the run's return dict and liveness record are unchanged,
so the finding still surfaces in the aggregated reflection report.

### Technical Approach

The resolution ladder in `_resolve_notify_chat`, in order:

1. Call `load_local_projects()` and find the entry whose `slug == project_key`.
2. If found, call `resolve_eng_group(project)`. On a `(name, chat_id)` tuple, return
   `str(chat_id)` — the numeric id, not the name. Rationale: `valor-telegram send
   --chat` accepts either, but a name goes through `resolve_chat_id`'s three-stage
   cascade which, in non-strict mode, silently picks the most-recent candidate on an
   ambiguous match. A numeric id short-circuits that (`tools/valor_telegram.py:836`).
   `sdlc_upvote_lanes` already routes by id for the same reason.
3. If the project is missing from `projects.json`, or `resolve_eng_group` returns `None`:
   return `FALLBACK_ENG_CHAT` **only when `project_key == DEFAULT_PROJECT_KEY`**,
   otherwise return `None`.
4. Wrap the whole ladder in `except Exception` → log a warning → fall through to the
   step-3 rule. The notification is best-effort by design and must never break an audit
   run; but the swallow logs, so it is observable.

**On the fallback rule.** The issue says "keep `Eng: Valor` as the fallback when the
resolver returns nothing." Taken unconditionally that re-creates the exact bug being
fixed: `popoto` with a malformed `Eng:` entry would still page `Eng: Valor`.
`resolve_eng_group`'s own docstring is explicit that a project with no properly
configured group "must be skipped by the caller, never routed somewhere
plausible-looking." Scoping the fallback to `project_key == "valor"` satisfies both: the
one path that runs in production today keeps byte-identical behavior even if
`projects.json` is unreadable on some machine, and no *other* project can ever be
misrouted. This is a deliberate narrowing of the issue's stated fix, recorded here so
review can rule on it.

**On `project_key` normalization.** `run_docs_auditor` already normalizes
(`.strip() or "valor"`, `:2299`). `_resolve_notify_chat` does not re-normalize; it
treats whatever it receives as opaque and lets a non-matching key fall to the step-3
rule, which for anything other than the literal `"valor"` means *skip*. An empty string
therefore skips rather than falling back — correct, since an empty key is a
misconfiguration, not the valor project.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `_send_telegram_notification`'s existing handlers (`FileNotFoundError`,
      `subprocess.TimeoutExpired`, bare `Exception`) each already log a
      `logger.warning`. They are unchanged by this work and stay untested here — no
      behavior of theirs changes.
- [ ] The **new** `except Exception` in `_resolve_notify_chat` gets a test: patch
      `load_local_projects` to raise, assert (a) a warning is logged and (b) the valor
      path still falls back to `Eng: Valor` while a non-valor path sends nothing.

### Empty/Invalid Input Handling

- [ ] `project_key=""` → `_resolve_notify_chat` returns `None` → no send, warning
      logged. Tested.
- [ ] `project_key` naming a project absent from `projects.json` → `None` → no send.
      Tested.
- [ ] `resolve_eng_group` returning `None` for a present non-valor project (the `royop`
      shape) → `None` → no send. Tested.
- [ ] `message=""` — out of scope for behavior change; the sender passes it through
      today and will continue to. No new handling.
- [ ] Not agent-output processing; no silent-loop risk.

### Error State Rendering

- [ ] The user-visible surface here is the Telegram alert itself. The failure mode this
      plan introduces is *no alert*. That must be observable, so the skip path asserts a
      `logger.warning` carrying the `project_key` — verified with `caplog`, not just by
      asserting `subprocess.run` was not called.
- [ ] The audit's return dict (`status`, `findings`, `summary`) is untouched on the skip
      path, so the withheld-fix finding still reaches the aggregated reflection report
      even when the Telegram alert is suppressed. Asserted in the substrate tests.

## Test Impact

- [ ] `tests/unit/test_docs_auditor_substrate.py` — the eleven `patch(
      "reflections.docs_auditor._send_telegram_notification")` sites (`:257`, `:292`,
      `:310`, `:330`, `:1376`, `:1413`, `:1453`, `:1481`, `:1687`, `:1709`, `:1761`) —
      **UPDATE (no-op expected)**: they patch the function object and assert on
      `call_count` / `call_args.args[0]`. The new parameter is keyword-only with a
      default and the message stays positional, so `args[0]` still resolves. Verify by
      running the file; only patch if something breaks.
- [ ] `tests/unit/test_docs_auditor_substrate.py::test_..._withheld_alert` (`:1376`,
      `:1413`) and the step-9 summary test (`:1453`) — **UPDATE**: add a
      `notify.call_args.kwargs["project_key"] == <expected>` assertion to each, so the
      two call sites are pinned to actually thread the key. Without this the sender could
      be fixed while the call sites keep defaulting, and every test would still pass.
- [ ] `tests/unit/test_docs_auditor_substrate.py` — **ADD** a new
      `TestTelegramChatRouting` class exercising `_send_telegram_notification` directly
      with `reflections.docs_auditor.subprocess.run` patched (the established pattern at
      `:1291`, `:1350`). Cases: resolved non-valor project sends the numeric id; valor
      with an unresolvable group falls back to `Eng: Valor`; non-valor with no `Eng:`
      group sends nothing and warns; `load_local_projects` raising is swallowed and
      warned.
- [ ] `tests/unit/reflections/test_utilities_resolve_eng_group.py` — **no change**.
      `resolve_eng_group` itself is untouched; its direct tests stay as-is.
- [ ] `tests/unit/reflections/test_sdlc_upvote_lanes.py` — **no change**. The other
      consumer is untouched.
- [ ] `tests/unit/reflections/test_docs_auditor_git_surface.py` — **no change**. Covers
      the git surface, not notifications.

## Rabbit Holes

- **Sweeping the four sibling reflections in this lane.** `expectation_reconciler.py:181`,
  `sentry_triage.py:701`, `stall_advisory.py:459`, and `sdlc_progress.py:792` carry the
  identical argv literal, and three of them genuinely iterate projects. It is tempting to
  fix all five at once. Don't: every one of those senders takes `message` only, and each
  is called from an escalation path several frames below where a project dict exists.
  That is four independent signature refactors, not four one-line swaps. Filed as #3072.
- **Building the shared `send_eng_telegram` helper in `utilities.py` now.** The right
  home for the resolve-and-send logic once there are five callers. With one caller it is
  speculative generality, and it would force this lane to design an interface for four
  modules it isn't touching. `_resolve_notify_chat` stays private in `docs_auditor.py`;
  #3072 promotes it when it has a second consumer.
- **Fixing the prompt-level chat references** in
  `reflections/agents/circuit_health_gate.py:64,75` and
  `reflections/agents/system_health_digest.py:7,137`. Those are English instructions
  embedded in LLM prompts, not argv. Changing them is a prompt-engineering problem with a
  completely different verification story.
- **Making `docs_auditor` a `run_per_project_audit` consumer.** It reads
  `VALOR_PROJECT_KEY` instead of iterating projects, which is arguably the deeper
  single-project assumption. Restructuring the auditor's driver loop is a separate,
  much larger piece of work and is not what this issue asks for.
- **Adding a `--chat-id` flag to `valor-telegram send`.** Unnecessary: `--chat` already
  accepts a numeric id via the digit fallback at `tools/valor_telegram.py:836`.

## Risks

### Risk 1: A signature change silently un-pins the existing notify assertions

**Impact:** Eleven test sites patch `_send_telegram_notification`. If the message stopped
being the first positional argument, `call_args.args[0]` would raise `IndexError` across
the file — noisy, so this fails loudly rather than silently. The real risk is the
inverse: the sender gets fixed, the *call sites* keep defaulting to `valor`, and nothing
catches it because no test inspects the chat or the key.
**Mitigation:** Make the new parameter keyword-only with a default (message stays
positional), and add explicit `call_args.kwargs["project_key"]` assertions at the two
call-site tests. That pins the threading, not just the sender.

### Risk 2: `load_local_projects()` does disk I/O on a notification path

**Impact:** `load_local_projects` reads and JSON-parses `~/Desktop/Valor/projects.json`
on every notify. On an iCloud-synced path a stall would delay the audit's return. The
file is small and the path is local-cached, so this is a minor concern, but it is new
I/O on a path that previously had none.
**Mitigation:** The call happens at most twice per audit run, and only on paths that are
already about to spawn a `valor-telegram` subprocess with a
`settings.timeouts.git_subprocess_s` budget — orders of magnitude more expensive than the
read. Additionally the whole ladder is inside `try/except Exception`, so an I/O failure
degrades to the fallback rule rather than propagating. No caching is added: a cache would
make the auditor read a stale `projects.json` across a long-lived process, which is the
same class of bug as `_BASENAME_INDEX_CACHE` (#2759).

### Risk 3: Routing by numeric id changes the observable send for the valor path

**Impact:** Today `valor` notifications go out as `--chat "Eng: Valor"`; after the change
they go out as `--chat "-100..."`. If the numeric id in `projects.json` were stale or
wrong, alerts would silently go to the wrong place or nowhere — a worse failure than the
name path, which resolves against live history.
**Mitigation:** The id comes from the same `projects.json` that `bridge/routing.py`
already trusts to grant the engineer persona, and `sdlc_upvote_lanes` has been routing
`Eng:` announcements by id since PR #2721 without incident. The `valor` fallback also
still uses the *name*, so a corrupted id for the valor project degrades to today's exact
behavior. Post-merge, confirm one real docs-auditor alert lands in `Eng: Valor` before
considering the lane closed.

### Risk 4: The narrowed fallback suppresses an alert an operator expected

**Impact:** A project with a typo'd `Eng:` group gets *no* docs-auditor Telegram alert
instead of a misrouted one. Silence can be mistaken for "nothing to report."
**Mitigation:** The skip logs a `logger.warning` naming the project key, and the audit's
`findings`/`summary` are unchanged, so the withheld-fix finding still reaches the
aggregated reflection report through the non-Telegram channel. This mirrors the
established precedent at `sdlc_upvote_lanes.py:443-447`, which deliberately returns
status `"ok"` (not `"skipped"`) with a `"no Eng: group configured"` finding for exactly
this case, so the config gap surfaces to the operator.

## Race Conditions

No race conditions identified. `_send_telegram_notification` and `_resolve_notify_chat`
are synchronous, single-threaded, and hold no shared mutable state. `load_local_projects`
performs a read-only file read with no caching. The `valor-telegram send` subprocess is
fire-and-forget with a bounded timeout and no ordering requirement relative to any other
audit step — the two call sites are on mutually exclusive branches (zero-diff vs.
PR-opened), so they can never both fire in one run.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #3072] The identical hardcoded argv in the four sibling reflections
  (`expectation_reconciler.py:181`, `sentry_triage.py:701`, `stall_advisory.py:459`,
  `sdlc_progress.py:792`). Each needs a per-module signature refactor to get a project
  into scope at the send site, which is a different size of job. This PR must not touch
  those four files.
- [SEPARATE-SLUG #3072] Promoting the resolve-and-send logic into a shared
  `reflections/utilities.py::send_eng_telegram` helper. That belongs with the sweep that
  gives it a second caller; building it here would be an interface designed against
  hypothetical consumers.
- [EXTERNAL] Confirming by eye that a real post-merge docs-auditor alert lands in the
  `Eng: Valor` Telegram group. Requires a human reading a Telegram chat; no automated
  check can substitute.

## Update System

No update system changes required — this is an internal change to one reflection module
with no new dependencies, no new config keys, no new files to propagate, and no
migration. `/update` picks it up as ordinary code on the next pull; the reflection
scheduler runs it in-process, so `worker-restart` (already part of `/update`) is
sufficient to pick up the new behavior.

## Agent Integration

No agent integration required — this is a reflections-internal change. `_send_telegram_
notification` is a private function invoked only from `audit()` inside the same module;
no new CLI entry point in `pyproject.toml [project.scripts]` is needed, and the bridge
does not import it. The existing `valor-telegram` entry point it shells out to is already
registered and unchanged.

## Documentation

### Feature Documentation

- [ ] Update `docs/features/docs-auditor.md:45` — it currently states the auditor
      "notifies the `Eng: Valor` Telegram chat". Replace with the per-project routing
      rule: notifications go to the audited project's `Eng:` group as resolved by
      `resolve_eng_group`, falling back to `Eng: Valor` only for the `valor` project,
      and are suppressed with a logged warning for any other project lacking a
      configured group.
- [ ] No new entry in `docs/features/README.md` — `docs-auditor.md` is already indexed.

### External Documentation Site

Not applicable — this repo has no Sphinx/MkDocs site.

### Inline Documentation

- [ ] Docstring on `_resolve_notify_chat` stating the ladder and, explicitly, *why* the
      fallback is scoped to `valor` — the reasoning is non-obvious and a future reader
      will otherwise "simplify" it back into the bug.
- [ ] Update `_send_telegram_notification`'s docstring to name the new parameter and the
      no-destination-means-no-send contract.
- [ ] Update `audit()`'s docstring for `project_key` (`:1533`), which currently reads
      "Used for vault-namespaced rotation keys" — it now also selects the notification
      destination.

## Success Criteria

- [ ] `reflections/docs_auditor.py` contains no `"--chat", "Eng: Valor"` argv adjacency.
- [ ] `_send_telegram_notification` resolves its destination through
      `resolve_eng_group`, and both call sites pass `project_key=project_key`.
- [ ] A non-`valor` project with a configured `Eng:` group receives its notification at
      that group's numeric `chat_id`.
- [ ] A non-`valor` project with no configured `Eng:` group receives no notification and
      produces a `logger.warning` naming the project key.
- [ ] The `valor` project's behavior is unchanged when `projects.json` is unreadable.
- [ ] `docs/features/docs-auditor.md` no longer claims a fixed destination chat.
- [ ] This PR touches none of the four files named in #3072.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] No xfail conversions needed — `tests/unit/test_docs_auditor_substrate.py` contains
      no `xfail` markers related to this bug (verified at plan time).

## Team Orchestration

### Team Members

- **Builder (routing)**
  - Name: `auditor-routing-builder`
  - Role: implement `_resolve_notify_chat`, modify the sender, thread `project_key`
    through both call sites, add and update tests
  - Agent Type: builder
  - Resume: true

- **Documentarian**
  - Name: `auditor-routing-docs`
  - Role: update `docs/features/docs-auditor.md` and the three docstrings
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: `auditor-routing-validator`
  - Role: verify every Success Criteria row and run the Verification table
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement per-project chat resolution

- **Task ID**: build-routing
- **Depends On**: none
- **Validates**: `tests/unit/test_docs_auditor_substrate.py`,
  `tests/unit/reflections/test_docs_auditor_git_surface.py`
- **Informed By**: Freshness Check (current line numbers), Technical Approach (the
  ladder and the fallback narrowing)
- **Assigned To**: `auditor-routing-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add module-scope `DEFAULT_PROJECT_KEY = "valor"` and `FALLBACK_ENG_CHAT = "Eng: Valor"`
  near the existing module constants in `reflections/docs_auditor.py`.
- Add a module-scope `from reflections.utilities import load_local_projects,
  resolve_eng_group` (no cycle — verified in Architectural Impact).
- Add `_resolve_notify_chat(project_key: str) -> str | None` implementing the four-step
  ladder from Technical Approach. Return `str(chat_id)`, never the group name, on the
  resolved path.
- Change `_send_telegram_notification(message: str)` (currently `:1486`) to
  `_send_telegram_notification(message: str, *, project_key: str = DEFAULT_PROJECT_KEY)`.
  Message stays the first positional parameter. Resolve first; on `None`, emit
  `logger.warning` naming the project key and return without invoking `subprocess.run`.
- Update the zero-diff withheld-alert call site (currently `:2434`) and the step-9 pass
  summary call site (currently `:2521`) to pass `project_key=project_key`.
- Leave the existing `FileNotFoundError` / `TimeoutExpired` / `Exception` handlers in
  the sender exactly as they are.

### 2. Add and update tests

- **Task ID**: build-tests
- **Depends On**: build-routing
- **Validates**: `tests/unit/test_docs_auditor_substrate.py`
- **Assigned To**: `auditor-routing-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add a `TestTelegramChatRouting` class to `tests/unit/test_docs_auditor_substrate.py`
  patching `reflections.docs_auditor.subprocess.run` (pattern at `:1291`/`:1350`) and
  `reflections.docs_auditor.load_local_projects`. Cases:
  1. non-`valor` project with a configured `Eng:` group → `--chat` carries
     `str(chat_id)`, not the group name;
  2. `valor` with `load_local_projects` returning `[]` → `--chat` carries `Eng: Valor`;
  3. non-`valor` project resolving to `None` → `subprocess.run` not called **and** a
     `logger.warning` naming the project key (assert via `caplog`, not just the
     non-call);
  4. `project_key=""` → no send, warning;
  5. `load_local_projects` raising → swallowed, warning logged, valor falls back and
     non-valor skips.
- Add `notify.call_args.kwargs["project_key"]` assertions to the existing withheld-alert
  tests (`:1376`, `:1413`) and the step-9 summary test (`:1453`) so both call sites are
  pinned to thread the key.
- Run the full file and confirm the other eight `patch(...)` sites still pass unchanged.

### 3. Documentation

- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: `auditor-routing-docs`
- **Agent Type**: documentarian
- **Parallel**: false
- Rewrite the destination sentence at `docs/features/docs-auditor.md:45`.
- Write the `_resolve_notify_chat` docstring including the rationale for the
  valor-only fallback.
- Update `_send_telegram_notification`'s docstring and `audit()`'s `project_key`
  docstring line (`:1533`).

### 4. Final Validation

- **Task ID**: validate-all
- **Depends On**: build-routing, build-tests, document-feature
- **Assigned To**: `auditor-routing-validator`
- **Agent Type**: validator
- **Parallel**: false
- Execute every row of the Verification table.
- Confirm each Success Criteria checkbox, including the #3072 anti-criterion.
- Report pass/fail.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| No hardcoded chat argv | `grep -c '"--chat", "Eng: Valor"' reflections/docs_auditor.py` | match count == 0 |
| Sender uses the resolver | `grep -c 'resolve_eng_group' reflections/docs_auditor.py` | output > 0 |
| Both call sites thread the key | `grep -c 'project_key=project_key' reflections/docs_auditor.py` | output > 1 |
| Routing tests pass | `scripts/pytest-clean.sh tests/unit/test_docs_auditor_substrate.py -q` | exit code 0 |
| Git-surface tests still pass | `scripts/pytest-clean.sh tests/unit/reflections/test_docs_auditor_git_surface.py -q` | exit code 0 |
| Resolver's own tests untouched and green | `scripts/pytest-clean.sh tests/unit/reflections/test_utilities_resolve_eng_group.py tests/unit/reflections/test_sdlc_upvote_lanes.py -q` | exit code 0 |
| Anti-criterion: #3072 files untouched | `git diff --name-only origin/main...HEAD \| grep -c 'expectation_reconciler\|sentry_triage\|stall_advisory\|sdlc_progress'` | match count == 0 |
| Feature doc no longer pins the chat | `grep -c 'notifies the .Eng: Valor. Telegram chat' docs/features/docs-auditor.md` | match count == 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

**Verdict (2026-09-02, /do-plan-critique cycle 1, FULL depth):** NEEDS REVISION — 1 blocker, 5 concerns, 3 nits.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Scope & Value | The Problem's premise is void. `run_docs_auditor` calls `audit(..., project_key=project_key, repo_root=PROJECT_ROOT)` (`reflections/docs_auditor.py:2360-2365`) and `PROJECT_ROOT = Path(__file__).parent.parent` (`:38`), so the auditor always audits **this** repo regardless of `VALOR_PROJECT_KEY`. `project_key` only namespaces the rotation Redis hash and the vault-drift key. Data Flow step 2 omits the `repo_root=PROJECT_ROOT` argument on that same call. The fix therefore removes no misroute and creates one: `VALOR_PROJECT_KEY=popoto` would route an **ai-repo** docs audit to `Eng: Popoto`. | pending | Resolve the destination from the repo actually audited, not from `project_key`. `load_local_projects()` returns `working_directory` as an absolute string (`reflections/utilities.py:114`), so the lookup is by resolved path: `next((p for p in load_local_projects() if Path(p["working_directory"]).resolve() == repo_root.resolve()), None)`. That threads `repo_root` rather than `project_key` into `_resolve_notify_chat`, which changes the signature Task 1 and the Success Criterion "both call sites pass `project_key=project_key`" currently pin — settle before build, not during. |
| CONCERN | History & Consistency | The only valor-path success criterion is the `projects.json`-unreadable fallback, yet Risk 3 states the *normal* valor argv changes from `--chat "Eng: Valor"` to `--chat "-100..."`. The production path has no criterion and no test: `TestTelegramChatRouting` case 2 is the fallback again, case 1 is a non-valor project. | pending | Add a criterion and case: stub `load_local_projects` to return `[{"slug": "valor", "telegram": {"groups": {"Eng: Valor": {"chat_id": -1001234567890}}}}]` and assert the `subprocess.run` argv contains the `str` `"-1001234567890"`, not `"Eng: Valor"`. Without it, Risk 3's mitigation rests entirely on a manual post-merge human check. |
| CONCERN | Scope & Value | The two Open Questions request a ruling, but the Solution, Technical Approach, Success Criteria, and Step-by-Step Tasks already implement one answer to each. A builder gets no signal either decision is provisional. | pending | Record the decision inline in Technical Approach and delete the questions, or mark task `build-routing` blocked on the ruling. The narrowing rationale must live in the plan body, not only in the planned `_resolve_notify_chat` docstring — `FALLBACK_ENG_CHAT` reachable only when `project_key == DEFAULT_PROJECT_KEY` is exactly the shape a future reader "simplifies" back into the bug. |
| CONCERN | Risk & Robustness | Ladder step 1 treats "project not in `load_local_projects()`" as misconfiguration, but that helper filters to projects whose `working_directory` exists on **this machine** (`reflections/utilities.py:110-115`). A correctly-configured project is invisible to the resolver on a machine lacking its checkout, and the narrowed fallback silently degrades that to no notification. The Empty/Invalid Input tests do not distinguish the two causes. | pending | Either read the unfiltered config (`resolve_projects_config_path()` + `json.loads`) so routing does not depend on local checkout presence, or make the warning name both causes: `logger.warning("docs_auditor: no Eng: group for project_key=%r (absent from the machine-filtered project list, or no configured Eng: group); notification suppressed", project_key)`. Otherwise an operator cannot tell a typo from a missing checkout. |
| CONCERN | Risk & Robustness | Risk 4's mitigation covers only the withheld-fix finding on the zero-diff path. It does not cover the step-9 pass summary (`reflections/docs_auditor.py:2521`), whose message carries the PR URL and the review-required warning. On the suppressed path the auditor opens a real non-draft PR and notifies nobody, while `docs/features/docs-auditor.md:45` names that Telegram message as how "review is required" is communicated. | pending | Make suppression observable to the caller, not just the logger: have `_send_telegram_notification` return `bool`, and at `:2521` do `if not _send_telegram_notification(msg, project_key=project_key): findings.append(f"docs-auditor: Telegram suppressed (no Eng: group for {project_key}); PR needs review: {pr_url}")`. The reflection report, not the log file, is the surface an operator reads. |
| CONCERN | History & Consistency | The No-Go enumerates four sibling sites, but a repo-wide grep for the literal argv returns a fifth live code site the plan never mentions: `scripts/memory_consolidation.py:352`. An enumerated site list is also the wrong closing shape for a replicated-value defect — a clean grep sweep is. As written, #3072 inherits an under-enumerated scope. | pending | State the No-Go as "every `.py` site matching the literal `--chat` / `Eng: Valor` argv adjacency other than `reflections/docs_auditor.py`", and add `scripts/memory_consolidation.py:352` to #3072's body. The Verification anti-criterion grep encodes the same four-item alternation and will not catch a stray edit to `memory_consolidation`; add it to that alternation too. |
| NIT | Risk & Robustness | Three Verification rows expect "match count == 0" but use `grep -c`, which exits 1 on zero matches; a validator reading exit codes sees three green rows as failures. | pending | n/a (NIT) — use `! grep -q ...` or append `|| true`. |
| NIT | Scope & Value | `DEFAULT_PROJECT_KEY = "valor"` names one of four copies of the same default: `audit()`'s own `project_key: str = "valor"` (`:1513`), `run_docs_auditor` (`:2299`), and the `__main__` block (`:2794`) keep bare literals. Naming one of four implies a single source of truth that does not exist. | pending | n/a (NIT) — substitute the constant at all four sites, or drop it and keep the literal local to `_resolve_notify_chat`. |
| NIT | History & Consistency | `reflections/docs_auditor.py:1482` reads "Telegram notification (mirrors `_send_log_review_telegram` pattern)", naming a function that no longer exists anywhere in the tree. Task `document-feature` rewrites this block's docstrings without noticing. | pending | n/a (NIT) — drop or correct the stale comment while in the block. |

**Critique execution note:** the `Task`/Agent tool was unavailable in this session, so the three FULL-depth critic lenses (Risk & Robustness, Scope & Value, History & Consistency) were executed in-process by the critique driver rather than as independent forked agents. The frozen `_roster.json` manifest, per-critic result files, terminal completion fence, and the `critique-roster-check --plan-path` grounding gate all ran as normal and reported `complete: true, ungrounded: []`.

---

## Open Questions

1. **Fallback narrowing.** The issue says "keep `Eng: Valor` as the fallback when the
   resolver returns nothing." This plan narrows that to *only when `project_key ==
   "valor"`*, because an unconditional fallback re-creates the misroute the issue is
   filed against, and `resolve_eng_group`'s docstring explicitly forbids routing an
   unresolvable project "somewhere plausible-looking." Naming the deviation for a ruling:
   accept the narrowing, or take the issue literally?
2. **Route by id or by name.** The plan sends the resolved numeric `chat_id`, matching
   `sdlc_upvote_lanes` and bypassing the ambiguity-tolerant name cascade. The cost is
   that the `valor` path's observable argv changes from a name to an id. Acceptable, or
   keep sending the resolved group *name* to minimize the behavioral delta?
