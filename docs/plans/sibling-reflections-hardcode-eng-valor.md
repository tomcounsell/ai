---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-09-05
tracking: https://github.com/tomcounsell/ai/issues/3072
last_comment_id:
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

**Desired outcome:** each sender resolves its destination from configuration — a numeric `chat_id` for the project whose work provoked the alert — and declines to send, loudly, when no destination resolves. The literal survives in exactly one place: `reflections/docs_auditor.py`'s deliberately narrowed `FALLBACK_ENG_CHAT`.

## Freshness Check

**Baseline commit:** `67d714662`
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

**Notes — the drift that matters:**

1. **The issue undercounts the sweep.** A fifth argv site carries the literal outside `reflections/`: `scripts/memory_consolidation.py:352`. The issue's stated exit criterion is "a clean grep sweep for the literal, not an enumerated site list", and that sweep fails today with `memory_consolidation` still present. See No-Gos for the disposition.
2. **The archived #2754 plan's anti-criterion is already stale.** It asserts six non-`docs_auditor` files carry the literal, naming `scripts/nightly_regression_tests.py` among them. That file carries no `valor-telegram` invocation today. The real count on `67d714662` is five: the four reflections plus `scripts/memory_consolidation.py`.
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

**Medium.** Five source files, one lifted helper, and a test-fixture sweep across five test modules.

What justifies Medium rather than Small: the arity change to four sender functions breaks roughly 25 monkeypatch sites that bind 1-argument lambdas, and the `_resolve_notify_chat` lift touches a module that merged three days ago with 248 lines of fresh tests. What holds it back from Large: no new abstraction is being invented — `resolve_eng_group` and the `_resolve_notify_chat` ladder both exist and both have passing tests to copy from.

If this exceeds the appetite, the fall-back scope is Flow A only (`expectation_reconciler`, `sdlc_progress`) — the two sites where alerts genuinely misroute — leaving the host-machine digests for a follow-up. That is a legitimate cut because Flow B's current behavior is right by accident rather than wrong.

## Prerequisites

- **#2754 / PR #3077 — landed** (merged 2026-09-02, merge commit `974be653`). Provides `reflections/utilities.py::resolve_eng_group` and the `reflections/docs_auditor.py::_resolve_notify_chat` ladder this plan lifts. Verified present at `67d714662`.

No other prerequisite. `python scripts/check_prerequisites.py docs/plans/sibling-reflections-hardcode-eng-valor.md` should report clean.

## Solution

Two entry points in `reflections/utilities.py`, because recon (spike-2) showed the four callers are not one shape.

### 1. `send_eng_telegram(project, message, *, logger_prefix) -> bool`

For callers that hold a project dict.

```
resolved = resolve_eng_group(project)
if resolved is None:
    log.warning("%s: no Eng: group for project %s; Telegram alert suppressed",
                logger_prefix, project.get("slug", "?"))
    return False                       # NO subprocess is invoked
_, chat_id = resolved
subprocess.run(["valor-telegram", "send", "--chat", str(chat_id), message], ...)
return True
```

Takes the **dict**, not a `project_key` — `resolve_eng_group` scans `project["telegram"]["groups"]`, and every call site already holds the dict (spike-1). A key-based signature would force a redundant `load_local_projects()` scan per send.

Returns `True` when a destination resolved and a send was *attempted* — including a swallowed `FileNotFoundError` / `TimeoutExpired` / non-zero exit. `False` means and only means "nothing resolved, nothing was sent". This is the same contract `docs_auditor._send_telegram_notification` already publishes, and preserving it is what lets callers write an accurate suppression notice.

`logger_prefix` keeps each module's existing log vocabulary (`"expectation_reconciler"`, `"sdlc_progress"`, …) so log greps and any alerting built on them survive.

### 2. `resolve_host_eng_chat() -> str | None` and `send_host_eng_telegram(message, *, logger_prefix) -> bool`

For callers with no project in scope — the fleet-wide digests.

`resolve_host_eng_chat()` is `docs_auditor._resolve_notify_chat` **lifted verbatim** and generalized from `repo_root: Path` to defaulting on `reflections.utilities.PROJECT_ROOT`, keeping every rung and every comment:

1. Match the repo root against a `projects.json` entry's `working_directory`.
2. On a match, return `str(chat_id)` from that project's `Eng:` group.
3. On no match or no configured group, return `FALLBACK_ENG_CHAT` **only when** the root is this very checkout. Never for a foreign repo.
4. Swallow, log, and fall through to rung 3 on any exception.

`docs_auditor._resolve_notify_chat(repo_root)` then becomes a thin delegation to the lifted function with an explicit `repo_root` argument, and `FALLBACK_ENG_CHAT` moves to `utilities.py`. `docs_auditor` keeps a re-export binding so `tests/unit/test_docs_auditor_substrate.py`'s existing patches of `docs_auditor.PROJECT_ROOT` and its 8 `TestTelegramChatRouting` cases keep passing unchanged. If they do not pass unchanged, the lift is wrong.

### 3. Per-module wiring

| Module | Change |
|---|---|
| `expectation_reconciler` | `_escalate_once(job_id, eid, message)` → `_escalate_once(project, job_id, eid, message)`. Update the three call sites (444, 480, 506) inside `_reconcile_project`, which already holds `project`. Body calls `send_eng_telegram(project, message, logger_prefix="expectation_reconciler")`. |
| `sdlc_progress` | `_send_alert(message)` → `_send_alert(project, message)`. `_escalate_once` already takes `project=project_key` (a *string*); add a `project_dict` keyword and thread it from `_check_project_stalls`, which holds it. Body calls `send_eng_telegram`. |
| `sentry_triage` | `_send_telegram_notification(message)` keeps its arity; body swaps to `send_host_eng_telegram(message, logger_prefix="sentry_triage")`. |
| `stall_advisory` | `_send_alert(message)` keeps its arity; body swaps to `send_host_eng_telegram(message, logger_prefix="stall_advisory")`. |
| `scripts/memory_consolidation.py:352` | Swaps to `send_host_eng_telegram`. Its `check=True` / `CalledProcessError` → `_write_contradiction_log` fallback must be preserved: the helper swallows errors and returns `True`, so the script needs the return value to decide whether to write the fallback log. Treat a `False` return as "bridge unreachable" and write the log. |

Only two of the five sender signatures change arity. That is a deliberate consequence of spike-2 and it cuts the test-fixture sweep roughly in half.

### 4. Suppression must be visible

Both `expectation_reconciler._escalate_once` and `sdlc_progress._escalate_once` currently return a truthy value meaning "the human was told". After this change that claim can be false. Each must thread the helper's `False` into the reflection's `findings` list — e.g. `f"alert-suppressed: no Eng: group for {project_key}"` — so it reaches the `summary` the scheduler persists. `docs_auditor` set this precedent in PR #3077 and it is the whole mitigation for the behavior boundary named in Architectural Impact.

## Failure Path Test Strategy

<!-- TODO -->

## Test Impact

<!-- TODO -->

## Rabbit Holes

<!-- TODO -->

## Risks

<!-- TODO -->

## Race Conditions

<!-- TODO -->

## No-Gos (Out of Scope)

<!-- TODO -->

## Update System

<!-- TODO -->

## Agent Integration

<!-- TODO -->

## Documentation

<!-- TODO -->

## Success Criteria

<!-- TODO -->

## Step by Step Tasks

<!-- TODO -->

## Verification

<!-- TODO -->

## Critique Results

<!-- Filled by /do-plan-critique -->

---

## Open Questions

<!-- TODO -->
