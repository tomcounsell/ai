---
status: Ready
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-07-27
tracking: https://github.com/tomcounsell/ai/issues/2334
last_comment_id: 5086978978
revision_applied: true
revision_applied_at: 2026-09-02T06:47:39Z
---

# Nightly Regression: Classify Before Paging a Human (shadow tier)

## Problem

The nightly regression detector (`scripts/nightly_regression_tests.py`) runs the
unit suite each night. On a newly-confirmed serial failure it does two things in
the same run: dispatches an **investigate-only** Eng session (`maybe_dispatch_triage_session`
L856; mandate `"...auto-hotfix — this is an investigation-and-file-an-issue task only"`
L846) **and** pages a human up front (`send_telegram(msg, dry_run=args.dry_run)` L1220,
on the `elif new_failures:` arm at L1207). The human is the *first responder* — they
wake to a raw "tests are red" ping carrying the full cognitive load of "what broke and
what do I do", even when the regression is a mechanical test carry-forward.

The worked example: **#2399 → PR #2402**. 11 newly-confirmed nightly failures, all
**test-stale** (source contracts moved in merged PRs, the tests weren't carried forward).
A human triaged and fixed them by hand.

**Current behavior:** Detection → dispatch investigate-only session **and** page a human
immediately. Every red suite pages, regardless of what kind of red it is.

**Desired end state:** Detection → classify each newly-confirmed failure as
*newly-broken-since-last-green* vs *pre-existing* vs *inconclusive* → route the
newly-broken-and-within-caps case to a bounded autonomous fix, and page a human only as
the escalation of last resort.

**What this plan ships (deliberate scope cut — round 7).** The classification stage and
the decision gate, running in `shadow`: every non-`off` run computes and logs the verdict
it *would* act on, while paging exactly as today. **It does not ship the autonomous fixer.**
The dispatch, hand-back, watchdog, PR guards, enforcement preflight, and the low-urgency
notify tier are deferred to **#3076**, because they depend on two seams that do not exist
on this machine and cannot be conjured by this plan (see Scope Boundary below).

This is the smaller plan that is entirely real, chosen over the complete one that was
partly fictional. The shadow verdict is the artifact that earns #3076: a month of logs
answering "would the gate have fired, and would it have been right?" is the evidence the
autonomous tier is currently missing.

## Scope Boundary — why `active` mode is not in this plan

Three consecutive critique rounds found the same failure class: a load-bearing mechanism
asserted without a verified invocation seam (round 1's permission profile, round 5's
`--silent` flag, round 6's `GH_TOKEN` export). Round 7 opened the source for every
mechanism the autonomous tier depends on. Two of them have no seam:

**1. There is no per-session environment seam, so the fixer's `gh` identity cannot be set.**
The only structural never-merge guarantee requires the fixer session's `gh` to authenticate
as a non-admin bot (`SDLC_AGENT_GH_TOKEN`), never the operator's admin credentials. Verified
at `2d60de31d`:

| Hop | Evidence | Verdict |
|---|---|---|
| Dispatch call site | `scripts/nightly_regression_tests.py` L909-927: `subprocess.run([sys.executable, "-m", "tools.valor_session", "create", ...], cwd=PROJECT_DIR, capture_output=True, text=True, timeout=30)` — **no `env=` kwarg** | No seam |
| The CLI itself | `valor-session create --help` lists `--role/--message/--chat-id/--telegram-message-id/--parent/--project-key/--slug/--model/--needs-real-chrome/--job-id/--expect-what/--json` — **no env or token flag**. It only *enqueues* an `AgentSession` | No seam, and adding `env=` above would configure the enqueuing CLI, not the session |
| Who actually spawns `claude -p` | The **worker**, a separate launchd process | Different process entirely |
| The worker's env overlay | `agent/session_runner/role_driver.py::subscription_auth_env` L76-100, called at L204 (`self.env = subscription_auth_env(env)`); merged into `proc_env` at `agent/session_runner/harness/claude.py` L431-433 | Process-global, derived from the worker's own `os.environ` — **not per-session** |
| A place to persist a per-session override | `models/agent_session.py` field list — **no env/token/permission field** | No seam |

`TurnRequest.env` (`agent/session_runner/harness/base.py` L52) is the one plumbing that
*could* carry it, but nothing session-specific reaches it. Building this means a new
per-session env override persisted on `AgentSession`, read by the worker, threaded through
`TurnRequest.env` into the `claude -p` spawn, with its own tests. That is standalone work,
filed as part of **#3076** — not a dispatch-site one-liner, and not something to assert in
a Success Criterion before it exists.

**2. Branch protection on `main` is absent.** `gh api repos/tomcounsell/ai/branches/main/protection`
→ `404 Not Found` (re-verified 2026-09-02). Enabling review-required protection is a
repo-governance action for the owner, and without it seam 1 buys nothing anyway.

Because both legs of the never-merge guarantee are unavailable, an `active` mode built now
would dispatch a `bypassPermissions` session with unrestricted bash and the operator's admin
`gh` credentials into an unprotected `main`. The correct move is not to ship it behind a
flag nobody can enable — that is unreachable code whose safety claims nothing tests. It is
to cut it.

**Consequently deferred to #3076** (design detail preserved there and in Critique Results
below): the narrow-mandate draft-PR fixer dispatch, the `GH_TOKEN` export, the
`AgentSession.nightly_fix_handback` field, `tools/nightly_fix_handback.py`, the
escalation/fail-safe watchdog preamble, the fail-closed diff-path and merge/draft-state
guards, the three-leg `active`-mode preflight, the per-node `fix_sessions` map, and the
`valor-telegram send --silent` notify transport (deferred with the rest because without an
`active` happy path it would be a flag with no consumer — a dead surface propagated to every
machine).

## Freshness Check

**Baseline commit:** `2d60de31d` (`git rev-parse origin/main`, 2026-09-02, round-7 revision
pass). Every file:line reference in this plan is resolved against that SHA and was re-read
at revision time, not carried forward from an earlier round.

**Issue filed at:** 2026-07-24T06:45:58Z · **Plan first written:** 2026-07-27
**Disposition:** Minor drift — **the premise is intact; the plan's scope changed for
seam reasons, not drift reasons.**

The defect this plan exists to fix is unchanged: `main()` still pages up front on the
`elif new_failures:` branch (L1207 → `send_telegram(msg, dry_run=args.dry_run)` L1220),
and the dispatched session is still investigate-only (L846; the seed prompt repeats it at
L1127). Nothing else has taken over the concern and no merged PR fixes it. **Proceed.**

**Re-verified at `2d60de31d` — claims this plan depends on:**

| Claim | Location | Status |
|---|---|---|
| Up-front unconditional page on newly-confirmed failures | `main()` `elif new_failures:` L1207; `send_telegram(...)` L1220 | **Holds** — the defect |
| `send_telegram(msg, dry_run=False)` has no urgency parameter | L609 | **Holds** |
| `send_telegram` never checks the subprocess `.returncode` and logs `"Telegram sent"` unconditionally | L626-635 (`subprocess.run(..., capture_output=True)` L626; `log(f"Telegram sent: {msg}")` L633) | **Holds** — a failed or rejected send is indistinguishable from a delivered one. Fixed by Step 1 |
| `_spawn_pytest(argv, timeout, env=None)` hardcodes `cwd=PROJECT_DIR` | L283 (signature), L302 (`cwd=PROJECT_DIR` in the `Popen` call) | **Holds** — the classifier cannot reuse it unmodified (round-6 blocker 2) |
| `scripts/pytest-clean.sh` derives its rootdir from the caller's cwd | L34-38: `REPO_ROOT="$(pwd)"` when cwd has a `[tool.pytest` section, else the script's own root | **Holds** — so a `cwd=<worktree>` spawn *does* target the worktree |
| `pytest-clean.sh` aborts on a linked worktree with no `.venv` of its own (#3033) | L135-145 | **Holds** — a bare `git worktree add` produces exactly this, and aborts |
| `pytest-clean.sh` aborts on an off-pin interpreter (#2617) | L169-175 (`scripts/check-interpreter-pin.sh`) | **Holds** — the provisioned venv must be on the `.python-version` pin |
| `head_commit` is already persisted on every non-fatal path | `current["head_commit"] = _get_head_commit()` L1092; helper L1344 | **Holds** — consumed, never added |
| `compute_new_failures` (L654) drives the alert; `compute_dispatch_set` (L728) answers "what has never been filed" — different sets since #2559 | L654 / L728 | **Holds** — the gate consumes `new_failures` |
| `MAX_DISPATCH_NODES = 10` (L185) truncates `dispatch_nodes` at L1160-1166 and never touches `new_failures` | L185, L1160-1166 | **Holds** — not a fixer disqualifier; see Cap arithmetic |
| Seed / re-baseline run flags | `is_reseed` L1026, `is_seed_run` L1027, `seeded_nodes()` L683 | **Holds** — hard gate disqualifier |
| `validate_run_integrity()` → `integrity_warnings` | L401 (definition), L1047 (call) | **Holds** — hard gate disqualifier |
| `reconfirm_serial()` serial gate → `confirmed_failing` | L532 | **Holds** |
| Per-node dedup helpers this plan must not disturb | `prior_dispatched` L669, `compute_dispatch_set` L728, `carry_dispatched_nodes` L754 | **Holds** |
| Scalar `current["dispatched_session_id"]` carried from `prev` | L1090 | **Holds** — untouched by this plan |
| `main` branch protection is ABSENT | `gh api repos/tomcounsell/ai/branches/main/protection` → `404` | **Holds, re-verified 2026-09-02** |
| `baseline-verifier` is reachable only via the Claude Task tool | sole caller `.claude/skills-global/do-test/baseline-verification.md` L76-81; zero `.py` callers repo-wide | **Holds** — classification is in-process Python; the subagent file stays untouched |
| No per-session env seam anywhere in the dispatch → worker → `claude -p` chain | See the Scope Boundary table above | **Newly recorded (round 7)** — the reason `active` mode is cut |
| `grep -E` treats `\|` as a **literal pipe**, not alternation | Demonstrated: a file containing both `gh pr ready` and `gh pr merge` returns `0` for `grep -Ec "gh pr ready\|gh pr merge"` and `2` for `grep -Ec "gh pr ready\|gh pr merge"` without the backslash | **Newly recorded (round 7)** — two Verification rows were passing vacuously |

**Overlapping active plans:** `nightly-serial-reconfirm.md` (the #2180 serial gate — a
dependency, not a conflict). No in-flight lane owns `scripts/nightly_regression_tests.py`.

## Prior Art

- **#2192 / PR #2195** — added `maybe_dispatch_triage_session` (investigate-and-file-an-issue) plus the run lock and LLM summarizer. **Deliberately deferred auto-hotfix as a No-Go.** This plan does not reverse that No-Go; it stops short of the fixer entirely.
- **#2399 / PR #2402** — the worked example. 11 test-stale failures across 3 groups. Two groups were mechanical carry-forward; **Group 2 hid a genuine design fork** (self-heal intended vs. a safety hole) that required a human reading #2144's intent. **Lesson:** "classifies as newly-broken" is *necessary but not sufficient* for autonomy. That is precisely why the classifier alone is a coherent shippable unit and the fixer is not.
- **#410** — Autoexperiment (autonomous prompt-optimization loop). Prior art for a bounded autonomous loop with caps; informs the guardrail-constant design.
- **#2559 / PR #2581** — replaced set-hash triage dedup with per-node dedup (`dispatched_nodes`, `compute_dispatch_set`, `carry_dispatched_nodes`). Its lesson applies here: a second, parallel keying scheme in the same state file is how the #2429/#2430/#2462 churn started. This plan adds **one** per-node map and prunes it with the existing rule.
- **#2823 (collection-aware baseline)** — added seed/re-baseline runs, `MAX_DISPATCH_NODES` truncation, and `validate_run_integrity`. These are the run shapes the gate must refuse to act on.
- **#3033 / #2617** — the `pytest-clean.sh` worktree-`.venv` and interpreter-pin aborts. Not background: they are the two guards that decide whether the classifier works or ships inert.
- **#3076** — the deferred `active`-mode stack (dispatch, hand-back, watchdog, guards, enforcement, notify transport), filed by this revision.
- **#2405** — Cowork parity / intra-day watchdog follow-up, still OPEN. Out of scope.

## Data Flow

1. **Entry point**: launchd fires `python scripts/nightly_regression_tests.py`. `load_env_or_die()`, run lock, `run_tests()`, `reconfirm_serial()` → `confirmed_failing`, `new_failures = compute_new_failures(prev, confirmed_failing)` (L654). **Unchanged.**
2. **Classification** *(new)*: when `NIGHTLY_FIX_MODE != "off"` and `new_failures` is non-empty, run `classify_against_baseline(new_failures, prev["head_commit"])` — a synchronous in-process function that re-runs exactly those node IDs at the last-known-good SHA in a **provisioned baseline worktree** and buckets each node `{newly_broken, pre_existing, inconclusive}`. No subagent, no session, no Task tool.
3. **Decision gate** *(new)*: `decide_fix_or_escalate(classification, new_failures, caps, run_flags)` — a pure function returning `"autonomous-fix" | "escalate"`.
4. **Verdict log** *(new)*: the gate's result is logged under a stable greppable prefix with the first failing condition as a `reason=` token.
5. **Alert** *(unchanged in this plan)*: the up-front page fires exactly as today. `off` skips steps 2-4 entirely; `shadow` runs them and still pages.

There is no step 6. Acting on the verdict is #3076.

## Architectural Impact

- **New dependencies**: none external. Reuses `git worktree`, `uv sync`, and pytest — machinery the detector and the repo already own.
- **Interface changes**:
  - `send_telegram` (L609) gains a `.returncode` check it currently lacks. No signature change.
  - `_spawn_pytest` (L283) gains a `cwd: Path | str = PROJECT_DIR` parameter. Existing callers (`run_tests` L379, `reconfirm_serial` L587) pass nothing and stay byte-identical.
  - `data/nightly_tests_last_run.json` gains **one** per-node map: `classify_attempts: {node_id: count}`, pruned by exactly the rule `carry_dispatched_nodes` already applies to `dispatched_nodes`. `head_commit` is already persisted (L1092) — consumed, not added. The scalar `dispatched_session_id` (L1090) is untouched.
  - `.claude/agents/baseline-verifier.md` is **not touched** (no Python invocation seam).
  - No `AgentSession` schema change, therefore **no Popoto migration** in this plan.
- **Coupling**: the classifier is self-contained in the nightly script. It reads git and runs pytest — no Redis, no ORM, no session substrate, no new orchestration.
- **Reversibility**: total. `NIGHTLY_FIX_MODE=off` skips classification and the gate entirely, leaving the detector byte-identical to today. The default `shadow` adds a bounded pytest run and a log line and changes no outbound behavior.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1 (confirm the baseline-worktree provisioning cost is acceptable on the nightly critical path)
- Review rounds: 1-2

Reduced from Large in round 7. The Large appetite was carried by the autonomous fixer's
safety-review surface; with that deferred to #3076, what remains is a bounded classifier
and a pure function. The remaining review weight is on the classifier being *real* rather
than inert.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `uv` available to the nightly process | `uv --version` | Provisions the baseline worktree's `.venv` |
| `git worktree` usable | `git worktree list` | The classifier checks out `prev["head_commit"]` detached |
| Committed interpreter pin present | `cat .python-version` | `pytest-clean.sh` L169-175 aborts on an off-pin venv; the provisioned venv must match |
| `valor-telegram` reachable | `test -x .venv/bin/valor-telegram` | Existing alert channel (unchanged) |

No new secrets. No branch-protection prerequisite — nothing in this plan dispatches a
session or opens a PR.

## Solution

### Key Elements

- **`send_telegram` returncode check**: strictly independent, strictly an improvement. Today a rejected or failed send logs `"Telegram sent"` (L633) and the run continues believing a human was paged. Ships first, on its own.
- **`_spawn_pytest` gains `cwd`**: the one-line seam that lets a second caller target a different tree. Default `PROJECT_DIR` keeps both existing callers byte-identical.
- **Provisioned baseline worktree**: a persistent worktree at `.worktrees/nightly-baseline/` with **its own `.venv`**, re-pointed to `prev["head_commit"]` each run. This is not a detail — it is the difference between a working classifier and an inert one (round-6 blocker 2). A bare `git worktree add` has no `.venv`, and `pytest-clean.sh` L135-145 refuses to run there; running at `PROJECT_DIR` instead would import HEAD's source and bucket every node `pre_existing`. Both failure modes are silent and safe-looking.
- **In-process classifier**: `classify_against_baseline(node_ids, baseline_sha)`, synchronous, returning three discrete buckets. Every exception path lands in `inconclusive`, which the gate treats as escalate — a broken classifier fails toward paging.
- **Pure decision gate**: `decide_fix_or_escalate(...)`, no I/O, exhaustively unit-testable.
- **Non-stubbed classifier test**: a two-commit fixture repo where a node passes at the baseline SHA and fails at HEAD must classify `newly_broken`. Every other classifier test stubs the classifier and therefore cannot distinguish "working" from "inert but safe" — this one test is what makes the feature's core claim falsifiable.
- **Greppable verdict log**: `nightly-fix shadow-verdict: {autonomous-fix|escalate} reason={first failing condition} nodes={n}` on every non-`off` run. This is the plan's delivered artifact: the evidence #3076 is gated on.
- **Guardrail constants**: three named, env-overridable knobs read via raw `os.environ.get` at module scope, each with a provisional/tunable comment. Deliberately not promoted to `config/settings.py` — see Technical Approach.

### Flow

Nightly run detects newly-confirmed failures →
- `NIGHTLY_FIX_MODE=off`: skip classification and the gate entirely; page as today. Byte-identical to current behavior.
- `NIGHTLY_FIX_MODE=shadow` (the default): re-point and sync the baseline worktree → `classify_against_baseline(new_failures, prev["head_commit"])` → `decide_fix_or_escalate(...)` → log the verdict under the stable prefix → **page as today**. Nothing else changes.

There is no third mode in this plan. `active` is #3076.

### Technical Approach

**Classifier — in-process Python, NOT the `baseline-verifier` subagent.**
Verified at `2d60de31d`: `baseline-verifier` is a Claude *subagent* dispatched only through
the Task tool; its sole caller repo-wide is `.claude/skills-global/do-test/baseline-verification.md`
L76-81, and no `.py` file invokes it. `scripts/nightly_regression_tests.py` is a plain
launchd Python process with no Task tool. The two escape hatches both fail: routing through
`tools.valor_session create` would make classification *asynchronous* (a two-night handshake
inside one nightly invocation), and the `${baseline_ref:-main}` shell default cannot be set
by a Task dispatch, so it would always resolve to literal `main` — silently reinstating the
exact masking this plan exists to avoid. `.claude/agents/baseline-verifier.md` is left
untouched, so every `/do-test` caller is unaffected.

The mechanic needs no LLM: it is "re-run these node IDs at a known commit and see whether
they passed."

- **The baseline ref is `prev["head_commit"]` — NOT bare `main`.** Nightly runs on `main` HEAD and the failure IS on `main`, so diffing against `main` would mask a regression that already landed there. A *newly-confirmed* failure was by definition absent from the prior run's confirmed-failing set, so the prior run's HEAD SHA is a commit where that test passed. `head_commit` is already persisted at L1092 — consumed, not added. The SHA is interpolated as a literal; there is no shell parameter default to mis-resolve.
- **Interpretation**:
  - **PASSED at last-green, FAILS at HEAD** → `newly_broken`. Something in `last_green..HEAD` moved a contract or introduced a regression. This is the set that would be eligible for a fix attempt under #3076.
  - **FAILED at last-green too** → `pre_existing`; not caused by recent change → escalate.
  - **`inconclusive`** — worktree provisioning failure, missing baseline SHA, pytest collection error, timeout, a node absent at baseline, or any raised exception → escalate; never guess.

**Baseline worktree provisioning — the seam that decides whether the classifier is real
(BLOCKER resolution, round 7).** Rounds 5-6 said "run the node IDs there with the same
pytest invocation the detector uses" and stopped. That instruction is not executable.
Verified:

- `_spawn_pytest` (L283) is `_spawn_pytest(argv: list[str], timeout: int, env: dict | None = None) -> int` and bakes `cwd=PROJECT_DIR` into its `Popen` call at L302.
- Its argv routes through `scripts/pytest-clean.sh`, which resolves `REPO_ROOT` from the **caller's cwd** (L34-38) — so a `cwd=<worktree>` spawn genuinely targets the worktree, which is what makes this approach viable at all.
- But that same wrapper **refuses to run in a linked worktree with no `.venv` of its own** (L135-145, #3033) and **refuses an off-pin interpreter** (L169-175, #2617).

So a bare `git worktree add` yields exactly the tree the wrapper rejects. The three
outcomes rounds 5-6 left open were: run at `PROJECT_DIR` (imports HEAD's source → every
node buckets `pre_existing` → gate escalates 100% of the time), run in a venv-less worktree
(wrapper aborts → `inconclusive` → escalates 100% of the time), or provision the worktree
(never mentioned). All three are indistinguishable from a working classifier under
fully-stubbed tests.

**Resolution — three concrete changes:**

1. **`_spawn_pytest` gains `cwd: Path | str = PROJECT_DIR`**, forwarded to `Popen`. `run_tests` (L379) and `reconfirm_serial` (L587) pass nothing and are byte-identical. This is the seam; it does not exist today.
2. **A persistent, provisioned baseline worktree** at `.worktrees/nightly-baseline/`:
   - If absent: `git worktree add --detach .worktrees/nightly-baseline <baseline_sha>`, then `uv sync` inside it to create a pin-matching `.venv`.
   - If present: `git -C .worktrees/nightly-baseline checkout --detach <baseline_sha>`, and re-run `uv sync` **only when `uv.lock` differs** between the worktree's checked-out revision and the last provisioned one (recorded in a marker file inside the worktree). This is what keeps the amortized cost near zero on the common night.
   - It is **persistent by design** — a fresh `uv sync` every night is the cost Risk 2b was written against. Add `.worktrees/` to the worktree-gc exclusion the repo already honors for named worktrees, or name it so `scripts/worktree-gc.sh` leaves it alone.
   - Any failure in provisioning (worktree add, checkout, `uv sync` non-zero) → the whole classification is `inconclusive` → escalate. It never falls back to `PROJECT_DIR`; a silent fallback is exactly the inert-but-safe-looking failure this resolution exists to prevent.
3. **The classifier's pytest invocation** mirrors `reconfirm_serial`'s serial form (L575-584): `[str(PYTEST_CLEAN_SH), *node_ids, "-n0", "--tb=no", "-q", "--json-report", f"--json-report-file=..."]`, spawned via `_spawn_pytest(argv, timeout=..., env=..., cwd=BASELINE_WORKTREE)`. It goes **through** `pytest-clean.sh`, not around it — the wrapper's guards are load-bearing here, and bypassing them is how #3033's false-green happens.

The report is parsed the same way `reconfirm_serial` parses its own; a node absent from
the baseline report is `inconclusive`, never assumed-passed.

**Cost.** One targeted pytest run over only the failing node IDs (never the suite), plus a
detached checkout and a conditional `uv sync`. It runs only when `new_failures` is non-empty
and only outside `off` mode. It carries its own timeout; a timeout is `inconclusive`.

**Decision gate** (`decide_fix_or_escalate(classification, new_failures, caps, run_flags) -> "autonomous-fix" | "escalate"`):
pure, unit-testable, no I/O. Returns `autonomous-fix` iff **all** of:

```
not run_flags.is_seed_run              # a re-baseline declares state, it does not discover a regression
and not run_flags.integrity_warnings   # an untrusted confirmed set is not a basis for any verdict
and not run_flags.dry_run              # --dry-run stays a pure preview
and classification.pre_existing == []
and classification.inconclusive == []
and set(new_failures) == set(classification.newly_broken)
and len(new_failures) <= NIGHTLY_FIX_MAX_FAILURES
and all(classify_attempts.get(n, 0) < NIGHTLY_FIX_MAX_ATTEMPTS for n in new_failures)
```

The gate consumes **`new_failures`** (from `compute_new_failures`, L654) — the population
this feature is about. It is deliberately *not* `compute_dispatch_set`'s output (L728),
which answers the different question of what has never been filed; the two sets diverged in
#2559 and conflating them would let a standing, already-filed failure enter the eligible
bucket. The classifier returns discrete buckets, so "high confidence" is bucket membership,
not a threshold float.

In this plan the verdict is **computed and logged only**. `autonomous-fix` means "the gate
would have attempted a fix" — it triggers nothing.

**Cap arithmetic — `MAX_DISPATCH_NODES` is NOT a disqualifier.** `MAX_DISPATCH_NODES = 10`
(L185) truncates `dispatch_nodes` — the output of `compute_dispatch_set` (L728), applied at
L1160-1166 — which answers *"what has never been filed as a triage issue"*. It never touches
`new_failures` (L654), which is not truncated anywhere. Carrying the triage cap into this
gate would make the effective ceiling `min(NIGHTLY_FIX_MAX_FAILURES, 10)`, killing every
configured value 11..15, **and would disqualify this plan's own motivating case**: #2399 had
11 newly-confirmed failures. The two caps govern disjoint sets and are never reconciled
numerically. `NIGHTLY_FIX_MAX_FAILURES` (default 15) is the sole volume cap here.

**Attempt tracking — one per-node map, pruned by the existing rule.** Persist
`classify_attempts: {node_id: count}` in `data/nightly_tests_last_run.json`, keyed by pytest
node ID to match the live per-node dedup model (`dispatched_nodes` / `compute_dispatch_set` /
`carry_dispatched_nodes`) that #2559 put in place of the retired `dispatched_hash`.

- A node entering the failing set starts at 0; a node absent from the map is unattempted.
- `classify_attempts[node]` increments once per classification attempt, **before** the baseline run, so a crash mid-classification still counts and cannot loop forever.
- **Going green prunes the node**, by exactly the keep-while-still-in-`confirmed_failing` rule `carry_dispatched_nodes` already applies. The map therefore holds only currently-red nodes, cannot grow unbounded, and a node that later re-regresses starts fresh.
- At `NIGHTLY_FIX_MAX_ATTEMPTS` the gate returns `escalate` rather than re-classifying.

Only **one** map, not two: `fix_sessions` existed to let a watchdog resolve a dispatched
session, and there is no dispatch in this plan. It moves to #3076 with the watchdog.

**Discoverable shadow verdict.** Every non-`off` run logs, under a stable prefix:

```
nightly-fix shadow-verdict: {autonomous-fix|escalate} reason={first failing gate condition} nodes={n}
```

One `reason` token names the *first* condition that failed the short-circuit, so a month of
logs answers "why did the gate refuse?" without re-deriving it. This line is the plan's
delivered artifact and the entry condition for #3076.

**Guardrail constants — raw `os.environ.get` at module scope, NOT `config/settings.py`.**
Three knobs (`NIGHTLY_FIX_MODE`, `NIGHTLY_FIX_MAX_FAILURES`, `NIGHTLY_FIX_MAX_ATTEMPTS`)
as module-level constants in `scripts/nightly_regression_tests.py`, each read via
`os.environ.get(...)` with an in-code default and a one-line provisional/tunable comment.
Deliberate: `config/settings.py`'s `TimeoutSettings` is the home for cross-cutting
timeout/retry/TTL values consumed across the bridge/worker/agent runtime (per
`docs/features/config-timeout-catalog.md`'s promote-vs-name-locally criterion). These are
single-consumer knobs of one standalone launchd script that imports nothing from the runtime
config surface. If a second consumer appears, promote them then. The other two knobs from
earlier rounds (`NIGHTLY_FIX_MAX_CHANGED_FILES`, `NIGHTLY_FIX_HANDBACK_TIMEOUT_HOURS`) are
**not declared here** — their only consumers were the deferred watchdog guards, and a
declared constant with no reader is the dead config round 4 already caught once.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] **Baseline worktree provisioning failures** — `git worktree add` non-zero, `git checkout --detach` non-zero (unknown SHA), `uv sync` non-zero — each → whole classification `inconclusive` → gate returns `escalate`. Assert explicitly that the classifier does **not** fall back to running at `PROJECT_DIR`.
- [ ] **Classifier run failures** — pytest collection error, `TimeoutExpired`, unparseable/missing JSON report, any raised exception → `inconclusive` → `escalate`.
- [ ] **Node absent from the baseline report** → that node is `inconclusive`, never assumed-passed.
- [ ] **`send_telegram` transport failure**: stub `subprocess.run` to return a non-zero `returncode` → a WARNING naming the return code is logged and the `"Telegram sent"` success line is NOT emitted. Regression test for the L626-635 defect.
- [ ] **`_spawn_pytest` back-compat**: `run_tests` and `reconfirm_serial` still spawn with `cwd=PROJECT_DIR` after the parameter is added (assert on the `Popen` kwargs via call capture).

### Empty/Invalid Input Handling
- [ ] `new_failures == []` → no classification, no gate, no verdict log, no behavior change. Test.
- [ ] `prev["head_commit"]` absent (first run after deploy, or state from an older schema) → no baseline can be established → `escalate` without classifying, and the verdict log names that reason. Test.
- [ ] `NIGHTLY_FIX_MODE` set to an unrecognized value → treated as `off` (fail toward today's behavior), logged once. Test.

### Non-Stubbed Classifier Validation (the falsifiability test)
- [ ] **Two-commit fixture repo, no stubbing of `classify_against_baseline`.** Build a temporary git repo with a trivial test that passes at commit A and fails at commit B, provision it the same way the real classifier provisions its baseline worktree, and assert the node buckets `newly_broken` when classified at baseline A. **This is the one test that can distinguish a working classifier from an inert one** — every other classifier test stubs the classifier and would pass identically against a function that returns all-`pre_existing` or all-`inconclusive` (round-6 blocker 2).
- [ ] Same fixture, node failing at **both** commits → `pre_existing`. Confirms the classifier is not simply echoing HEAD's result.

### Verdict & Gate
- [ ] Gate returns `escalate` on each of: non-empty `pre_existing`, non-empty `inconclusive`, `new_failures` ⊄ `newly_broken`, count over `NIGHTLY_FIX_MAX_FAILURES`, any node at `NIGHTLY_FIX_MAX_ATTEMPTS`.
- [ ] **The motivating case passes its own gate**: 11 all-`newly_broken` failures → `autonomous-fix`. #2399's shape must not be disqualified by the feature written for it.
- [ ] **Truncation is NOT a disqualifier**: a run whose `dispatch_nodes` exceeded `MAX_DISPATCH_NODES` still reaches `autonomous-fix` when its `new_failures` set is clean and within cap.
- [ ] The verdict log line is emitted on every non-`off` run with a `reason=` token naming the first failing condition; asserted on both an `autonomous-fix` and an `escalate` run.

### Run-Shape Disqualifiers
- [ ] **Seed / re-baseline run** (`is_seed_run` L1027) → `escalate`, and the existing seed path is unchanged.
- [ ] **Integrity-warned run** (non-empty `integrity_warnings` L1047) → `escalate`.
- [ ] **`--dry-run`** → classification is skipped entirely (no worktree, no pytest) and the run stays a pure preview.

### Mode Gating
- [ ] `NIGHTLY_FIX_MODE=off`: byte-identical to today. The up-front `send_telegram` on the `new_failures` branch **fires**; no classification, no worktree, no gate, no verdict log. Asserted via `send_telegram` call capture.
- [ ] `NIGHTLY_FIX_MODE=shadow` (the default): the classifier and gate run, the verdict is logged, **and the up-front page still fires exactly as today**. Asserted via `send_telegram` call capture.

### Attempt-Map Semantics
- [ ] A node newly entering `confirmed_failing` starts at 0; `classify_attempts[node]` increments once per attempt, before the baseline run.
- [ ] A node no longer in `confirmed_failing` is pruned from the map (same rule as `carry_dispatched_nodes`); a re-regressed node starts fresh.
- [ ] At `NIGHTLY_FIX_MAX_ATTEMPTS` the gate escalates rather than re-classifying.

## Test Impact

- [ ] `tests/unit/test_nightly_regression_tests.py` — UPDATE/ADD only. The up-front page fires in **both** modes this plan ships, so **every existing assertion expecting an immediate `send_telegram` on new failures stays valid unchanged.** Add the `send_telegram` returncode test and the `_spawn_pytest` `cwd` back-compat test. **Do not disturb** the per-node dedup tests (`compute_dispatch_set` / `carry_dispatched_nodes` / `prior_dispatched` / `seeded_nodes`), the seed/re-baseline tests, or the run-integrity tests.
- [ ] Tests asserting the dispatch prompt contains "Do NOT attempt an auto-hotfix" — **NOT affected.** `_build_triage_prompt` (L830-855) and `maybe_dispatch_triage_session` (L856) are untouched by this plan; the mandate rewrite moves to #3076.
- [ ] `tests/unit/` baseline-verifier tests — **NOT affected.** `.claude/agents/baseline-verifier.md` is unmodified; `/do-test` callers are untouched.
- [ ] `tests/unit/` `valor-telegram` CLI tests — **NOT affected.** No `--silent` flag ships in this plan (deferred to #3076 with its only consumer).
- [ ] `tests/unit/` relay tests — **NOT affected.** No `bridge/telegram_relay.py` change.
- [ ] `tests/unit/` AgentSession model/schema tests — **NOT affected.** No schema change, therefore no migration.
- [ ] New files: `tests/unit/test_nightly_classifier.py` (incl. the non-stubbed fixture-repo tests) and `tests/unit/test_nightly_decision_gate.py`.

Justification for anything not affected: the base detector mechanics (`run_tests`,
`reconfirm_serial`, run lock, TTFT gate, `load_env_or_die`, dispatch, alerting) are
untouched — this plan layers a classification + verdict-logging stage alongside the existing
`new_failures` branch without changing a single outbound message.

## Rabbit Holes

- **Building the autonomous fixer anyway.** Do not. Its two load-bearing seams do not exist (Scope Boundary); it is #3076, gated on them.
- **Adding `--silent` / the notify tier "while we're in there".** Do not. Without an `active` happy path it has no consumer, and it is a three-hop CLI surface change propagated to every machine for a flag nothing calls.
- **Touching `baseline-verifier`.** It has no Python invocation seam. Do not parameterize the subagent, do not add a `baseline_ref` Input field, and do not route classification through a spawned session (asynchronous — a two-night handshake).
- **A general-purpose "is this test stale?" classifier.** The mechanical precondition (passes at last-green, fails at HEAD) is all this plan claims. #2399 Group 2 proved a mechanical bucket is not a staleness proof; the plan says `newly_broken`, never `test-stale`.
- **Bypassing `pytest-clean.sh` in the baseline worktree** to dodge the `.venv` requirement. That is exactly #3033's false-green: imports resolve through the primary checkout's editable path entry and the baseline run silently exercises HEAD's source.
- **Falling back to `PROJECT_DIR` when worktree provisioning fails.** It would look like a working classifier while classifying everything `pre_existing`. Fail to `inconclusive`.
- **A new permission subsystem.** No session is dispatched here at all.
- **Tuning the guardrail numbers to perfection.** Ship provisional env-overridable constants; tune from real shadow data.

## Risks

### Risk 1: The classifier ships inert and nobody notices
**Impact:** The feature's entire deliverable — the shadow verdict — is a constant. Either every node buckets `pre_existing` (ran against HEAD's source) or every node buckets `inconclusive` (wrapper aborted). Both look safe, both page as today, and both would pass a fully-stubbed test suite. This is the round-6 blocker and the single largest risk in the remaining scope.
**Mitigation:** (a) The provisioning path is specified concretely against verified wrapper behavior (`pytest-clean.sh` L34-38 cwd-derived rootdir, L135-145 `.venv` requirement, L169-175 pin requirement) rather than left as "run it there"; (b) `_spawn_pytest` gains the `cwd` parameter it lacks, so the classifier cannot silently inherit `PROJECT_DIR`; (c) provisioning failure is `inconclusive`, never a `PROJECT_DIR` fallback; (d) **the non-stubbed two-commit fixture test** is the falsifier, and a companion case (`pre_existing` when the node fails at both commits) proves the classifier is not echoing HEAD; (e) shadow verdicts that are 100% one value across a month are themselves the operational tell, and the `reason=` token names which.

### Risk 2: The baseline is wrong or unavailable
**Impact:** Wrong classification → a misleading shadow verdict.
**Mitigation:** The last-green-SHA mapping (`prev["head_commit"]` L1092, never bare `main`) is explicit, and the SHA is interpolated as a literal so no shell default can resolve to `main`. Missing SHA → `escalate` without classifying. Every worktree/pytest failure mode lands in `inconclusive`, a hard escalate.

### Risk 2b: The classifier lengthens the nightly critical path
**Impact:** A slow or hanging baseline run delays the nightly job.
**Mitigation:** It runs only when `new_failures` is non-empty and only outside `off` mode, re-runs **only the failing node IDs** with `-n0`, and carries its own timeout (a timeout is `inconclusive`). The baseline worktree is **persistent**, so the `uv sync` cost is paid once and thereafter only when `uv.lock` moves. A detached checkout of an existing worktree is near-instant.

### Risk 3: The persistent baseline worktree accumulates or is garbage-collected
**Impact:** Either disk growth or a surprise re-provision cost every night.
**Mitigation:** One fixed path, never per-run. The build task explicitly reconciles it with `scripts/worktree-gc.sh` so it is neither swept nor duplicated. Its size is one checkout plus one venv.

### Risk 4: The shadow verdict is greppable but never actually read
**Impact:** #3076 stays permanently ungated on evidence nobody looks at.
**Mitigation:** The `reason=` token makes a single `grep` over a month of logs a complete answer, and #3076 names reading them as its entry condition. This is a process risk the plan can bound but not eliminate; it is accepted.

## Race Conditions

### Race 1: Two nightly invocations overlap
**Location:** `main()` entry.
**Mitigation:** Unchanged — the existing `_acquire_run_lock()` flock serializes nightly runs; the loser is a no-op. The baseline worktree therefore has exactly one writer.

### Race 2: A concurrent human `git worktree` operation touches the baseline worktree
**Location:** `.worktrees/nightly-baseline/`.
**Trigger:** A developer or another lane running `git worktree prune` or `scripts/worktree-gc.sh` mid-classification.
**Data prerequisite:** The worktree must exist and be on the baseline SHA for the duration of one classification.
**State prerequisite:** A pruned-mid-run worktree must not produce a *wrong* verdict.
**Mitigation:** Any git or pytest failure during classification → `inconclusive` → escalate. The failure mode is a false escalation (noisy, safe), never a false `autonomous-fix`. The build task reconciles the path with `worktree-gc.sh` so the common case does not arise.

*(The hand-back write/read race from earlier rounds is gone with the hand-back — it moves to #3076.)*

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #3076] The autonomous fixer dispatch, the `GH_TOKEN`/per-session env seam, the `AgentSession.nightly_fix_handback` field and its helper CLI, the escalation/fail-safe watchdog, the fail-closed diff-path and merge/draft-state guards, the three-leg `active`-mode preflight, the `fix_sessions` map, and the `valor-telegram send --silent` notify transport. **Blocked on two seams that do not exist** (Scope Boundary) and on shadow evidence this plan produces.
- [SEPARATE-SLUG #2405] Migrating the nightly dispatch to a Claude Cowork routine.
- [SEPARATE-SLUG #2405] A dedicated intra-day launchd watchdog.
- Any cross-run learning or auto-tuning of the guardrail constants — they ship provisional and env-overridable.

The invariants "never auto-merge to main" and "never edit source files to make a failing
test pass" remain permanent safety boundaries of the *eventual* feature. **This plan does not
need them, because it dispatches nothing and edits nothing.** That is the point of the cut:
rather than asserting a never-merge guarantee whose two legs are a 404 and a missing env
seam, the plan ships the half that needs no such guarantee, and #3076 carries the half that
does — gated in its own entry condition on both legs existing.

## Update System

- Three new env-overridable constants (`NIGHTLY_FIX_MODE`, `NIGHTLY_FIX_MAX_FAILURES`, `NIGHTLY_FIX_MAX_ATTEMPTS`) have safe in-code defaults (`NIGHTLY_FIX_MODE` defaults to `shadow`), so no `.env` propagation is required. Document them in `.env.example` for discoverability. **Env-completeness note:** the check requires a comment line immediately above each `KEY=` line, so each ships as a two-line block — a `#` comment describing the knob and its default, then the commented `# NIGHTLY_FIX_...=` placeholder. A bare `KEY=` with no preceding comment fails the check.
- **No Popoto schema change**, therefore **no `scripts/update/migrations.py` entry.** The `AgentSession` field moved to #3076 with the hand-back.
- **No CLI surface change.** `valor-telegram` is untouched, so nothing must land in lockstep across machines and no bridge restart is required by this plan.
- No new launchd job: classification runs inside the existing nightly invocation, so `scripts/install_nightly_tests.sh` needs no change.
- No new config files, no permission subsystem, no new secrets.
- **One filesystem addition:** the persistent baseline worktree at `.worktrees/nightly-baseline/`. `.worktrees/` is already gitignored; the build task confirms `scripts/worktree-gc.sh` leaves this fixed path alone rather than sweeping it as a stale lane worktree.

## Agent Integration

**No agent integration is required — this plan dispatches no session and adds no
agent-reachable surface.** The nightly runner is a launchd script, not a bridge tool; it
gains an internal function and a log line. There is no new CLI entry point in
`pyproject.toml [project.scripts]`, no bridge import, and no new `AgentSession` behavior.

The existing `maybe_dispatch_triage_session` (L856) investigate-only dispatch continues
unchanged. Rewriting that mandate into a bounded-fix mandate is #3076's work, and it is
explicitly not started here — because without the per-session env seam the dispatched
session's `gh` would carry the operator's admin credentials, which is the condition that
made the never-merge guarantee vacuous in round 6.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/nightly-regression-tests.md` — document the new classification stage: the in-process `classify_against_baseline` function, the last-green baseline ref (`prev["head_commit"]`, never bare `main`), the **provisioned persistent baseline worktree and why it must have its own `.venv`** (#3033), the three buckets and the fail-toward-escalate rule, the pure decision gate, the `off`/`shadow` modes, the `nightly-fix shadow-verdict:` log contract, and the three guardrail constants.
- [ ] Update `docs/features/nightly-alert-triage.md` — state plainly that alerting behavior is **unchanged** by this plan (the up-front page fires in both shipped modes), and that the autonomous-fix tier is deferred to #3076 with its two blocking seams named. Do not describe a fixer that does not exist.
- [ ] Add/confirm entries in `docs/features/README.md` index.

### Inline Documentation
- [ ] Each guardrail constant carries a grain-of-salt/provisional comment and its env-override name.
- [ ] Docstring `decide_fix_or_escalate` (the gate's exact conditions and their order, since `reason=` reports the first failure) and `classify_against_baseline`'s baseline-ref contract (`prev["head_commit"]`, never bare `main`) plus its provisioning preconditions.
- [ ] Docstring the `cwd` parameter added to `_spawn_pytest`, noting that the default preserves both existing callers byte-identically.

## Success Criteria

- [ ] `send_telegram` logs a WARNING naming the return code (and does **not** log `"Telegram sent"`) when the subprocess exits non-zero — asserted by test. This lands first and is independently valuable.
- [ ] `_spawn_pytest` accepts a `cwd` parameter defaulting to `PROJECT_DIR`, and `run_tests` / `reconfirm_serial` still spawn with `cwd=PROJECT_DIR` (asserted via `Popen` call capture).
- [ ] **The classifier is demonstrably not inert**: a non-stubbed test over a two-commit fixture repo classifies a node that passes at the baseline SHA and fails at HEAD as `newly_broken`, and a node failing at both as `pre_existing`. This criterion cannot be satisfied by a stub.
- [ ] Classification is **synchronous and in-process** — no subagent, no spawned session, no Task tool — and `.claude/agents/baseline-verifier.md` is unmodified (`git diff --name-only origin/main -- .claude/agents/` is empty).
- [ ] The baseline worktree is provisioned with its own pin-matching `.venv`, and **every** provisioning failure (worktree add, detached checkout, `uv sync`) yields `inconclusive` → `escalate` with **no fallback to `PROJECT_DIR`** (asserted for each failure).
- [ ] `NIGHTLY_FIX_MODE=off` reproduces current behavior exactly: the up-front page fires, and no classification, worktree, gate, or verdict log occurs. `NIGHTLY_FIX_MODE=shadow` (the default) classifies, gates, logs the verdict, **and still pages up front**. Both asserted via `send_telegram` call capture.
- [ ] Every non-`off` run with a non-empty `new_failures` logs `nightly-fix shadow-verdict: {autonomous-fix|escalate} reason=... nodes=...`, with `reason` naming the first failing gate condition.
- [ ] The motivating case passes its own gate: 11 all-`newly_broken` failures → `autonomous-fix` (asserted). `MAX_DISPATCH_NODES` truncation is not a disqualifier and `NIGHTLY_FIX_MAX_FAILURES` is not dead config.
- [ ] On any `pre_existing` / `inconclusive` bucket, on cap-exceeded, on attempts-exhausted, or on a missing last-green baseline, the gate returns `escalate`.
- [ ] A seed/re-baseline run, an integrity-warned run, or a `--dry-run` never classifies and never returns `autonomous-fix` (asserted for each of the three).
- [ ] `classify_attempts` increments before each attempt, prunes any node no longer in `confirmed_failing` (same rule as `carry_dispatched_nodes`), and escalates at `NIGHTLY_FIX_MAX_ATTEMPTS` (asserted).
- [ ] Existing detector behavior is unchanged: `tests/unit/test_nightly_regression_tests.py` passes with no modification to any `compute_dispatch_set` / `carry_dispatched_nodes` / `seeded_nodes` / `validate_run_integrity` / dispatch-prompt assertion.
- [ ] Every guardrail number is a named env-overridable constant read via `os.environ.get` with a provisional comment; **no constant is declared without a reader** (the two whose only consumers were the deferred guards are not declared).
- [ ] **Nothing from the deferred #3076 stack ships**: no session dispatch change, no `nightly_fix_handback` field or migration, no watchdog, no PR guards, no preflight, no `--silent` flag (grep-verifiable — see Verification).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

The lead orchestrates; it never builds directly.

### Team Members

- **Builder (transport + spawn seam)**
  - Name: `seam-builder`
  - Role: the `send_telegram` `.returncode` check; the `_spawn_pytest` `cwd` parameter with byte-identical existing callers.
  - Agent Type: builder
  - Domain: subprocess handling
  - Resume: true

- **Builder (classifier + gate)**
  - Name: `gate-builder`
  - Role: baseline-worktree provisioning (`git worktree add --detach` + conditional `uv sync` + `worktree-gc.sh` reconciliation); `classify_against_baseline`; the pure `decide_fix_or_escalate`; the `classify_attempts` map; the three guardrail constants; the mode-scoped wiring and verdict log in `main()`.
  - Agent Type: builder
  - Domain: git/subprocess + pytest-wrapper semantics
  - Resume: true

- **Test engineer**
  - Name: `classifier-tester`
  - Role: the **non-stubbed two-commit fixture-repo tests** (the falsifiability suite), the stubbed gate/mode/attempt-map units, and the failure-path coverage.
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `scope-validator`
  - Role: verify the classifier is not inert (the fixture tests genuinely exercise the real function, not a stub); verify provisioning never falls back to `PROJECT_DIR`; verify `off`/`shadow` both still page; verify **nothing from #3076 leaked in** (no dispatch-mandate change, no `nightly_fix_handback`, no watchdog, no `--silent`, no preflight); run every Verification command.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `nightly-doc`
  - Role: update the two feature docs and the index; ensure neither describes a fixer that does not exist.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Alert-transport returncode check (independently shippable)
- **Task ID**: build-returncode
- **Depends On**: none
- **Validates**: `tests/unit/test_nightly_regression_tests.py` returncode test
- **Assigned To**: seam-builder
- **Agent Type**: builder
- **Parallel**: true
- In `send_telegram` (`scripts/nightly_regression_tests.py` L609-635), capture the `CompletedProcess` from the `subprocess.run` at L626, log `WARNING: telegram send failed rc=... stderr=...` on non-zero, and emit the `"Telegram sent: {msg}"` line (L633) **only** on zero.
- No signature change, no new parameter. `--silent` is **not** added — it has no consumer in this plan (deferred to #3076).
- This is a strict improvement to the existing detector and lands first: without it, any future transport failure is invisible.

### 2. `_spawn_pytest` cwd seam
- **Task ID**: build-cwd-seam
- **Depends On**: none
- **Validates**: `tests/unit/test_nightly_regression_tests.py` cwd back-compat test
- **Assigned To**: seam-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `cwd: Path | str = PROJECT_DIR` to `_spawn_pytest` (L283) and forward it to the `Popen` call (currently hardcoded `cwd=PROJECT_DIR` at L302).
- `run_tests` (L379) and `reconfirm_serial` (L587) pass nothing — assert both remain byte-identical in behavior via `Popen` call capture.
- Docstring the parameter, noting the default preserves existing callers.

### 3. Baseline worktree provisioning + in-process classifier
- **Task ID**: build-classifier
- **Depends On**: build-cwd-seam
- **Validates**: `tests/unit/test_nightly_classifier.py` (create)
- **Assigned To**: gate-builder
- **Agent Type**: builder
- **Parallel**: false
- **`head_commit` is already persisted** (`current["head_commit"] = _get_head_commit()`, L1092; helper L1344). Do NOT re-add it. Consume `prev["head_commit"]` as the last-green baseline ref.
- Provision a **persistent** baseline worktree at a fixed path (`.worktrees/nightly-baseline/`): create with `git worktree add --detach <path> <baseline_sha>` + `uv sync` inside it if absent; otherwise `git -C <path> checkout --detach <baseline_sha>` and re-run `uv sync` **only when `uv.lock` changed** since the last provision (marker file inside the worktree). The `.venv` is mandatory — `scripts/pytest-clean.sh` L135-145 refuses a linked worktree without one (#3033) and L169-175 refuses an off-pin interpreter (#2617).
- Reconcile the fixed path with `scripts/worktree-gc.sh` so it is not swept as a stale lane worktree.
- Write `classify_against_baseline(node_ids, baseline_sha) -> {newly_broken, pre_existing, inconclusive}`: provision, then spawn the serial pytest form used by `reconfirm_serial` (L575-584 — `[str(PYTEST_CLEAN_SH), *node_ids, "-n0", "--tb=no", "-q", "--json-report", "--json-report-file=..."]`) via `_spawn_pytest(..., cwd=<worktree>)`, parse the JSON report, and bucket each node. Interpolate the SHA as a literal — no shell parameter expansion. Carry a timeout.
- **Every** failure path (worktree add, checkout, `uv sync`, collection error, timeout, unparseable report, node absent from the report, any exception) → `inconclusive`. **Never fall back to running at `PROJECT_DIR`** — that fallback classifies everything `pre_existing` and looks like a working classifier.
- **Do NOT touch `.claude/agents/baseline-verifier.md`** and do NOT route classification through a spawned session (no Python invocation seam; a session would make classification asynchronous — a two-night handshake).
- Handle absent `prev["head_commit"]` → no classification, gate escalates with that reason.

### 4. Decision gate, mode wiring, verdict log, attempt map
- **Task ID**: build-gate
- **Depends On**: build-classifier
- **Validates**: `tests/unit/test_nightly_decision_gate.py` (create)
- **Assigned To**: gate-builder
- **Agent Type**: builder
- **Parallel**: false
- Pure `decide_fix_or_escalate(classification, new_failures, caps, run_flags) -> "autonomous-fix" | "escalate"` with exactly the short-circuit in Technical Approach. Three run-shape disqualifiers only (`is_seed_run` L1027, non-empty `integrity_warnings` L1047, `args.dry_run`) — **do NOT include a `MAX_DISPATCH_NODES` truncation clause**: it truncates the triage-filing set (L1160-1166), not `new_failures` (L654), and would disqualify the plan's own 11-failure motivating case while making `NIGHTLY_FIX_MAX_FAILURES` dead config.
- Three module-scope guardrail constants via `os.environ.get` with provisional comments: `NIGHTLY_FIX_MODE` (`off`|`shadow`, default `shadow`; unrecognized → treated as `off`), `NIGHTLY_FIX_MAX_FAILURES` (default 15), `NIGHTLY_FIX_MAX_ATTEMPTS` (default 1). **Declare no constant without a reader** — `NIGHTLY_FIX_MAX_CHANGED_FILES` and `NIGHTLY_FIX_HANDBACK_TIMEOUT_HOURS` belong to #3076's guards and are not declared here.
- Wire classification + gate into `main()` alongside the `elif new_failures:` arm (L1207), **without changing the alert**: `send_telegram(msg, dry_run=args.dry_run)` (L1220) fires in both `off` and `shadow` exactly as today. `off` additionally skips classification and the gate entirely.
- Log the verdict on every non-`off` run: `nightly-fix shadow-verdict: {autonomous-fix|escalate} reason={first failing condition} nodes={n}`.
- Add the per-node `classify_attempts: {node_id: count}` map to `data/nightly_tests_last_run.json`: increment before each attempt; prune any node no longer in `confirmed_failing` using the same keep-while-still-failing rule as `carry_dispatched_nodes` (L754) so it prunes in lockstep with `dispatched_nodes` and cannot grow unbounded. Do **not** add a second map — `fix_sessions` belongs to #3076's watchdog. Leave the scalar `dispatched_session_id` (L1090) untouched.

### 5. Tests
- **Task ID**: build-tests
- **Depends On**: build-returncode, build-gate
- **Assigned To**: classifier-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- **Non-stubbed** (`tests/unit/test_nightly_classifier.py`): a two-commit fixture git repo where a trivial test passes at commit A and fails at commit B — provisioned the way the real classifier provisions its worktree — asserts `newly_broken` when classified at baseline A, and `pre_existing` for a node failing at both. **These two tests are the feature's falsifiability guarantee**; a fully-stubbed suite cannot distinguish a working classifier from an inert one.
- Stubbed classifier failure paths: worktree-add non-zero, checkout non-zero (unknown SHA), `uv sync` non-zero, collection error, timeout, unparseable/missing report, node absent from the report → `inconclusive` → `escalate`; plus an explicit assertion that no spawn ever used `cwd=PROJECT_DIR` on the classifier path.
- Gate units (`tests/unit/test_nightly_decision_gate.py`): every escalate branch; the three run-shape disqualifiers; the **positive motivating case** (11 all-`newly_broken` → `autonomous-fix`); the **negative** that a `MAX_DISPATCH_NODES`-truncated run is not disqualified; attempt-map increment/prune/cap semantics; missing-`head_commit`; empty `new_failures`; unrecognized `NIGHTLY_FIX_MODE` → treated as `off`.
- Mode gating: `send_telegram` **is** called on the `new_failures` branch under **both** `off` and `shadow`; classification and the verdict log occur under `shadow` only.
- Transport + seam: `send_telegram` non-zero returncode → WARNING, no success line; `run_tests` / `reconfirm_serial` still spawn with `cwd=PROJECT_DIR`.
- Verdict-log format asserted on both an `autonomous-fix` and an `escalate` run, including the `reason=` token.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: nightly-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/nightly-regression-tests.md` and `docs/features/nightly-alert-triage.md` + the index, per the Documentation section. Neither doc may describe a fixer, a watchdog, a hand-back, or a notify tier — those do not exist; point to #3076.

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: build-tests, document-feature
- **Assigned To**: scope-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification command; confirm all Success Criteria; confirm no #3076 surface leaked into the diff.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/ -x -q -k nightly` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| `send_telegram` no longer claims success on a failed send | `pytest tests/unit/test_nightly_regression_tests.py -q -k "telegram_returncode"` | exit code 0 |
| `_spawn_pytest` gained a `cwd` seam; existing callers unchanged | `pytest tests/unit/test_nightly_regression_tests.py -q -k "spawn_pytest_cwd"` | exit code 0 |
| **The classifier is not inert** (non-stubbed fixture repo) | `pytest tests/unit/test_nightly_classifier.py -q -k "fixture_repo"` — a node passing at baseline and failing at HEAD classifies `newly_broken`; failing at both classifies `pre_existing` | exit code 0 |
| Classifier never falls back to the main checkout | `pytest tests/unit/test_nightly_classifier.py -q -k "no_project_dir_fallback"` | exit code 0 |
| In-process classifier present and synchronous | `grep -c "classify_against_baseline" scripts/nightly_regression_tests.py` | output ≥ 2 (definition + call) |
| `baseline-verifier` untouched | `git diff --name-only origin/main -- .claude/agents/` | empty output |
| Baseline worktree is provisioned with its own venv | `grep -c "uv" scripts/nightly_regression_tests.py` and `pytest tests/unit/test_nightly_classifier.py -q -k "provision"` | output > 0; exit code 0 |
| Up-front page fires in BOTH shipped modes (behavioral) | `pytest tests/unit/test_nightly_decision_gate.py -q -k "mode_gating"` — asserts `send_telegram` **is** called on the `new_failures` branch under `off` **and** under `shadow` | exit code 0 |
| Shadow verdict is greppable in logs | `grep -c "shadow-verdict" scripts/nightly_regression_tests.py` | output > 0 |
| The motivating case passes its own gate | `pytest tests/unit/test_nightly_decision_gate.py -q -k "eleven or motivating"` — 11 all-`newly_broken` → `autonomous-fix` | exit code 0 |
| Truncation is not a disqualifier | `grep -c "dispatch_truncated" scripts/nightly_regression_tests.py` | match count == 0 |
| Run-shape disqualifiers gate the verdict (three, not four) | `pytest tests/unit/test_nightly_decision_gate.py -q -k "seed or integrity or dry_run"` | exit code 0 |
| Guardrail constants are env-overridable (module-scope) | `grep -c 'os.environ.get("NIGHTLY_FIX' scripts/nightly_regression_tests.py` | output == 3 |
| No constant declared without a reader (the two deferred knobs are absent) | `grep -Ec "NIGHTLY_FIX_MAX_CHANGED_FILES\|NIGHTLY_FIX_HANDBACK_TIMEOUT_HOURS" scripts/nightly_regression_tests.py` | match count == 0 |
| Attempt map prunes in lockstep with the live dedup model | `pytest tests/unit/test_nightly_decision_gate.py -q -k "attempts"` | exit code 0 |
| Retired `dispatched_hash` not resurrected | `grep -Ec "dispatched_hash\|failing_set_hash" scripts/nightly_regression_tests.py` | match count == 0 |
| Pre-existing detector behavior untouched (dedup, seed umbrella, run integrity, dispatch prompt) | `pytest tests/unit/test_nightly_regression_tests.py -q` | exit code 0 |
| **#3076 surface did not leak in — no fixer dispatch** | `grep -Ec "nightly_fix_handback\|maybe_dispatch_fix_session\|pr diff --name-only\|branches/main/protection\|SDLC_AGENT_GH_TOKEN" scripts/nightly_regression_tests.py` | match count == 0 |
| **#3076 surface did not leak in — no notify transport** | `grep -c '"--silent"' tools/valor_telegram.py` | match count == 0 |
| **#3076 surface did not leak in — no schema change** | `git diff --name-only origin/main -- models/ scripts/update/migrations.py` | empty output |
| Investigate-only triage mandate is unchanged by this plan | `grep -c "auto-hotfix" scripts/nightly_regression_tests.py` | output > 0 (L846 intact) |

**Note on `grep` alternation (round-7 correction).** `grep -E` treats `\|` as a **literal
pipe**, not alternation — verified on a file containing both `gh pr ready` and `gh pr merge`:
the `-E`-with-backslash form returns `0`, the unescaped `-E` form returns `2`. Two rows in
earlier rounds used `grep -Ec "a\|b"` and therefore passed vacuously regardless of file
contents, including a never-raw-Redis safety guard. Every `-E` row above uses **unescaped**
alternation; every backslash-escaped alternation row uses plain `grep` (BRE). This distinction
is load-bearing for any row whose expectation is `== 0`.

**Note on unsatisfiable anti-criteria (round-7 correction).** Earlier rounds asserted
`grep -Ec "gh pr ready\|gh pr merge" == 0` while simultaneously requiring the dispatch mandate
to *explicitly forbid* those verbs — a prompt string in the same file. A correct implementation
would have turned the safety anti-criterion red, pressuring a builder to weaken the mandate or
obfuscate the strings. Both rows are **removed** along with the mandate rewrite they guarded
(#3076). When #3076 reinstates them, the criterion must be behavioral ("the runner never
*executes* those verbs", asserted over the runner's `subprocess` argv literals plus a positive
assertion that the mandate string contains the prohibition), never a whole-file string absence.

## Critique Results

<!-- Rounds 1-8 findings and their dispositions. Round 8 (2026-09-02, FULL depth, baseline
     b2e15a9a0) returned NEEDS REVISION — 3 blockers, 2 concerns, 2 nits; rows at the bottom of
     the table. Round 6 (baseline 33fe1d2c7) also returned NEEDS REVISION and was fully resolved
     in the round-7 revision at baseline 2d60de31d, two of its blockers by cutting scope rather
     than by asserting a mechanism. Round-6 and round-8 note: the Agent/Task tool was unavailable
     in both sessions, so the three FULL lenses were applied by the driving agent directly;
     the standing deviation note is .critique-runs/2334-1788331879504381000/ROUND8-NOTE.md
     (the round-6 run dir was garbage-collected on completion). -->

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness + History & Consistency + Scope & Value (round 1, all 3 agreed) | The "structural never-merge" resolution has no implementation seam. `tools.valor_session create` exposes no permission flag; `AgentSession` has no permission field; every `claude-cli` spawn is hardcoded `--permission-mode bypassPermissions` (`agent/session_runner/harness/claude.py` L154-168); `PermissionRequest` hooks do not fire under `claude -p`. | **RESOLVED (round 2), and the underlying tier is now DEFERRED (round 7)** | The permission profile was deleted in round 2. Round 7 established that the *entire* enforcement stack it was replaced by is also unbuildable here (no per-session env seam, branch protection 404), so the fixer tier moved to **#3076** rather than shipping behind an unreachable flag. |
| CONCERN | Risk & Robustness (round 2) | The `gh pr diff --name-only` guard's failure path was unspecified and could vacuously satisfy "every path under `tests/`". | **RESOLVED (round 2); DEFERRED (round 7)** | Guard was made fail-closed in round 2; the guard itself moves to #3076 with the watchdog. The fail-closed design is recorded there. |
| CONCERN | Scope & Value (round 2) | The notify-vs-page tiering was verified only by a prose grep, but `send_telegram` has no urgency parameter. | **RESOLVED (round 2), re-resolved (round 5), DEFERRED (round 7)** | Rounds 2-5 progressively established the tier needed a real three-hop transport. Round 7 defers the whole notify tier to #3076 — without an `active` happy path it has no consumer, and a `--silent` flag nothing calls is a dead CLI surface on every machine. |
| CONCERN | History & Consistency (round 2) | The plan was internally inconsistent about how settled the permission mechanism was. | **RESOLVED (round 2)** | Resolved Decision #2 rewritten to a single honest enforcement decision; the "build-time detail" hedge removed. |
| BLOCKER | Risk & Robustness (round 3) | Round 2's "draft-PR structural no-auto-merge" guarantee is only prompt-strength — under `bypassPermissions` the session's bash can run `gh pr ready` then `gh pr merge`. | **RESOLVED (round 3); DEFERRED (round 7)** | The honest limitation was stated in round 3 and the real guarantee moved server-side. Round 7 verified that guarantee's legs do not exist here and cut the tier that needs them. |
| CONCERN | Risk & Robustness (round 3) | `fix_attempt_count` dedup reset semantics unspecified; unbounded growth. | **RESOLVED (round 3-4); CARRIED (round 7)** | Keyed map with increment-per-attempt and prune-when-green survives as `classify_attempts`, pruned by `carry_dispatched_nodes`'s existing rule. |
| CONCERN | Scope & Value (round 3) | The bespoke AST verify script was redundant with behavioral mode-gating tests. | **RESOLVED (round 3)** | Script and its Verification row removed; the anti-criterion is behavioral. |
| CONCERN | History & Consistency (round 3) | The shadow→active flip prerequisite was prose-only and unenforceable. | **RESOLVED (round 3); SUPERSEDED (round 7)** | The runtime preflight moved to #3076 with `active` mode. The flip is now gated by #3076's entry condition (both seams existing) plus this plan's shadow-log evidence. |
| CONCERN | Risk & Robustness (round 3) | Ambiguity about which PR the watchdog inspects. | **RESOLVED (round 3); DEFERRED (round 7)** | The hand-back-`pr_url`-only rule is recorded in #3076. |
| NIT | History & Consistency (round 3) | `.env.example` requires a comment line above each `KEY=`. | **RESOLVED (round 3); CARRIED (round 7)** | Update System specifies two-line blocks for the three surviving constants. |
| BLOCKER | Risk & Robustness (round 4) | The branch-protection preflight ignored `enforce_admins`, so an admin-scoped token could merge a zero-approval PR. | **RESOLVED (round 4), REVERSED (round 5), DEFERRED (round 7)** | Round 5 replaced the repo-wide `enforce_admins` requirement with a non-admin fixer identity. Round 7 found that identity has no injection seam, so the whole enforcement design moves to #3076 where the seam is a prerequisite. |
| CONCERN | Risk & Robustness (round 4) | A scalar `dispatched_session_id` would be orphaned by a second dispatch. | **RESOLVED (round 4); DEFERRED (round 7)** | The keyed `fix_sessions` map existed to serve the watchdog; both move to #3076. This plan adds only `classify_attempts` and leaves the live scalar (L1090) untouched. |
| CONCERN | Scope & Value (round 4) | `NIGHTLY_FIX_MAX_CHANGED_FILES` was declared but never wired (dead constant). | **RESOLVED (round 4); RE-RESOLVED (round 7)** | Round 4 wired it into the diff-path guard. Round 7 defers that guard, so the constant is **not declared here** — the lesson generalized into a Success Criterion and a Verification row asserting no constant ships without a reader. |
| CONCERN | History & Consistency (round 4) | `fix_attempt_count` vs `fix_attempts` naming/semantics contradiction. | **RESOLVED (round 4); CARRIED (round 7)** | One name, one semantics — now `classify_attempts`, the only attempt map in the plan. |
| BLOCKER | Risk & Robustness (round 5) | `valor-telegram send --silent` does not exist; `disable_notification` has zero hits; and `send_telegram` never checks `.returncode`, so a dropped notify would log success. | **RESOLVED (round 5); SPLIT (round 7)** | The `.returncode` half is a genuine standalone defect and **ships here as Step 1**. The `--silent` transport half moves to #3076 with its only consumer. |
| BLOCKER | Risk & Robustness (round 5) | `baseline-verifier` is Task-tool-only with zero `.py` callers; the classification stage had no invocation seam. | **RESOLVED (round 5); CARRIED and COMPLETED (round 7)** | Classification is in-process. Round 7 completed the resolution by supplying the *execution* seam round 5 left open — see the next row. |
| BLOCKER | History & Consistency (round 5) | `NIGHTLY_FIX_MODE=off` carried two incompatible semantics (pages as today vs. no longer pages). | **RESOLVED (round 5); SIMPLIFIED (round 7)** | With `active` cut, both shipped modes page exactly as today, so the contradiction cannot recur. Every existing `send_telegram`-on-new-failures test stays valid unmodified. |
| BLOCKER | History & Consistency (round 5) | The `enforce_admins` prerequisite is repo-wide and breaks direct-to-`main` `docs/plans/` commits and `/do-merge`. | **RESOLVED (round 5); DEFERRED (round 7)** | The two-leg design is recorded in #3076. Nothing in this plan requires any branch protection. |
| CONCERN | Risk & Robustness (round 5) | The watchdog never handled an absent `AgentSession` record (`query.filter` returns `[]`, not `None`). | **RESOLVED (round 5); DEFERRED (round 7)** | The absent-record-first ordering and the empty-list return shape are recorded in #3076 with the watchdog. |
| CONCERN | Scope & Value (round 5) | The plan's own motivating case (#2399, 11 failures) could not pass its own gate under the `MAX_DISPATCH_NODES` disqualifier. | **RESOLVED (round 5); CARRIED (round 7)** | The truncation clause stays removed; the positive 11-failure test and the `dispatch_truncated == 0` row survive into this plan's gate. |
| NIT | Scope & Value (round 5) | The shadow verdict was not discoverable. | **RESOLVED (round 5); PROMOTED (round 7)** | The greppable verdict line is now the plan's **primary deliverable**, not a nicety — it is #3076's entry condition. |
| NIT | History & Consistency (round 5) | Freshness Check baseline stale. | **RESOLVED (round 5), recurred, RESOLVED again (round 7)** | See the round-6 nit below. |
| BLOCKER | Risk & Robustness (round 6) | Leg B of the only structural never-merge guarantee has no invocation seam: `maybe_dispatch_triage_session` spawns `tools.valor_session create` with no `env=` (L913-931), that CLI has no env/token flag, and it only *enqueues* — the worker, a separate launchd process, later spawns `claude -p`. The fixer would use the operator's ambient admin credentials, making both legs vacuous. Third recurrence of the same failure class. | **RESOLVED (round 7) — by CUTTING, not by asserting** | Every hop was opened and read at `2d60de31d`; the finding is confirmed exactly, and the chain is now documented as a table in **Scope Boundary** with file:line for each hop, including the two the finding did not name: `role_driver.py::subscription_auth_env` L76-100 (called L204) is a *process-global* overlay, and `models/agent_session.py` has **no** env/token/permission field, so there is nowhere to persist a per-session override. `TurnRequest.env` (`harness/base.py` L52) is the plumbing that could carry it, but nothing session-specific reaches it. Per the supervisor directive to prefer cutting over asserting: the entire fixer/enforcement tier is **removed from this plan** and filed as **#3076**, whose first blocking item is building that seam with its own tests. **No Success Criterion in this plan asserts any env export.** |
| BLOCKER | Risk & Robustness (round 6) | The in-process classifier has no working-tree or venv story and can ship completely inert while passing every one of its own criteria: `_spawn_pytest` hardcodes `cwd=PROJECT_DIR` (L297-306) and routes through `pytest-clean.sh`, which aborts on an off-pin venv — and a bare `git worktree add` has no `.venv`. Every resolution either classifies against HEAD's source (all `pre_existing`) or aborts (all `inconclusive`), and no listed test detects it because every classifier test stubs the classifier. | **RESOLVED (round 7)** | Confirmed at `2d60de31d`: `_spawn_pytest` is `(argv, timeout, env=None)` with `cwd=PROJECT_DIR` at L302. Also read the wrapper, which the finding's remedy depends on: `pytest-clean.sh` resolves `REPO_ROOT` from the **caller's cwd** (L34-38), so a `cwd=<worktree>` spawn genuinely targets the worktree — and it refuses a linked worktree with no `.venv` (L135-145, #3033) and an off-pin interpreter (L169-175, #2617). Three concrete changes now specified: (1) `_spawn_pytest` gains `cwd: Path \| str = PROJECT_DIR` (Step 2, with a back-compat test on both existing callers); (2) a **persistent provisioned** baseline worktree at a fixed path with its own pin-matching `.venv` via `uv sync`, re-synced only when `uv.lock` moves, reconciled with `worktree-gc.sh` (Step 3, and Risk 2b re-costed accordingly); (3) the classifier goes **through** the wrapper, never around it, and **never falls back to `PROJECT_DIR`** — provisioning failure is `inconclusive`. The finding's own remedy is adopted verbatim as a required test: a **non-stubbed two-commit fixture repo** where a node passing at baseline and failing at HEAD must classify `newly_broken`, plus a companion `pre_existing` case, promoted to a Success Criterion and a Verification row. Risk 1 is rewritten around this exact failure mode. |
| BLOCKER | History & Consistency (round 6) | Two Verification anti-criteria are self-defeating. (a) `grep -Ec "gh pr ready\|gh pr merge"` and the raw-Redis guard use BRE alternation under `-E`, where `\|` is a literal pipe — both return 0 unconditionally. (b) Even corrected, `== 0` is unsatisfiable: Step 3 requires the mandate to explicitly forbid those verbs, and the mandate is a prompt string in that same file. | **RESOLVED (round 7)** | The regex claim was reproduced before acting: on a file containing both strings, `grep -Ec "gh pr ready\|gh pr merge"` returns `0` and the unescaped `-E` form returns `2`. Every `-E` row in the new Verification table uses **unescaped** alternation, and a standing note records the rule so it cannot silently recur. For (b): both offending rows are **removed** along with the dispatch-mandate rewrite they guarded (deferred to #3076), and the note prescribes the correct shape for when #3076 reinstates them — a behavioral assertion over the runner's `subprocess` argv literals plus a *positive* assertion that the mandate contains the prohibition, never a whole-file string absence. The raw-Redis row is dropped outright: this plan adds no Redis or ORM code (no schema change, no hand-back), so the guard has nothing to guard. |
| CONCERN | Risk & Robustness (round 6) | Step 4 consumes Step 0's `send_telegram(..., silent=True)` transport but no dependency edge orders them; Step 0 declares `Depends On: none, Parallel: true`, so Step 4 could land first and raise `TypeError` on a path no default-mode test exercises. | **RESOLVED (round 7) — dissolved with the coupling** | `send_telegram`'s signature (L609) is unchanged by this plan: no `silent` parameter is added, no caller passes one, so the `TypeError` is unreachable. The returncode fix (Step 1) is genuinely independent. The remaining ordering constraint is real and **declared**: Step 3 (`build-classifier`) depends on Step 2 (`build-cwd-seam`), because `classify_against_baseline` cannot target the worktree until `_spawn_pytest` accepts `cwd`; Step 4 depends on Step 3; Step 5 depends on both Step 1 and Step 4. |
| CONCERN | Scope & Value (round 6) | Day-one value is thin and unvalidated: leg A 404s, leg B is opt-in, so `active` is unreachable; what ships is a returncode fix, a flag nothing uses, and a log line, while the fixer/watchdog/preflight stack is unreachable code. No criterion checks the shadow verdict is *correct*, only greppable. | **RESOLVED (round 7)** | The finding's second option is taken: **split the lane.** Everything unreachable is removed from this plan and filed as #3076 — no dead flag, no unreachable code, no safety claim without a mechanism. What remains is three things that are all real and all reachable on the default path: the `send_telegram` returncode fix (a live defect: L633 logs `"Telegram sent"` unconditionally), the classifier, and the shadow gate. On correctness: the finding's suggested end-to-end check against #2399's real commits was considered and **not** adopted as a suite test — it needs a historical SHA, a live test DB, and a long run, which is a flaky nightly-suite dependency. The **two-commit fixture-repo test** is adopted instead: it exercises the same claim (`passes at baseline, fails at HEAD → newly_broken`) deterministically and in-suite, and it is precisely the test that would have caught the inert-classifier blocker. The unit-level 11-failure motivating case is retained for gate *arithmetic*, now clearly labeled as such. |
| NIT | History & Consistency (round 6) | Freshness Check named two different stale baselines (`0b6688433` L63, `3b6eb651b` L78); `origin/main` was `33fe1d2c7`. | **RESOLVED (round 7)** | One baseline SHA stated once — `2d60de31d` (`git rev-parse origin/main` at revision time) — and the second sentence deleted rather than a third added. Every file:line reference in the plan was re-read against that SHA during this pass, not carried forward. |
| NIT | History & Consistency (round 6) | Inline Documentation still tasked documenting "the baseline-ref parameterization of `baseline-verifier`", which round 5 deleted. | **RESOLVED (round 7)** | Replaced with `classify_against_baseline`'s baseline-ref contract (`prev["head_commit"]`, never bare `main`) and its provisioning preconditions, plus a new bullet for `_spawn_pytest`'s `cwd` parameter. |
| BLOCKER | Risk & Robustness (round 8) | The classifier's pytest invocation is specified by quoting `reconfirm_serial`'s argv verbatim with the report path elided (`--json-report-file=...`, Technical Approach and Step 3). `reconfirm_serial` writes `PYTEST_SERIAL_JSON_TMP` (L582; constant L109), and `main()` re-reads that exact file **after** the classifier would run, at L1209, to build `summarize_failures(new_failures, serial_report)` (L1213). A builder copying the quoted invocation overwrites the serial report with the baseline commit's results — where the newly-broken nodes passed — so the human's alert is summarized from a report containing no failures for those nodes. This breaks the plan's headline guarantee that it "changes no outbound message", and no listed test asserts on the alert's content. | pending | Add `PYTEST_BASELINE_JSON_TMP = "/tmp/nightly_pytest_baseline_report.json"` beside L108-109; `Path(PYTEST_BASELINE_JSON_TMP).unlink(missing_ok=True)` before the spawn; pass `f"--json-report-file={PYTEST_BASELINE_JSON_TMP}"`. Add a test asserting `PYTEST_SERIAL_JSON_TMP` is byte-identical before and after `classify_against_baseline(...)`. |
| BLOCKER | Risk & Robustness (round 8) | The non-stubbed two-commit fixture test — the plan's declared falsifiability guarantee — cannot exercise the real path as specified. (a) The mandated signature `classify_against_baseline(node_ids, baseline_sha)` has no repo-root / worktree / wrapper injection seam, so a fixture repo requires overriding module globals, at which point production provisioning is not what runs. (b) `pytest-clean.sh` L33-39 sets `REPO_ROOT="$(pwd)"` only when cwd has a `pyproject.toml` containing `[tool.pytest`, else it falls back to `SCRIPT_ROOT` and `cd`s there — a minimal fixture repo silently redirects the classifier's pytest at the real repo. (c) The #3033 guard the whole design leans on keys on `.git` being a **file** (pytest-clean.sh L136-146), which is false for a standalone fixture repo, so the fixture never exercises the guard Risk 1 is written about. | pending | Widen the signature to `classify_against_baseline(node_ids, baseline_sha, *, repo_root: Path = PROJECT_DIR, worktree_path: Path = BASELINE_WORKTREE, wrapper: Path = PYTEST_CLEAN_SH)`. Build the fixture as a **linked worktree** of a temp repo (`git worktree add --detach`) so `.git` is a file, and give the fixture a `pyproject.toml` with a literal `[tool.pytest.ini_options]` section so the wrapper resolves `REPO_ROOT` to the fixture. Mark the test slow; assert on the bucket, not wall time. |
| BLOCKER | History & Consistency (round 8) | Two incompatible preconditions for *running* the classifier. Data Flow step 2 gates it on only `NIGHTLY_FIX_MODE != "off"` and non-empty `new_failures`, with the three run-shape disqualifiers living inside `decide_fix_or_escalate` — which by construction runs after classification. But Failure Path Test Strategy requires `--dry-run` to skip classification "entirely (no worktree, no pytest)" and Success Criteria requires a seed/re-baseline run to "never classify". The clash is operational, not just editorial: on a re-baseline night (`is_reseed`, L1026-1027) `prev["head_commit"]` exists and `new_failures` is the entire newly-absorbed population — the very set `MAX_DISPATCH_NODES` exists to contain — while `NIGHTLY_FIX_MAX_FAILURES` is a gate condition, not a classification precondition. The detector would serially re-run hundreds of nodes at the baseline SHA on the nightly critical path and then discard the verdict. Risk 2b's cost argument does not cover this case. | pending | Guard the call site with all six conditions before `classify_against_baseline(...)`: `NIGHTLY_FIX_MODE != "off" and new_failures and not args.dry_run and not is_seed_run and not integrity_warnings and len(new_failures) <= NIGHTLY_FIX_MAX_FAILURES`. On the skip path still emit the verdict line with `reason=` naming the skipped condition — the log contract says "every non-`off` run", and a silent skip loses the deliverable on the noisiest nights. Keep the same conditions inside the pure gate so its unit tests are unchanged, and rewrite Data Flow step 2 to list all six. |
| CONCERN | Risk & Robustness (round 8) | The classifier spawn's `env=` is elided. Both existing spawns deliberately pass `env = {**os.environ, "TEST_DB_CLAIM_WAIT_S": "300"}` (L374, L584) because the 30s interactive default in `tests/db_claim.py` is wrong for an unattended 03:00 run and the claim happens in `pytest_configure`, before any per-item timer. A classifier spawning with the default fails to claim a db under contention, errors during configure, and buckets every node `inconclusive` — indistinguishable from Risk 1's inert classifier, silently "safe", and undetected by the fixture test, which has no `tests/conftest.py` and therefore no db claim. | pending | Specify `env = {**os.environ, "TEST_DB_CLAIM_WAIT_S": "300"}` for the classifier spawn, matching L374/L584 verbatim; assert via `Popen` call capture that the classifier's kwargs carry it. Add "db-claim timeout" to Exception Handling Coverage as a named `inconclusive` cause. |
| CONCERN | Scope & Value (round 8) | `classify_attempts` and `NIGHTLY_FIX_MAX_ATTEMPTS` are structurally unreachable at this scope — the dead-config pattern round 4 caught and this plan's own Success Criterion forbids. `compute_new_failures` (L654) admits a node only on the night it was absent from the prior confirmed set; a node that stays red is never in `new_failures` again so is never re-classified, and a node that goes green is pruned to 0 by the plan's own keep-while-still-failing rule. With the default `NIGHTLY_FIX_MAX_ATTEMPTS = 1`, no reachable path makes the cap fire; the map exists only to be pruned. | pending | Drop the map and the constant to #3076 with the fixer that motivates them: removes one persisted key, the `all(classify_attempts.get(n, 0) < ...)` clause, the "Attempt-Map Semantics" block, the `-k "attempts"` row, and changes the `os.environ.get("NIGHTLY_FIX` count row from `== 3` to `== 2`. If the real intent is bounding classifier cost, the correct key is a per-run node ceiling enforced before `classify_against_baseline`, not per-node attempts inside the gate. |
| CONCERN | History & Consistency (round 8) | Update System specifies the `.env.example` two-line comment block but omits the marker that decides whether the key is required. Per `scripts/update/verify.py` L975-982 / `check_env_completeness` L1072-1081, an unmarked declaration is **required** (fail-closed default) and only a bare `# @optional` line exempts it. All three knobs are behavior toggles with in-code defaults (`NIGHTLY_FIX_MODE` defaults to `shadow`), so as written every machine without all three in its vault `.env` reports them missing on every `/update`. | pending | Make each block three lines: a `#` line describing the knob and its default, a bare `# @optional` line matching `_OPTIONAL_SIGIL_RE = re.compile(r"^@optional$")` (the sigil must be the whole comment line — `# @optional (tunable)` does not match), then the commented `# NIGHTLY_FIX_...=` placeholder. The axes are independent: `@optional` does not exempt the key from `tests/unit/test_env_declaration_readers.py`, which is satisfied by the `os.environ.get` reads. |
| NIT | Scope & Value (round 8) | The Verification row `grep -c "uv" scripts/nightly_regression_tests.py` (expected `> 0`) is a two-character substring check standing in for the most load-bearing claim in the plan (Risk 1). It is satisfied by a comment, or by an implementation that shells `uv` and ignores its return code. It returns `0` on the file today so it is not vacuous, but it is far weaker than the claim it guards. | pending | Drop the grep half of that row and keep only `pytest tests/unit/test_nightly_classifier.py -q -k "provision"`, with that test asserting `uv sync` is invoked with `cwd=<worktree>` and that a non-zero return code yields `inconclusive` with no `PROJECT_DIR` fallback. |
| NIT | History & Consistency (round 8) | `prev["head_commit"]` is called "the last-known-good SHA" (Data Flow step 2) and "the last-green nightly SHA" (Resolved Decision 1), but L1092 writes it on every non-fatal run regardless of how red it was — nothing in the detector records greenness. No false `newly_broken` is reachable, because the plan's real justification is the different and correct one in Technical Approach, but the Documentation task propagates "the last-green baseline ref" into `docs/features/nightly-regression-tests.md` where a reader will infer a guarantee that does not exist. | pending | Replace "last-green"/"last-known-good" with "the prior run's HEAD SHA" in Data Flow step 2, Resolved Decision 1, Risk 2, and the Documentation bullet, keeping the one-sentence justification (absent from the prior confirmed-failing set implies the node was not failing at that SHA) beside it — that sentence, not the name, is what makes the classification sound. |

---

## Resolved Decisions

1. **Baseline semantics + classifier mechanism.** The known-good ref is the *last-green nightly SHA* (`prev["head_commit"]`, L1092), not `main` — nightly runs on main and the failure IS on main, so diffing against `main` would mask a regression that already landed. The mechanism is an **in-process Python classifier**: `baseline-verifier` is Task-tool-only with zero `.py` callers, so a launchd script has no seam, and a `${baseline_ref:-main}` shell default could never be set by a prose dispatch. `.claude/agents/baseline-verifier.md` is untouched.
2. **The classifier's execution environment (round 7).** It runs through `scripts/pytest-clean.sh` with `cwd` pointed at a **persistent, `uv sync`-provisioned** baseline worktree — never at `PROJECT_DIR`, never around the wrapper. This required adding a `cwd` parameter to `_spawn_pytest`, which did not exist. Any provisioning failure is `inconclusive`, never a fallback.
3. **Enforcement of "never auto-merge" — deferred, not solved (round 7).** The only structural guarantee has two legs: review-required protection on `main` (404 today) and a non-admin fixer identity (no per-session env seam exists anywhere in the dispatch → worker → `claude -p` chain). Rather than assert either, the plan **cuts the tier that needs them**. #3076 owns it and names both as blocking prerequisites.
4. **Guardrail constants home.** Three knobs as module-scope `os.environ.get` reads with provisional comments, deliberately not promoted to `config/settings.py` (single-consumer script). Provisional defaults: `NIGHTLY_FIX_MODE=shadow`, `NIGHTLY_FIX_MAX_FAILURES=15`, `NIGHTLY_FIX_MAX_ATTEMPTS=1`. The two knobs whose only consumers were the deferred guards are **not declared**.
5. **Rollout.** `NIGHTLY_FIX_MODE` ships defaulting to `shadow` (classify + log the verdict, page as today). `off` restores byte-identical current behavior. There is no `active` in this plan; the flip is #3076's, gated on its two seams plus this plan's shadow evidence.
6. **Alerting is unchanged.** Both shipped modes fire the existing up-front page. This plan changes no outbound message, which is why every existing `send_telegram` assertion stays valid unmodified.

No open questions remain.

**2026-09-02 round-7 revision (baseline `2d60de31d`).** Three blockers resolved, two of
them by **cutting scope rather than asserting capability**, per the supervisor directive
following a third consecutive recurrence of "load-bearing mechanism asserted without a
verified seam". (1) The `GH_TOKEN`-into-the-fixer seam was traced hop by hop and confirmed
absent at every one — dispatch call site, CLI surface, process boundary, worker env overlay,
and model schema — so the entire fixer/enforcement/watchdog/notify tier is deferred to the
newly filed **#3076**, which names the missing seam as its first blocking item. (2) The
classifier's inert-shipping hazard is closed with three concrete, source-verified changes
(a `cwd` parameter on `_spawn_pytest`, a persistent `uv sync`-provisioned baseline worktree,
and no-fallback-to-`PROJECT_DIR`) plus a **non-stubbed two-commit fixture test** that is the
first thing in six rounds able to distinguish a working classifier from an inert one.
(3) The two self-defeating Verification rows are removed with the mandate they guarded, and
the `grep -E` alternation bug is reproduced, documented, and swept from every remaining row.
Both concerns resolved (the Step 0/Step 4 ordering hazard dissolves with the transport
coupling; day-one value is now three reachable things instead of one reachable thing and a
stack of unreachable code). Both nits resolved. Appetite reduced Large → Medium to match.
