---
status: Planning
type: feature
appetite: Large
owner: Valor Engels
created: 2026-09-10
tracking: https://github.com/tomcounsell/ai/issues/3274
last_comment_id:
---

# Improvement controller lane 7: cloud execution capacity

## Problem

Charter §2 sets a first-month expectation: Valor runs mostly in cloud sandboxes, around the clock, on affordable resources acquired within the charter's authority and budgets. It then attaches a reporting obligation that is easy to satisfy dishonestly and hard to satisfy well. A progress report must say **which sessions run in cloud sandboxes, whether the loop continues unattended, what resources sustain it, what they cost, and what still prevents the intended result.** Five answers. The fifth is the one a progress report usually drops, and it is the one that makes the other four trustworthy.

Lane 2b (#3255) measured the resource position that this expectation rests on and found it unverified. The probe returned four `unknown`s, one `absent` for `wrangler`, and one `absent` for the vault writer. Its verified set is empty. That is a measurement, not a verdict: `op` was installed but `OP_SERVICE_ACCOUNT_TOKEN` was not set in the build shell, so the vault listing failed and the probe honestly reported that it did not know.

**Current behavior:**

- No RSI session has ever run anywhere but a developer workstation. `worker/__main__.py` runs on macOS under launchd, reads `.env` through an iCloud symlink, and talks to a Redis on `localhost:6379`. Every one of those three is machine-local.
- Nothing meters the $50/week infrastructure unit. `ImprovementSettings.weekly_infrastructure_usd` is on the #3255 branch, unlanded, and has no reader anywhere. Charter §8's second spending category exists as a number and nothing else.
- `spend_receipt` is not a value `ImprovementEvidence` accepts (`models/improvement_evidence.py:58-64`). A receipt written today is coerced to `"other"` at `:219-223` and becomes indistinguishable from every other `other` row, so the fallback metering path the charter depends on cannot even record.
- No teardown policy exists, so "the week is exhausted" has no defined consequence. The obvious consequence, stop the sandboxes, is the wrong one: tearing a sandbox down mid-trial destroys the evidence the trial was gathering.
- Charter §2's progress report has never been written. #3177 carries no statement of how far the operating model has moved.
- `max_concurrent_research_sessions` is pinned at 1 with a hard `le=4` bound (`config/settings.py:606-617`). Gap D calls the one-lane limit an operating choice to revisit once sessions run in sandboxes, and that revisit has not happened.

**Desired outcome:**

The resource position is verified rather than unknown. Cloud execution is a decided question with recorded evidence behind the decision, whichever way it goes. Unit 3 has a meter, a window, and a teardown policy that protects evidence instead of destroying it. And #3177 carries a progress report that answers all five of charter §2's questions, including an honest fifth answer about what still prevents mostly-cloud operation.

The fifth answer may turn out to be the most valuable output of this lane. Research during planning surfaced a constraint that no amount of infrastructure work removes: since 2026-04-04 Anthropic does not permit Claude Pro/Max subscription capacity to be consumed by third-party harnesses, and subscription OAuth tokens are blocked outside the official Claude Code CLI. This repo's worker drives the official `claude` CLI, which is the supported shape, but "ordinary, individual usage" is the standard subscription limits are written against, and a fleet of always-on sandboxes is not obviously that. A plan that quietly builds sandboxes without resolving this would be building toward a wall.

## Freshness Check

**Baseline commit:** `191bd42a1` (last code commit on `main`; tree HEAD at plan start was `ff169cfbb`, a peer lane's plan skeleton)
**Issue filed at:** 2026-09-09T14:46:37Z
**Disposition:** **Major drift**, on one premise, corrected in place rather than silently.

**The drift.** The issue states "`ImprovementSettings.weekly_infrastructure_usd = 50.00` exists on `main` as of #3255". It does not. #3255's PR **#3275 is open and unmerged**. On `main`, `config/settings.py` declares `max_concurrent_research_sessions` at `:606` (with `le=4` at `:609`) and `daily_external_llm_usd` at `:618`, and stops there. There is no `weekly_infrastructure_usd`, no `budget_day_boundary`, no `budget_week_start`, and no `tools/improvement_resources.py`. All of it, plus the rename of `daily_external_llm_usd` to `daily_paid_inference_usd`, lives on `origin/session/sdlc-3255` at `b05dde885`.

This does not invalidate the lane; it fixes its starting line. Every task below is written against a **landed #3255**, and the Prerequisites table makes that a checked precondition rather than an assumption.

**File:line references re-verified:**

| Citation | Where checked | Result |
|---|---|---|
| `tools/improvement_resources.py::probe` | `main` | **absent**; present on `origin/session/sdlc-3255` |
| `ImprovementSettings.weekly_infrastructure_usd` | `config/settings.py` on `main` | **absent**; on the #3255 branch at the position after `daily_paid_inference_usd` |
| `budget_week_start` / `budget_day_boundary` | `config/settings.py` on `main` | **absent**; both `Literal`-typed on the #3255 branch |
| `max_concurrent_research_sessions` | `config/settings.py:606-617` on `main` | exact, and the `le=4` bound at `:609` is a fact the issue does not mention |
| `ImprovementEvidence` kind `spend_receipt` | `models/improvement_evidence.py:58-64` | **not in `EVIDENCE_KINDS`** on either checkout; `record_once:219-223` coerces it to `"other"` |
| `tools/vault_write.py`, `tools/paid_inference_meter.py` | `main` and the #3255 branch | absent on both, as the issue states |
| Charter §2 and §8 text | `docs/improvement-charter.md` | quoted verbatim and correctly |
| Gap D unit-3 rules | `docs/plans/recursive-self-improvement.md:363-367` | exact, including "Lane 7 owns the teardown policy" |
| Lane 7 scope and success criterion | `docs/plans/recursive-self-improvement.md:804`, `:650` | exact |

**Cited sibling issues/PRs re-checked:** #3177 OPEN, #3215 OPEN (no PR, no plan document), #3216 OPEN, #3217 OPEN, #3218 OPEN, #3255 OPEN with PR #3275 OPEN. Lane 3's absence matters: this lane's acquisition and reservation tasks have no control namespace and no vault writer to build against until #3215 ships, while its spike, policy, and reporting tasks do not.

**Commits on `main` since the issue was filed (touching referenced files):**

- `191bd42a1` "Stop the unprompted Telegram repeat replies" — irrelevant; touches bridge reply behavior, none of this lane's files.
- `ff169cfbb` "Plan skeleton: improvement controller lane 4 frozen evaluation inputs (Refs #3216)" — a sibling lane planning concurrently. Documentation only.

**Active plans in `docs/plans/` overlapping this area:** `recursive-self-improvement.md` is the declared parent, not a collision. `improvement-controller-lane-2b-charter-v2-delta.md` is the dependency, and its build has landed on a branch. `improvement-controller-lane-4-*` was created during this planning pass by a sibling lane; lanes 4 and 7 share no files. `codex-exec-dev-lane.md` touches a Codex execution path — charter §7 names Codex as a subscription route, and if remote execution ever runs Codex instead of Claude, that plan is where the harness lives. This plan builds no Codex harness.

**Notes.** A naming collision worth flagging to a builder: `tools/code_execution/` already exists and its README says "sandboxed code execution environment". It runs Python, JS, and bash in temp files on the local machine with a timeout. It has nothing to do with cloud sandboxes and is not this lane's code.

## Prior Art

Searched closed issues and merged PRs for cloud execution, sandboxes, Cloudflare, remote workers, and budget metering. **No prior attempt at cloud execution exists in this repository.** The searches returned the fleet-update and launchd cluster instead, which is prior art of a different and more useful kind: it is the accumulated evidence of how hard unattended remote operation already is on machines Valor controls.

- **#2013, #2018, #2089, #2104, #2161 / PR #2183** — a five-issue run on `install_worker()` and `launchctl bootstrap` failing with EIO, exit codes going unchecked, and `/update` reporting success while the worker was down. Relevance: every one of these is a failure of *unattended restart on a machine that already existed*. A sandbox adds a machine with no launchd, no iCloud `.env`, and no operator, so this lane inherits the whole class rather than escaping it.
- **#1898 / PR #1914** — "Update reports OK but bridge process keeps running pre-update code." Fixed by verifying that the running process's release matches pulled HEAD. Relevance: the same verification is the only honest way to state which code a sandbox is running in the §2 progress report.
- **#3164 / PR #3168** — "Bridge release verify reports unknown: macOS pgrep hides the bridge from its own descendants." Relevance: liveness detection is platform-specific and has silently lied here before. A sandbox's liveness answer must not be assumed to work because the macOS one does.
- **#1546 / PR #1570, PR #1664 — Granite PTY container.** A proof of concept that drove an interactive Claude Code session from a container via PTY, later purged of its PoC framing. This is the closest thing in the repo's history to running Claude in a container, and it ran locally against a local Claude. It never became a remote-execution path and does not supply one.
- **#1312 / PR #2196** — bridge reacts when no worker is alive; established the `worker:registered_pid:*` heartbeat convention. Relevance: this is the existing liveness key a remote worker would have to write, and it is a plain Redis string key, so it presumes a shared Redis.
- **`docs/plans/codex-exec-dev-lane.md`** and **`docs/infra/harness-cross-compat.md`** — active work on a Codex execution path, with typed settings for sandbox mode and approval. Charter §7 names Codex as a subscription route. This is where a Codex-based remote harness would live if one is ever wanted; this lane does not build it.

**Nothing has failed here before, because nothing has been tried.** The `## Why Previous Fixes Failed` section is therefore omitted: there are no prior fixes to this problem to analyze.

## Research

**Queries used:**

- Cloudflare Sandbox SDK containers pricing 2026, long-running and persistent processes
- Claude Code subscription authentication on a headless remote server, `CLAUDE_CODE_OAUTH_TOKEN`
- Anthropic Claude Pro/Max subscription terms of service for remote-server automation, 2026

**Key findings:**

**1. Subscription capacity cannot be routed to a third-party harness, and OAuth tokens are blocked outside the official CLI.** Anthropic's Consumer Terms §3.7 has forbidden third-party harness access since 2024; technical enforcement landed in January 2026, the February 2026 compliance update stated that the Agent SDK requires API-key authentication and that Free/Pro/Max OAuth tokens cannot be used with it, and effective 2026-04-04 subscription limits stopped being usable by third-party harnesses at all. Running the **official Claude Code CLI on a remote host over SSH is explicitly supported**; hosted/VPS use of subscription OAuth is not. Subscription limits also assume "ordinary, individual usage." ([The Register](https://www.theregister.com/2026/02/20/anthropic_clarifies_ban_third_party_claude_access/), [Claude Code ToS explainer](https://autonomee.ai/blog/claude-code-terms-of-service-explained/))

*How it informs the plan:* this is the single most load-bearing external fact in the lane, and it is a compliance question before it is an engineering one. This repo's worker drives the official `claude` CLI, which is on the supported side of the line, but Gap D budgets Claude work as *subscription concurrency*, and a fleet of always-on sandboxes consuming that concurrency is the shape the April 2026 change was aimed at. It is spike-1, it gates every acquisition task, and if it resolves against us it becomes the honest fifth answer in the §2 progress report rather than a reason to stop the lane.

**2. Headless subscription auth has a known refresh defect.** `claude setup-token` mints a portable `CLAUDE_CODE_OAUTH_TOKEN` valid for a year, but a reported CLI issue has the token failing to auto-refresh on headless Linux — a valid `refreshToken` sits in `.credentials.json` while the CLI returns 401 on access-token expiry. A separate report has Cloudflare blocking entire Hetzner datacenter IP ranges. ([anthropics/claude-code#50743](https://github.com/anthropics/claude-code/issues/50743))

*How it informs the plan:* "unattended" is the charter's word, and a sandbox that 401s at token expiry with nobody watching is attended operation with extra steps. Any sandbox trial must run long enough to cross a token boundary before it counts as evidence, and datacenter-IP reputation is a provider-selection criterion, not an afterthought.

**3. Cloudflare Containers and the Sandbox SDK went GA on 2026-04-13, priced scale-to-zero.** Workers Paid is $5/month and includes 25 GiB-hours of memory, 375 vCPU-minutes, and 200 GB-hours of disk; overage runs roughly $0.0000025/GiB-second of memory, $0.000020/vCPU-second, and $0.00000007/GB-second of disk. Instances cap at 4 GiB RAM and half a vCPU. Sandboxes sleep after 10 minutes of inactivity by default; `keepAlive` plus an explicit `sandbox.destroy()` is required or containers run indefinitely. Community consensus is that the model is expensive for continuously-running sandboxes and cheap for workloads that idle. ([Cloudflare Sandbox pricing](https://developers.cloudflare.com/sandbox/platform/pricing/), [Containers/Sandbox GA changelog](https://developers.cloudflare.com/changelog/post/2026-04-13-containers-sandbox-ga/), [HN discussion](https://news.ycombinator.com/item?id=45611237))

*How it informs the plan:* charter §8 names a funded Cloudflare account and its CLI, so Cloudflare is the default candidate, and half a vCPU is a genuine ceiling for a `claude -p` subprocess plus a worker. The included allowance is nowhere near a 24/7 instance, so the $50/week unit meets real overage immediately. That makes forecastability, which Gap D already requires, the deciding criterion between a metered container and a flat-rate host — and the "**a resource whose charge cannot be forecast is refused**" rule does real work here rather than sitting decorative. It also makes `keepAlive`-plus-`destroy()` a direct input to the teardown policy: a provider whose default is "run forever unless told otherwise" is exactly the shape that turns a paused budget into an unbounded charge.

## Spike Results

Six spikes ran during planning, four code-reads and two web-research. They are recorded here so the build does not re-investigate them. The build opens with a second, shorter spike phase for the two questions that need a live provider account to answer (see tasks 1 through 3).

### spike-1: May RSI sessions consume Claude subscription capacity from a cloud sandbox?
- **Assumption**: "Charter §2's cloud-sandbox operating model is an infrastructure problem."
- **Method**: web-research
- **Finding**: It is a compliance problem first. Anthropic blocks Free/Pro/Max OAuth tokens outside the official Claude Code CLI (enforced January 2026), and since 2026-04-04 subscription limits cannot be consumed by third-party harnesses at all. Running the **official CLI on a remote host is explicitly supported**, which is the shape this repo already has: `worker/` spawns `claude -p` as a subprocess of the official CLI. What is *not* resolvable from public documentation is whether a continuously-running fleet of sandboxes falls inside "ordinary, individual usage," the standard subscription limits are written against.
- **Confidence**: high on the rule, **low on the scale question**
- **Impact on plan**: The lane proceeds on the official-CLI path and nothing else. The scale question becomes a recorded provisional assumption under charter §9 (evidence, confidence, consequence, and the observation that would overturn it) rather than a blocker or a question to Tom, and it is a named candidate for the fifth answer in the §2 progress report. It also kills, before anyone writes code, the tempting shortcut of exporting a `CLAUDE_CODE_OAUTH_TOKEN` into a hosted runtime.

### spike-2: Can a cloud sandbox run the worker without becoming a bridge host?
- **Assumption**: "Any machine running this codebase has to be registered in `projects.json`'s machine roster, so a sandbox collides with single-machine ownership."
- **Method**: code-read (`worker/__main__.py`, `bridge/config_validation.py:546`, `docs/features/single-machine-ownership.md`)
- **Finding**: No collision. `worker/__main__.py`'s own docstring states the supported topology: "Developer workstations run just the worker. Bridge machines run bridge + worker as separate processes." A worker-only host takes work off the Redis queue and never resolves an inbound bridge-contact identifier, so `validate_projects_config` has nothing to complain about. The constraint binds only if a sandbox ever runs the Telegram bridge.
- **Confidence**: high
- **Impact on plan**: The sandbox target is **worker-only**, stated as a No-Go for bridge hosting rather than left implicit. This removes what looked like the lane's hardest architectural obstacle.

### spike-3: What does the fleet-update path assume that a Linux sandbox will not have?
- **Assumption**: "`/update` reaches a sandbox the same way it reaches a laptop."
- **Method**: code-read (`scripts/remote-update.sh`, `worker/__main__.py`, `scripts/valor-service.sh`)
- **Finding**: Three hard macOS assumptions, all in the first forty lines of the update path. `remote-update.sh` insists on a `~/Desktop/Valor/.env` symlink and warns about iCloud sync when it is missing. `scripts/valor-service.sh` sources `lib/launchctl.sh` and drives `launchctl` for every service operation. `worker/__main__.py` branches on `VALOR_LAUNCHD` to decide whether to load dotenv at all. A Linux container has no launchd, no iCloud, and no `~/Desktop/Valor`.
- **Confidence**: high
- **Impact on plan**: A sandbox is not a fleet machine and must not be pushed through `remote-update.sh`. Code reaches it by image rebuild and redeploy, which is a different update contract and is written into the Update System section. It also means the prior-art cluster of launchd restart bugs (#2013, #2089, #2104, #2161) is *not* inherited verbatim — but its lesson, that unattended restart fails silently and reports success, is.

### spike-4: Where do evidence, the control namespace, and artifacts live for a remote worker?
- **Assumption**: "Redis is shared, so a sandbox worker just points at it."
- **Method**: code-read (`config/settings.py:653-668`, `.env.example:198`, `tests/_worker_guard.py`)
- **Finding**: `RedisSettings.url` defaults to `redis://localhost:6379/0` and there is no TLS, password, or `rediss://` handling anywhere in settings. The `worker:registered_pid:*` liveness convention is a plain Redis string key, so it presumes one shared Redis. A sandbox therefore has exactly two options, and both cost something: its own Redis, which strands every `ImprovementEvidence` row where the dashboard cannot see it, or a network-reachable shared Redis, which does not exist today and would need transport security before it does.
- **Confidence**: high
- **Impact on plan**: Durable-state topology is a first-class design decision in this lane, not a deployment detail. It is task 4, it precedes acquisition, and "the sandbox writes evidence the dashboard can read" is a success criterion rather than an assumption.

### spike-5: Can a spend receipt be recorded today?
- **Assumption**: "Receipt-based metering is available as the fallback when a provider has no billing API."
- **Method**: code-read (`models/improvement_evidence.py:58-64`, `:219-223`)
- **Finding**: No. `EVIDENCE_KINDS` is `("correction", "inspiration", "shipped_work", "owner_liveness", "other")` on `main` and on the #3255 branch alike, and `record_once` silently rewrites any other kind to `"other"` after a `logger.warning`. A receipt would land in the same bucket as everything else uncategorized and be unqueryable as spend.
- **Confidence**: high
- **Impact on plan**: Adding `spend_receipt` to the vocabulary is this lane's, and it drags a TTL question with it: `ImprovementEvidence` expires on a 30-day window, which is shorter than the audit life of a budget week. Both are task 5.

### spike-6: Does Cloudflare fit the $50/week unit for a 24/7 sandbox?
- **Assumption**: "Charter §8 names a funded Cloudflare account, so Cloudflare is the answer."
- **Method**: web-research
- **Finding**: Cloudflare is a *candidate*, and a metered one. Containers and Sandboxes are GA as of 2026-04-13 on a scale-to-zero model: $5/month Workers Paid including 25 GiB-hours of memory, 375 vCPU-minutes, and 200 GB-hours of disk, then per-second overage. A continuously-running instance blows through the included allowance in the first day or two, and instances cap at 4 GiB RAM and half a vCPU. Sandboxes sleep after 10 minutes idle unless `keepAlive` is set, and the SDK requires an explicit `sandbox.destroy()` or containers run indefinitely.
- **Confidence**: medium (rates partly from third-party calculators and community discussion; the live pricing page is the authority and is re-read in task 2)
- **Impact on plan**: The provider question stays open into the build with Cloudflare as the default rather than the foregone conclusion, and Gap D's "**a resource whose charge cannot be forecast is refused**" becomes the deciding rule between per-second metering and a flat monthly rate. Half a vCPU is separately a real risk for a `claude -p` subprocess and is a measured criterion in task 3.

## Data Flow

Two flows matter in this lane, and they are separate on purpose. Conflating them is how a progress report ends up reporting activity as improvement.

**Flow A — a dollar becomes an auditable, forecast reservation.**

1. **Entry point**: the controller (or an operator running `valor-improve`) proposes acquiring or renewing an infrastructure resource.
2. **Forecast**: `tools/infrastructure_budget.py` computes the resource's charge for the remainder of the current ISO week plus its forecast for the next, from a declared recurring rate. **A resource whose charge cannot be forecast is refused here**, before any provider call. A free tier, credit, or promotion is admitted with its expiry and the paid rate that follows it, and a credit expiring inside the window converts to a forecast charge on its expiry day.
3. **Admission**: the forecast is checked against `weekly_infrastructure_usd` for the window keyed by `budget_week_start` and `budget_day_boundary`. Both boundary settings are echoed into the decision record, because a reservation resetting on an undisclosed boundary cannot be audited. Unit 2's headroom is never read here; there is no code path between the units.
4. **Acquisition**: on admission, the resource is acquired under charter §8 authority, using only resources the probe reports `verified`. Any credential issued lands in `m-valor` through lane 3's `tools/vault_write.py` and never touches a log, a message, or a committed file.
5. **Settlement**: actual spend arrives from the provider's billing API where one exists, and otherwise from a receipt written as an `ImprovementEvidence` row of kind `spend_receipt`. Missing or uncertain metering settles as **the forecast**, never as zero.
6. **Output**: the week's reserved, settled, and remaining figures, with both boundary settings disclosed, readable by the dashboard and by the progress report.

**Flow B — a sandbox session becomes reportable evidence.**

1. **Entry point**: the controller admits a research session under `max_concurrent_research_sessions`.
2. **Dispatch**: the session is placed on the shared Redis queue. Nothing in the queue names a machine, which is why a remote worker can take it and why the sandbox needs no bridge identity.
3. **Execution**: the sandbox's `python -m worker` claims the session and spawns `claude -p` through the official CLI — the only shape spike-1 leaves open.
4. **Persistence**: the session writes `AgentSession` state, `ImprovementEvidence` rows, and artifacts to the durable store chosen in task 4. This is the step that fails silently today if the sandbox runs its own Redis: the run succeeds and the evidence is invisible.
5. **Liveness and recovery**: the sandbox writes `worker:registered_pid:*`; a crash is detected and the sandbox restarts unattended, or the session is re-queued.
6. **Output**: the dashboard shows the session's evidence beside every local session's, and the §2 progress report can say *which* sessions ran where, on what, at what cost, and what stopped the rest.

The join between the flows is the fifth answer. Flow A's remaining budget and Flow B's completed-unattended count are two different measurements, and neither one alone says whether the operating model moved.

## Architectural Impact

- **New dependencies**: one infrastructure provider account (Cloudflare by default under charter §8, decided in task 2), its CLI (`wrangler`, currently `absent` on the probing machine), and a container image definition for the worker. No new Python runtime dependency is expected; the budget meter is stdlib plus Popoto.
- **Interface changes**: `ImprovementEvidence.EVIDENCE_KINDS` gains `spend_receipt` — an additive change to a module constant that is read by `record_once` and asserted by tests. `ImprovementSettings` gains no new fields if #3255 lands as written; the only candidate change is relaxing the `le=4` bound on `max_concurrent_research_sessions`, and that happens only if task 9's evidence supports it.
- **Coupling**: this lane deliberately **decreases** coupling in one place and increases it in another. It decreases it by proving the worker is separable from the bridge and from launchd, which the codebase asserts in a docstring and has never demonstrated. It increases it by making durable state a network dependency: today a worker that loses Redis has lost localhost, which does not happen; tomorrow it has lost a network hop, which does.
- **Data ownership**: unchanged for sessions and evidence — Redis and Popoto stay authoritative. New: the infrastructure ledger (reservations, settlements, credit expiries) is owned by this lane's meter, and receipts are owned by `ImprovementEvidence`. The vault stays the sole owner of credentials.
- **Reversibility**: high, and deliberately so. Every artifact is additive: a new tool module, a new evidence kind, a new infra doc, a new report. Tearing the lane out means destroying a provider account and deleting three files. **The one irreversible act is spending money**, which is why admission refuses an unforecastable charge rather than reserving optimistically and reconciling later.

## Appetite

**Size:** Large

**Team:** Solo dev (spike agent, builder, validator, documentarian), PM

**Interactions:**
- PM check-ins: 2-3 (provider selection is a spend decision; the teardown policy is a policy decision; the concurrency revisit changes an operating parameter)
- Review rounds: 2+

Large is the honest size, and most of it is not coding time. The lane has three distinct kinds of work with different failure modes — a compliance and provider decision that cannot be rushed, a metering and policy build that is ordinary engineering, and a report whose whole value is that it is truthful — plus a dependency on two lanes that have not landed. The alignment overhead is the appetite.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| #3255 landed on `main` | `git -C . fetch origin main -q && git show origin/main:tools/improvement_resources.py > /dev/null 2>&1` | The resource probe this lane re-runs |
| Unit 3 exists in settings | `python -c "from config.settings import ImprovementSettings as S; assert 'weekly_infrastructure_usd' in S.model_fields and 'budget_week_start' in S.model_fields"` | The budget this lane meters, and its disclosed window |
| `op` authenticates non-interactively | `test -n "$OP_SERVICE_ACCOUNT_TOKEN" && OP_CACHE=false op vault list --format json > /dev/null` | The probe's `unknown`s resolve only under the service account |
| Vault writer available (acquisition tasks only) | `test -f tools/vault_write.py` | Lane 3 (#3215) owns credential writes; no credential is stored without it |
| `gh` reaches this repo | `GH_REPO=tomcounsell/ai gh issue view 3274 --json number -q .number` | The progress report is posted to #3177 |

The `op` check is the one that must run **on the machine that owns the `valor` project**, not on whichever machine the builder happens to be on. A probe re-run somewhere else measures a different machine and answers a different question. `wrangler` is deliberately absent from this table: whether it is needed is task 2's output, and requiring it up front presumes the provider decision.

## Solution

### Key Elements

- **Probe re-run under service-account auth** — the same `tools/improvement_resources.py::probe`, run on the machine that owns `valor` with `OP_SERVICE_ACCOUNT_TOKEN` set, with its result recorded as durable evidence rather than pasted into a report. spike-4 in lane 2b showed all four vault `unknown`s come from **one** failed listing, so one successful run resolves all four together.
- **`tools/infrastructure_budget.py`** — unit 3's meter and admission gate. Computes the ISO-week window from `budget_week_start` and `budget_day_boundary`, forecasts a recurring charge for the remainder of the window and the next, refuses what it cannot forecast, tracks credits with their expiry and the paid rate that follows, and settles from a billing API or a `spend_receipt` row. It reads `weekly_infrastructure_usd` and nothing else; the paid-inference pool is not visible to it.
- **`spend_receipt` as a first-class evidence kind** — added to `EVIDENCE_KINDS` with a TTL decision that outlives a budget week, so a settled dollar can still be audited when someone asks in month two.
- **The teardown policy** — a written, tested ladder that says what a budget-exhausted window actually does, given that tearing down a running trial is the wrong answer. Encoded as code, not as prose in a doc.
- **A cloud sandbox trial** — one worker-only sandbox that claims a research session from the shared queue, runs it through the official `claude` CLI, writes its evidence where the dashboard can read it, and survives a crash unattended. One is enough to convert an expectation into a measurement.
- **`tools/improvement_operating_report.py`** — generates charter §2's five answers from records, including the fifth. Machine-generated so it cannot drift into optimistic prose between runs, and re-runnable so the next report is comparable to this one.
- **The concurrency revisit** — a recorded, evidence-backed decision on `max_concurrent_research_sessions`, which may well be "unchanged, and here is why."

### Flow

**Charter §2 expectation** → Verify what we actually have (probe under service-account auth) → **Known resource position** → Decide the provider against a forecastable rate → **Admitted, reserved acquisition** → Run one session in the sandbox unattended → **Measured operating position** → Generate the five answers from records → **Progress report on #3177, including what still prevents the result**

### Technical Approach

**1. Verify before acquiring, and record the verification.** The probe re-run is the lane's first task and gates every acquisition task. Charter §8's "verify availability before relying on it" is satisfied by a recorded probe result, not by a builder's recollection. Any resource still `unknown` after the service-account run is treated as **absent for acquisition purposes and unknown for reporting purposes** — the probe's own asymmetry, carried forward: we do not acquire against an uncertain credential, and we do not report a resource missing on uncertain evidence.

**2. The provider is chosen on forecastability, not on price alone.** Gap D already refuses a resource whose charge cannot be forecast. spike-6 makes that rule decisive rather than decorative: Cloudflare's Containers model is per-second metered with a small included allowance and a documented failure mode where a container runs indefinitely unless explicitly destroyed. A flat monthly rate is trivially forecastable; a per-second rate is forecastable only with a bounded duty cycle. The build records the comparison and the decision, and charter §8 names Cloudflare, so a decision *against* it needs its evidence written down.

**3. Unit 3 has one reader and no bridge to unit 2.** `tools/infrastructure_budget.py` imports `weekly_infrastructure_usd`, `budget_week_start`, and `budget_day_boundary`, and never `daily_paid_inference_usd`. Charter §8 forbids moving spend between categories to evade a limit; the enforcement is that no code exists to do it, and an anti-criterion in Verification asserts the absence.

**4. The teardown policy, stated.** A budget-exhausted window:

- **closes admission** — no new acquisition, no renewal, no scale-up, for the remainder of the window;
- **classifies each running resource** as `trial` (attached to an open experiment that is still gathering evidence) or `standing` (no open trial depends on it);
- **tears down `standing` resources** at the end of the window, because idle capacity is not protecting evidence;
- **lets `trial` resources run** to the trial's end or the end of the following window, whichever comes first, and **records the continuation as a forecast overrun against the next window before it accrues**, so the next window opens already reserved against rather than discovering the charge later;
- **requires a verified evidence export as the precondition of every teardown.** A teardown that cannot confirm the export **fails closed, leaves the resource running, and escalates.** Losing a trial's evidence to save its hosting cost is the trade this policy exists to refuse;
- **treats a resource it cannot confirm torn down as still running and still charging.** This mirrors the probe's `unknown` bias and answers spike-6 directly: with a provider whose documented default is to run forever unless destroyed, an unconfirmed teardown is the expensive failure, not the cheap one.

**5. The sandbox is worker-only.** It runs `python -m worker` and the official `claude` CLI; it does not run the Telegram bridge, does not appear in `projects.json`, and is not reached by `remote-update.sh` (spikes 2 and 3). Code reaches it by image rebuild and redeploy. This is a genuinely different update contract from the fleet's, and saying so is cheaper than discovering it when `/update` reports success against a host it never touched.

**6. Durable state is decided before anything is acquired.** spike-4 leaves two options with real costs. The build picks one, writes down why, and proves the choice by the dashboard rendering a sandbox session's evidence beside a local one's. A sandbox whose evidence the dashboard cannot see has not moved the operating model, however well it ran.

**7. The report is generated, not written.** `tools/improvement_operating_report.py` reads records and emits the five answers. The fifth — what still prevents the intended result — is assembled from the recorded provisional assumptions, the `unknown` entries in the latest probe, and any resource refused by admission for want of a forecast. It is structurally impossible for it to come back empty while those inputs are non-empty, which is the property that makes the report worth reading.

**8. The concurrency revisit is a decision with evidence, and the null result is a real outcome.** The hypothesis to test is that a sandbox adds a *machine*, not subscription capacity — Gap D budgets Claude work as subscription concurrency precisely because the subscription is the constraint, and spike-1 confirms sandbox sessions draw on the same subscription under the same "ordinary, individual usage" standard. If that holds, the correct outcome is `max_concurrent_research_sessions` unchanged with the reasoning recorded, and the `le=4` bound at `config/settings.py:609` untouched. Raising it needs evidence that concurrency, not the subscription, was the binding constraint.

## Failure Path Test Strategy

This lane's failure paths are unusually consequential: one of them spends money and another loses evidence. Each gets an explicit test.

### Exception Handling Coverage
- [ ] `tools/infrastructure_budget.py` — every provider-facing call (billing API read, provider CLI invocation) is wrapped, and each handler is tested for an **observable** result: a `logger.warning` **and** a settlement that equals the forecast. Charter §8's "uncertain or missing metering is not zero cost" is a testable assertion, not a comment. Test: seed a billing reader that raises, assert settled == forecast and the warning fired.
- [ ] `tools/improvement_operating_report.py` — a record source that raises must degrade that one answer to an explicit "could not be determined" **and keep the other four**, never blank the report. Test: raise from each of the five sources in turn, assert four answers survive and the failed one says so.
- [ ] Teardown — a failure to *verify* the export must not fall through into teardown. Test: an export verifier that raises, and one that returns False, both leave the resource running and escalate. This is the single most important negative test in the lane, and it is mutation-checked: invert the guard and the test must fail.
- [ ] `tools/improvement_resources.py::probe` already has full exception coverage from #3255 and is not re-tested here. Its wrapper in this lane (the recorded-evidence write) is new and is tested for the case where the probe returns and the write fails.
- [ ] No `except Exception: pass` is introduced. Verification asserts the absence in this lane's files.

### Empty/Invalid Input Handling
- [ ] `tools/infrastructure_budget.py` with an empty ledger returns a full week of headroom, not a crash and not zero. Tested.
- [ ] A resource declared with a `None`, empty, or non-numeric rate is **refused**, not defaulted to zero. Tested per shape; this is the "cannot be forecast is refused" rule and it is the difference between a refusal and a free acquisition.
- [ ] A credit with no expiry is treated as expiring at the end of the current window rather than as indefinite. Tested.
- [ ] The report with **no** sandbox sessions must produce five real answers, one of which is "none," rather than an empty section. Tested: this is the state the first report will actually be generated in, so it is the primary case, not the edge case.
- [ ] Whitespace-only and empty `project_key` on any new record write is rejected before the write.

### Error State Rendering
- [ ] The report is the user-visible output. Its failure rendering (a source unavailable) is tested to reach the reader as a named gap, because a report that silently omits an answer reads as a report that had nothing to say.
- [ ] A refused acquisition surfaces the refusal *and its reason* — "no forecastable rate" and "week exhausted" are different states and must not both render as "not acquired."
- [ ] If the dashboard gains an infrastructure-spend panel, its empty and error states render before its populated state does. `ui/data/improvement.py`'s own docstring is the precedent: it refuses to ship permanently-empty tiles, and a spend panel with nothing behind it would be one.

## Test Impact

- [ ] `tests/unit/test_improvement_models.py` — UPDATE: the `EVIDENCE_KINDS` membership assertions gain `spend_receipt`. Any test asserting the tuple's exact length or exact contents fails on the addition and must be updated rather than loosened.
- [ ] `tests/unit/test_settings.py` — UPDATE **only if** task 9 changes `max_concurrent_research_sessions`. The `le=4` bound is asserted there; if the revisit concludes "unchanged," this file is untouched and that is the expected outcome.
- [ ] `tests/unit/test_improvement_resources.py` — no change. This lane runs the probe; it does not modify it. Listed so a builder does not "improve" a file that #3255 owns while its PR is still open.
- [ ] `tests/unit/test_ui_app.py` — UPDATE only if an infrastructure-spend partial is added. It carries the route assertions for the improvement partials; a new route without a new assertion there is an untested route.

New test files, all greenfield:

- [ ] `tests/unit/test_infrastructure_budget.py` — CREATE: window computation across the Monday 00:00 UTC boundary, forecast refusal, credit expiry mid-window, no-transfer-between-units, settlement from receipt and from billing API, missing metering settling at forecast.
- [ ] `tests/unit/test_teardown_policy.py` — CREATE: the full ladder, with the export-verification guard mutation-checked in both directions.
- [ ] `tests/unit/test_improvement_operating_report.py` — CREATE: all five answers present with no sandbox sessions; per-source degradation; the fifth answer non-empty whenever its inputs are non-empty.

No integration test asserts that a real sandbox ran. That evidence is a recorded artifact from task 8, not a CI fixture — a test that provisions a paid sandbox on every run is a recurring charge disguised as a test, and Gap D would have to reserve for it.

## Rabbit Holes

- **Building a general multi-provider sandbox abstraction.** One provider, chosen on recorded evidence, running one session. An interface with two implementations and no second provider in sight is speculative generality, and it makes the "which sessions run where" answer harder to compute, not easier.
- **Migrating the whole SDLC pipeline to the cloud.** Charter §2 says *mostly cloud sandboxes* as a first-month expectation for RSI operation. This lane proves one unattended RSI session and reports the distance to "mostly." Moving ordinary lanes is a different mandate with different risks.
- **Making `remote-update.sh` work on Linux.** Tempting, because it looks like the missing piece. It is a rewrite of a script whose first forty lines assume iCloud and launchd, in service of a host that should be updated by image rebuild anyway. spike-3 exists so nobody spends a week here.
- **Securing a network-reachable Redis as a side quest.** If task 4 chooses shared Redis, transport security is real work with real scope — TLS, auth, network boundaries — and it belongs to a task with its own name, not to a bullet inside "make the sandbox work."
- **Litigating Anthropic's terms.** spike-1 gives a rule and an unresolved scale question. The response to the unresolved part is a recorded provisional assumption under charter §9 and an honest fifth answer, not a legal analysis and not a message to Tom.
- **Perfecting the cost model before spending a dollar.** A forecast good enough to admit or refuse is the bar. A model that predicts the bill to the cent is a research project, and the actual bill settles it anyway.
- **Reporting sandbox count, uptime, or token volume as progress.** Charter §2 names all three as things that do not establish improvement. The report generator has no function that returns them, for the same reason `ui/data/improvement.py` has no function returning experiment count.

## Risks

### Risk 1: Subscription capacity is not available to a sandbox at the scale §2 expects
**Impact:** The first-month expectation is unreachable by any amount of infrastructure work. A lane that acquires sandboxes without resolving this buys hosting for sessions that cannot authenticate.
**Mitigation:** spike-1 resolved the rule (official CLI on a remote host: supported; subscription OAuth in a hosted runtime: blocked since January 2026) and left only the scale question open. The build runs the official CLI and nothing else, records the scale question as a provisional assumption with the observation that would overturn it (a rate-limit or account action attributable to sandbox usage), and reports it as the fifth answer. **This risk materializing is a successful outcome for this lane**, because the charter asks for an honest account of what prevents the result, not for the result at any cost.

### Risk 2: A running resource keeps charging after the week is exhausted
**Impact:** Silent budget overrun, and charter §8's limit breached by inaction rather than by decision.
**Mitigation:** The teardown policy makes continuation an explicit, recorded forecast overrun against the next window, booked before it accrues. `standing` resources are torn down; only `trial` resources continue, and only to a bounded horizon.

### Risk 3: Teardown destroys the evidence the trial was gathering
**Impact:** The lane spends money to produce evidence and then deletes it — the exact failure Gap D's "does not tear down running resources" clause anticipates.
**Mitigation:** Verified export is the precondition of teardown, and the guard fails closed: a verifier that raises or returns False leaves the resource running and escalates. Mutation-checked in both directions, because a guard that never fires is indistinguishable from no guard.

### Risk 4: The sandbox runs and its evidence never reaches the dashboard
**Impact:** The most expensive kind of null result — a successful unattended run that cannot be reported, because §2 asks *which sessions* ran in sandboxes and the answer lives in records nobody can query.
**Mitigation:** spike-4 makes durable-state topology a decision (task 4) that precedes acquisition, and "the dashboard renders a sandbox session's evidence beside a local one's" is a success criterion with a Verification row.

### Risk 5: A metered provider's charge cannot be forecast, and admission refuses the charter's own named resource
**Impact:** Charter §8 names a funded Cloudflare account. Refusing it on forecastability grounds looks like the lane ignoring the charter.
**Mitigation:** It is the opposite, and the plan says so where a reader will find it: Gap D's refusal rule is charter-derived, and a decision against Cloudflare is recorded with its evidence and its arithmetic. §8 grants authority to use the account; it does not require spending through it on an unforecastable rate. If a bounded duty cycle makes the rate forecastable, the refusal does not arise.

### Risk 6: The lane is blocked behind two unlanded lanes and stalls entirely
**Impact:** #3255 is an open PR and #3215 has no PR and no plan. A lane that waits for both does nothing for weeks.
**Mitigation:** The task list is explicitly split. Tasks 1 through 5 (probe re-run, provider decision, sandbox feasibility, state topology, evidence-kind and meter) need only #3255. Tasks 6 through 8 (acquisition, credential storage, the trial) need #3215's vault writer and control namespace. Tasks 9 and 10 (concurrency revisit, report) need whatever has landed by then and are written to produce a truthful report from a partial position — which is precisely what charter §2 asks for.

### Risk 7: "Unattended" is claimed on a run too short to have tested it
**Impact:** A four-hour green run reported as unattended operation, when the known headless failure mode is an OAuth refresh 401 at token expiry (spike-2's finding on issue #50743).
**Mitigation:** The trial's duration criterion is defined by **crossing a credential-refresh boundary and surviving at least one induced crash**, not by wall-clock hours. A run that has not crossed both has produced a different, lesser finding, and the report says which.

### Risk 8: The progress report drifts into optimism between runs
**Impact:** Charter §2's reporting obligation is discharged in form and defeated in substance — the specific dishonesty the parent plan names.
**Mitigation:** The report is generated from records rather than written, the fifth answer is assembled from recorded assumptions, `unknown` probe entries, and refused acquisitions, and the generator has no function returning sandbox count, uptime, or token volume. A Verification anti-criterion asserts those functions do not exist.

## Race Conditions

### Race 1: Concurrent admission against the same budget window
**Location:** `tools/infrastructure_budget.py`, the admission path
**Trigger:** Two acquisitions admitted in the same tick, or a controller tick overlapping an operator-initiated acquisition. Each reads the window's remaining headroom, each sees room, both are admitted, and the sum exceeds `weekly_infrastructure_usd`.
**Data prerequisite:** The window's reserved total must reflect every prior admission before the next admission reads it.
**State prerequisite:** No two admissions may hold a read-modify-write on the same window concurrently.
**Mitigation:** Reserve-then-check in a single atomic step against the window key, the same shape Gap D specifies for unit 2's `outstanding_slots + 1 <= max` check. A read followed by a separate write is the bug; the test drives two concurrent admissions whose sum exceeds the limit and asserts exactly one is admitted.

### Race 2: A resource is torn down while its trial is still writing evidence
**Location:** the teardown path, against Flow B step 4
**Trigger:** The window ends, the resource is classified `standing` because the trial's last evidence write has not landed yet, and teardown proceeds while a session is mid-write.
**Data prerequisite:** The `trial` / `standing` classification must be read after any in-flight session on that resource has quiesced, not concurrently with it.
**State prerequisite:** A session claimed on that resource must be either complete or re-queued before classification is trusted.
**Mitigation:** Classification reads session state and treats a resource with any claimed-and-unfinished session as `trial` regardless of its declared attachment. Combined with export-verification-before-teardown, an in-flight write blocks teardown twice over. The uncertain case resolves toward "leave it running," which costs money; the alternative costs evidence.

### Race 3: The report reads a window mid-settlement
**Location:** `tools/improvement_operating_report.py`, the cost answer
**Trigger:** The report runs while a settlement is being written, and reads reserved-but-unsettled figures as though settled.
**Data prerequisite:** Reserved and settled totals must be read as one consistent snapshot.
**State prerequisite:** None beyond a consistent read.
**Mitigation:** The report reads reserved and settled as a single snapshot and **publishes both figures separately** rather than a derived net. Two disclosed numbers cannot be internally inconsistent the way one derived number can, and the parent plan's "raw measures publish beside normalized ones" rule already requires it.

### Race 4: The probe result is read while being re-run
**Location:** task 1's recorded probe evidence
**Trigger:** The report or an admission reads the latest probe row while a fresh probe is being recorded, and sees a partial state.
**Data prerequisite:** A probe result is consumed only as a complete, single record.
**State prerequisite:** None.
**Mitigation:** The probe's result is written as **one immutable record per run** and readers take the newest complete one. `probe()` already returns a complete dict for all six resources or an `unknown` entry per resource, so there is no partial shape to write — the record is written once or not at all, never patched field by field.

## No-Gos (Out of Scope)

- `[EXTERNAL]` **Granting the `valor-local` service account write access to the `m-valor` vault.** Tom's action; the parent plan already names it as his one remaining manual step. Until it lands, this lane can read the vault under the service account but cannot store an issued credential, so tasks 6 and 7 stop at the boundary rather than working around it.
- `[EXTERNAL]` **Funding the provider account and any card authorization it needs.** Charter §8 grants authority to spend within the limit and names a virtual debit card; it does not make a card work. A declined card is a human/world condition this lane reports rather than routes around.
- `[ORDERED]` **Every acquisition task waits on #3215's `tools/vault_write.py`.** Lane 3 has no PR and no plan as of this writing. Storing a credential any other way violates charter §8's vault rule, and there is no acceptable interim shape — a credential in `.env`, in a log, or in a commit is the failure the rule exists to prevent.
- `[ORDERED]` **The whole lane waits on PR #3275.** The probe, the three budget units, and the window-boundary settings all live there. Building against the branch would fork the settings vocabulary a second time, which is the exact cost lane 2b was created to avoid paying four times.
- `[SEPARATE-SLUG #3218]` **Production promotion and rollback of anything this lane builds.** Lane 6 owns promotion, rollback drills, and the recursive comparison. A sandbox trial produces evidence; it does not promote a release.
- `[SEPARATE-SLUG #3217]` **Running the first complete research cycle in the sandbox.** Lane 5 owns the first full cycle and runs it on this machine by the parent plan's own sequencing. This lane proves one session runs unattended remotely; it does not take lane 5's cycle hostage to that.
- `[EXTERNAL]` **Making the Telegram bridge run in a sandbox.** Bridge hosting requires a `projects.json` machine-roster entry and an owner decision about inbound routing, and `~/Desktop/Valor/projects.json` is Tom's iCloud-private file, invisible from a sandbox. The sandbox target is worker-only (spike-2), and this stays out by design rather than by omission.
- `[SEPARATE-SLUG #3216]` **Anything about evaluation inputs, blinding, or statistics for work produced in a sandbox.** Lane 4 owns evaluation; a sandbox is where a session runs, not how its output is judged.

Also deliberately not done, and needing no tag because they are not deferrals — they are refusals with anti-criteria in Verification: no `CLAUDE_CODE_OAUTH_TOKEN` is introduced anywhere in the repository (spike-1's forbidden shortcut); no code path reads the paid-inference pool while computing infrastructure headroom (charter §8's no-transfer rule); and the report generator contains no function returning sandbox count, uptime, or token volume (charter §2's explicit non-measures).

## Update System

<!-- skeleton -->

## Agent Integration

<!-- skeleton -->

## Documentation

<!-- skeleton -->

## Success Criteria

<!-- skeleton -->

## Team Orchestration

<!-- skeleton -->

## Step by Step Tasks

<!-- skeleton -->

## Verification

<!-- skeleton -->

## Critique Results

<!-- Populated by /do-plan-critique. -->

---

## Open Questions

<!-- skeleton -->
