---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-09-02
tracking: https://github.com/tomcounsell/ai/issues/2754
last_comment_id: none
revision_applied: true
revision_applied_at: 2026-09-02T06:25:46Z
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
   `tools/valor_telegram.py::cmd_send` calls `resolve_chat(args.chat, strict=False)` first
   (`:831`) — that resolver *does* run, performing its history and user lookups — and falls
   back to accepting the raw value when it returns falsy and
   `args.chat.lstrip("-").isdigit()` (`:836`). A numeric id therefore still enters the
   cascade but can never match a title or username, so it always lands on the digit fallback
   rather than on an ambiguous most-recent pick.

## Architectural Impact

- **New dependencies**: none external. One new intra-package import edge:
  `reflections.docs_auditor -> reflections.utilities`. `docs_auditor` currently imports
  only `config.machine`, `config.settings`, and stdlib. `utilities` imports only
  `config.settings` at module scope, so **no import cycle** is created. The import is
  placed at module scope (not function-local) — there is no cycle to defend against and
  a function-local import would be cargo-culted defensiveness.
- **Interface changes**: `_send_telegram_notification(message)` becomes
  `_send_telegram_notification(message, *, repo_root: Path | None = None) -> bool`, with
  `(repo_root or PROJECT_ROOT).resolve()` computed in the body so the module global is read
  at call time and stays patchable. Message stays the first positional parameter; the new
  parameter is keyword-only with a `None` default, so every existing `patch(...)`-style test
  target stays valid. Return type goes `None -> bool` — a widening, and no current caller
  reads the return value.
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

One private helper, one modified sender (new `bool` contract threaded through both of its
return branches), two updated call sites, **nine new tests** (seven `TestTelegramChatRouting`
cases plus two suppression-summary cases) and **three assertion updates** to existing tests,
**four Documentation bullets** (one feature-doc sentence at `:45`, one at `:344-347`, two
docstrings, one stale section comment). Still Small: every file is one of three, and every
genuine decision is settled in this plan (see **Settled Decisions**); nothing is left for
the builder to rule on.

**What this actually buys, stated plainly.** In production today `repo_root` is always
`PROJECT_ROOT`, which always matches `valor`'s `working_directory` (see the Freshness Check
table), and the Rabbit Holes deliberately foreclose the only work that would produce a
different `repo_root`. So the *production* delta is narrow and honest: the destination stops
being a hardcoded literal and starts being read from `projects.json`, addressed by numeric
`chat_id` instead of an ambiguity-tolerant name, and a previously-discarded `valor-telegram`
exit code becomes observable. Ladder steps 4-5 and the whole suppression branch are
unreachable on production traffic — one of the seven routing cases covers it.

That is a deliberate trade, not an oversight, and the ladder must **not** be trimmed to
match. The suppression branch is the correctness precondition that makes Settled Decision
2's fallback narrowing safe: without it, an unmatched checkout falls back to `Eng: Valor`
and the misroute this issue exists to remove is re-created the moment #3072's sweep or any
future multi-checkout auditor produces a non-`PROJECT_ROOT` `repo_root`. Those branches are
paid for by unit tests rather than by production traffic, which is the cheap way to hold a
precondition.

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
   match. A numeric id still enters that cascade — `cmd_send` calls
   `resolve_chat(args.chat, strict=False)` unconditionally at `tools/valor_telegram.py:831`
   — but can never match a title or username, so it always falls through to the digit
   fallback at `:836` and the ambiguous-pick branch is unreachable for it.
   `sdlc_upvote_lanes` already routes by id for the same reason. The cost — the valor
   path's observable argv changes from `"Eng: Valor"` to `"-1003449100931"` — is accepted,
   and is pinned by a test (see Test Impact) rather than by a manual post-merge eyeball.
4. **Suppression is reported to the caller, not only logged. DECIDED: return `bool`, and
   the caller carries the notice in `summary` — not only in `findings`.**
   The step-9 message carries the PR URL and the review-required warning, and
   `docs/features/docs-auditor.md` names that Telegram message as *how* "review is
   required" is communicated. A suppressed send there means a real non-draft PR opens and
   nobody is told. The sender returns `False` on the no-destination path and both call
   sites convert that into a suppression notice.

   **`findings` alone does not reach an operator.** `docs_auditor` is a directly-registered
   function reflection, and `agent/reflection_scheduler.py:644-649` persists **only**
   `result["summary"]` (`output_summary=str(summary_str)[:500]`). The `findings` key is read
   by `reflections/utilities.py:194` solely inside `run_per_project_audit` (which
   `docs_auditor` does not use) and by `reflections/pm_briefings/__init__.py:236` against a
   different structure; the dashboard renders `output_summary` only
   (`ui/data/reflections.py:286`). So the notice **must** appear in `summary`:

   - Zero-diff return (`reflections/docs_auditor.py:2441-2443`) becomes
     `f"docs-auditor: zero-diff ({slug}){withheld_note}{suppressed_note}"`.
   - Step-9 return (`:2553-2558`) gains
     `f" [Telegram suppressed: no Eng: group for {repo_root}]"` inserted **before**
     `PR={pr_url}`, so the 500-char truncation at `agent/reflection_scheduler.py:648` can
     never cut the notice off the end.

   The `findings` appends stay as well — they are free and they are what a future
   `run_per_project_audit` consumer would read — but `summary` is the load-bearing channel
   and is what the new tests assert on.

## Solution

### Key Elements

- **`_resolve_notify_chat(repo_root: Path) -> str | None`** (new, private, in
  `reflections/docs_auditor.py`): maps the audited repo root to the string the `--chat`
  flag should carry, or `None` meaning *do not send*. Owns the entire resolution +
  fallback policy so the sender stays a thin subprocess wrapper.
- **`_send_telegram_notification(message, *, repo_root: Path | None = None) -> bool`**
  (modified): resolves `root = (repo_root or PROJECT_ROOT).resolve()` **in the body** —
  the same idiom `audit()` uses at `reflections/docs_auditor.py:1547` — then asks
  `_resolve_notify_chat` for a destination; on `None`, logs a warning naming the repo root
  and **returns `False`** without shelling out; otherwise sends as before and returns
  `True`. The default must **not** be `repo_root: Path = PROJECT_ROOT`: a default argument
  binds once at `def` time and captures the real module-level `PROJECT_ROOT`, while ladder
  step 4 re-reads `PROJECT_ROOT` at call time. Every `run_docs_auditor` test patches that
  global (`tests/unit/test_docs_auditor_substrate.py:1367`, `:1403`, `:1448`, `:1477`, and
  the shared `_run` helper), so a captured default and a call-time comparison would
  disagree under test and on any call relying on the default.
- **Two updated call sites**: both pass `repo_root=PROJECT_ROOT`, and when the call returns
  `False` both append a `findings` entry **and** thread the suppression notice into the
  returned `summary` (Settled Decision 4) — `summary` is the only field the reflection
  scheduler persists.
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
a `findings` entry **and** splices the suppression notice into the returned `summary`
(before `PR={pr_url}` on the step-9 path), which is the field
`agent/reflection_scheduler.py:648` persists and the dashboard renders.

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

**On the discarded `valor-telegram` exit code.** The sender today calls
`subprocess.run(..., check=False)` and never inspects the result
(`reflections/docs_auditor.py:1490-1496`), so a `valor-telegram` failure is silently
discarded. Routing by numeric id makes this sharper, not milder: a bad *name* fails locally
and loudly at `tools/valor_telegram.py:838` (`Error: Unknown chat`), while a bad *id* sails
through the digit fallback at `:836` and fails remotely at the API. Bind the result and log
the non-zero case:

```python
proc = subprocess.run(...)
if proc.returncode != 0:
    logger.warning(
        "docs_auditor: valor-telegram exited %s for chat %s: %s",
        proc.returncode,
        chat,
        (proc.stderr or "")[:200],
    )
```

This is a **new warning only, not a new return value**. The function still returns `True`
here: a destination was resolved and a send was attempted, which is exactly what keeps the
caller's "no Eng: group configured" finding text accurate. Test case 7 pins that split.

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
      by asserting `subprocess.run` was not called), **and** a suppression notice spliced
      into `result["summary"]` by the caller — the only result field the reflection
      scheduler persists (`agent/reflection_scheduler.py:648`) and the dashboard renders
      (`ui/data/reflections.py:286`). A matching `findings` entry is appended too, but
      `findings` is read nowhere for this reflection and cannot be the mitigation.
- [ ] On the step-9 path the notice must precede `PR={pr_url}` in the summary, because the
      persisted field is truncated at 500 chars and a suppressed notification there means a
      real non-draft PR is open with nobody notified. Asserted by index comparison.

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
- [ ] The three `run_docs_auditor` tests that inspect `notify` — **UPDATE**: add a
      `notify.call_args.kwargs["repo_root"]` assertion to each, so both call sites are
      pinned to actually thread the repo root. Without this the sender could be fixed while
      the call sites keep defaulting, and every test would still pass. Their roles, verified
      at revision time (the previous revision had two of the three labels inverted):
      - `:1376` — `test_bare_name_withhold_propagates_to_pr_body_telegram_and_liveness`
        (asserts `status == "skipped"`) → the **zero-diff** call site.
      - `:1413` — `test_rotation_result_surfaces_withheld_count` (asserts `status == "ok"`
        with a mocked PR return) → the **step-9** call site.
      - `:1453` — `test_all_withheld_zero_diff_run_still_notifies` (asserts
        `status == "skipped"`, `"zero-diff" in summary`) → the **zero-diff** call site.
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
         returns `True` (send attempted, not suppressed). Same case also covers a non-zero
         `returncode` on a resolved destination: `True` is returned and a `logger.warning`
         carrying the exit code is asserted via `caplog`.
- [ ] `tests/unit/test_docs_auditor_substrate.py` — **ADD** two cases asserting the
      suppression notice reaches the persisted field: patch `_send_telegram_notification`
      to return `False` and drive `run_docs_auditor` through (a) the zero-diff withheld
      path and (b) the step-9 path. Each must assert on **`result["summary"]`** — the only
      field `agent/reflection_scheduler.py:648` persists — not merely on
      `result["findings"]`. The step-9 case must additionally assert that the suppression
      notice appears *before* `PR=` in the summary string (`summary.index("suppressed") <
      summary.index("PR=")`), which is what makes it truncation-safe at 500 chars. Assert
      the `findings` entry too, but a `findings`-only test would pass against an inert
      mitigation and is therefore not sufficient on its own.
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
behavior only when the project row is unmatched entirely. Critically, this lane also stops
discarding `valor-telegram`'s exit code (see Technical Approach): a bad id fails remotely
at the API rather than locally, so the non-zero `returncode` warning is the only place that
failure can surface at all. Post-merge, confirm one real docs-auditor alert lands in
`Eng: Valor` before considering the lane closed.

### Risk 4: The narrowed fallback suppresses an alert an operator expected

**Impact:** An audited repo with a typo'd `Eng:` group, or one not registered in
`projects.json`, gets *no* docs-auditor Telegram alert instead of a misrouted one. Silence
can be mistaken for "nothing to report." On the step-9 path this is sharper: a real
non-draft PR opens and the "review required" message reaches nobody.
**Mitigation:** Two channels, not one. The skip logs a `logger.warning` naming the
resolved repo path *and* both possible causes, and the sender returns `False` so each call
site both appends a `findings` entry and splices the notice into `result["summary"]`.
`summary` is the load-bearing half: it is the only field
`agent/reflection_scheduler.py:648` persists (`output_summary`) and the only one
`ui/data/reflections.py:286` renders, so a `findings`-only mitigation would leave the
suppressed path with exactly the one-channel outcome this risk declares unacceptable. On
the step-9 path the notice is spliced **before** `PR={pr_url}` so the 500-char truncation
cannot drop it. This mirrors and extends the established precedent at
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
      warning **and** a suppression notice in the run's `summary` (carried before the PR
      URL so truncation cannot drop it).
- [ ] Amend `docs/features/docs-auditor.md:344-347` — the withheld-set paragraph currently
      says the withheld set is threaded into `findings`, `summary`, "and Telegram message —
      which states plainly that review is required". That asserts *unconditional* Telegram
      delivery, which this change falsifies, and the `:45` rewrite does not reach it.
      Condition the claim ("…and, when a destination resolves, a Telegram message which
      states plainly that review is required…") and state the suppression outcome there —
      that block is already where the `findings`/`summary` threading is explained, so it is
      also the right home for the summary-carrier behavior from Settled Decision 4.
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
- [ ] A `False` return from either call site puts a suppression notice in
      `result["summary"]` (the only persisted field), with the step-9 notice placed before
      `PR={pr_url}` so 500-char truncation cannot cut it; a matching `findings` entry is
      appended as well.
- [ ] A non-zero `valor-telegram` exit code on a resolved destination logs a warning
      carrying the exit code and still returns `True`.
- [ ] Auditing this checkout with an unreadable or unmatched `projects.json` still sends
      to `Eng: Valor` — today's behavior preserved on the degraded path.
- [ ] `docs/features/docs-auditor.md` no longer claims a fixed destination chat at `:45`
      **and** no longer claims unconditional Telegram delivery at `:344-347`.
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
  `_send_telegram_notification(message: str, *, repo_root: Path | None = None) -> bool`.
  Message stays the first positional parameter. **Do not** default the parameter to
  `PROJECT_ROOT` — use `(repo_root or PROJECT_ROOT).resolve()` in the body, the idiom
  `audit()` already uses at `:1547`, so the patched global is read at call time. Resolve
  first; on `None`, emit the warning and `return False` without invoking `subprocess.run`.
  Every other path returns `True`, including the existing swallowed-failure handlers.
- Bind the `subprocess.run` result and log a `logger.warning` on a non-zero `returncode`
  (exact shape in Technical Approach). Still `return True` — a send was attempted.
- Leave the existing `FileNotFoundError` / `TimeoutExpired` / `Exception` handlers in the
  sender otherwise exactly as they are (add only the `return True`).
- Update the zero-diff withheld-alert call site (currently `:2434`) to pass
  `repo_root=PROJECT_ROOT` and, when it returns `False`, add a suppression entry to the
  findings list that return branch builds **and** append a `suppressed_note` to the
  `summary` f-string at `:2441-2443`.
- Update the step-9 pass summary call site (currently `:2521`) to pass
  `repo_root=PROJECT_ROOT` and, when it returns `False`, append to the in-scope `findings`
  list an entry naming the suppression and carrying `pr_url`, **and** splice
  `f" [Telegram suppressed: no Eng: group for {root}]"` into the `summary` f-string at
  `:2553-2558` **before** the `PR={pr_url}` fragment. The summary edit is the load-bearing
  half: `agent/reflection_scheduler.py:648` persists `summary` only and truncates it at 500
  chars, so a notice appended after the PR URL can be cut and a `findings`-only change is
  inert.

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
- Add the two suppression tests (zero-diff path and step-9 path) described in Test Impact.
  Both assert on `result["summary"]`; the step-9 one also asserts the notice precedes
  `PR=`.
- Add `notify.call_args.kwargs["repo_root"]` assertions to the three existing tests that
  inspect `notify` — `:1376` and `:1453` (zero-diff sites) and `:1413` (the step-9 site) —
  so both call sites are pinned to thread the repo root.
- Run the full file and confirm the other eight `patch(...)` sites still pass unchanged.

### 3. Documentation

- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: `auditor-routing-docs`
- **Agent Type**: documentarian
- **Parallel**: false
- Rewrite the destination sentence at `docs/features/docs-auditor.md:45` per the
  Documentation section.
- Amend the withheld-set paragraph at `docs/features/docs-auditor.md:344-347` so the
  Telegram delivery claim is conditional, and state there that a suppressed run carries the
  notice in `summary` instead. Use the literal phrase "Telegram notification is suppressed"
  so the Verification row anchors on something the pre-existing cap wording does not
  already match.
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
| Feature doc no longer pins the chat | `! grep -q 'Eng: Valor. Telegram chat' docs/features/docs-auditor.md` | exit code 0 — broader than the old `notifies the ...` anchor, so a reintroduced claim in any phrasing fails |
| Feature doc documents suppression | `grep -q 'Telegram notification is suppressed' docs/features/docs-auditor.md` | exit code 0 — a distinct phrase, because a bare `suppress` grep already matches the pre-existing "entries past the cap are logged and suppressed" at `:343` and would pass vacuously |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

**Verdict (2026-09-02, /do-plan-critique cycle 1, FULL depth):** NEEDS REVISION — 1 blocker, 5 concerns, 3 nits. All 9 findings were addressed by the cycle-1 revision pass; the blocker was upheld and the plan's causal premise rewritten around it. Superseded.

**Verdict (2026-09-02, /do-plan-critique cycle 2, FULL depth):** NEEDS REVISION — 1 blocker, 4 concerns, 2 nits. All 7 findings were addressed by the cycle-2 revision pass (2026-09-02T06:25:46Z), including both nits: the `summary` field became the load-bearing suppression channel, the sender's `repo_root` default became `None`, the discarded `valor-telegram` exit code gained a warning, `docs/features/docs-auditor.md:344-347` joined the Documentation scope, the Appetite gained the "What this actually buys" paragraph, and the two test-role/`resolve_chat` nits were corrected. Superseded.

**Verdict (2026-09-02, /do-plan-critique cycle 3, FULL depth):** READY TO BUILD (with concerns) — 0 blockers, 2 concerns, 4 nits. All cycle-2 findings re-verified as landed. Every line-number, test-role, sweep-count, and import-graph claim in the plan was re-checked against HEAD and holds. The two concerns below are specification gaps a builder can trip on, not design defects.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness | `suppressed_note` is assigned only inside the branch that calls the sender, but it is interpolated into a `summary` f-string evaluated on **every** zero-diff return. The notify call at `reflections/docs_auditor.py:2434` sits inside `if fixes_withheld:`; the return at `:2440-2443` is outside it. A literal reading of Task 1 produces a `NameError` on the clean zero-diff path — the common production path, since most rotation runs withhold nothing. `run_docs_auditor` wraps everything in `try:` at `:2301` / `except Exception` at `:2561`, so the `NameError` never raises: it degrades to `{"status": "error"}` plus a `logger.warning`. The only existing test on that path, `test_clean_zero_diff_run_does_not_notify` (`tests/unit/test_docs_auditor_substrate.py:1471`), calls `run_docs_auditor()` without binding the result and asserts only `notify.call_count == 0`, so it passes against the broken build; both new suppression tests drive the suppressed path and would also pass. | pending | Mandate `suppressed_note = ""` immediately before the `if fixes_withheld:` guard at `:2433`, mirroring how `withheld_note` is already computed unconditionally, and assign it only inside the `if not _send_telegram_notification(..., repo_root=PROJECT_ROOT):` branch. Apply the same initialize-first rule on the step-9 path even though the sender is unconditional there. Then extend `test_clean_zero_diff_run_does_not_notify` to bind the result and assert `result["status"] == "skipped"` — without that assertion the `except Exception` at `:2561` converts any name or attribute slip this change introduces into a green test run and a silently broken production reflection. |
| CONCERN | History & Consistency | The plan hand-rolls a repo-path to project match without acknowledging that `config/project_key_resolver.py::resolve_project_key` already owns that mapping — its module docstring advertises "projects.json working_directory prefix match against cwd" as priority 3. Two costs: review has no recorded answer for why the canonical resolver was not reused, and a builder who finds it would re-introduce exactly the defect this plan removes, because its priority chain returns `VALOR_PROJECT_KEY` **before** consulting the path (`config/project_key_resolver.py:120-125`) — the `project_key` routing the Problem section rules out as creating a misroute rather than removing one. Its module-level `_projects_cache` also contradicts Risk 2's explicit "No caching is added", and it returns a bare key rather than the project dict `resolve_eng_group` requires. | pending | Add a paragraph under Architectural Impact, or a fifth Settled Decision, recording that `config/project_key_resolver.py::resolve_project_key` is deliberately **not** used, for three named reasons: its `VALOR_PROJECT_KEY` precedence at `:120-125` would re-create the env-keyed misroute the Problem section voids; it returns a `str` key, not the project dict `resolve_eng_group(project)` needs; and its `_projects_cache` is the staleness Risk 2 rejects. Then add a Task 1 bullet: "Do not import from `config.project_key_resolver`." Without that line the rejection is invisible to both the builder and review. |
| NIT | Risk & Robustness | Task 1 says "Add a module-scope `from reflections.utilities import load_local_projects, resolve_eng_group`". `reflections/utilities.py` also exports `PROJECT_ROOT`, and every existing consumer of that import in the tree pulls it in together (`reflections/audits/pr_review_audit.py:29`, `reflections/audits/task_backlog_check.py:22`, `reflections/sentry_triage.py:44`, `reflections/housekeeping/merged_branch_cleanup.py:33`). A builder copying the established idiom would rebind `docs_auditor`'s own `PROJECT_ROOT` from `:38`. | pending | Import exactly `load_local_projects` and `resolve_eng_group`, never `PROJECT_ROOT`. The values are identical today (both `Path(__file__).parent.parent` of a file in `reflections/`), so the rebind is currently harmless — but it would survive a future change to either definition, and every `patch("reflections.docs_auditor.PROJECT_ROOT", repo)` site would then be patching a name owned by a different module. |
| NIT | Scope & Value | Test Impact case 7 bundles two behaviours that cannot share one test body: `subprocess.run` raising `FileNotFoundError`, and `subprocess.run` returning a non-zero `returncode`. They need different mock configurations and assert different things. As one case it is unimplementable; as two the Appetite's "nine new tests" becomes ten. | pending | Split into 7a — `patch("reflections.docs_auditor.subprocess.run", side_effect=FileNotFoundError)`, assert the call returns `True` (send attempted, not suppressed) — and 7b — a `CompletedProcess`-shaped mock with `returncode=1` and `stderr="boom"`, assert `True` **and** that `caplog` carries the "valor-telegram exited 1" warning. Restate the Appetite count as ten. Bookkeeping only; drop neither behaviour. |
| NIT | Scope & Value | The Verification row "Feature doc documents suppression" cites the pre-existing cap wording at `docs/features/docs-auditor.md:343`; the wrapped line actually lands at `:344`, and 20-plus other `suppress` hits exist in that file. The row's reasoning is right and its chosen anchor `grep -q 'Telegram notification is suppressed'` matches zero lines at HEAD, so it is a genuine post-Task-3 check. | pending | Cosmetic — correct `:343` to `:344` or drop the line citation. The Verification row itself needs no change. |
| NIT | History & Consistency | The Verification row `test "$(grep -c 'repo_root=PROJECT_ROOT' reflections/docs_auditor.py)" -ge 3` cannot tell *which* three lines matched; a build threading the root at one notify site plus an unrelated kwarg satisfies it. The plan's Risk 1 names this exact failure and mitigates it with the `notify.call_args.kwargs["repo_root"]` assertions, so this is the weaker of two overlapping checks, not a gap. | pending | No change required. Noted so a validator reading the Verification table in isolation does not treat this row as proof of Risk 1's mitigation — the authoritative check is the three `notify.call_args.kwargs["repo_root"]` assertions in Task 2. |

**Structural checks (cycle 3):** required sections present; task numbering 1-4 with no gaps; `Depends On` graph acyclic and every reference valid; all 15 referenced file paths exist; every Success Criterion maps to a task; no Rabbit Hole or No-Go appears in the Solution or tasks as planned work; no prerequisites to check. The `git grep -lF '"Eng: Valor"' -- '*.py'` sweep returns exactly the six #3072 files the plan names, and `grep -c 'repo_root=PROJECT_ROOT'` is 1 at HEAD, so the `-ge 3` target is correct.

**Critique execution note (cycle 3):** no `Task`/`Agent` tool was available in this session, so the three FULL-depth critic lenses (Risk & Robustness, Scope & Value, History & Consistency) were executed in-process by the critique driver rather than as independent forked agents. The frozen `_roster.json` manifest, per-critic result files, terminal completion fence, and the `critique-roster-check --plan-path` grounding gate all ran as normal and reported `complete: true, ungrounded: []`.
