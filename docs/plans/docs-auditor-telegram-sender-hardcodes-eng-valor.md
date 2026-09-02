---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-09-02
tracking: https://github.com/tomcounsell/ai/issues/2754
last_comment_id: none
revision_applied: true
revision_applied_at: 2026-09-02T06:34:00Z
---

# docs-auditor Telegram sender hardcodes "Eng: Valor" instead of using resolve_eng_group

## Problem

The docs-auditor rotation reflection sends two Telegram notifications per run — a
withheld-fixes alert on the zero-diff path, and a pass summary after it opens a rotation
PR. Both go through `reflections/docs_auditor.py::_send_telegram_notification`, whose
argv pins the destination chat to a string literal:

```python
["valor-telegram", "send", "--chat", "Eng: Valor", message]
```

Meanwhile `reflections/utilities.py` already exposes `resolve_eng_group(project)` — the
per-project resolver that `reflections/sdlc_upvote_lanes.py:441` uses to find a
project's `Eng: X` group and numeric chat id. The auditor duplicates a value that
`projects.json` already owns.

**What the auditor actually audits (corrected premise).** The original issue framed this
as a `project_key` misroute. That framing is wrong and the plan-critique blocker
(recorded below) is upheld. `run_docs_auditor` calls
`audit(..., project_key=project_key, repo_root=PROJECT_ROOT)`
(`reflections/docs_auditor.py:2360-2365`) and `PROJECT_ROOT = Path(__file__).parent.parent`
(`:38`), so the auditor **always audits the checkout it is imported from**, regardless of
`VALOR_PROJECT_KEY`. `project_key` only namespaces the rotation Redis hash
(`_update_rotation_hash`) and the vault-drift root (`_resolve_vault_root`). Routing the
notification by `project_key` would therefore not remove a misroute — it would *create*
one: `VALOR_PROJECT_KEY=popoto` would send an alert about an **ai-repo** docs audit to
`Eng: Popoto`.

**Current behavior:**

Every docs-auditor notification is addressed to the literal string `Eng: Valor`,
independent of anything. Today that is accidentally correct, because the auditor only
ever audits this repo and this repo *is* the `valor` project. It is a hardcoded
deployment fact sitting where a lookup belongs, and it resolves through
`valor-telegram`'s ambiguity-tolerant name cascade rather than a numeric id.

**Desired outcome:**

`_send_telegram_notification` resolves its destination from **the repo root actually
audited**, by matching that path against `projects.json`'s `working_directory` entries and
handing the matched project to `resolve_eng_group`. It sends to the resolved numeric
`chat_id`. When the audited repo cannot be matched to a project with a properly
configured `Eng:` group, it declines to send — and the suppression is reported to the
caller so it reaches the reflection report, not only the log file. A literal `Eng: Valor`
fallback survives for the one case where it is provably correct: auditing this very
checkout with an unreadable or unmatched `projects.json`.

## Freshness Check

**Baseline commit:** `3b6eb651b78fb77b295b7e9a4741b1f614876f1b`
**Issue filed at:** 2026-08-13T05:15:58Z
**Disposition:** Minor drift

**File:line references re-verified:**

- `reflections/docs_auditor.py:1023` — issue claimed the hardcoded argv — **drifted to
  `:1490`**. Literal is byte-identical.
- `reflections/docs_auditor.py:1018` — issue claimed `_send_telegram_notification` —
  **drifted to `:1486`**. Signature still `(message: str) -> None`.
- Call site 1 (zero-diff withheld alert) — **now `:2434`**. **Correction to the previous
  revision of this plan:** it is inside `run_docs_auditor` (which begins at `:2263`), at
  that function's step-6 zero-diff gate — *not* inside `audit()`. `audit()` ends before
  `:2012`.
- Call site 2 (step-9 pass summary) — **now `:2521`**, also inside `run_docs_auditor`.
- `PROJECT_ROOT` — `reflections/docs_auditor.py:38`, `Path(__file__).parent.parent`.
- `audit()` — `:1509`, signature `(primary_path, *, scope_mode, apply_mode, project_key,
  repo_root)`; `root = (repo_root or PROJECT_ROOT).resolve()` at `:1547`.
- `reflections/utilities.py:310` — `resolve_eng_group` — **unchanged**, same line, same
  `(project: dict) -> tuple[str, int] | None` signature.
- `reflections/utilities.py:110-115` — `load_local_projects` filters to projects whose
  `working_directory` exists on disk and returns it as an absolute `str`. Confirmed.
- `reflections/sdlc_upvote_lanes.py:429` — issue claimed the consuming call site —
  **drifted to `:441`**. Claim holds: `eng_group = resolve_eng_group(project)`.
- `tests/unit/test_docs_auditor_substrate.py` — the `notify.call_count` /
  `notify.call_args.args[0]` assertions are at `:1376`, `:1413`, `:1453`, `:1463-1464`,
  `:1487`, `:1784`. All patch the function object wholesale; none inspect the chat
  argument. Claim holds.

**Live resolution against the current `projects.json`** (executed at plan time):

| slug | `working_directory` | `resolve_eng_group` |
|------|---------------------|---------------------|
| `valor` | `/Users/valorengels/src/ai` | `('Eng: Valor', -1003449100931)` |
| `popoto` | `/Users/valorengels/src/popoto` | `('Eng: Popoto', -5189826365)` |
| `cuttlefish` | `/Users/valorengels/src/cuttlefish` | `('Eng: Cuttlefish', -1003801797780)` |
| `psyoptimal` | `/Users/valorengels/src/psyoptimal` | `('Eng: PsyOPTIMAL', -1003743854645)` |
| `royop` | `/Users/valorengels/src/royop` | `None` |

`PROJECT_ROOT.resolve()` is `/Users/valorengels/src/ai`, which matches `valor`'s
`working_directory` **exactly**. The production path therefore resolves, and the fallback
is genuinely a degraded path rather than the normal one.

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
numbers moved, claims hold; the *issue's causal story* was wrong from the start, which
the critique caught and this plan now corrects.

**Active plans in `docs/plans/` overlapping this area:** none. The most recent plans
(`overclaim-guard-greps-whole-worktree`, `promise-gate-recorded-obligations`,
`wave4-hooks-guards-gates`) touch guards, expectations, and hooks — no overlap with
reflections Telegram routing.

**Bug still present:** yes, by inspection. The destination is a constant where a lookup
belongs, and it is resolved by ambiguity-tolerant name rather than by id.

## Prior Art

- **PR #2721**: *Autonomous SDLC pickup on upvote-labeled issues (#2717)* — merged
  2026-08-12. This is the PR that **introduced** `resolve_eng_group` and its first
  consumer in `sdlc_upvote_lanes.py`. It is the pattern this fix copies, including the
  decision to route by numeric `chat_id` rather than group name, and the decision to
  surface an unresolvable project as a *finding* rather than a silent skip
  (`sdlc_upvote_lanes.py:443-447`). Successful; not a failed prior attempt.
- **PR #2728**: *fix(docs-auditor): word-anchor stale terms and enforce a path-existence
  invariant* — merged 2026-08-13. Added the second hardcoded call site (the zero-diff
  withheld alert). The reviewer flagged the hardcode during the cycle-6 consensus review
  and severed it into this issue rather than blocking the merge. Not a failed fix — a
  deliberate deferral.
- **Issue #2629** (CLOSED): *react() cannot derive a transport: reflection emoji
  reactions still RPUSH to `telegram:outbox:0`* — the same defect class one layer down
  (a reflection hardcoding a Telegram destination instead of deriving it). Its
  resolution is prior evidence that hardcoded reflection transports get fixed by
  deriving from context, not by adding a second constant.
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
2. **Project identity vs. audited repo**: `run_docs_auditor` reads
   `project_key = os.environ.get("VALOR_PROJECT_KEY", "valor").strip() or "valor"`
   (`:2299`) and calls
   `audit(primary_path=primary, scope_mode="rotation", apply_mode="apply",
   project_key=project_key, repo_root=PROJECT_ROOT)` (`:2360-2365`). **`repo_root` is
   always `PROJECT_ROOT`.** `project_key` reaches only `_update_rotation_hash` and
   `_resolve_vault_root`. The `__main__` block (`:2794`) passes no `repo_root` at all, so
   `audit()` falls back to `PROJECT_ROOT` at `:1547`. There is no path on which the
   auditor audits a repo other than its own checkout.
3. **Notification points**: both live in `run_docs_auditor`, not in `audit()`. The
   zero-diff-with-withheld-fixes path (`:2431-2440`) and the PR-opened step-9 pass
   summary (`:2521`). `PROJECT_ROOT` is in scope at both — it is used two lines above the
   first one (`_git_diff_quiet(PROJECT_ROOT)`).
4. **`_send_telegram_notification`** (`:1486`): **today** discards all context and shells
   out with a constant `--chat "Eng: Valor"`. **After this change** it resolves
   `repo_root -> project dict -> (group_name, chat_id)` and shells out with the numeric
   id, returning a bool so the caller learns about suppression.
5. **Resolution**: `reflections.utilities.load_local_projects()` reads
   `~/Desktop/Valor/projects.json` and returns dicts each carrying `slug` and an absolute
   `working_directory` string; the new resolver matches on that path.
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
- **Interface changes**: `_send_telegram_notification(message)` becomes
  `_send_telegram_notification(message, *, repo_root: Path = PROJECT_ROOT) -> bool`.
  Message stays the first positional parameter; the new parameter is keyword-only with a
  default, so every existing `patch(...)`-style test target stays valid. Return type goes
  `None -> bool` — a widening, and no current caller reads the return value.
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

One private helper, one modified sender, two call sites, five or six new tests, one doc
line. Every genuine decision is settled in this plan (see **Settled Decisions**); nothing
is left for the builder to rule on.

## Prerequisites

No prerequisites — this work has no external dependencies. `projects.json` is read at
runtime and already required by the reflections package; the new tests stub
`load_local_projects` rather than depending on the real file.

## Settled Decisions

These were Open Questions in the pre-critique draft. Both are now **decided**; the
Solution, Tasks, and Success Criteria below implement these rulings and a builder should
treat them as fixed, not provisional.

1. **Route by audited repo, not by `project_key`. DECIDED: by repo.** The issue's
   `project_key` framing is void (see Problem). The destination is derived from
   `repo_root`, matched against `working_directory`. This supersedes the issue's stated
   fix shape and is the direct resolution of the critique blocker.
2. **The fallback is narrowed. DECIDED: fall back to the `Eng: Valor` literal only when
   the audited repo *is this checkout* (`repo_root.resolve() == PROJECT_ROOT.resolve()`).**
   The issue says "keep `Eng: Valor` as the fallback when the resolver returns nothing."
   Taken unconditionally that re-creates the defect: an unmatched foreign checkout would
   still page the valor engineers. `resolve_eng_group`'s own docstring is explicit that a
   project with no properly configured group "must be skipped by the caller, never routed
   somewhere plausible-looking." Scoping the fallback to *this* checkout satisfies both
   constraints, because this checkout's engineer group is `Eng: Valor` by construction —
   it is the same fact `bridge/routing.py` relies on. **This is a deliberate deviation
   from the issue's literal wording, recorded here so review can rule on it.** The
   rationale must also appear in the `_resolve_notify_chat` docstring, because
   `FALLBACK_ENG_CHAT` reachable only under a path equality is exactly the shape a future
   reader "simplifies" back into the bug.
3. **Route by numeric id, not by group name. DECIDED: by id.** `valor-telegram send
   --chat` accepts either, but a name goes through `resolve_chat_id`'s three-stage cascade
   which, in non-strict mode, silently picks the most-recent candidate on an ambiguous
   match. A numeric id short-circuits that (`tools/valor_telegram.py:836`).
   `sdlc_upvote_lanes` already routes by id for the same reason. The cost — the valor
   path's observable argv changes from `"Eng: Valor"` to `"-1003449100931"` — is accepted,
   and is pinned by a test (see Test Impact) rather than by a manual post-merge eyeball.
4. **Suppression is reported to the caller, not only logged. DECIDED: return `bool`.**
   The step-9 message carries the PR URL and the review-required warning, and
   `docs/features/docs-auditor.md` names that Telegram message as *how* "review is
   required" is communicated. A suppressed send there means a real non-draft PR opens and
   nobody is told. The sender returns `False` on the no-destination path and both call
   sites convert that into a `findings` entry, so the reflection report — the surface an
   operator actually reads — carries it.

## Solution

### Key Elements

- **`_resolve_notify_chat(repo_root: Path) -> str | None`** (new, private, in
  `reflections/docs_auditor.py`): maps the audited repo root to the string the `--chat`
  flag should carry, or `None` meaning *do not send*. Owns the entire resolution +
  fallback policy so the sender stays a thin subprocess wrapper.
- **`_send_telegram_notification(message, *, repo_root=PROJECT_ROOT) -> bool`**
  (modified): asks `_resolve_notify_chat` for a destination; on `None`, logs a warning
  naming the repo root and **returns `False`** without shelling out; otherwise sends as
  before and returns `True`.
- **Two updated call sites**: both pass `repo_root=PROJECT_ROOT` and append a `findings`
  entry when the call returns `False`.
- **One named constant** replacing the literal: `FALLBACK_ENG_CHAT = "Eng: Valor"`. The
  string survives in exactly one place, as a named fallback rather than an inline argv
  element. **No `DEFAULT_PROJECT_KEY` constant is introduced** — the resolver is not keyed
  on `project_key` at all, so there is nothing to name, and naming one of the four
  existing bare `"valor"` defaults (`audit()` `:1513`, `run_docs_auditor` `:2299`,
  `__main__` `:2794`) would imply a single source of truth that does not exist.

### Flow

Scheduled docs-auditor run → `run_docs_auditor` reaches a notify point →
`_send_telegram_notification(msg, repo_root=PROJECT_ROOT)` →
`_resolve_notify_chat(PROJECT_ROOT)` → matches `valor`'s `working_directory` →
`resolve_eng_group` → `--chat "-1003449100931"` → alert lands in `Eng: Valor` by id.

Alternate branch (foreign or unmatched checkout with no `Eng:` group): →
`_resolve_notify_chat` returns `None` → `logger.warning` naming the repo root and both
possible causes → **no message sent** → the sender returns `False` → the call site appends
a `findings` entry naming the suppression and, at step 9, the PR URL that still needs
review.

Alternate branch (this checkout, `projects.json` unreadable or unmatched): →
`_resolve_notify_chat` returns `FALLBACK_ENG_CHAT` → `--chat "Eng: Valor"` → today's exact
behavior.

### Technical Approach

The resolution ladder in `_resolve_notify_chat(repo_root)`, in order:

1. Compute `target = repo_root.resolve()`.
2. Call `load_local_projects()` and find the entry whose
   `Path(p["working_directory"]).resolve() == target`.
3. If found, call `resolve_eng_group(project)`. On a `(name, chat_id)` tuple, return
   `str(chat_id)` — the numeric id, never the name (Settled Decision 3).
4. If no entry matches, or `resolve_eng_group` returns `None`: return `FALLBACK_ENG_CHAT`
   **only when `target == PROJECT_ROOT.resolve()`**, otherwise return `None`
   (Settled Decision 2).
5. Wrap steps 2-3 in `except Exception` → log a warning → fall through to the step-4 rule.
   The notification is best-effort by design and must never break an audit run; but the
   swallow logs, so it is observable.

**On `load_local_projects`'s machine filter.** `load_local_projects` returns only projects
whose `working_directory` exists on this machine (`reflections/utilities.py:110-115`).
Keying the lookup on `repo_root` dissolves the ambiguity that filter would otherwise
introduce: the repo being audited necessarily exists on this machine, so a
correctly-configured project can never be filtered out of its *own* audit. The filter can
still hide a *different* project, but no notification is ever addressed to one.
Nonetheless the skip warning must name both remaining causes so an operator can tell a
config typo from an unregistered checkout:

```python
logger.warning(
    "docs_auditor: no Eng: group for audited repo %s (repo not registered in "
    "projects.json, or its project has no configured Eng: group); "
    "Telegram notification suppressed",
    target,
)
```

**On worktrees.** `PROJECT_ROOT` is derived from `__file__`, so a run executing inside
`.worktrees/{slug}/` computes `PROJECT_ROOT` as that worktree path. The
`working_directory` match then fails (worktrees are not registered in `projects.json`) and
step 4's equality still holds (`repo_root` *is* that same `PROJECT_ROOT`), so the run
falls back to `Eng: Valor`. That is the correct outcome and requires no special-casing.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `_send_telegram_notification`'s existing handlers (`FileNotFoundError`,
      `subprocess.TimeoutExpired`, bare `Exception`) each already log a
      `logger.warning`. They are unchanged by this work. They return `True` — the
      destination *was* resolved and a send *was* attempted; `False` is reserved for the
      no-destination case, so the caller's finding text ("no Eng: group configured") stays
      accurate. This return-value split is asserted by one test.
- [ ] The **new** `except Exception` in `_resolve_notify_chat` gets a test: patch
      `load_local_projects` to raise, assert (a) a warning is logged and (b) the
      `PROJECT_ROOT` path still falls back to `Eng: Valor` while a foreign path sends
      nothing and returns `False`.

### Empty/Invalid Input Handling

- [ ] `repo_root` naming a path absent from `projects.json` and different from
      `PROJECT_ROOT` → `None` → no send, `False`, warning naming both causes. Tested.
- [ ] `repo_root` matching a registered project whose `resolve_eng_group` returns `None`
      (the `royop` shape) → `None` → no send, `False`. Tested.
- [ ] `repo_root == PROJECT_ROOT` with `load_local_projects` returning `[]` → fallback
      literal, send attempted, `True`. Tested.
- [ ] `message=""` — out of scope for behavior change; the sender passes it through today
      and will continue to. No new handling.
- [ ] Not agent-output processing; no silent-loop risk.

### Error State Rendering

- [ ] The user-visible surface here is the Telegram alert itself. The failure mode this
      plan introduces is *no alert*. That is made observable on two channels: a
      `logger.warning` carrying the resolved repo path (verified with `caplog`, not just
      by asserting `subprocess.run` was not called), **and** a `findings` entry appended by
      the caller, which is what reaches the aggregated reflection report.
- [ ] On the step-9 path the finding must carry the PR URL, because a suppressed
      notification there means a real non-draft PR is open with nobody notified. Asserted.

## Test Impact

- [ ] `tests/unit/test_docs_auditor_substrate.py` — the eleven `patch(
      "reflections.docs_auditor._send_telegram_notification")` sites (`:257`, `:292`,
      `:310`, `:330`, `:1376`, `:1413`, `:1453`, `:1481`, `:1687`, `:1709`, `:1761`) —
      **UPDATE (no-op expected)**: they patch the function object and assert on
      `call_count` / `call_args.args[0]`. The new parameter is keyword-only with a
      default and the message stays positional, so `args[0]` still resolves. A `MagicMock`
      patch returns a truthy `Mock` by default, so the new `if not ...` branch at each
      call site does not fire and no existing assertion changes. Verify by running the
      file; only patch if something breaks.
- [ ] `tests/unit/test_docs_auditor_substrate.py::test_..._withheld_alert` (`:1376`,
      `:1413`) and the step-9 summary test (`:1453`) — **UPDATE**: add a
      `notify.call_args.kwargs["repo_root"]` assertion to each, so the two call sites are
      pinned to actually thread the repo root. Without this the sender could be fixed while
      the call sites keep defaulting, and every test would still pass.
- [ ] `tests/unit/test_docs_auditor_substrate.py` — **ADD** a new `TestTelegramChatRouting`
      class exercising `_send_telegram_notification` directly with
      `reflections.docs_auditor.subprocess.run` patched (the established pattern at
      `:1291`, `:1350`) and `reflections.docs_auditor.load_local_projects` stubbed. Cases:
      1. **the production valor path** — stub returns
         `[{"slug": "valor", "working_directory": str(PROJECT_ROOT), "telegram":
         {"groups": {"Eng: Valor": {"chat_id": -1003449100931}}}}]`, call with
         `repo_root=PROJECT_ROOT`, assert the argv contains the string
         `"-1003449100931"` and **not** `"Eng: Valor"`, and that the call returns `True`;
      2. a foreign registered repo with a configured `Eng:` group → argv carries that
         project's `str(chat_id)`;
      3. `repo_root == PROJECT_ROOT` with `load_local_projects` returning `[]` → argv
         carries `"Eng: Valor"`, returns `True`;
      4. foreign unregistered repo → `subprocess.run` not called, returns `False`, and a
         `logger.warning` naming the path (assert via `caplog`);
      5. registered repo whose group is malformed (`royop` shape) → not called, `False`;
      6. `load_local_projects` raising → swallowed, warning logged, `PROJECT_ROOT` falls
         back and a foreign path returns `False`;
      7. `subprocess.run` raising `FileNotFoundError` on a *resolved* destination → still
         returns `True` (send attempted, not suppressed).
- [ ] `tests/unit/test_docs_auditor_substrate.py` — **ADD** two cases asserting the
      suppression finding: patch `_send_telegram_notification` to return `False` and drive
      `run_docs_auditor` through (a) the zero-diff withheld path and (b) the step-9 path,
      asserting the returned `findings` list contains an entry naming the suppression, and
      that the step-9 entry carries the PR URL.
- [ ] `tests/unit/reflections/test_utilities_resolve_eng_group.py` — **no change**.
      `resolve_eng_group` itself is untouched; its direct tests stay as-is.
- [ ] `tests/unit/reflections/test_sdlc_upvote_lanes.py` — **no change**. The other
      consumer is untouched.
- [ ] `tests/unit/reflections/test_docs_auditor_git_surface.py` — **no change**. Covers
      the git surface, not notifications.

## Rabbit Holes

- **Sweeping every other hardcoded `Eng: Valor` sender in the tree.** A repo-wide grep for
  the literal argv finds five more live code sites beyond `docs_auditor`
  (`expectation_reconciler`, `sentry_triage`, `stall_advisory`, `sdlc_progress`,
  `scripts/memory_consolidation.py`) plus a module constant in
  `scripts/nightly_regression_tests.py`. It is tempting to fix them all at once. Don't:
  every one of those senders takes `message` only, and each is called from a path several
  frames below where any project or repo context exists. That is five independent
  signature refactors, not five one-line swaps. Filed as #3072, whose scope was corrected
  at plan time to be a grep sweep rather than the four-item list it originally carried.
- **Building the shared `send_eng_telegram` helper in `utilities.py` now.** The right
  home for the resolve-and-send logic once there are several callers. With one caller it
  is speculative generality, and it would force this lane to design an interface for
  modules it isn't touching. `_resolve_notify_chat` stays private in `docs_auditor.py`;
  #3072 promotes it when it has a second consumer.
- **Fixing the prompt-level chat references** in
  `reflections/agents/circuit_health_gate.py:64,75` and
  `reflections/agents/system_health_digest.py:7,137`. Those are English instructions
  embedded in LLM prompts, not argv. Changing them is a prompt-engineering problem with a
  completely different verification story.
- **Making `docs_auditor` audit more than its own checkout.** `PROJECT_ROOT` is baked in
  at both callers, and `project_key` namespaces only Redis keys and the vault root.
  Turning the auditor into a genuine multi-project consumer (a `run_per_project_audit`
  driver over other checkouts) is a much larger piece of work and is not what this issue
  asks for. This plan makes the *destination* correct for whatever repo is audited; it
  does not change *which* repo is audited.
- **Adding a `--chat-id` flag to `valor-telegram send`.** Unnecessary: `--chat` already
  accepts a numeric id via the digit fallback at `tools/valor_telegram.py:836`.

## Risks

### Risk 1: The call sites keep defaulting while the sender looks fixed

**Impact:** Eleven test sites patch `_send_telegram_notification`. If the message stopped
being the first positional argument, `call_args.args[0]` would raise `IndexError` across
the file — noisy, so that direction fails loudly. The real risk is the inverse: the sender
gets fixed, the *call sites* keep relying on the default, and nothing catches it because
no test inspects the chat or the threaded root.
**Mitigation:** Make the new parameter keyword-only with a default (message stays
positional), and add explicit `call_args.kwargs["repo_root"]` assertions at the two
call-site tests. That pins the threading, not just the sender.

### Risk 2: `load_local_projects()` does disk I/O on a notification path

**Impact:** `load_local_projects` reads and JSON-parses `~/Desktop/Valor/projects.json`
and stats each project's `working_directory` on every notify. On an iCloud-synced path a
stall would delay the audit's return. The file is small and the path is local-cached, so
this is a minor concern, but it is new I/O on a path that previously had none.
**Mitigation:** The call happens at most once per audit run (the two call sites are on
mutually exclusive branches), and only on a path that is already about to spawn a
`valor-telegram` subprocess with a `settings.timeouts.git_subprocess_s` budget — orders of
magnitude more expensive than the read. Additionally the whole ladder is inside
`try/except Exception`, so an I/O failure degrades to the fallback rule rather than
propagating. No caching is added: a cache would make the auditor read a stale
`projects.json` across a long-lived process, which is the same class of bug as
`_BASENAME_INDEX_CACHE` (#2759).

### Risk 3: Routing by numeric id changes the observable send for the production path

**Impact:** Today notifications go out as `--chat "Eng: Valor"`; after the change they go
out as `--chat "-1003449100931"`. If the numeric id in `projects.json` were stale or
wrong, alerts would silently go to the wrong place or nowhere — a worse failure than the
name path, which resolves against live history.
**Mitigation:** The id comes from the same `projects.json` that `bridge/routing.py`
already trusts to grant the engineer persona, and `sdlc_upvote_lanes` has been routing
`Eng:` announcements by id since PR #2721 without incident. Test case 1 pins the
production path's argv explicitly rather than leaving it to a post-merge eyeball. The
fallback branch also still uses the *name*, so a corrupted id degrades to today's exact
behavior only when the project row is unmatched entirely. Post-merge, confirm one real
docs-auditor alert lands in `Eng: Valor` before considering the lane closed.

### Risk 4: The narrowed fallback suppresses an alert an operator expected

**Impact:** An audited repo with a typo'd `Eng:` group, or one not registered in
`projects.json`, gets *no* docs-auditor Telegram alert instead of a misrouted one. Silence
can be mistaken for "nothing to report." On the step-9 path this is sharper: a real
non-draft PR opens and the "review required" message reaches nobody.
**Mitigation:** Two channels, not one. The skip logs a `logger.warning` naming the
resolved repo path *and* both possible causes, and the sender returns `False` so each call
site appends a `findings` entry — the step-9 one carrying the PR URL — to the dict the
aggregated reflection report reads. This mirrors and extends the established precedent at
`sdlc_upvote_lanes.py:443-447`, which deliberately returns status `"ok"` with a
`"no Eng: group configured"` finding for exactly this case.

## Race Conditions

No race conditions identified. `_send_telegram_notification` and `_resolve_notify_chat`
are synchronous, single-threaded, and hold no shared mutable state. `load_local_projects`
performs a read-only file read with no caching. The `valor-telegram send` subprocess is
fire-and-forget with a bounded timeout and no ordering requirement relative to any other
audit step — the two call sites are on mutually exclusive branches (zero-diff vs.
PR-opened), so they can never both fire in one run.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #3072] **Every `.py` site in the tree carrying the `["valor-telegram",
  "send", "--chat", "Eng: Valor", ...]` argv adjacency, or the bare `"Eng: Valor"` literal
  as a routing constant, other than `reflections/docs_auditor.py`.** Stated as a sweep, not
  a list, because this is a replicated-value defect and an enumerated list under-scopes it
  — the pre-critique draft's four-item list already missed two sites. Each such site needs
  a per-module signature refactor to get a repo or project into scope at the send point,
  which is a different size of job. This PR must not touch any of them.
- [SEPARATE-SLUG #3072] Promoting the resolve-and-send logic into a shared
  `reflections/utilities.py::send_eng_telegram` helper. That belongs with the sweep that
  gives it a second caller; building it here would be an interface designed against
  hypothetical consumers.
- [OUT OF SCOPE] Changing *which* repo the auditor audits. `repo_root=PROJECT_ROOT` at
  both callers stays exactly as it is; this plan only makes the notification destination
  derive from it.
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

No agent integration required — this is a reflections-internal change.
`_send_telegram_notification` is a private function invoked only from `run_docs_auditor`
inside the same module; no new CLI entry point in `pyproject.toml [project.scripts]` is
needed, and the bridge does not import it. The existing `valor-telegram` entry point it
shells out to is already registered and unchanged.

## Documentation

### Feature Documentation

- [ ] Update `docs/features/docs-auditor.md:45` — it currently states the auditor
      "notifies the `Eng: Valor` Telegram chat that review is required". Replace with the
      derived-destination rule: the notification goes to the `Eng:` group of the project
      whose `working_directory` matches the audited repo root, addressed by numeric
      `chat_id`; it falls back to the `Eng: Valor` literal only when the audited repo is
      this checkout and no match is found; and it is otherwise suppressed with a logged
      warning **and** a finding in the reflection report carrying the PR URL.
- [ ] No new entry in `docs/features/README.md` — `docs-auditor.md` is already indexed.

### External Documentation Site

Not applicable — this repo has no Sphinx/MkDocs site.

### Inline Documentation

- [ ] Docstring on `_resolve_notify_chat` stating the ladder and, explicitly, *why* the
      fallback is scoped to `PROJECT_ROOT` (Settled Decision 2) — the reasoning is
      non-obvious and a future reader will otherwise "simplify" it back into the bug.
- [ ] Update `_send_telegram_notification`'s docstring to name the new parameter, the
      `bool` return contract (`False` **only** on the no-destination path, never on a
      swallowed subprocess failure), and the no-destination-means-no-send rule.
- [ ] Correct the stale section comment at `reflections/docs_auditor.py:1482`, which reads
      "Telegram notification (mirrors `_send_log_review_telegram` pattern)" and names a
      function that no longer exists anywhere in the tree.
- [ ] Leave `audit()`'s `project_key` docstring line (`:1533`) as-is — "Used for
      vault-namespaced rotation keys" is accurate and stays accurate, because this plan
      does **not** route on `project_key`.

## Success Criteria

- [ ] `reflections/docs_auditor.py` contains no `"--chat", "Eng: Valor"` argv adjacency.
- [ ] `_send_telegram_notification` resolves its destination through `resolve_eng_group`
      keyed on the audited repo root, and both call sites pass `repo_root=PROJECT_ROOT`.
- [ ] **Production path:** auditing this checkout with the real-shaped `projects.json`
      sends to `str(chat_id)` (`"-1003449100931"` in the test stub), not to the group name.
- [ ] A repo registered in `projects.json` with a configured `Eng:` group receives its
      notification at that group's numeric `chat_id`.
- [ ] An audited repo with no resolvable `Eng:` group, other than this checkout, receives
      no notification, produces a `logger.warning` naming the repo path and both possible
      causes, and returns `False`.
- [ ] A `False` return from either call site appends a `findings` entry; the step-9 entry
      carries the PR URL.
- [ ] Auditing this checkout with an unreadable or unmatched `projects.json` still sends
      to `Eng: Valor` — today's behavior preserved on the degraded path.
- [ ] `docs/features/docs-auditor.md` no longer claims a fixed destination chat.
- [ ] This PR touches no file named in #3072's sweep.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] No xfail conversions needed — `tests/unit/test_docs_auditor_substrate.py` contains
      no `xfail` markers related to this bug (verified at plan time).

## Team Orchestration

### Team Members

- **Builder (routing)**
  - Name: `auditor-routing-builder`
  - Role: implement `_resolve_notify_chat`, modify the sender, thread `repo_root` and the
    suppression findings through both call sites, add and update tests
  - Agent Type: builder
  - Resume: true

- **Documentarian**
  - Name: `auditor-routing-docs`
  - Role: update `docs/features/docs-auditor.md`, the two docstrings, and the stale
    section comment
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: `auditor-routing-validator`
  - Role: verify every Success Criteria row and run the Verification table
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement repo-keyed chat resolution

- **Task ID**: build-routing
- **Depends On**: none
- **Validates**: `tests/unit/test_docs_auditor_substrate.py`,
  `tests/unit/reflections/test_docs_auditor_git_surface.py`
- **Informed By**: Freshness Check (current line numbers), Settled Decisions 1-4,
  Technical Approach (the ladder)
- **Assigned To**: `auditor-routing-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add module-scope `FALLBACK_ENG_CHAT = "Eng: Valor"` near the existing module constants
  in `reflections/docs_auditor.py`. Do **not** add a `DEFAULT_PROJECT_KEY` constant.
- Add a module-scope `from reflections.utilities import load_local_projects,
  resolve_eng_group` (no cycle — verified in Architectural Impact).
- Add `_resolve_notify_chat(repo_root: Path) -> str | None` implementing the five-step
  ladder from Technical Approach. Return `str(chat_id)`, never the group name, on the
  resolved path. Use the exact dual-cause warning text given in Technical Approach.
- Change `_send_telegram_notification(message: str) -> None` (currently `:1486`) to
  `_send_telegram_notification(message: str, *, repo_root: Path = PROJECT_ROOT) -> bool`.
  Message stays the first positional parameter. Resolve first; on `None`, emit the warning
  and `return False` without invoking `subprocess.run`. Every other path returns `True`,
  including the existing swallowed-failure handlers.
- Leave the existing `FileNotFoundError` / `TimeoutExpired` / `Exception` handlers in the
  sender otherwise exactly as they are (add only the `return True`).
- Update the zero-diff withheld-alert call site (currently `:2434`) to pass
  `repo_root=PROJECT_ROOT` and, when it returns `False`, add a suppression entry to the
  findings list that return branch builds.
- Update the step-9 pass summary call site (currently `:2521`) to pass
  `repo_root=PROJECT_ROOT` and, when it returns `False`, append to the in-scope `findings`
  list an entry naming the suppression and carrying `pr_url`.

### 2. Add and update tests

- **Task ID**: build-tests
- **Depends On**: build-routing
- **Validates**: `tests/unit/test_docs_auditor_substrate.py`
- **Assigned To**: `auditor-routing-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add a `TestTelegramChatRouting` class to `tests/unit/test_docs_auditor_substrate.py`
  patching `reflections.docs_auditor.subprocess.run` (pattern at `:1291`/`:1350`) and
  `reflections.docs_auditor.load_local_projects`. Implement all seven cases enumerated in
  **Test Impact**, starting with the production valor path (case 1).
- Add the two suppression-finding tests (zero-diff path and step-9 path) described in
  Test Impact.
- Add `notify.call_args.kwargs["repo_root"]` assertions to the existing withheld-alert
  tests (`:1376`, `:1413`) and the step-9 summary test (`:1453`) so both call sites are
  pinned to thread the repo root.
- Run the full file and confirm the other eight `patch(...)` sites still pass unchanged.

### 3. Documentation

- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: `auditor-routing-docs`
- **Agent Type**: documentarian
- **Parallel**: false
- Rewrite the destination sentence at `docs/features/docs-auditor.md:45` per the
  Documentation section.
- Write the `_resolve_notify_chat` docstring including the rationale for the
  `PROJECT_ROOT`-only fallback.
- Update `_send_telegram_notification`'s docstring, including the `bool` return contract.
- Correct the stale `_send_log_review_telegram` section comment at `:1482`.
- Leave `audit()`'s `project_key` docstring line untouched.

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

Rows expecting "no match" use `! grep -q ...` so a zero-match result is exit code 0 rather
than `grep -c`'s exit 1, which a validator reading exit codes would score as a failure.

| Check | Command | Expected |
|-------|---------|----------|
| No hardcoded chat argv | `! grep -q '"--chat", "Eng: Valor"' reflections/docs_auditor.py` | exit code 0 |
| Sender uses the resolver | `grep -q 'resolve_eng_group' reflections/docs_auditor.py` | exit code 0 |
| Both call sites thread the repo root | `test "$(grep -c 'repo_root=PROJECT_ROOT' reflections/docs_auditor.py)" -ge 3` | exit code 0 (2 notify sites + the existing `audit()` call) |
| Routing tests pass | `scripts/pytest-clean.sh tests/unit/test_docs_auditor_substrate.py -q` | exit code 0 |
| Git-surface tests still pass | `scripts/pytest-clean.sh tests/unit/reflections/test_docs_auditor_git_surface.py -q` | exit code 0 |
| Resolver's own tests untouched and green | `scripts/pytest-clean.sh tests/unit/reflections/test_utilities_resolve_eng_group.py tests/unit/reflections/test_sdlc_upvote_lanes.py -q` | exit code 0 |
| Anti-criterion: no #3072 file touched | `! git diff --name-only origin/main...HEAD \| grep -qE 'expectation_reconciler\|sentry_triage\|stall_advisory\|sdlc_progress\|memory_consolidation\|nightly_regression_tests'` | exit code 0 |
| Anti-criterion: the sweep did not widen | `test "$(git grep -lF '"Eng: Valor"' -- '*.py' \| grep -v '^tests/' \| grep -v '^reflections/docs_auditor.py' \| wc -l)" -eq 6` | exit code 0 — the six #3072 sender files, verified at plan time: `expectation_reconciler`, `sdlc_progress`, `sentry_triage`, `stall_advisory`, `scripts/memory_consolidation`, `scripts/nightly_regression_tests`. The two prompt files use single quotes and are deliberately outside this double-quoted sweep. |
| Feature doc no longer pins the chat | `! grep -q 'notifies the .Eng: Valor. Telegram chat' docs/features/docs-auditor.md` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

**Verdict (2026-09-02, /do-plan-critique cycle 1, FULL depth):** NEEDS REVISION — 1 blocker, 5 concerns, 3 nits.
**Revision (2026-09-02, /do-plan revision pass):** all 9 findings addressed; the blocker
is upheld and the plan's causal premise rewritten around it.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Scope & Value | The Problem's premise is void. `run_docs_auditor` calls `audit(..., project_key=project_key, repo_root=PROJECT_ROOT)` (`reflections/docs_auditor.py:2360-2365`) and `PROJECT_ROOT = Path(__file__).parent.parent` (`:38`), so the auditor always audits **this** repo regardless of `VALOR_PROJECT_KEY`. `project_key` only namespaces the rotation Redis hash and the vault-drift key. Data Flow step 2 omits the `repo_root=PROJECT_ROOT` argument on that same call. The fix therefore removes no misroute and creates one: `VALOR_PROJECT_KEY=popoto` would route an **ai-repo** docs audit to `Eng: Popoto`. | **UPHELD — plan rewritten** | Verified independently: `PROJECT_ROOT` at `:38`, `repo_root=PROJECT_ROOT` at `:2364`, `root = (repo_root or PROJECT_ROOT).resolve()` at `:1547`, `__main__` at `:2794` passing no `repo_root`. Also found: both notify call sites are in `run_docs_auditor`, **not** in `audit()` as the draft claimed. The resolver is now keyed on `repo_root` and matched against `working_directory` (`reflections/utilities.py:114`); `project_key` is not consulted. See Problem, Data Flow, Settled Decision 1, and the ladder in Technical Approach. Signature is `_resolve_notify_chat(repo_root: Path)`; Task 1 and the Success Criteria now pin `repo_root=PROJECT_ROOT`, not `project_key=project_key`. |
| CONCERN | History & Consistency | The only valor-path success criterion is the `projects.json`-unreadable fallback, yet Risk 3 states the *normal* valor argv changes from `--chat "Eng: Valor"` to `--chat "-100..."`. The production path has no criterion and no test: `TestTelegramChatRouting` case 2 is the fallback again, case 1 is a non-valor project. | **addressed** | Test Impact case 1 is now the production path explicitly, stubbing `working_directory = str(PROJECT_ROOT)` with `chat_id: -1003449100931` (the real value, verified live at plan time) and asserting the argv carries the id and **not** the name. A matching Success Criterion row was added. Risk 3's mitigation no longer rests on a manual post-merge check. |
| CONCERN | Scope & Value | The two Open Questions request a ruling, but the Solution, Technical Approach, Success Criteria, and Step-by-Step Tasks already implement one answer to each. A builder gets no signal either decision is provisional. | **addressed** | Open Questions deleted. Replaced by a **Settled Decisions** section carrying four explicit rulings (repo-keyed routing, narrowed fallback, route-by-id, bool return) with rationale in the plan body. Decision 2 names the deviation from the issue's literal wording for review, and Documentation requires the same rationale in the `_resolve_notify_chat` docstring. |
| CONCERN | Risk & Robustness | Ladder step 1 treats "project not in `load_local_projects()`" as misconfiguration, but that helper filters to projects whose `working_directory` exists on **this machine** (`reflections/utilities.py:110-115`). A correctly-configured project is invisible to the resolver on a machine lacking its checkout, and the narrowed fallback silently degrades that to no notification. The Empty/Invalid Input tests do not distinguish the two causes. | **addressed** | Largely dissolved by the blocker fix: keying on `repo_root` means the audited repo necessarily exists on this machine, so the filter can never hide the project being routed to. The residual ambiguity (unregistered checkout vs. malformed `Eng:` entry) is handled by the second half of the critic's suggestion — Technical Approach specifies the exact dual-cause `logger.warning` text, and test case 4 asserts it via `caplog`. |
| CONCERN | Risk & Robustness | Risk 4's mitigation covers only the withheld-fix finding on the zero-diff path. It does not cover the step-9 pass summary (`reflections/docs_auditor.py:2521`), whose message carries the PR URL and the review-required warning. On the suppressed path the auditor opens a real non-draft PR and notifies nobody, while `docs/features/docs-auditor.md:45` names that Telegram message as how "review is required" is communicated. | **addressed** | Adopted as written. `_send_telegram_notification` now returns `bool` (Settled Decision 4); both call sites append a `findings` entry on `False`, the step-9 one carrying `pr_url`. The `findings` list is already in scope at `:2551`. Return semantics are narrowed deliberately: `False` **only** on the no-destination path, so the finding text stays accurate when a subprocess failure is swallowed — pinned by test case 7. Risk 4, Failure Path Test Strategy, Task 1, and two new tests all cover it. |
| CONCERN | History & Consistency | The No-Go enumerates four sibling sites, but a repo-wide grep for the literal argv returns a fifth live code site the plan never mentions: `scripts/memory_consolidation.py:352`. An enumerated site list is also the wrong closing shape for a replicated-value defect — a clean grep sweep is. As written, #3072 inherits an under-enumerated scope. | **addressed** | Confirmed, and the sweep found a **sixth**: `scripts/nightly_regression_tests.py:105` (`TELEGRAM_CHAT = "Eng: Valor"`). The No-Go is restated as a sweep over the argv adjacency and the bare routing literal, not a list. #3072 was commented at plan time with both extra sites and a note that its **What** list is illustrative, not exhaustive. The Verification anti-criterion grep alternation now includes both, and a second anti-criterion row pins the total sweep count so a stray widening fails loudly. |
| NIT | Risk & Robustness | Three Verification rows expect "match count == 0" but use `grep -c`, which exits 1 on zero matches; a validator reading exit codes sees three green rows as failures. | **addressed** | All no-match rows converted to `! grep -q ...` with expected exit code 0, and the table carries a note explaining why. |
| NIT | Scope & Value | `DEFAULT_PROJECT_KEY = "valor"` names one of four copies of the same default: `audit()`'s own `project_key: str = "valor"` (`:1513`), `run_docs_auditor` (`:2299`), and the `__main__` block (`:2794`) keep bare literals. Naming one of four implies a single source of truth that does not exist. | **addressed** | Dissolved by the blocker fix — the resolver is no longer keyed on `project_key`, so there is no default to name. Task 1 explicitly forbids introducing the constant. `FALLBACK_ENG_CHAT` is the only new constant, and it genuinely has one use site. |
| NIT | History & Consistency | `reflections/docs_auditor.py:1482` reads "Telegram notification (mirrors `_send_log_review_telegram` pattern)", naming a function that no longer exists anywhere in the tree. Task `document-feature` rewrites this block's docstrings without noticing. | **addressed** | Confirmed absent from the tree. Correcting the comment is now an explicit bullet under Documentation → Inline Documentation and under Task 3. |

**Critique execution note:** the `Task`/Agent tool was unavailable in that session, so the three FULL-depth critic lenses (Risk & Robustness, Scope & Value, History & Consistency) were executed in-process by the critique driver rather than as independent forked agents. The frozen `_roster.json` manifest, per-critic result files, terminal completion fence, and the `critique-roster-check --plan-path` grounding gate all ran as normal and reported `complete: true, ungrounded: []`.
