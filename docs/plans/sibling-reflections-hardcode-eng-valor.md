---
status: docs_complete
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-09-05
tracking: https://github.com/tomcounsell/ai/issues/3072
last_comment_id: 5505275340
revision_applied: true
revision_applied_at: 2026-09-06T08:15:22Z
---

# Sibling reflections route Telegram alerts to a hardcoded "Eng: Valor"

## Problem

Four reflections page a human over Telegram by shelling out to `valor-telegram send --chat "Eng: Valor" <message>` with the chat name as a string literal in the argv:

| Site | Function | What it alerts about |
|---|---|---|
| `reflections/expectation_reconciler.py:181` | `_escalate_once` | An orphaned expectation on a Job whose owning lane is gone |
| `reflections/sdlc_progress.py:792` | `_send_alert` | An SDLC lane stalled at a head sha after auto-resume gave up |
| `reflections/sentry_triage.py:701` | `_send_telegram_notification` | The per-run Sentry classification digest |
| `reflections/stall_advisory.py:459` | `_send_alert` | Running sessions classified stalled or suspect |

Two of them, `expectation_reconciler` and `sdlc_progress`, run once per project through `run_per_project_audit(load_local_projects())`. When the reflection acts on `popoto` or `cuttlefish`, the alert about that project still lands in the `valor` engineers' chat. The message body carries a `[{project_key}]` prefix, so the misroute is legible after the fact, but the wrong humans get paged and the right ones never do.

The other two, `sentry_triage` and `stall_advisory`, aggregate across every project into one digest. For those the destination is not wrong so much as unresolved: the correct chat is this checkout's own engineer group, and today that identity is asserted by a string literal rather than looked up.

`valor-telegram`'s `--chat` accepts a group *name*, which re-enters its ambiguity-tolerant `resolve_chat` cascade. Two projects whose engineer groups differ only in suffix could resolve to whichever the cascade prefers. A numeric `chat_id` cannot.

**Current behavior:** every one of these four alerts is addressed to the literal string `Eng: Valor`, regardless of which project the reflection is acting on and regardless of what `projects.json` says.

**Desired outcome:** each sender resolves its destination from configuration — a numeric `chat_id` for the project whose work provoked the alert — and declines to send, loudly, when no destination resolves. The literal survives in exactly one place: `reflections/utilities.py`'s deliberately narrowed `FALLBACK_ENG_CHAT`, relocated intact from `docs_auditor`.

## Freshness Check

**Baseline commit:** `da6e5789a` — supersedes `67d714662`, the original plan-pass read. The four file:line re-verifications below were first made on `67d714662` and re-confirmed byte-exact on `da6e5789a`; every quantitative re-measurement in this document (Note 2's sweep counts, Success Criterion 2, the Verification table's "`6` before the change", Task 0's open-PR scan, and the Critique Results rows) was taken on `da6e5789a` and that is the hash to reproduce against. Both commits are on `main`; `da6e5789a` is the later of the two.

A third hash appears in `## Critique Results`: the round-3 critique re-derived its line numbers and test names against `bf0a5d577`. It is reconciled here rather than left dangling — `67d714662` → `da6e5789a` → `bf0a5d577` → `HEAD`, each an ancestor of the next (`git merge-base --is-ancestor`), and `git diff da6e5789a HEAD` is **empty** across every path this plan touches (`reflections/`, `tests/`, `scripts/memory_consolidation.py`). The three hashes are therefore interchangeable for every measurement in this document; `da6e5789a` remains the pinned baseline because the quantitative sweeps were taken there.
**Issue filed at:** 2026-09-02T05:51:09Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `reflections/expectation_reconciler.py:181` — hardcoded argv in `_escalate_once` — still holds, exact line.
- `reflections/sentry_triage.py:701` — hardcoded argv in `_send_telegram_notification` — still holds, exact line.
- `reflections/stall_advisory.py:459` — hardcoded argv in `_send_alert` — still holds, exact line.
- `reflections/sdlc_progress.py:792` — hardcoded argv in `_send_alert` — still holds, exact line.
- `reflections/utilities.py::resolve_eng_group` — the prerequisite resolver — present, line 310.
- `reflections/docs_auditor.py::_resolve_notify_chat` — the landed reference implementation — present, line 1552.

**Cited sibling issues/PRs re-checked:**
- #2754 — closed 2026-09-02T11:58:29Z. Resolution: PR #3077 (`fix(docs-auditor): route Telegram notifications by audited repo root`, merge commit `974be653`) added `_resolve_notify_chat(repo_root)` plus 8 routing tests, and documented the ladder in `docs/features/docs-auditor.md`. The prerequisite this issue was severed to wait for has landed.

**Commits on main since the issue was filed (touching referenced files):**
- `3c77e1eab` *AgentSession: one newest-wins resolver for every session_id read* — touched `reflections/expectation_reconciler.py`'s owner-row lookup. Irrelevant to the sender.
- `b964baf63` *Job: a corrupt goal fails closed on every write and loud on every read* — irrelevant to the sender.
- Neither moved a sender line; all four line references are byte-exact.

**Active plans in `docs/plans/` overlapping this area:** none. No plan document mentions `resolve_eng_group`, `send_eng_telegram`, or the literal.

**Issue comment incorporated:** comment `5505275340` (valorengels, 2026-09-02T06:08:46Z), a scope correction carried over from the #2754 plan-critique pass. It states the **What** list is illustrative rather than exhaustive and names two further sites. Re-verified here:
- `scripts/memory_consolidation.py:352` — **still present**, exactly as described. In scope; see Solution and Success Criteria.
- `scripts/nightly_regression_tests.py:105` — `TELEGRAM_CHAT = "Eng: Valor"` — **gone**. That file carries no `TELEGRAM_CHAT` constant and no `valor-telegram` reference at all on `67d714662`; it was removed sometime after the comment was written. Independently corroborates note 2 below.

The comment also fixes the closing condition: a clean repo-wide grep sweep, covering both the `--chat` argv adjacency **and** the bare literal as a module constant, across tracked `.py` outside `reflections/agents/` prompt text. That is stricter than the argv-only sweep and it collides with `FALLBACK_ENG_CHAT` — see Success Criteria criterion 2 and Open Question 2.

**Notes — the drift that matters:**

1. **The issue undercounts the sweep.** A fifth argv site carries the literal outside `reflections/`: `scripts/memory_consolidation.py:352`. The issue's stated exit criterion is "a clean grep sweep for the literal, not an enumerated site list", and that sweep fails today with `memory_consolidation` still present. See No-Gos for the disposition.
2. **The archived #2754 plan's anti-criterion is already stale, and the two sweeps have different baselines.** The archived plan asserts six non-`docs_auditor` files carry the literal, naming `scripts/nightly_regression_tests.py` among them. Re-verified on `da6e5789a`: that file carries no `TELEGRAM_CHAT` constant, no `Eng: Valor` literal, and no `valor-telegram` reference at all — it is not a site, and lane #3170's concurrent edits to it are therefore irrelevant to this work. This plan touches it not at all.

   The two sweeps this plan uses count differently, and the earlier draft conflated them:

   - **Argv adjacency** (`git grep -n '"--chat", "Eng: Valor"' -- '*.py'`) — **five** sites today: `reflections/expectation_reconciler.py:181`, `reflections/sdlc_progress.py:792`, `reflections/sentry_triage.py:701`, `reflections/stall_advisory.py:459`, `scripts/memory_consolidation.py:352`.
   - **Bare literal outside tests and prompt text** (`git grep -lF '"Eng: Valor"' -- '*.py' | grep -v '^tests/' | grep -v '^reflections/agents/'`) — **six** paths today: those same five plus `reflections/docs_auditor.py`, whose `FALLBACK_ENG_CHAT = "Eng: Valor"` at line 44 already carries the bare literal. That sixth site is #2754's merged narrowing, pre-existing and deliberate; this plan relocates it rather than introducing it.

   Six files today, one after — measured, not asserted. Success Criterion 2 uses the six-file baseline.
3. **`sentry_triage`'s `load_local_projects()` call is not what the issue thinks it is.** It appears at line 913, inside the Class-C filing branch, and exists only to map a Sentry project slug to a `working_directory` for `gh issue create`. It has no bearing on the notification at line 1034, which is a single cross-project digest. Corrected in the Solution.

## Prior Art

- **#2754 / PR #3077 (merged 2026-09-02)** — the direct parent. Fixed `reflections/docs_auditor.py` alone, because that is the one site where the audited repo identity was already in scope at the send call. Its plan (`docs/archive/plans-completed/docs-auditor-telegram-sender-hardcodes-eng-valor.md`) explicitly deferred both the sweep and the shared helper to this issue: *"Building the shared `send_eng_telegram` helper in `utilities.py` now"* is listed as a No-Go there, with the note that *"that belongs with the sweep"*. This plan is the sweep.
- **#2717** — *Autonomously start SDLC on `upvote`-labeled issues via a scheduled reflection*. Introduced `reflections/sdlc_upvote_lanes.py`, which is already a `resolve_eng_group` consumer; `tests/unit/reflections/test_sdlc_upvote_lanes.py` monkeypatches `resolve_eng_group` at two sites. A second working consumer to pattern-match against.
- **#2629** — *react() cannot derive a transport: reflection emoji reactions still RPUSH to `telegram:outbox:0`*. Same failure family: a reflection asserting a Telegram destination as a constant instead of deriving it. Confirms this is a recurring shape, not a one-off.
- **#1633** — *Merge PM/Dev bridge roles into a single Eng role*. Established the `Eng:` group-name prefix convention that `resolve_eng_group` scans for and that `bridge/routing.py` keys the engineer persona off.

No prior attempt at *these four* sites exists. The `## Why Previous Fixes Failed` section is therefore not applicable.

## Research

No external research performed, and none warranted. This is an internal refactor: no new dependency, no external API, no ecosystem pattern in play. Every fact the plan rests on came from reading the checkout at `67d714662` and from `gh` queries against this repo's own issue history, both recorded above.

## Spike Results

All four verifiable assumptions were resolved inline by code-read against `67d714662` — none needed a fan-out, and each came in well under the 5-minute cap. Recorded here because the findings, not the method, are what the build needs.

### spike-1: is the project dict actually in scope at every escalation call site?
- **Assumption**: "Threading a project through is a per-module signature refactor" (issue body) — implying the project is reachable but several frames up.
- **Method**: code-read
- **Result**: Better than assumed for both per-project modules. In `expectation_reconciler`, `_escalate_once` has exactly three call sites (lines 444, 480, 506), all inside `_reconcile_project(project: dict)`, which already binds `project_key = project.get("slug", "?")` at line 372. In `sdlc_progress`, `_escalate_once` has exactly one call site (line 1160), inside the `_escalate` closure in `_check_project_stalls(project: dict)`, and it *already passes* `project=project_key`. No intermediate frames need new parameters — the dict is on the same frame in both cases.
- **Confidence**: high
- **Impact if false**: would have forced threading through `_attempt_action` and the action ladder. It does not.

### spike-2: does `sentry_triage`'s digest have a project to thread?
- **Assumption**: issue body — "`sentry_triage` is a `load_local_projects()` consumer, i.e. it genuinely iterates multiple projects. So its alerts would misroute."
- **Method**: code-read
- **Result**: **False as stated.** `run_sentry_triage()` calls `_send_telegram_notification` once, at line 1034, with a digest built from `classified[A..E]` spanning every Sentry project in the org. Its `load_local_projects()` call at line 913 resolves a `working_directory` for `gh issue create` and never reaches the notification. `stall_advisory` is the same shape by a different route: it never calls `load_local_projects()` at all, and `run_stall_advisory()` emits one aggregate alert at line 223 from a global `AgentSession.query.filter(status__in=...)`.
- **Confidence**: high
- **Impact if false**: none — this *is* the falsification, and it splits the fix in two. See Solution.

### spike-3: what signature does the shared helper actually need?
- **Assumption**: issue body — `send_eng_telegram(project_key, message)`.
- **Method**: code-read
- **Result**: wrong key type. `resolve_eng_group(project: dict)` takes the full project dict and scans `project["telegram"]["groups"]`. A `project_key`-keyed helper would have to re-run `load_local_projects()` and re-scan on every send, at call sites that already hold the dict. `docs_auditor._resolve_notify_chat` sidesteps this by keying on `repo_root: Path` and matching against each project's `working_directory` — the right shape for the host-machine case, the wrong one for a caller holding the dict.
- **Confidence**: high
- **Impact if false**: the helper API changes. It does — see Solution's two-entry-point design.

### spike-4: can resolution be verified on this machine?
- **Assumption**: a local run can demonstrate a resolved numeric `chat_id`.
- **Method**: code-read + live probe (`load_local_projects()` → `resolve_eng_group` over every local project).
- **Result**: **No.** All 20 projects in this machine's `~/Desktop/Valor/projects.json` carry an empty `telegram` block; `resolve_eng_group` returns `None` for every one. This checkout is a worker-only machine whose `projects.json` is Tom's, not production's. The archived #2754 plan records that on the production machine `valor` resolves to `('Eng: Valor', -1003449100931)`.
- **Confidence**: high
- **Impact if false**: n/a. The consequence is a hard constraint on verification: only the *skip* branch is observable here. Every resolved-destination assertion must be a unit test against a synthetic project dict, exactly as `tests/unit/test_docs_auditor_substrate.py::TestTelegramChatRouting` does it. Recorded in Risks.

## Data Flow

Two distinct flows reach the same broken argv. They are the reason this plan builds two entry points rather than one helper.

**Flow A — per-project escalation (`expectation_reconciler`, `sdlc_progress`):**

```
reflection scheduler
  └─ run_expectation_reconciliation() / run_sdlc_progress_check()
       └─ run_per_project_audit(body, name=...)          reflections/utilities.py:118
            └─ load_local_projects()                      → [{slug, working_directory, telegram:{groups:{...}}, ...}]
                 └─ for project in projects:
                      └─ body(project)                    _reconcile_project / _check_project_stalls
                           ├─ project_key = project["slug"]
                           └─ ...ladder... → _escalate_once(...)
                                └─ subprocess.run(["valor-telegram","send","--chat","Eng: Valor", msg])
                                                          ^^^^^^^^^^^^  project identity discarded here
```

The project dict travels the whole way and is dropped at the last frame. The `[{project_key}]` prefix already baked into the message body is the surviving trace of it.

**Flow B — host-machine digest (`sentry_triage`, `stall_advisory`):**

```
reflection scheduler
  └─ run_sentry_triage()                                  no per-project iteration for the digest
       ├─ _fetch_unresolved_issues(...)                   → issues across every Sentry project
       ├─ _classify_issue(...) → classified[A..E]
       ├─ (Class-C branch only) load_local_projects() → proj_wd, for `gh issue create` cwd
       └─ _send_telegram_notification("\n".join(tg_lines))   sentry_triage.py:1034
            └─ subprocess.run([... "--chat","Eng: Valor", msg])

  └─ run_stall_advisory(params)                            never calls load_local_projects()
       ├─ AgentSession.query.filter(status__in=_RUNNING_PROBE_STATUSES)   global, all projects
       └─ if telegram_enabled and findings: _send_alert(msg)   stall_advisory.py:223
            └─ subprocess.run([... "--chat","Eng: Valor", msg])
```

No project dict is ever in scope at the send. The digest describes the *fleet*, so its correct destination is the engineer group of the checkout running the reflection.

**Target flow (both):**

```
caller ─┬─ holds a project dict ──► send_eng_telegram(project, message, logger_prefix=...)
        │                              └─ resolve_eng_group(project) → (name, chat_id) | None
        │                                   ├─ None  → log warning, return False, NO subprocess
        │                                   └─ (…,id)→ subprocess.run([... "--chat", str(chat_id), msg]) → True
        │
        └─ holds no project ──────► send_host_eng_telegram(message, logger_prefix=...)
                                       └─ resolve_host_eng_chat() → str | None
                                            ├─ match PROJECT_ROOT against a project's working_directory
                                            ├─ resolve_eng_group(that project) → str(chat_id)
                                            └─ else FALLBACK_ENG_CHAT ("Eng: Valor") — this checkout only
```

The critical transformation is `int → str(chat_id)` before it enters argv, never `group_name`. A name re-enters `valor-telegram`'s ambiguity-tolerant `resolve_chat` cascade; an id cannot. This is the same rule `docs_auditor._resolve_notify_chat` documents in its docstring, and it must not be softened here.

## Why Previous Fixes Failed

Not applicable — no prior fix has been attempted at these four sites. #2754 deliberately scoped itself to `docs_auditor` and named this sweep as its own No-Go; that is a scoping decision, not a failed fix.

## Architectural Impact

**Consolidating, not expanding.** After this change `reflections/utilities.py` owns the single "how does a reflection page an engineer" rule, and five modules consume it instead of each asserting a destination. `resolve_eng_group` already lives there and is already consumed by `docs_auditor` and `sdlc_upvote_lanes`; the send-side wrapper is the missing half.

**One deliberate duplication is retired.** `docs_auditor._resolve_notify_chat`'s `PROJECT_ROOT`-narrowed fallback is exactly the host-machine rule that `sentry_triage` and `stall_advisory` need. Rather than copy it a third time, this plan **lifts** it into `reflections/utilities.py::resolve_host_eng_chat()` and has `docs_auditor` delegate. That keeps the fallback's narrowing — and its "do not simplify this into an unconditional default" reasoning — in one place. It also means the #2754 tests keep passing against a thinner `_resolve_notify_chat`, which is the cheapest possible regression check on the lift.

**No new module, no new dependency, no new config key.** The change is confined to `reflections/` plus one script, and every fact it consumes (`projects.json` `telegram.groups.<Eng: X>.chat_id`) already exists and is already read by two callers.

**A behavior boundary moves.** Today an alert always goes somewhere. After this change an alert can be *suppressed* when nothing resolves. That is the correct trade — paging the wrong humans is worse than paging none, and #2754 already made the same call for `docs_auditor` — but it means "no Telegram message arrived" stops being proof that nothing was wrong. The mitigation is that every suppression logs at `warning` with the project key and reaches the reflection's `summary` field, which is the only thing the scheduler persists.

## Appetite

**Medium.** Five source files, one lifted helper, and a test-fixture sweep across four test modules.

What justifies Medium rather than Small is the lift, not the test sweep. Moving `_resolve_notify_chat` out of a module that merged three days ago with 248 lines of fresh tests — and doing it without editing one line of those tests — is the hard part, and it lands on a file two other open lanes (#2743, #3050) intend to edit. `## Test Impact` enumerates **sixteen** entries: six fixture edits, four new cases (one new module, two counter pins in existing modules, and the short-circuit pin at `expectation_reconciler.py:476-483`), five no-change verifications, and one verify-only. Only two of the five sender signatures change arity, which is what keeps the edit count at six rather than across every consumer suite. What holds it back from Large: no new abstraction is being invented — `resolve_eng_group` and the `_resolve_notify_chat` ladder both exist and both have passing tests to copy from.

If this exceeds the appetite, the fall-back scope is Flow A only (`expectation_reconciler`, `sdlc_progress`) — the two sites where alerts genuinely misroute — leaving the host-machine digests for a follow-up. That is a legitimate cut because Flow B's current behavior is right by accident rather than wrong.

## Prerequisites

**#2754 / PR #3077 — landed** (merged 2026-09-02, merge commit `974be653`). Provides `reflections/utilities.py::resolve_eng_group` and the `reflections/docs_auditor.py::_resolve_notify_chat` ladder this plan lifts. Verified present at `67d714662`. No other prerequisite.

| Requirement | Check Command | Purpose |
|---|---|---|
| `resolve_eng_group` exists | `git grep -q "def resolve_eng_group" reflections/utilities.py` | The project-dict resolver this plan's `send_eng_telegram` wraps |
| `_resolve_notify_chat` exists | `git grep -q "def _resolve_notify_chat" reflections/docs_auditor.py` | The host-machine ladder this plan lifts into `resolve_host_eng_chat` |
| `#2754` is closed | `bash -c 'test "$(gh issue view 2754 --json state -q .state)" = CLOSED'` | The severance condition: this sweep waits on the parent fix |

## Solution

Two entry points in `reflections/utilities.py`, because recon (spike-2) showed the four callers are not one shape.

### 1. `send_eng_telegram(project, message, *, logger_prefix) -> bool`

For callers that hold a project dict.

**Two try/except scopes, never one.** The resolver call and the transport call map their failures to *opposite* return values, so a single blanket handler spanning both would invert the contract:

```
# --- scope 1: resolution. Any failure here means nothing was sent. ---
try:
    resolved = resolve_eng_group(project)
except Exception:                      # a monkeypatched resolver, or a future raise
    log.warning("%s: Eng: group lookup failed for project %s; Telegram alert suppressed",
                logger_prefix, project.get("slug", "?"))
    return False                       # NO subprocess is invoked
if resolved is None:
    log.warning("%s: no Eng: group for project %s; Telegram alert suppressed",
                logger_prefix, project.get("slug", "?"))
    return False                       # NO subprocess is invoked
_, chat_id = resolved

# --- scope 2: transport. Any failure here still means a send was attempted. ---
try:
    proc = subprocess.run(["valor-telegram", "send", "--chat", str(chat_id), message], ...)
    if proc.returncode != 0:
        log.warning(...)               # non-zero exit is a transport failure, not suppression
except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
    log.warning(...)
return True
```

`resolve_eng_group` already swallows its own internal exceptions and returns `None` (`reflections/utilities.py:339-341`), so scope 1's `except` fires only when a caller or a test replaces the function object with one that raises — which is exactly the "`resolve_eng_group` raises" row of the Failure Path table, and it demands `False` with no subprocess. Scope 2 swallows to `True`. This split is the whole content of Task 3's swallow instruction; read the two as one and the contract inverts.

Takes the **dict**, not a `project_key` — `resolve_eng_group` scans `project["telegram"]["groups"]`, and every call site already holds the dict (spike-1). A key-based signature would force a redundant `load_local_projects()` scan per send.

Returns `True` when a destination resolved and a send was *attempted* — including a swallowed `FileNotFoundError` / `TimeoutExpired` / any other transport exception / a non-zero exit. `False` means and only means "nothing resolved, nothing was sent", and it covers both ways resolution can fail: `resolve_eng_group` returning `None`, and `resolve_eng_group` raising. This is the same contract `docs_auditor._send_telegram_notification` already publishes, and preserving it is what lets callers write an accurate suppression notice.

`logger_prefix` keeps each module's existing log vocabulary (`"expectation_reconciler"`, `"sdlc_progress"`, …) so log greps and any alerting built on them survive.

### 2. `resolve_host_eng_chat() -> str | None` and `send_host_eng_telegram(message, *, logger_prefix) -> bool`

For callers with no project in scope — the fleet-wide digests.

`resolve_host_eng_chat()` is `docs_auditor._resolve_notify_chat` **lifted verbatim** and generalized from `repo_root: Path` to defaulting on `reflections.utilities.PROJECT_ROOT`, keeping every rung and every comment:

1. Match the repo root against a `projects.json` entry's `working_directory`.
2. On a match, return `str(chat_id)` from that project's `Eng:` group.
3. On no match or no configured group, return `FALLBACK_ENG_CHAT` **only when** the root is this very checkout. Never for a foreign repo.
4. Swallow, log, and fall through to rung 3 on any exception.

**Two seams are injectable, and that is what makes the lift safe.** The signature is:

```python
def resolve_host_eng_chat(
    repo_root: Path | None = None,
    *,
    load_projects: Callable[[], list[dict]] | None = None,
    project_root: Path | None = None,
) -> str | None:
    loader = load_projects or load_local_projects      # utilities' own binding
    anchor = project_root or PROJECT_ROOT              # utilities' own binding
    target = (repo_root or anchor).resolve()
    ...
```

and `docs_auditor._resolve_notify_chat(repo_root)` delegates by passing **its own module-level bindings** through, read at call time:

```python
def _resolve_notify_chat(repo_root: Path) -> str | None:
    return resolve_host_eng_chat(
        repo_root, load_projects=load_local_projects, project_root=PROJECT_ROOT
    )
```

This is the whole answer to the critique's blocker, and the reason it works is mechanical: `unittest.mock.patch` rebinds a name inside the target module's `__dict__` only — it does not follow the object into another module's globals. A bare `load_local_projects()` call inside `utilities` would resolve against `utilities.__dict__` and the docs_auditor-scoped patch would sail past it. Reading `load_local_projects` in the *delegation body*, which lives in `docs_auditor`, resolves against `docs_auditor.__dict__` at call time, so the patch lands on the value actually used.

Verified against the test file on `da6e5789a`, not assumed: all 8 `TestTelegramChatRouting` cases (`tests/unit/test_docs_auditor_substrate.py:1554-1730`) patch exactly two docs_auditor-scoped names — `reflections.docs_auditor.load_local_projects` and `reflections.docs_auditor.subprocess.run`. The `subprocess.run` patch is unaffected: only the *resolver* moves; `_send_telegram_notification` and its `subprocess.run` call stay in `docs_auditor`. The `load_local_projects` patch is carried by the injection above. No case patches `docs_auditor.resolve_eng_group` or `docs_auditor.FALLBACK_ENG_CHAT`.

`project_root` closes a second, latent seam that no current test exercises but that the lift would otherwise shift silently. Nine tests in that module patch `reflections.docs_auditor.PROJECT_ROOT` (lines 291-1535, 1747-2005); every one of them either never reaches the resolver or patches `_send_telegram_notification` wholesale, so none would fail today. But rung 3's `target == PROJECT_ROOT.resolve()` guard currently reads *docs_auditor's* `PROJECT_ROOT`, and after an uninjected lift it would read *utilities'*. The two are the same path today (both `Path(__file__).parent.parent` from `reflections/`), so the divergence appears only under a patch — which is exactly the kind of trap that surfaces six months later as an unexplainable test. One keyword closes it.

`FALLBACK_ENG_CHAT` moves to `utilities.py`. `docs_auditor` keeps a re-export binding (`from reflections.utilities import FALLBACK_ENG_CHAT`) so any external reader of `docs_auditor.FALLBACK_ENG_CHAT` still resolves, and so the module's own docstring reference at line 1569 stays honest.

**Why `docs_auditor._send_telegram_notification` stays where it is.** It publishes the same return contract as `send_host_eng_telegram` — `False` only on no-destination, `True` on any swallowed transport failure — and it keeps its own `subprocess.run` (`reflections/docs_auditor.py:1602-1642`) rather than delegating. The two are not interchangeable: `_send_telegram_notification` is **parameterized by the audited repo root** (`repo_root: Path | None`), which is the entire point of #2754 — it addresses whichever repo the audit is acting on, including a foreign one. `send_host_eng_telegram` deliberately takes no root and always resolves *this* checkout, because its callers (`sentry_triage`, `stall_advisory`) have no repo in scope. Folding one into the other means re-adding the `repo_root` parameter to the host helper, which turns it back into the general sender and hands every host-digest caller a knob it must not have.

The cheaper reason is decisive on its own: all 8 `TestTelegramChatRouting` cases patch `reflections.docs_auditor.subprocess.run`. Moving that `subprocess.run` out of `docs_auditor` breaks all 8 and violates Success Criterion 4 — and folding is a fourth hunk in a file the plan holds to three while lane #2743 is live on it. Only the *resolver* moves; the sender stays. This is the same shape as the `memory_consolidation` split below, minus the error-contract divergence: there the caller keeps its transport because its contract is stronger, here because its signature is wider.

**The docs_auditor diff is bounded to three hunks, deliberately** — see the coordination note in Risks. (i) the import line gains `resolve_host_eng_chat` and `FALLBACK_ENG_CHAT`; (ii) line 44's constant assignment is deleted; (iii) the `_resolve_notify_chat` body (currently lines ~1552-1600) collapses to the three-line delegation above, keeping a one-line docstring that points at the new owner. Nothing else in that 2000-line file is touched.

If the 8 cases do not pass unchanged, the lift is wrong. Run them immediately after Task 2 and before any other code exists.

### 3. Per-module wiring

| Module | Change |
|---|---|
| `expectation_reconciler` | `_escalate_once(job_id, eid, message)` → `_escalate_once(project, job_id, eid, message)`. Update the three call sites (444, 480, 506) inside `_reconcile_project`, which already holds `project`. Body calls `send_eng_telegram(project, message, logger_prefix="expectation_reconciler")`. |
| `sdlc_progress` | `_send_alert(message)` → `_send_alert(project, message)`. `_escalate_once` already takes `project=project_key` (a *string*); add a `project_dict` keyword and thread it from `_check_project_stalls`, which holds it. Body calls `send_eng_telegram`. |
| `sentry_triage` | `_send_telegram_notification(message)` keeps its arity; body swaps to `send_host_eng_telegram(message, logger_prefix="sentry_triage")`. |
| `stall_advisory` | `_send_alert(message)` keeps its arity; body swaps to `send_host_eng_telegram(message, logger_prefix="stall_advisory")`. |
| `scripts/memory_consolidation.py:352` | **Resolver only — this one caller does not adopt `send_host_eng_telegram`.** `_flag_contradiction` (line 333) calls `resolve_host_eng_chat()` for the destination and keeps its own `subprocess.run([..., "--chat", chat, telegram_msg], check=True, capture_output=True, timeout=10)` at line 350-356 together with both existing handlers — `except subprocess.CalledProcessError` (line 357) and `except Exception` (line 365), each already routing to `_write_contradiction_log`. The only new branch is a suppression guard: when `resolve_host_eng_chat()` returns `None`, log a warning and skip the send without writing the contradiction log. See the note below for why. |

Only two of the five sender signatures change arity. That is a deliberate consequence of spike-2 and it cuts the test-fixture sweep roughly in half.

**Why `memory_consolidation` keeps its own `subprocess.run`.** The two contracts do not compose. `send_host_eng_telegram` returns `False` for exactly one condition — *no destination resolved* — and swallows every transport failure into a `True`. `_flag_contradiction`'s guarantee is the opposite axis: it writes `logs/memory-contradictions.log` precisely when the **transport** fails (`check=True` → `CalledProcessError`, or any other exception), so that a contradiction survives a bridge outage on disk. Routing the log write off a `False` return would retire that guarantee while appearing to preserve it, because the helper never returns `False` for a transport failure.

Worse, `False` is structurally unreachable for this caller. `send_host_eng_telegram` calls `resolve_host_eng_chat()` with no `repo_root`, so `target == PROJECT_ROOT.resolve()` always holds and rung 3 always returns `FALLBACK_ENG_CHAT`. A `False`-keyed log write would be dead code on every machine in the fleet, and the test pinning it would have to force a resolution failure that cannot occur in production.

So the split is: **resolution** moves to the shared helper (which is the whole point of the sweep — the argv literal goes away), and **transport error handling** stays with the caller that has a stronger contract than the helper offers. The suppression branch (`resolve_host_eng_chat()` → `None`) deliberately does *not* write the contradiction log: nothing was attempted and nothing failed, and conflating "no chat configured" with "bridge down" would make the fallback log lie about what happened. It logs a warning naming the suppression instead.

### 4. Suppression must be visible

Both `expectation_reconciler._escalate_once` and `sdlc_progress._escalate_once` currently return a truthy value meaning "the human was told". After this change that claim can be false. Each must thread the helper's `False` into the reflection's `findings` list — e.g. `f"alert-suppressed: no Eng: group for {project_key}"` — so it reaches the `summary` the scheduler persists. `docs_auditor` set this precedent in PR #3077 and it is the whole mitigation for the behavior boundary named in Architectural Impact.

**In `sdlc_progress` that requires a return-type change, not just a new finding.** Its `_escalate_once` returns `str | None` and its sole caller — the `_escalate` closure in `_check_project_stalls`, `reflections/sdlc_progress.py:1159-1172` — reads that single value as *both* signals at once:

```python
if msg:
    counts["escalated"] += 1     # line 1171 — "a human was paged"
    findings.append(msg)         # line 1172 — "there is something to report"
```

After this change those diverge: a suppressed page has something to report and nobody was paged. `_escalate_once` therefore returns `tuple[bool, str | None]` and the closure gates the two lines separately. The exact shape and the reason it is a tuple rather than a message-prefix convention are in Task 7; it is called out here because Success Criterion 6 is unreachable without the closure edit, and the closure is not visible from the per-module wiring table above.

**`expectation_reconciler` carries the same collapse, three times.** `_escalate_once(job_id, eid, message) -> bool` (line 170) is read at all three call sites in the identical shape — line 444 gated by 451-452, line 480 gated by 481-482, line 506 gated by 513-514 — each `if _escalate_once(...):` followed by `counts["escalated"] += 1` and a `findings.append(f"escalated: {eid}")`. Today a `False` return means only "already escalated for this `(job, eid)`", which correctly counts nothing and reports nothing. After this change `False` also covers "no `Eng:` group resolved", which must report while still counting nothing. So it takes the same 2-tuple treatment: `-> tuple[bool, str | None]`, with `(False, None)` for the burned sentinel, `(True, None)` on a successful page (the caller supplies its own site-specific finding text, which differs at each of the three sites), and `(False, f"alert-suppressed: no Eng: group for {project_key} ({eid})")` on suppression. Each call site becomes `sent, sup = _escalate_once(...)`, then `if sent: counts["escalated"] += 1; findings.append(<its existing string>)` and `elif sup: findings.append(sup)`. Task 6 carries this.

## Failure Path Test Strategy

The whole point of this change is what happens when resolution *fails*, so the failure paths carry more weight than the happy path. Every case below is a unit test against a synthetic project dict — spike-4 established that no local run can resolve a real chat.

**`send_eng_telegram` (mirroring `TestTelegramChatRouting`'s shape):**

| Case | Input | Assertion |
|---|---|---|
| Resolves | `{"slug":"valor","telegram":{"groups":{"Eng: Valor":{"chat_id":-1003449100931}}}}` | argv carries `"-1003449100931"`, **not** `"Eng: Valor"`; returns `True` |
| No `telegram` key | `{"slug":"royop"}` | `subprocess.run` **never called**; returns `False`; warning names `royop` |
| No `Eng:` group | `{"telegram":{"groups":{"Ops: X":{"chat_id":1}}}}` | never called; `False` |
| Malformed `chat_id` | `{"telegram":{"groups":{"Eng: X":{"chat_id":"-100123"}}}}` (string) | never called; `False` |
| Bool `chat_id` | `{"telegram":{"groups":{"Eng: X":{"chat_id":True}}}}` | never called; `False` — `resolve_eng_group` rejects `bool` explicitly; pin it |
| `valor-telegram` absent | resolvable dict, `subprocess.run` raises `FileNotFoundError` | swallowed; returns **`True`** (a destination resolved) |
| Timeout | raises `TimeoutExpired` | swallowed; `True` |
| Non-zero exit | `returncode=1` | swallowed, warning logged; `True` |
| `resolve_eng_group` raises | monkeypatched to raise | swallowed; `False`; no subprocess |

**`resolve_host_eng_chat` / `send_host_eng_telegram`:**

| Case | Assertion |
|---|---|
| `PROJECT_ROOT` matches a project with an `Eng:` group | returns `str(chat_id)`, not the name |
| `PROJECT_ROOT` matches a project **without** an `Eng:` group | returns `FALLBACK_ENG_CHAT` — the narrowed fallback, this checkout only |
| Root is a **foreign** registered repo with no group | returns `None`; no subprocess; warning |
| Root is unregistered and is **not** `PROJECT_ROOT` | returns `None` |
| `load_local_projects()` raises | swallowed; falls through to the rung-3 rule |

**Suppression reaches the summary** (the mitigation named in Architectural Impact):

- `_reconcile_project` with an unresolvable project → `findings` contains an `alert-suppressed` entry and the reflection's `summary` carries it.
- `_check_project_stalls` likewise, and `counts["escalated"]` is **not** incremented for a suppressed page. This is the case that pins the `_escalate` closure split (Task 7), and no test in `tests/unit/reflections/test_sdlc_progress_check.py` asserts on `counts["escalated"]` today — line 1878 only checks that the four rung keys exist in the result dict. Drive `_check_project_stalls` to the escalation rung with a project dict carrying no `Eng:` group, then assert **all three** at once: `result["counts"]["escalated"] == 0`, an `alert-suppressed` entry in `result["findings"]`, and the string present in `result["summary"]`. Asserting only the finding leaves the double-count bug green; asserting only the counter leaves the dropped-finding bug green.
- The mirror case for `expectation_reconciler`: `_reconcile_project` reaching any of its three escalation sites with an unresolvable project → `counts["escalated"] == 0`, the `alert-suppressed` entry in `findings`, and no `escalated: {eid}` entry. The burned-sentinel case (`_escalate_once` returning `(False, None)`) must stay distinguishable from it: neither counter nor finding moves.

**Anti-test 1 — send by id, not name (owned by Task 5).** Change `str(chat_id)` back to `group_name` in `send_eng_telegram` and the resolves-case test must go red. If it stays green the test is asserting the wrong thing (it is checking "not `Eng: Valor`" rather than "is the id"), which is exactly the shape of assertion that passes without reaching the code. This one runs against the new module `tests/unit/reflections/test_utilities_eng_telegram.py`, so it is meaningful as soon as that module exists.

**Anti-test 2 — the fallback stays narrowed (owned by Task 2, not Task 5).** Delete the `if target == PROJECT_ROOT.resolve():` clause from `resolve_host_eng_chat` so rung 3 returns `FALLBACK_ENG_CHAT` unconditionally, then run `scripts/pytest-clean.sh 'tests/unit/test_docs_auditor_substrate.py::TestTelegramChatRouting' -q`. **Exactly three cases go red** — replayed against the checkout, not predicted from reading:

- `test_foreign_unregistered_repo_suppresses_send` (`tests/unit/test_docs_auditor_substrate.py:1631`) — `assert sent is False` fails as `assert True is False`.
- `test_registered_repo_with_malformed_group_suppresses_send` (`:1644`) — the same assertion, via the empty-`groups` royop fixture.
- `test_load_local_projects_raising_falls_back_for_project_root_only` (`:1663`) — `assert sent_foreign is False` fails.

The run under the mutation is **3 failed, 5 passed**. Restore the clause and confirm **8 passed**.

`test_foreign_registered_repo_routes_to_its_own_group` (`:1587`) **stays green, and naming it here would be wrong.** Its fixture is a registered foreign repo carrying a valid `Eng: Popoto` group, so it resolves at rung 2 and returns at `reflections/docs_auditor.py:1585`, never reaching the rung-3 guard at line 1590. Only fixtures that fall through to rung 3 can observe this mutation. The rule to carry past this plan: **a mutation anti-test may name only cases whose fixtures reach the mutated line.** Naming one that cannot leaves a builder seeing half the predicted result and guessing whether the lift failed.

This guards the plan's own top-named temptation — Rabbit Holes' "Simplifying the `PROJECT_ROOT`-narrowed fallback into an unconditional default" — at the moment a builder is most exposed to it, which is while the function is in transit between modules. The guard is `reflections/docs_auditor.py:1590` today and moves into `reflections/utilities.py::resolve_host_eng_chat` under the lift.

It belongs to **Task 2**, not Task 5, for a timing reason: it runs against `tests/unit/test_docs_auditor_substrate.py`, which only exercises the lifted function once the delegation is in place. Task 5's "before any consumer is wired" framing is right for anti-test 1 and wrong for this one. Run each mutation once per guard, per review round — a green test that reaches no code confirms everything.

## Test Impact

Arity changes to `_escalate_once` (`expectation_reconciler`) and `_send_alert` (`sdlc_progress`) break every 1-argument monkeypatch bound to them. The other three senders keep their arity, so their fixtures survive untouched.

- [x] `tests/unit/reflections/test_expectation_reconciler.py:204` — **UPDATE**: the `lambda j, e, m:` patch of `_escalate_once` must become `lambda p, j, e, m:` **and return a 2-tuple** — the return type changes from `bool` to `tuple[bool, str | None]` (Task 6), so a stub returning a bare `True` will unpack-error at the call site. One site.
- [x] `reflections/expectation_reconciler.py` call-site gates at 451-452, 481-482, 513-514 — **CREATE** a case in `tests/unit/reflections/test_expectation_reconciler.py`: `_reconcile_project` with an unresolvable project reaches an escalation site → `counts["escalated"] == 0`, an `alert-suppressed` entry in `findings`, no `escalated: {eid}` entry. No existing case pins the counter against a suppressed page.
- [x] `reflections/expectation_reconciler.py:476-483` — the short-circuited `elif` at the evidence site — **CREATE** a case in `tests/unit/reflections/test_expectation_reconciler.py` pinning that a **successful steer never escalates**. Drive `_reconcile_project` to the evidence branch with a live PM whose `_steer` returns `True`, then assert all three: `counts["escalated"] == 0`, no `escalated-evidence` entry in `findings`, and `_escalate_once` was **not called** (spy it, so the burned `SET NX` sentinel is observable — a suppressed-but-called escalation would otherwise look identical from the counters). `test_expectation_reconciler.py:139` covers the same branch today and asserts only that `steered-evidence` reached `findings`, so the hoisted-unpack restructure Task 6 warns against passes it unchanged. This case is what makes the hoist go red.
- [x] `reflections/sdlc_progress.py:1159-1172` — the `_escalate` closure in `_check_project_stalls` — **CREATE** a case in `tests/unit/reflections/test_sdlc_progress_check.py`. The closure currently gates `counts["escalated"] += 1` (line 1171) and `findings.append(msg)` (line 1172) behind one `if msg:` at line 1170; Task 7 splits them. Nothing in that module asserts on `counts["escalated"]` today — line 1878 only checks the four rung keys are present — so the split is unguarded until this case exists. Assert counter, finding, and summary together (see Failure Path Test Strategy).
- [x] `tests/unit/reflections/test_sdlc_progress_check.py:326` — **UPDATE**: `lambda msg: lab.alerts.append(msg)` → 2-arg form.
- [x] `tests/unit/reflections/test_sdlc_progress_check.py:48,1699-1706` — **UPDATE**: `_REAL_SEND_ALERT` is re-installed deliberately to put the real subprocess boundary under test; the restore and the call that follows both need the new arity.
- [x] `tests/unit/reflections/test_sdlc_progress_check.py:1855-1859` — **UPDATE**: `test_send_alert_swallows_filenotfound` calls `sdlc_progress._send_alert("hello")` directly; must pass a project dict.
- [x] `tests/integration/test_sdlc_stall_auto_resume_e2e.py:133` — **UPDATE**: `monkeypatch.setattr(sdlc_progress, "_send_alert", alerts.append)` — `list.append` is 1-arity and will raise on the new signature.
- [x] `tests/unit/test_sentry_triage_apply.py` (13 references) — **NO CHANGE EXPECTED**: `_send_telegram_notification(message)` keeps its arity. Any breakage here means the Solution's arity claim was wrong; treat it as a signal to re-read, not to edit fixtures.
- [x] `tests/unit/reflections/test_stall_advisory_reflection.py` (5 sites) — **NO CHANGE EXPECTED**, same reasoning. These are `patch.object(...)` autospec-free mocks and tolerate either way, so they are a weak signal; the real check is that `_send_alert`'s signature is genuinely unchanged.
- [x] `tests/unit/test_docs_auditor_substrate.py::TestTelegramChatRouting` (8 cases at lines 1554-1730, in a 176-test module) — **NO CHANGE EXPECTED**, and this is the load-bearing regression check on the `_resolve_notify_chat` lift. Verified on `da6e5789a` that all 8 patch exactly two docs_auditor-scoped names, `load_local_projects` and `subprocess.run`, and that the injected-seam delegation in Solution §2 carries both. If these need editing, the lift changed behavior and must be reworked rather than the tests adjusted.
- [x] `tests/unit/test_docs_auditor_substrate.py` — nine further cases patch `reflections.docs_auditor.PROJECT_ROOT` (lines 291, 308, 340, 361, 378, 1422, 1459, 1505, 1535, and again at 1747-2005) — **NO CHANGE EXPECTED**. None of them reaches the resolver: they either exercise unrelated paths or patch `_send_telegram_notification` wholesale. The `project_root` keyword seam exists so that stays true if one of them ever does.
- [x] `tests/unit/reflections/test_docs_auditor_git_surface.py` — **VERIFY ONLY**: PR #3077 touched it (14 lines); confirm the lift leaves it green.
- [x] `tests/unit/test_memory_consolidation.py::TestContradictionFlagging::test_contradiction_telegram_failure_writes_log_file` (line 550) — **NO CHANGE EXPECTED**, and this is the load-bearing regression check on Task 10. It patches `scripts.memory_consolidation.subprocess.run` to raise `CalledProcessError` and asserts `_write_contradiction_log` was called once. Task 10 leaves that `subprocess.run` and both handlers in place, so it must pass untouched. Needing to edit it means the transport contract was retired — stop and re-read Solution §3.
- [x] `tests/unit/test_memory_consolidation.py::TestContradictionFlagging::test_contradiction_sends_telegram_notification` (line 513) — **UPDATE**: it patches `subprocess.run` and asserts only that `"valor-telegram"` and `"send"` are in the argv, so it would stay green while reading the real `projects.json` through the new resolver — an ambient dependency this change introduces. Patch `scripts.memory_consolidation.resolve_host_eng_chat` to a fixed value and assert that value appears in the argv. Add a sibling suppression case to the same class: resolver patched to `None` → `subprocess.run` never called **and** `_write_contradiction_log` never called.
- [x] New: `tests/unit/reflections/test_utilities_eng_telegram.py` — **CREATE**: the two tables in Failure Path Test Strategy, alongside the existing `test_utilities_resolve_eng_group.py`.

## Rabbit Holes

- **Rewriting `valor-telegram`'s `resolve_chat` cascade.** Sending by numeric id sidesteps the ambiguity entirely. Do not go fix the cascade; that is its own issue if anyone wants it.
- **Making `stall_advisory` per-project.** It queries `AgentSession` globally by design (`_RUNNING_PROBE_STATUSES`), and a session's project is not the axis it classifies on. Turning it into a `run_per_project_audit` consumer is a redesign of the reflection, not a routing fix.
- **Splitting the `sentry_triage` digest per project.** Superficially attractive — the digest already carries a `[{proj}]` per finding. But the class counters, the auto-action block, the per-run filing cap, and the new-issue suppression state are all computed once per run across every project. Per-project digests mean re-deriving all of that. Out of scope; note it as a possible follow-up issue and move on.
- **"Simplifying" the `PROJECT_ROOT`-narrowed fallback into an unconditional default.** `docs_auditor`'s docstring warns against exactly this, in those words. Lifting the function is the moment someone will be tempted. The narrowing is the fix; an unconditional default recreates the bug.
- **Chasing the `Eng:` prefix convention into `bridge/routing.py` and `_resolve_persona`.** `resolve_eng_group` keys off the same prefix those do. Reading them to confirm the convention is fine; changing them is not this issue.
- **Repointing `docs/features/*.md` chat-resolution prose.** The blast-radius tool flags nine docs sections mentioning `Eng:` or `resolve_chat`. Most describe the bridge's inbound routing, which this change does not touch. Only the docs listed under `## Documentation` are in scope.

## Risks

- **Alerts can now go nowhere.** A project with no configured `Eng:` group gets silence instead of a misrouted page. Mitigated by the warning log plus the `findings`/`summary` thread-through, but "no message arrived" stops being proof of health. This is an accepted trade, matching #2754.
- **No local end-to-end verification is possible.** Every project in this machine's `projects.json` carries an empty `telegram` block (spike-4), so only the suppression branch is observable here. Resolution correctness rests entirely on unit tests against synthetic dicts. A reviewer cannot confirm the happy path by running anything on this box, and should not be asked to.
- **The `_resolve_notify_chat` lift touches three-day-old code.** PR #3077 merged 2026-09-02 with 248 lines of new tests. The lift is safe only insofar as `TestTelegramChatRouting` passes **unmodified**. Any need to edit those 8 cases is a stop-and-rethink signal. The mechanical hazard — a lifted function's unqualified `load_local_projects()` resolving against the wrong module's globals and stepping out from under `patch("reflections.docs_auditor.load_local_projects", ...)` — is closed by the injected `load_projects` seam in Solution §2. Task 2 verifies it before anything else is written.
- **`reflections/docs_auditor.py` has two other lanes aimed at it.** #2743 (`Delete _write_liveness from docs_auditor`) and #3050 (`docs_auditor: rotation write path has no restore/escalation`) are both open. Measured on `da6e5789a`: neither has an open PR touching the file, so there is no live source diff to merge against right now — but that is a snapshot, not a guarantee, and both lanes are expected to produce one. Mitigation is Task 0's re-measure plus the bounded three-hunk diff: this plan's only edits to that file are the import line, the deleted constant at line 44, and the collapsed resolver body. Neither sibling lane's stated scope (`_write_liveness`, the rotation write path) overlaps those three regions, so a conflict is unlikely and, if it happens, is textually local. Land this lane's `docs_auditor` hunks early rather than at the end of the build, so the sibling lanes rebase onto them instead of the reverse.
- **`scripts/memory_consolidation.py` has a stronger error contract than the helper offers, so it keeps its own transport.** It uses `check=True` and routes `CalledProcessError` — plus any other exception — to `_write_contradiction_log`, so a contradiction survives a bridge outage on disk. `send_host_eng_telegram` swallows transport failures and returns `True`, and returns `False` only for an unresolved destination, which for this caller is unreachable anyway (no `repo_root`, so rung 3 always returns `FALLBACK_ENG_CHAT`). Keying the fallback-log write off the helper's return value would therefore retire the guarantee while looking like it preserved it. Mitigation is the split in Solution §3: the resolver moves, the `subprocess.run` and both handlers stay. Task 10 and Success Criterion 7 are written against the transport axis, and `test_contradiction_telegram_failure_writes_log_file` passing unmodified is the check.
- **`list.append` as a monkeypatch is arity-fragile.** `tests/integration/test_sdlc_stall_auto_resume_e2e.py:133` binds `alerts.append` directly to `_send_alert`. It will raise, not silently pass — which is the good outcome, but it means an integration test fails in a way that looks unrelated to routing.
- **Concurrent `docs/plans/` edits.** Observed during this very plan: a peer lane's `git add -A` swept an uncommitted section of this file into its own commit. Commit each section as it lands; never leave the plan dirty across an await.

## Race Conditions

None introduced. Every changed path is synchronous, single-threaded `subprocess.run` inside a reflection tick, and no shared mutable state is added.

Two pre-existing orderings are worth naming so the change does not disturb them:

- **`expectation_reconciler._escalate_once`** claims its `SET NX` sentinel *before* sending. If the send then suppresses, the sentinel is already burned and no retry happens for that `(job, eid)` until the TTL lapses. This plan does not change the ordering — reversing it would re-open the double-page window the sentinel exists to close — but the suppression finding is what makes the burned sentinel legible.
- **`sdlc_progress._escalate_once`** has the same shape via `_escalation_set(slug, sha)`, and its docstring already documents "under-alert during a flap beats spam during one". Suppression is consistent with that stance.

## No-Gos (Out of Scope)

- **Prompt-text references to the chat name.** `reflections/agents/circuit_health_gate.py:64,75` and `reflections/agents/system_health_digest.py:7,137` name `'Eng: Valor'` inside LLM prompt strings, not argv. Different fix shape, explicitly excluded by the issue. They are single-quoted, so they also sit outside any double-quoted grep sweep.
- **Deleting the narrowed fallback.** `FALLBACK_ENG_CHAT = "Eng: Valor"` relocates from `docs_auditor.py:44` to `utilities.py`; it is not removed. It plus the docstring mention at `docs_auditor.py:1569` are #2754's deliberate narrowing, and the supervisor has confirmed (Open Question 2) that it is the model to generalize rather than a defect. Any "clean grep" success criterion must exempt that one named constant, or it will demand undoing a merged fix and silencing the two host-machine digests on the production machine. The two criteria this plan uses are the **argv adjacency** `"--chat", "Eng: Valor"` and the absence of any *per-module* chat-name constant — see Success Criteria 1 and 2.
- **Per-project `sentry_triage` digests** and **per-project `stall_advisory`**. Named in Rabbit Holes; both are reflection redesigns.
- **`valor-telegram`'s chat-resolution cascade.** Untouched.
- **Bridge inbound routing and persona resolution.** `bridge/routing.py`, `_resolve_persona`, and the `Eng:` prefix convention they share with `resolve_eng_group` stay as they are.
- **`scripts/memory_consolidation.py` — IN scope, decided.** It is the fifth argv site (line 352) and the issue's own exit criterion is a grep sweep that fails while it stands. Including it costs one call-site swap plus preserving the `CalledProcessError` fallback contract. Excluding it would leave the lane unable to close on its stated criterion. This is a scope *addition* relative to the issue body's four-site list and is flagged for the critique to confirm.

## Update System

No update-system changes required. This adds no dependency, no config key, no service, and no new file that must reach other machines beyond the ordinary `git pull` that `/update` already performs. `projects.json` is iCloud-synced and per-machine; this change reads it and does not alter its schema.

One deployment note that is **not** an update-script change but must be said: the fleet machines that actually run these reflections need their projects' `telegram.groups."Eng: X".chat_id` populated for resolution to succeed. Where it is absent, the new behavior is suppression-with-a-warning rather than a misroute. That is the intended outcome, and it is visible in the reflection `summary`, so no migration or backfill is required — but an operator seeing alerts stop should look there first.

No Popoto model changes, so no `scripts/update/migrations.py` entry.

## Agent Integration

No agent integration required. This is entirely internal to the reflection modules.

- **No new CLI entry point.** Nothing is added to `pyproject.toml [project.scripts]`. The existing `valor-telegram` console script is the transport and its interface is unchanged — the same `send --chat <dest> <message>` argv, with `<dest>` now a numeric id string instead of a group name.
- **No bridge import.** `bridge/telegram_bridge.py` does not call any of these five modules; the reflection scheduler does, out of process.
- **The agent's reachable surface is unchanged.** The only observable difference to a human in Telegram is *which* chat an alert lands in, which is the point.

## Documentation

- [x] Update `docs/features/expectation-reconciler.md` — the escalation step (line 40, "action failed → escalate once (Telegram operator alert), stop") must say the alert is addressed to the project's own `Eng:` group by numeric `chat_id`, and that it is suppressed with a finding when no group resolves.
- [x] Update `docs/features/stall-advisory-classifier.md` — the Telegram Alert Flag section (lines 140-142) and the component table (line 156) must record that the alert now goes to the host checkout's engineer group via `resolve_host_eng_chat`, with the `PROJECT_ROOT`-narrowed fallback.
- [x] Update `docs/features/sentry-triage.md` — the "Telegram digest" section (line 120) must record the same host-machine resolution, and that a foreign or unregistered checkout suppresses the digest rather than sending it to `Eng: Valor`.
- [x] Update `docs/features/docs-auditor.md` — lines 50-55 describe `_resolve_notify_chat`'s ladder inline. After the lift, point them at `reflections/utilities.py::resolve_host_eng_chat` as the owner of the rule, keeping the behavior description intact.
- [x] Create `docs/features/reflection-telegram-routing.md` — one page describing the two entry points (`send_eng_telegram`, `send_host_eng_telegram`), the id-not-name rule and why, the `PROJECT_ROOT`-narrowed fallback and the warning against unconditionalizing it, the `True`/`False` return contract, and the table of the five consumer modules.
- [x] Add `reflection-telegram-routing` to the `docs/features/README.md` index table.

## Success Criteria

1. `git grep -n '"--chat", "Eng: Valor"' -- '*.py'` returns **nothing**. This is the argv-adjacency form, not the bare literal — it deliberately spares `docs_auditor`'s named `FALLBACK_ENG_CHAT` constant and its docstring, which are #2754's merged narrowing.
2. `git grep -lF '"Eng: Valor"' -- '*.py' | grep -v '^tests/' | grep -v '^reflections/agents/'` returns exactly **one** path: `reflections/utilities.py`, where `FALLBACK_ENG_CHAT` now lives. **Six** paths today (measured on `da6e5789a`, enumerated in Freshness Check note 2); one after.

   The sixth of those six is `reflections/docs_auditor.py` itself, whose `FALLBACK_ENG_CHAT` at line 44 predates this plan. The lift relocates that constant; it does not add or remove one. The end state is one named constant in one shared module.

   Zero is not the target, and that is a decision rather than a shortfall. Issue comment `5505275340` asks for the bare literal as a *per-module* constant to be swept, and after this change no per-module constant remains — the one survivor is the shared fallback in the shared helper. Reaching literal zero would mean deleting `FALLBACK_ENG_CHAT`, which reverts #2754's `PROJECT_ROOT`-narrowed fallback and silences `sentry_triage` and `stall_advisory` on the production machine. Answered by the supervisor in Open Question 2: keep the fallback.
3. `reflections/utilities.py` exports `send_eng_telegram`, `send_host_eng_telegram`, and `resolve_host_eng_chat`.
4. `tests/unit/test_docs_auditor_substrate.py::TestTelegramChatRouting` — all 8 cases pass **with no edits to the test file**, and `git diff --stat` on that path is empty. This is the regression check on the lift, and it is only achievable because `_resolve_notify_chat` passes `load_projects=load_local_projects` through from its own module namespace (Solution §2). A green run here with an edited test file is a failed criterion, not a passed one.
5. New `tests/unit/reflections/test_utilities_eng_telegram.py` covers every row of both Failure Path Test Strategy tables, including the two mutation anti-tests.
6. A suppressed alert appears in the reflection's `findings` and `summary` for both `expectation_reconciler` and `sdlc_progress`, pinned by test.
7. `scripts/memory_consolidation.py` still writes `logs/memory-contradictions.log` on a **transport** failure, pinned by test. The pinning test drives the transport, not the resolver: `scripts.memory_consolidation.subprocess.run` patched to raise `CalledProcessError` (or `FileNotFoundError` / `TimeoutExpired`, or to return `returncode=1` under `check=True`), asserting `_write_contradiction_log` was called exactly once. `tests/unit/test_memory_consolidation.py::TestContradictionFlagging::test_contradiction_telegram_failure_writes_log_file` (line 550) already has exactly this shape and must pass **unmodified** — that is the criterion. A test that drives a `False`/`None` resolution result does not satisfy this criterion, because resolution failure and transport failure are different axes and only the second one is what the fallback log exists for.

   The suppression branch gets its own separate pin: `resolve_host_eng_chat` patched to return `None` → `subprocess.run` never called, `_write_contradiction_log` never called, warning logged.
8. `scripts/pytest-clean.sh tests/unit/reflections/ tests/unit/test_sentry_triage_apply.py tests/unit/test_docs_auditor_substrate.py` is green.
9. `python -m ruff check` and `python -m ruff format --check` clean on the changed files.
10. The six Documentation checkboxes are done, and `docs/features/README.md` indexes the new page.

## Team Orchestration

Single builder. The five source files share one new helper, and the helper's exact signature is the thing every call site depends on — splitting this across parallel agents would have them converging on each other's design, which is the shared-worktree livelock shape. Sequence it instead: helper first, then consumers, then fixtures, then docs.

If a second agent is available, the one genuinely disjoint slice is the **documentation set** (six files, no source dependency once the Solution is settled). That can run in parallel with the fixture sweep, with an explicit file-level ownership split: docs agent owns `docs/features/*`, builder owns `reflections/`, `scripts/`, and `tests/`.

## Step by Step Tasks

0. **Re-measure the `docs_auditor.py` coordination surface before writing anything.** Lanes #2743 and #3050 both intend to edit this file. On `da6e5789a` neither has an open PR touching it — `gh pr list --state open --json number,files --jq '.[] | select(.files[].path == "reflections/docs_auditor.py") | .number'` returned empty. Re-run that at build time. If either lane now has a diff landing inside line 36 (the import), line 44 (`FALLBACK_ENG_CHAT`), or lines 1545-1645 (`_resolve_notify_chat` and `_send_telegram_notification`), rebase onto the post-merge file and redo Tasks 1-2 against it rather than assuming those line numbers held. This is a five-second check that prevents a three-way merge on a 2000-line file.
1. **Lift the host resolver into `reflections/utilities.py`.** Move `FALLBACK_ENG_CHAT` from `docs_auditor`. Add `resolve_host_eng_chat(repo_root=None, *, load_projects=None, project_root=None) -> str | None` carrying every rung and every comment from `docs_auditor._resolve_notify_chat`. Both keyword seams default to `utilities`' own module bindings, resolved at call time inside the body — never in the signature default, which would freeze them at import.
2. **Reduce `docs_auditor._resolve_notify_chat` to the three-line delegation** in Solution §2, passing `load_projects=load_local_projects` and `project_root=PROJECT_ROOT` — the module-scoped names, read in a body that lives in `docs_auditor`, which is what keeps `patch("reflections.docs_auditor.load_local_projects", ...)` effective. Keep a `FALLBACK_ENG_CHAT` re-export binding. Then run `scripts/pytest-clean.sh tests/unit/test_docs_auditor_substrate.py -q` — **all 176 tests green with zero test edits**, and `git diff --stat tests/unit/test_docs_auditor_substrate.py` empty — or stop and rethink the lift. Do this before Task 3; it is cheapest to abandon the lift while nothing depends on it.

   **Then, immediately, run mutation anti-test 2** (Failure Path Test Strategy) — it lives here rather than in Task 5 because it only bites once the delegation exists. Delete the `if target == PROJECT_ROOT.resolve():` clause from the lifted `resolve_host_eng_chat` so rung 3 returns `FALLBACK_ENG_CHAT` unconditionally, run `scripts/pytest-clean.sh 'tests/unit/test_docs_auditor_substrate.py::TestTelegramChatRouting' -q`, and confirm **exactly these three** go **red** — `test_foreign_unregistered_repo_suppresses_send` (line 1631), `test_registered_repo_with_malformed_group_suppresses_send` (line 1644), and `test_load_local_projects_raising_falls_back_for_project_root_only` (line 1663) — for a run of **3 failed, 5 passed**. `test_foreign_registered_repo_routes_to_its_own_group` (line 1587) is **expected to stay green**: its fixture resolves at rung 2 and never reaches the mutated guard, so do not read its passing as a failed mutation. Restore the clause and confirm 8 passed. Fewer than three red means the lift carried the code but not the guarantee, and the narrowing is the whole fix — treat it the same as a failed lift.
3. **Add `send_eng_telegram(project, message, *, logger_prefix) -> bool`** to `reflections/utilities.py`, with **two separate try/except scopes** — see the illustrated body in Solution §1, which this task restates without widening:

   - **Around the resolver call only.** `resolve_eng_group(project)` raising → warn and `return False`, **no subprocess**. `resolve_eng_group(project)` returning `None` → the same warn-and-`return False`, **no subprocess**. Both are "nothing resolved, nothing was sent", and the Failure Path table's "`resolve_eng_group` raises" row pins the first of them.
   - **Around the `subprocess.run` call only.** `FileNotFoundError`, `subprocess.TimeoutExpired`, any other `Exception`, or a non-zero `returncode` → warn and `return True`. A destination resolved and a send was attempted; that is what `True` asserts.

   Do **not** write one try/except spanning both calls. That is the shortest thing to type and it returns `True` (or lets the exception escape) on a raising resolver, inverting Solution §1's "`False` means and only means nothing resolved, nothing was sent" and contradicting the Failure Path table. The argv carries `str(chat_id)`, never `group_name`.
4. **Add `send_host_eng_telegram(message, *, logger_prefix) -> bool`** wrapping `resolve_host_eng_chat` with the same swallow-and-return contract.
5. **Write `tests/unit/reflections/test_utilities_eng_telegram.py`** covering both tables from Failure Path Test Strategy. Then run **mutation anti-test 1** — revert `str(chat_id)` to `group_name` in `send_eng_telegram`, confirm the resolves-case goes red, restore. Do it now, before any consumer is wired. (Anti-test 2, the `PROJECT_ROOT` guard deletion, ran back in Task 2 — it needs the docs_auditor delegation in place to bite. Both are specified in Failure Path Test Strategy.)
6. **Wire `expectation_reconciler`**: `_escalate_once(job_id, eid, message)` (line 170) → `_escalate_once(project, job_id, eid, message)`, body calling `send_eng_telegram(project, message, logger_prefix="expectation_reconciler")`. Its return type changes from `bool` to `tuple[bool, str | None]` for the reason in Solution §4 — the three call sites each gate a counter and a finding behind that one boolean, and suppression must reach the finding without touching the counter. All three sites sit inside `_reconcile_project(project: dict)`, which already binds `project_key` at line 372, so the dict needs no threading (spike-1).

   **Sites 444 (gated by 451-452) and 506 (gated by 513-514) take the recipe as written.** Both are plain `if _escalate_once(...):`, so each becomes `sent, sup = _escalate_once(project, ...)` followed by `if sent:` → the existing `counts["escalated"] += 1` plus that site's existing finding string, `elif sup:` → `findings.append(sup)`.

   **Site 480 cannot take that recipe, and the shortest restructure that compiles is wrong.** Lines 476-483 are a short-circuited chain: the call is an `elif`, reached only when steering did not happen or failed.

   ```python
   if pm is not None and _steer(pm, message):          # 476
       counts["steered"] += 1                          # 477
       findings.append(f"steered-evidence: {eid} ({evidence})")   # 478
       _bump_attempts(job.job_id, eid)                 # 479
   elif _escalate_once(job.job_id, eid, f"[{project_key}] {message}"):   # 480
       counts["escalated"] += 1                        # 481
       findings.append(f"escalated-evidence: {eid}")   # 482
   continue                                            # 483
   ```

   A tuple unpack cannot live in an `elif` condition, so this site must be restructured. **Do not hoist the unpack above line 476.** That is the shortest edit that compiles and it calls `_escalate_once` unconditionally — burning the `SET NX` sentinel (claimed before the send, see Race Conditions) and paging a human even when the steer succeeded. Convert the `elif` into a nested `else` instead, so the call stays inside the failure branch:

   ```python
   if pm is not None and _steer(pm, message):
       counts["steered"] += 1
       findings.append(f"steered-evidence: {eid} ({evidence})")
       _bump_attempts(job.job_id, eid)
   else:
       sent, sup = _escalate_once(
           project, job.job_id, eid, f"[{project_key}] {message}"
       )
       if sent:
           counts["escalated"] += 1
           findings.append(f"escalated-evidence: {eid}")
       elif sup:
           findings.append(sup)
   continue
   ```

   Nothing today catches the hoist: `tests/unit/reflections/test_expectation_reconciler.py:139` asserts `steered-evidence` is in `findings` and never asserts that no alert was sent, so a hoisted unpack passes it. The pin is the new short-circuit case in `## Test Impact` — a successful steer must leave `counts["escalated"] == 0`, must not append an `escalated-evidence` finding, and must not burn the sentinel.
7. **Wire `sdlc_progress`, and change `_escalate_once`'s return type to a 2-tuple.** Three coupled edits, all in `reflections/sdlc_progress.py`:

   - `_send_alert(message)` (line 784) → `_send_alert(project_dict, message) -> bool`, body calling `send_eng_telegram(project_dict, message, logger_prefix="sdlc_progress")` and returning its result.
   - `_escalate_once` (line 750) gains a `project_dict: dict` keyword — it already takes `project=project_key` as a *string* that feeds the message text; keep that, it is a different thing. **Its return type changes from `str | None` to `tuple[bool, str | None]`**, `(sent, msg)`:
     - sentinel already burned or Redis unreadable → `(False, None)` — nothing to report, nothing to count (unchanged behavior, just re-typed).
     - `_send_alert` returned `True` → `(True, message)`.
     - `_send_alert` returned `False` (no `Eng:` group resolved) → `(False, f"alert-suppressed: no Eng: group for {project} ({slug}@{sha[:8]})")`. Update the docstring, which currently promises "the message that was sent, or None".
   - **The `_escalate` closure inside `_check_project_stalls` (lines 1159-1172) must be edited, and this is the part a builder following only the table would miss.** It currently collapses both outcomes into one gate at lines 1170-1172 — `if msg:` → `counts["escalated"] += 1; findings.append(msg)`. One boolean cannot express "have something to report" and "told a human" independently. Split it:

     ```python
     sent, msg = _escalate_once(project_dict=project, project=project_key, ...)
     if msg:
         findings.append(msg)
     if sent:
         counts["escalated"] += 1
     ```

     The tuple is chosen over branching on an `"alert-suppressed"` string prefix deliberately: a prefix makes the counter's correctness depend on message wording, and the suppression string is exactly the kind of text a later edit rewords without realizing a counter reads it.

   `_escalate_once` has exactly one call site — this closure (line 1160) — so the return-type change has no other blast radius. `project` (the dict) is already bound on the same frame in `_check_project_stalls`; nothing new needs threading (spike-1).
8. **Wire `sentry_triage`**: `_send_telegram_notification` body → `send_host_eng_telegram(message, logger_prefix="sentry_triage")`. Arity unchanged.
9. **Wire `stall_advisory`**: `_send_alert` body → `send_host_eng_telegram(message, logger_prefix="stall_advisory")`. Arity unchanged.
10. **Wire `scripts/memory_consolidation.py:352` to `resolve_host_eng_chat` only — do NOT route it through `send_host_eng_telegram`.** In `_flag_contradiction` (line 333), replace the literal `"Eng: Valor"` in the argv at line 352 with the return of `resolve_host_eng_chat()`, and guard: `chat = resolve_host_eng_chat()`; if `chat is None`, log a warning naming the suppression and return without sending *and without* writing the contradiction log. Otherwise keep the existing `subprocess.run([..., "--chat", chat, telegram_msg], check=True, capture_output=True, timeout=10)` verbatim, and keep **both** existing handlers untouched — `except subprocess.CalledProcessError` (line 357) and `except Exception` (line 365), each already calling `_write_contradiction_log(ids, rationale, contents)`. The transport-failure → log-file guarantee must come out of this task byte-identical in behavior; only the destination changes. See "Why `memory_consolidation` keeps its own `subprocess.run`" in Solution §3.
11. **Sweep the fixtures and add the new cases** listed in Test Impact — sixteen entries. Edit: `test_expectation_reconciler.py:204` (new arity **and** 2-tuple return), `test_sdlc_progress_check.py:48,326,1699-1706,1855-1859`, `test_sdlc_stall_auto_resume_e2e.py:133`, `test_memory_consolidation.py:513`. Create: the suppression-counter case in `test_sdlc_progress_check.py`, the mirror in `test_expectation_reconciler.py`, the short-circuit pin in `test_expectation_reconciler.py` (a successful steer escalates nothing — Task 6), and the resolver-suppression case in `test_memory_consolidation.py`. Confirm `test_sentry_triage_apply.py`, `test_stall_advisory_reflection.py`, and `test_memory_consolidation.py:550` need **no** edits — if they do, either the arity claim in the Solution or the transport-contract split in Task 10 was wrong; re-read rather than adjusting the fixture.
12. **Run the grep criteria** (Success Criteria 1 and 2) and record the actual output, not a claim about it.
13. **Documentation**: the six items under `## Documentation`.
14. **`python -m ruff check` and `python -m ruff format`** on changed files. Formatting only, no linting beyond ruff's own check.

## Verification

| Check | Command | Expected |
|---|---|---|
| No hardcoded chat argv anywhere | `git grep -n '"--chat", "Eng: Valor"' -- '*.py'` | no output, exit 1 |
| Literal survives in exactly one non-test file | `git grep -lF '"Eng: Valor"' -- '*.py' \| grep -v '^tests/' \| grep -v '^reflections/agents/' \| wc -l` | `1` (`reflections/utilities.py`); `6` on `da6e5789a` before the change |
| Helpers exist | `git grep -n 'def send_eng_telegram\|def send_host_eng_telegram\|def resolve_host_eng_chat' reflections/utilities.py` | three matches |
| Lift is behavior-preserving | `scripts/pytest-clean.sh tests/unit/test_docs_auditor_substrate.py -q` | green, and `git diff --stat tests/unit/test_docs_auditor_substrate.py` is empty |
| Lift preserves the routing patch seam | `scripts/pytest-clean.sh 'tests/unit/test_docs_auditor_substrate.py::TestTelegramChatRouting' -q` | 8 passed — run this first, immediately after Task 2 |
| docs_auditor diff stayed bounded | `git diff --stat main -- reflections/docs_auditor.py` and `git diff main -- reflections/docs_auditor.py \| grep -c '^@@'` | three hunks: import, deleted constant, collapsed resolver |
| New helper coverage | `scripts/pytest-clean.sh tests/unit/reflections/test_utilities_eng_telegram.py -q` | green |
| Consumer suites | `scripts/pytest-clean.sh tests/unit/reflections/ tests/unit/test_sentry_triage_apply.py -q` | green |
| Contradiction log survives a transport failure | `scripts/pytest-clean.sh 'tests/unit/test_memory_consolidation.py::TestContradictionFlagging' -q` | 2 passed, and `git diff --stat tests/unit/test_memory_consolidation.py` shows only the added suppression case — `test_contradiction_telegram_failure_writes_log_file` unedited |
| memory_consolidation sends by id, not name | `git grep -n 'resolve_host_eng_chat' scripts/memory_consolidation.py` and `git grep -n 'check=True' scripts/memory_consolidation.py` | one resolver call; the `check=True` subprocess and both `_write_contradiction_log` handlers still present |
| Auto-resume integration | `scripts/pytest-clean.sh tests/integration/test_sdlc_stall_auto_resume_e2e.py -q` | green |
| Mutation 1: name instead of id (**Task 5**) | swap `str(chat_id)` → `group_name` in `send_eng_telegram`, run `tests/unit/reflections/test_utilities_eng_telegram.py` | **red**; restore and re-run green |
| Mutation 2: unconditional fallback (**Task 2**) | drop the `if target == PROJECT_ROOT.resolve():` clause in `resolve_host_eng_chat`, run `scripts/pytest-clean.sh 'tests/unit/test_docs_auditor_substrate.py::TestTelegramChatRouting' -q` | **3 failed, 5 passed** — red: `test_foreign_unregistered_repo_suppresses_send` (:1631), `test_registered_repo_with_malformed_group_suppresses_send` (:1644), `test_load_local_projects_raising_falls_back_for_project_root_only` (:1663). `test_foreign_registered_repo_routes_to_its_own_group` (:1587) stays green by design. Restore, 8 passed |
| Format | `python -m ruff check` and `python -m ruff format --check` on changed files | clean |

**Manual check that cannot be automated here:** confirming a real alert lands in the right group requires a machine whose `projects.json` carries populated `telegram.groups` (spike-4 — this one does not). Defer to the operator on a fleet machine after deploy; do not gate the lane on it and do not claim it was done.

## Critique Results

**Verdict (round 3): READY TO BUILD (with concerns)** — 0 blockers, 3 concerns, 2 nits. War room ran FULL depth with an independent roster of 3 critics; roster gate 3/3, all grounded. This is the final critique round: the lane sat at the G2 cycle cap and the owner authorized exactly one revision-plus-critique round past it, so the concerns below are accepted on the record and the build proceeds with them. Each one carries an Implementation Note the builder applies in place of a further revision pass.

**All five round-2 rows were re-verified as genuinely resolved against the checkout, not taken from the plan's prose.** The blocker: `scripts/memory_consolidation.py:351-369` today runs `subprocess.run([...], check=True, capture_output=True, timeout=10)` with a `CalledProcessError` handler at 357 and a bare `except Exception` at 364, each calling `_write_contradiction_log` — and the rewritten Task 10 preserves that block byte-for-byte while changing only the destination, so the transport guarantee survives. Success Criterion 7 now drives the transport axis and names `test_contradiction_telegram_failure_writes_log_file` (`tests/unit/test_memory_consolidation.py:550`, which patches `subprocess.run` to raise `CalledProcessError` and asserts `_write_contradiction_log` was called) as the pin — the correct axis. Concern 1: the `_escalate` closure at `reflections/sdlc_progress.py:1159-1172` does collapse both outcomes behind `if msg:` at 1170-1172, and the plan's 2-tuple shape does split them. Concern 2 and the two nits: see the findings below and the structural re-measurement paragraph after the table.

**The round-3 revision's own new claim is confirmed true.** `reflections/expectation_reconciler.py` does carry the identical counter/finding collapse at all three `_escalate_once` call sites — the call at 444 gated by `counts["escalated"] += 1` at 451 and `findings.append` at 452, the call at 480 gated by 481-482, and the call at 506 gated by 513-514 — with `_escalate_once` declared `-> bool` at line 170. The revising agent's self-correction, not its first draft, is the accurate one, and extending the 2-tuple there is warranted.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| CONCERN | Critique driver (structural + empirical mutation replay) | **Anti-test 2 names a test that does not go red under its own mutation.** Task 2, the Failure Path Test Strategy "Anti-test 2" paragraph, and the Verification table's Mutation 2 row all assert that deleting the `if target == PROJECT_ROOT.resolve():` clause makes both `test_foreign_registered_repo_routes_to_its_own_group` (`tests/unit/test_docs_auditor_substrate.py:1587`) and `test_foreign_unregistered_repo_suppresses_send` (`:1631`) go red. Replayed empirically against the checkout: test 1587 **stays green**. Its fixture is a registered foreign repo *with* a valid `Eng: Popoto` group, so it resolves at rung 2 and returns at `reflections/docs_auditor.py:1585`, never reaching the rung-3 guard at 1590. Only tests whose fixtures fall through to rung 3 can observe the mutation. A builder following the plan literally sees half the predicted result and is left deciding whether the lift failed. | **APPLIED** — `## Failure Path Test Strategy` anti-test 2 paragraph rewritten to name the three cases with their line numbers and the `3 failed, 5 passed` expected result, plus an explicit "1587 stays green and must not be named here" clause and the carry-forward rule; Task 2's mutation paragraph rewritten to the same three; the Verification table's Mutation 2 row rewritten to the same three. The three names were re-derived from the current checkout at `:1631`, `:1644`, `:1663` and **confirmed by replaying the mutation** (`if target == PROJECT_ROOT.resolve():` → `if True:`), which produced exactly `3 failed, 5 passed` with `test_foreign_registered_repo_routes_to_its_own_group` green; the file was restored and `TestTelegramChatRouting` re-run at `8 passed`, `git status` clean on `reflections/`. | The mutation is still a real guard — it just bites through different cases. Replace the named pair with the three that actually go red, all verified by replay: `test_foreign_unregistered_repo_suppresses_send` (:1631, asserts `sent is False` and `run.assert_not_called()`), `test_registered_repo_with_malformed_group_suppresses_send` (:1644, same assertions via the empty-`groups` royop shape), and `test_load_local_projects_raising_falls_back_for_project_root_only` (:1663, asserts `sent_foreign is False`). Drop 1587 from the expectation. Keep the restore-and-confirm-8-passed step unchanged. The rule to carry forward: a mutation anti-test may only name cases whose fixtures reach the mutated line. |
| CONCERN | Critique driver (call-site re-read at `reflections/expectation_reconciler.py:476-483`) | **Task 6's rewrite recipe cannot be applied literally at the second of the three call sites, and the obvious workaround is silently wrong.** Task 6 says each site becomes `sent, sup = _escalate_once(project, ...)` followed by `if sent:` / `elif sup:`. That works at 444 and 506, which are plain `if _escalate_once(...):`. The site at 480 is an **`elif`** — `if pm is not None and _steer(pm, message): ... elif _escalate_once(...):` — so the call is short-circuited and runs only when steering failed. A tuple unpack cannot live in an `elif` condition, so the builder must restructure; hoisting the unpack above the `if pm ...` line is the shortest restructure that compiles, and it calls `_escalate_once` unconditionally — burning the `SET NX` sentinel and paging a human even when the steer succeeded. No existing test catches it: `tests/unit/reflections/test_expectation_reconciler.py:139` asserts `steered-evidence` is in `findings` and never asserts that no alert was sent. | **APPLIED** — Task 6 restructured: sites 444 and 506 keep the recipe, and site 480 gets the verbatim `reflections/expectation_reconciler.py:476-483` block quoted with line numbers, an explicit "do not hoist the unpack above 476" warning naming the burned `SET NX` sentinel, and the nested-`else` replacement in full. `## Test Impact` gains a CREATE entry pinning the short-circuit (successful steer → `counts["escalated"] == 0`, no `escalated-evidence` finding, `_escalate_once` spied and not called), taking the section to 16 entries; `## Appetite` and Task 11's counts updated to match. | At site 480 the call must stay inside the failure branch. Convert the `elif` into a nested `else`, not a hoisted unpack: `if pm is not None and _steer(pm, message): counts["steered"] += 1; findings.append(f"steered-evidence: {eid} ({evidence})"); _bump_attempts(...)` / `else: sent, sup = _escalate_once(project, job.job_id, eid, f"[{project_key}] {message}"); if sent: counts["escalated"] += 1; findings.append(f"escalated-evidence: {eid}"); elif sup: findings.append(sup)`. Sites 444 and 506 take the recipe as written. Add an assertion to the new suppression case that a successful steer leaves `counts["escalated"] == 0`, so the hoist cannot pass. |
| CONCERN | Risk & Robustness (Skeptic) | **Task 3's swallow instruction is unscoped and, read as one blanket try/except, inverts the return contract.** Solution §1's illustrated body wraps only `subprocess.run` in error handling, but Task 3 says flatly: "Swallow `FileNotFoundError` / `TimeoutExpired` / `Exception` / non-zero exit and return `True`." The Failure Path Test Strategy table requires the opposite mapping for the other call — its "`resolve_eng_group` raises" row demands swallow-to-**`False`** with no subprocess. `resolve_eng_group` already swallows its own internal exceptions and returns `None` (`reflections/utilities.py:339-341`), so that row only fires when a test monkeypatches the function object to raise. A single try/except spanning both calls returns `True` (or lets the exception escape) on a raising resolver, contradicting Solution §1's "`False` means and only means nothing resolved, nothing was sent". | **APPLIED** — Task 3 rewritten as two explicitly separated bullets (resolver scope → warn and `False`, no subprocess, covering both `None` and a raise; transport scope → warn and `True`, covering `FileNotFoundError` / `TimeoutExpired` / `Exception` / non-zero exit) with a "do not write one try/except spanning both" instruction. Solution §1's illustrated body was rewritten to show the two scopes as separate blocks, and its return-contract sentence now states that `False` covers both `None` and a raising resolver — byte-consistent with the Failure Path table's "`resolve_eng_group` raises → swallowed; `False`; no subprocess" row. | Two distinct try/except scopes, not one. Around the resolver only: `try: resolved = resolve_eng_group(project) except Exception: log.warning(...); return False` — the same branch as the `resolved is None` case, no subprocess. Around the transport only: the `subprocess.run([...])` invocation gets its own handler swallowing `FileNotFoundError` / `subprocess.TimeoutExpired` / `Exception` / a non-zero `returncode` and returning `True`. The pin is the "`resolve_eng_group` raises" row of the new `tests/unit/reflections/test_utilities_eng_telegram.py`. |
| NIT | Scope & Value (Simplifier) | `reflections/docs_auditor.py::_send_telegram_notification` (lines 1602-1642) publishes the identical contract to the new `send_host_eng_telegram` — `False` only on no-destination, `True` on any swallowed transport failure — and keeps its own `subprocess.run` rather than delegating, but the plan never says why. `scripts/memory_consolidation.py` gets a full justifying subsection for the same choice, so a reader is left to reverse-engineer why one duplicate sender was explained and the functionally identical one was not. | **APPLIED** — Solution §2 gains a "Why `docs_auditor._send_telegram_notification` stays where it is" paragraph. Kept rather than folded, for two reasons: it is parameterized by `repo_root` (the point of #2754 — it addresses foreign repos), where `send_host_eng_telegram` deliberately has no such knob; and all 8 `TestTelegramChatRouting` cases patch `reflections.docs_auditor.subprocess.run`, so moving that call breaks Success Criterion 4 and adds a fourth hunk to a file this plan holds to three while lane #2743 is live. The bounded diff is preserved. | Nit — exempt, but stating the reason costs one paragraph and removes the asymmetry. |
| NIT | History & Consistency (Consistency Auditor) | The Freshness Check names `da6e5789a` as "the hash to reproduce against" and reconciles it against `67d714662`, but the round-3 revision paragraph re-derives a dozen line numbers and test names against a third hash, `bf0a5d577`, which the Freshness Check never mentions — the same unreconciled-baseline shape round 2 flagged and closed. (Ancestry checked by the driver: `67d714662` → `da6e5789a` → `bf0a5d577`, each an ancestor of the next and of `HEAD`; the facts are consistent, only the narrative is silent.) | **APPLIED** — `## Freshness Check` gains a reconciling paragraph directly under the baseline line: the ancestry `67d714662` → `da6e5789a` → `bf0a5d577` → `HEAD` is stated, and `git diff da6e5789a HEAD` was measured **empty** across `reflections/`, `tests/`, and `scripts/memory_consolidation.py`, so the three hashes are interchangeable for every measurement here. `da6e5789a` stays the pinned baseline. | Nit — exempt, closed by one measured sentence. |

**Round-3 structural re-measurement performed by the critique driver against the checkout (not taken from the plan's prose).** The argv sweep returns exactly the five sites the plan names (`expectation_reconciler.py:181`, `sdlc_progress.py:792`, `sentry_triage.py:701`, `stall_advisory.py:459`, `memory_consolidation.py:352`); the bare-literal sweep returns six paths; `git grep -nE "^[A-Z_]+ *= *[\"']Eng: "` returns exactly one constant, `reflections/docs_auditor.py:44`. The two round-2 nits are closed on the numbers: `## Test Impact` carries 15 checkbox entries — 6 UPDATE, 5 NO CHANGE EXPECTED, 1 VERIFY ONLY, 3 CREATE — matching `## Appetite` exactly, and `git merge-base --is-ancestor 67d714662 da6e5789a` passes with `da6e5789a` an ancestor of `HEAD`, so the supersession sentence is measured rather than asserted. Every file path the plan names exists; the two it marks CREATE (`tests/unit/reflections/test_utilities_eng_telegram.py`, `docs/features/reflection-telegram-routing.md`) are correctly absent. All three prerequisite check commands pass. Tasks are numbered 0-14 with no gaps, every Success Criterion maps to at least one task, and no Rabbit Hole or No-Go appears in the task list as planned work. Every Test Impact line reference is byte-exact: `test_expectation_reconciler.py:204`, `test_sdlc_progress_check.py:48,326,1699-1706,1855-1859`, `test_sdlc_stall_auto_resume_e2e.py:133`, `test_memory_consolidation.py:513,550`, and the claim that no test in `test_sdlc_progress_check.py` pins `counts["escalated"]` (line 1878 checks only that the four rung keys appear in the summary). `sentry_triage._send_telegram_notification(message)` at 697 and `stall_advisory._send_alert(message)` at 455 are both single-argument, so the plan's arity claim — and the 13 and 5 NO-CHANGE fixture references that rest on it — holds.

**Concern-closing revision pass (2026-09-06T08:15:22Z).** All five rows above are dispositioned APPLIED. No design was reopened and no task was added beyond what the rows required. Two claims were re-derived against the checkout rather than copied from the critique: the three anti-test-2 case names (empirically replayed — `3 failed, 5 passed`, restored, `8 passed`) and the `reflections/expectation_reconciler.py:476-483` `elif` block (read and quoted verbatim). `## Test Impact` moved from 15 to 16 entries — 6 UPDATE, 4 CREATE, 5 NO CHANGE EXPECTED, 1 VERIFY ONLY — with `## Appetite` and Task 11 updated to match; the round-3 count of 15 above is the measurement as of that round. The `reflections/docs_auditor.py` diff stays bounded to three hunks.

### Round 4 — delta re-critique of the concern-closing revision

**Verdict (round 4): READY TO BUILD (no concerns)** — 0 blockers, 0 concerns, 2 nits. War room ran FULL depth with an independent roster of 3 critics; roster gate `complete: true`, 3/3, 0 ungrounded. Scope was a **delta review** of `c022c5c70..8c4a48360` only — everything outside that diff was verified green in round 3 and was not re-opened.

**All five round-3 rows are confirmed genuinely closed, by measurement rather than by reading the disposition text.**

- *Anti-test 2 (row 1).* The mutation was replayed against the checkout — `if target == PROJECT_ROOT.resolve():` → `if True:` in `reflections/docs_auditor.py`, then `TestTelegramChatRouting`. Result: **exactly `3 failed, 5 passed`**, failing precisely `test_foreign_unregistered_repo_suppresses_send`, `test_registered_repo_with_malformed_group_suppresses_send`, and `test_load_local_projects_raising_falls_back_for_project_root_only`, with `test_foreign_registered_repo_routes_to_its_own_group` green — the plan's prediction, verbatim. File restored, `TestTelegramChatRouting` back to `8 passed`, `git status` clean on `reflections/`. All four line citations (`:1587`, `:1631`, `:1644`, `:1663`) re-derived byte-exact.
- *Task 6 site 480 (row 2).* `reflections/expectation_reconciler.py:476-483` is **byte-exact** to the plan's quoted block, line for line. The proposed nested-`else` replacement was **compiled and executed** at the real nesting depth: a successful steer leaves `_escalate_once` uncalled, `counts["escalated"] == 0`, and no `escalated-evidence` finding; a failed steer and a `None` PM each escalate exactly once. Short-circuit semantics are preserved exactly, so the `SET NX` sentinel at `reflections/expectation_reconciler.py:170-176` is not burned on the steer path — which is what the hoist warning exists to protect. `tests/unit/reflections/test_expectation_reconciler.py:139` confirmed to assert only `steered-evidence`, so the new CREATE pin is the thing that makes the hoist go red.
- *Task 3 swallow split (row 3).* The two scopes are byte-consistent across all four places a builder reads them: Solution §1's illustrated body, Solution §1's return-contract sentence, Task 3's two bullets, and the Failure Path table's `` `resolve_eng_group` raises `` row (`swallowed; False; no subprocess`). No path out of the illustrated body returns neither `True` nor `False`. `reflections/utilities.py:339-341` confirmed byte-exact, so the "monkeypatch-only" reachability claim holds.
- *docs_auditor justification (row 4).* Verified against `reflections/docs_auditor.py:1602-1642`: the `repo_root: Path | None = None` signature and the retained `subprocess.run` are both as described, so the stated reason for keeping the sender in place is accurate.
- *Freshness reconciliation (row 5).* Ancestry re-derived: `67d714662` → `da6e5789a` → `bf0a5d577` → `HEAD`, each an ancestor of the next. Every file the plan cites by `file:line` is byte-identical across `da6e5789a..HEAD`, so the three hashes are interchangeable for every measurement in this document, as claimed.

**Round-4 structural re-measurement.** `## Test Impact` carries 16 checkbox entries — 6 UPDATE, 4 CREATE, 5 NO CHANGE EXPECTED, 1 VERIFY ONLY — matching `## Appetite` and Task 11 exactly. Tasks are numbered 0-14 with no gaps. All four required sections are present and substantive, with 6 `docs/features/` references under `## Documentation`. Every file path the plan names exists except the two it marks CREATE (`tests/unit/reflections/test_utilities_eng_telegram.py`, `docs/features/reflection-telegram-routing.md`), correctly absent. All three prerequisite check commands pass, including `#2754` CLOSED.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| NIT | Scope & Value (Simplifier); Risk & Robustness (Skeptic, concurring) | Solution §1's illustrated transport handler catches `(FileNotFoundError, subprocess.TimeoutExpired, Exception)` as one tuple. `Exception` is a superclass of both named classes, so naming them alongside it is inert repetition. Behavior is identical either way and a builder copying it verbatim gets the correct result. | pending | Nit — exempt. |
| NIT | History & Consistency (Consistency Auditor); Critique driver (independent measurement, concurring) | The new `## Freshness Check` reconciliation paragraph says `git diff da6e5789a HEAD` is empty across `` `reflections/` ``, `` `tests/` ``, and `` `scripts/memory_consolidation.py` ``. It is empty across the first and third, and across every file this plan cites within `tests/`, but not across the whole `tests/` tree — 14 unrelated test modules changed there. The paragraph's conclusion (the three hashes are interchangeable for every measurement here) is correct; only the glob is wider than what was measured. | pending | Nit — exempt. |

**Both round-4 findings are NITs, and neither changes a line a builder types.** No BLOCKER and no CONCERN was found in the delta. The concern-closing revision did what it set out to do and added no work beyond the five rows: no new task, no new file, no new abstraction. The `reflections/docs_auditor.py` diff stays bounded to three hunks.

---

## Open Questions

*All three are answered. Nothing here blocks the build.*

1. ~~**Is `scripts/memory_consolidation.py:352` in scope?**~~ **Answered** by issue comment `5505275340`: the four-site list is "illustrative, not exhaustive" and the closing condition is the grep sweep. It is in scope, and the plan includes it. The comment's second site, `scripts/nightly_regression_tests.py:105`, no longer exists — re-verified on `da6e5789a`, that file carries no `TELEGRAM_CHAT` constant, no `Eng: Valor` literal, and no `valor-telegram` reference. Lane #3170 is editing that file for unrelated reasons; this plan does not touch it and expects no rebase against it.

2. ~~**Should the two host-machine digests suppress, or keep the narrowed fallback?**~~ **Answered by the supervisor: keep the fallback.**

   `FALLBACK_ENG_CHAT` is #2754's model to generalize, not a defect to remove. It survives as exactly one named constant, relocated into the shared helper `reflections/utilities.py` with the `PROJECT_ROOT` narrowing intact, so `sentry_triage` and `stall_advisory` keep paging on the production machine instead of going silent there. Dropping it would revert part of merged PR #3077 to satisfy a grep, which is the wrong trade.

   The closing sweep is therefore defined as two commands, and neither expects zero occurrences of the string in the tree:

   - **(a) argv adjacency** — `git grep -n '"--chat", "Eng: Valor"' -- '*.py'` returns nothing, anywhere in the repo. Five sites today.
   - **(b) the bare literal as a per-module constant** — no module outside `reflections/utilities.py` declares its own chat-name constant. Measured on `da6e5789a`, `reflections/docs_auditor.py:44`'s `FALLBACK_ENG_CHAT` is the only such constant in the tree (`git grep -nE "^[A-Z_]+ *= *[\"']Eng: " -- '*.py'`), and the lift relocates it. The older `TELEGRAM_CHAT`-style constant the issue comment cited in `scripts/` is already gone.

   Both sweeps exclude `tests/`, `reflections/agents/` prompt text, and that single named fallback. Success Criterion 2 and the Verification table now carry the byte-identical form of (b).

3. ~~**Does the suppression finding need to reach Telegram some other way?**~~ **Answered: out of scope, deliberately.** When resolution fails there is by definition no chat to tell. The finding lands in the reflection's `summary` — which the scheduler persists — and in a `warning` log line naming the project. If a suppressed page needs to escalate somewhere a human actually watches, that is a separate mechanism with its own design questions (where does it go, what stops it from becoming the same hardcoded destination this issue exists to remove), and it belongs in its own issue. It stays out of this lane and no task here implements it.
