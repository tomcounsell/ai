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
- `_check_project_stalls` likewise, and `counts["escalated"]` is **not** incremented for a suppressed page.

**Anti-test — the mutation that must fail.** Change `str(chat_id)` back to `group_name` in `send_eng_telegram` and the resolves-case test must go red. If it stays green the test is asserting the wrong thing (it is checking "not `Eng: Valor`" rather than "is the id"), which is exactly the shape of assertion that passes without reaching the code. Run this mutation once per guard, per review round.

## Test Impact

Arity changes to `_escalate_once` (`expectation_reconciler`) and `_send_alert` (`sdlc_progress`) break every 1-argument monkeypatch bound to them. The other three senders keep their arity, so their fixtures survive untouched.

- [ ] `tests/unit/reflections/test_expectation_reconciler.py:204` — **UPDATE**: the `lambda j, e, m:` patch of `_escalate_once` must become `lambda p, j, e, m:`. One site.
- [ ] `tests/unit/reflections/test_sdlc_progress_check.py:326` — **UPDATE**: `lambda msg: lab.alerts.append(msg)` → 2-arg form.
- [ ] `tests/unit/reflections/test_sdlc_progress_check.py:48,1699-1706` — **UPDATE**: `_REAL_SEND_ALERT` is re-installed deliberately to put the real subprocess boundary under test; the restore and the call that follows both need the new arity.
- [ ] `tests/unit/reflections/test_sdlc_progress_check.py:1855-1859` — **UPDATE**: `test_send_alert_swallows_filenotfound` calls `sdlc_progress._send_alert("hello")` directly; must pass a project dict.
- [ ] `tests/integration/test_sdlc_stall_auto_resume_e2e.py:133` — **UPDATE**: `monkeypatch.setattr(sdlc_progress, "_send_alert", alerts.append)` — `list.append` is 1-arity and will raise on the new signature.
- [ ] `tests/unit/test_sentry_triage_apply.py` (13 references) — **NO CHANGE EXPECTED**: `_send_telegram_notification(message)` keeps its arity. Any breakage here means the Solution's arity claim was wrong; treat it as a signal to re-read, not to edit fixtures.
- [ ] `tests/unit/reflections/test_stall_advisory_reflection.py` (5 sites) — **NO CHANGE EXPECTED**, same reasoning. These are `patch.object(...)` autospec-free mocks and tolerate either way, so they are a weak signal; the real check is that `_send_alert`'s signature is genuinely unchanged.
- [ ] `tests/unit/test_docs_auditor_substrate.py::TestTelegramChatRouting` (8 cases, in a 176-test module) — **NO CHANGE EXPECTED**, and this is the load-bearing regression check on the `_resolve_notify_chat` lift. If these need editing, the lift changed behavior and must be reworked rather than the tests adjusted.
- [ ] `tests/unit/reflections/test_docs_auditor_git_surface.py` — **VERIFY ONLY**: PR #3077 touched it (14 lines); confirm the lift leaves it green.
- [ ] New: `tests/unit/reflections/test_utilities_eng_telegram.py` — **CREATE**: the two tables in Failure Path Test Strategy, alongside the existing `test_utilities_resolve_eng_group.py`.

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
- **The `_resolve_notify_chat` lift touches three-day-old code.** PR #3077 merged 2026-09-02 with 248 lines of new tests. The lift is safe only insofar as `TestTelegramChatRouting` passes **unmodified**. Any need to edit those 8 cases is a stop-and-rethink signal.
- **`scripts/memory_consolidation.py` has a different error contract.** It uses `check=True` and routes `CalledProcessError` to a fallback log file. The helper swallows errors and returns `True`, which would silently retire that fallback. The wiring must map a `False` return to the fallback-log write, or contradiction records get dropped on a bridge outage.
- **`list.append` as a monkeypatch is arity-fragile.** `tests/integration/test_sdlc_stall_auto_resume_e2e.py:133` binds `alerts.append` directly to `_send_alert`. It will raise, not silently pass — which is the good outcome, but it means an integration test fails in a way that looks unrelated to routing.
- **Concurrent `docs/plans/` edits.** Observed during this very plan: a peer lane's `git add -A` swept an uncommitted section of this file into its own commit. Commit each section as it lands; never leave the plan dirty across an await.

## Race Conditions

None introduced. Every changed path is synchronous, single-threaded `subprocess.run` inside a reflection tick, and no shared mutable state is added.

Two pre-existing orderings are worth naming so the change does not disturb them:

- **`expectation_reconciler._escalate_once`** claims its `SET NX` sentinel *before* sending. If the send then suppresses, the sentinel is already burned and no retry happens for that `(job, eid)` until the TTL lapses. This plan does not change the ordering — reversing it would re-open the double-page window the sentinel exists to close — but the suppression finding is what makes the burned sentinel legible.
- **`sdlc_progress._escalate_once`** has the same shape via `_escalation_set(slug, sha)`, and its docstring already documents "under-alert during a flap beats spam during one". Suppression is consistent with that stance.

## No-Gos (Out of Scope)

- **Prompt-text references to the chat name.** `reflections/agents/circuit_health_gate.py:64,75` and `reflections/agents/system_health_digest.py:7,137` name `'Eng: Valor'` inside LLM prompt strings, not argv. Different fix shape, explicitly excluded by the issue. They are single-quoted, so they also sit outside any double-quoted grep sweep.
- **`docs_auditor`'s surviving literal.** `FALLBACK_ENG_CHAT = "Eng: Valor"` (moving to `utilities.py`) and the docstring mention at `docs_auditor.py:1569` are #2754's deliberate narrowing. Any "clean grep" success criterion must exempt the named constant and its docstring, or it will demand undoing a merged fix. The criterion this plan uses is the **argv adjacency** `"--chat", "Eng: Valor"`, not the bare literal — see Success Criteria.
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

- [ ] Update `docs/features/expectation-reconciler.md` — the escalation step (line 40, "action failed → escalate once (Telegram operator alert), stop") must say the alert is addressed to the project's own `Eng:` group by numeric `chat_id`, and that it is suppressed with a finding when no group resolves.
- [ ] Update `docs/features/stall-advisory-classifier.md` — the Telegram Alert Flag section (lines 140-142) and the component table (line 156) must record that the alert now goes to the host checkout's engineer group via `resolve_host_eng_chat`, with the `PROJECT_ROOT`-narrowed fallback.
- [ ] Update `docs/features/sentry-triage.md` — the "Telegram digest" section (line 120) must record the same host-machine resolution, and that a foreign or unregistered checkout suppresses the digest rather than sending it to `Eng: Valor`.
- [ ] Update `docs/features/docs-auditor.md` — lines 50-55 describe `_resolve_notify_chat`'s ladder inline. After the lift, point them at `reflections/utilities.py::resolve_host_eng_chat` as the owner of the rule, keeping the behavior description intact.
- [ ] Create `docs/features/reflection-telegram-routing.md` — one page describing the two entry points (`send_eng_telegram`, `send_host_eng_telegram`), the id-not-name rule and why, the `PROJECT_ROOT`-narrowed fallback and the warning against unconditionalizing it, the `True`/`False` return contract, and the table of the five consumer modules.
- [ ] Add `reflection-telegram-routing` to the `docs/features/README.md` index table.

## Success Criteria

1. `git grep -n '"--chat", "Eng: Valor"' -- '*.py'` returns **nothing**. This is the argv-adjacency form, not the bare literal — it deliberately spares `docs_auditor`'s named `FALLBACK_ENG_CHAT` constant and its docstring, which are #2754's merged narrowing.
2. `git grep -lF '"Eng: Valor"' -- '*.py' | grep -v '^tests/'` returns exactly **one** path: `reflections/utilities.py` (where `FALLBACK_ENG_CHAT` now lives). Five files today; one after.
3. `reflections/utilities.py` exports `send_eng_telegram`, `send_host_eng_telegram`, and `resolve_host_eng_chat`.
4. `tests/unit/test_docs_auditor_substrate.py::TestTelegramChatRouting` — all 8 cases pass **with no edits to the test file**. This is the regression check on the lift.
5. New `tests/unit/reflections/test_utilities_eng_telegram.py` covers every row of both Failure Path Test Strategy tables, including the two mutation anti-tests.
6. A suppressed alert appears in the reflection's `findings` and `summary` for both `expectation_reconciler` and `sdlc_progress`, pinned by test.
7. `scripts/memory_consolidation.py` still writes `logs/memory-contradictions.log` when the send does not land, pinned by test.
8. `scripts/pytest-clean.sh tests/unit/reflections/ tests/unit/test_sentry_triage_apply.py tests/unit/test_docs_auditor_substrate.py` is green.
9. `python -m ruff check` and `python -m ruff format --check` clean on the changed files.
10. The six Documentation checkboxes are done, and `docs/features/README.md` indexes the new page.

## Team Orchestration

Single builder. The five source files share one new helper, and the helper's exact signature is the thing every call site depends on — splitting this across parallel agents would have them converging on each other's design, which is the shared-worktree livelock shape. Sequence it instead: helper first, then consumers, then fixtures, then docs.

If a second agent is available, the one genuinely disjoint slice is the **documentation set** (six files, no source dependency once the Solution is settled). That can run in parallel with the fixture sweep, with an explicit file-level ownership split: docs agent owns `docs/features/*`, builder owns `reflections/`, `scripts/`, and `tests/`.

## Step by Step Tasks

1. **Lift the host resolver into `reflections/utilities.py`.** Move `FALLBACK_ENG_CHAT` from `docs_auditor`. Add `resolve_host_eng_chat(repo_root: Path | None = None) -> str | None` carrying every rung and every comment from `docs_auditor._resolve_notify_chat`, defaulting `repo_root` to `utilities.PROJECT_ROOT` computed at call time, not in the signature default.
2. **Reduce `docs_auditor._resolve_notify_chat` to a delegation**, keeping a `FALLBACK_ENG_CHAT` re-export binding so existing patches of `docs_auditor.PROJECT_ROOT` still work. Run `tests/unit/test_docs_auditor_substrate.py` — **all 176 tests green with zero test edits**, or stop and rethink the lift.
3. **Add `send_eng_telegram(project, message, *, logger_prefix) -> bool`** to `reflections/utilities.py`: `resolve_eng_group` → warn-and-return-`False` on `None` with no subprocess, else `subprocess.run` with `str(chat_id)`. Swallow `FileNotFoundError` / `TimeoutExpired` / `Exception` / non-zero exit and return `True`.
4. **Add `send_host_eng_telegram(message, *, logger_prefix) -> bool`** wrapping `resolve_host_eng_chat` with the same swallow-and-return contract.
5. **Write `tests/unit/reflections/test_utilities_eng_telegram.py`** covering both tables from Failure Path Test Strategy. Run the two mutation anti-tests now, before any consumer is wired: revert `str(chat_id)` to `group_name` and confirm red; restore.
6. **Wire `expectation_reconciler`**: `_escalate_once(project, job_id, eid, message)`, update the three call sites at 444 / 480 / 506, body calls `send_eng_telegram(..., logger_prefix="expectation_reconciler")`, and thread a `False` return into `findings` as an `alert-suppressed` entry.
7. **Wire `sdlc_progress`**: `_send_alert(project, message)`; add a `project_dict` keyword to `_escalate_once` (it already takes `project=project_key` as a string — keep that, it feeds the message text) and thread it from `_check_project_stalls`; thread suppression into `findings` and do **not** increment `counts["escalated"]` on a suppressed page.
8. **Wire `sentry_triage`**: `_send_telegram_notification` body → `send_host_eng_telegram(message, logger_prefix="sentry_triage")`. Arity unchanged.
9. **Wire `stall_advisory`**: `_send_alert` body → `send_host_eng_telegram(message, logger_prefix="stall_advisory")`. Arity unchanged.
10. **Wire `scripts/memory_consolidation.py:352`** → `send_host_eng_telegram`, mapping a `False` return to the existing `_write_contradiction_log` fallback so a bridge outage still records the contradiction.
11. **Sweep the fixtures** listed in Test Impact: `test_expectation_reconciler.py:204`, `test_sdlc_progress_check.py:48,326,1699-1706,1855-1859`, `test_sdlc_stall_auto_resume_e2e.py:133`. Confirm `test_sentry_triage_apply.py` and `test_stall_advisory_reflection.py` need **no** edits — if they do, the arity claim in the Solution was wrong.
12. **Run the grep criteria** (Success Criteria 1 and 2) and record the actual output, not a claim about it.
13. **Documentation**: the six items under `## Documentation`.
14. **`python -m ruff check` and `python -m ruff format`** on changed files. Formatting only, no linting beyond ruff's own check.

## Verification

| Check | Command | Expected |
|---|---|---|
| No hardcoded chat argv anywhere | `git grep -n '"--chat", "Eng: Valor"' -- '*.py'` | no output, exit 1 |
| Literal survives in exactly one non-test file | `git grep -lF '"Eng: Valor"' -- '*.py' \| grep -v '^tests/' \| wc -l` | `1` (`reflections/utilities.py`) |
| Helpers exist | `git grep -n 'def send_eng_telegram\|def send_host_eng_telegram\|def resolve_host_eng_chat' reflections/utilities.py` | three matches |
| Lift is behavior-preserving | `scripts/pytest-clean.sh tests/unit/test_docs_auditor_substrate.py -q` | green, and `git diff --stat tests/unit/test_docs_auditor_substrate.py` is empty |
| New helper coverage | `scripts/pytest-clean.sh tests/unit/reflections/test_utilities_eng_telegram.py -q` | green |
| Consumer suites | `scripts/pytest-clean.sh tests/unit/reflections/ tests/unit/test_sentry_triage_apply.py -q` | green |
| Auto-resume integration | `scripts/pytest-clean.sh tests/integration/test_sdlc_stall_auto_resume_e2e.py -q` | green |
| Mutation: name instead of id | swap `str(chat_id)` → `group_name` in `send_eng_telegram`, run the new test module | **red**; restore and re-run green |
| Mutation: unconditional fallback | drop the `target == PROJECT_ROOT` guard in `resolve_host_eng_chat`, run `test_docs_auditor_substrate.py` | **red** (the foreign-repo case); restore |
| Format | `python -m ruff check` and `python -m ruff format --check` on changed files | clean |

**Manual check that cannot be automated here:** confirming a real alert lands in the right group requires a machine whose `projects.json` carries populated `telegram.groups` (spike-4 — this one does not). Defer to the operator on a fleet machine after deploy; do not gate the lane on it and do not claim it was done.

## Critique Results

<!-- Filled by /do-plan-critique -->

---

## Open Questions

1. **Is `scripts/memory_consolidation.py:352` in scope?** The issue enumerates four reflections but sets the exit criterion as "a clean grep sweep for the literal". That sweep fails while this fifth argv site stands. This plan includes it (one call-site swap plus preserving the `CalledProcessError` → contradiction-log fallback). Confirm, or cut it and soften the criterion to the four named files.

2. **Should the two host-machine digests suppress, or keep the narrowed fallback?** `sentry_triage` and `stall_advisory` have no project in scope, so this plan routes them through `resolve_host_eng_chat`, which falls back to the `Eng: Valor` literal when the root is *this checkout*. That preserves today's behavior on the production machine. The stricter reading of the issue — "resolve → send by numeric `chat_id` → skip-and-warn when unresolvable" with no fallback — would make the digests go silent on any machine whose `projects.json` lacks a populated `Eng:` group. Preserving the fallback is the conservative call and matches #2754; confirm that is what is wanted.

3. **Does the suppression finding need to reach Telegram some other way?** When resolution fails there is by definition no chat to tell. The finding lands in the reflection's `summary` (which the scheduler persists) and in the warning log. If a suppressed page needs to escalate somewhere a human actually watches, that is a separate mechanism and a separate issue — say so and it stays out.
