---
status: Ready
type: feature
appetite: Large
owner: Valor Engels
created: 2026-09-10
tracking: https://github.com/tomcounsell/ai/issues/3274
last_comment_id:
revision_applied: true
revision_applied_at: 2026-09-09T17:57:12Z
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
- **`docs/infra/granite-oauth-token.md`** — the repo's durable, non-archived record of how headless subscription auth actually works here, and the closest prior art this lane has. It documents the same failure the external GitHub issue rediscovers (short-lived session tokens expire "after roughly an hour", which a headless subprocess cannot re-auth through) and names `CLAUDE_CODE_OAUTH_TOKEN` (prefix `sk-ant-oat01-`, ~1-year life) as the prevention credential. Two of its facts bind this lane directly: the token is minted by `claude setup-token`, which "opens a browser window; the resulting token must be copied manually to the vault," and "a single token is shared across all machines via iCloud vault propagation." spike-3 established a Linux container has no iCloud and no browser, so **the repo's existing distribution mechanism does not reach a sandbox** and its rotation is a manual, browser-bound, roughly annual act performed elsewhere. Task 4 owns closing that gap; Risk 7 is narrowed accordingly.
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

Six spikes ran during planning, four code-reads and two web-research. They are recorded here so the build does not re-investigate them. The build opens with a second, shorter spike phase for the two questions that need a live provider account to answer (see tasks 2 through 4).

### spike-1: May RSI sessions consume Claude subscription capacity from a cloud sandbox?
- **Assumption**: "Charter §2's cloud-sandbox operating model is an infrastructure problem."
- **Method**: web-research
- **Finding**: It is a compliance problem first. Anthropic blocks Free/Pro/Max OAuth tokens outside the official Claude Code CLI (enforced January 2026), and since 2026-04-04 subscription limits cannot be consumed by third-party harnesses at all. Running the **official CLI on a remote host is explicitly supported**, which is the shape this repo already has: `worker/` spawns `claude -p` as a subprocess of the official CLI. What is *not* resolvable from public documentation is whether a continuously-running fleet of sandboxes falls inside "ordinary, individual usage," the standard subscription limits are written against.
- **Confidence**: high on the rule, **low on the scale question**
- **Impact on plan**: The lane proceeds on the official-CLI path and nothing else. The scale question becomes a recorded provisional assumption under charter §9 (evidence, confidence, consequence, and the observation that would overturn it) rather than a blocker or a question to Tom, and it is a named candidate for the fifth answer in the §2 progress report.
- **What the rule does and does not forbid** (corrected in revision; the round-1 reading was too broad). The blocked pairing is **token plus third-party harness**. Token plus the **official CLI** is the supported shape, and it is exactly what this repo already does: `agent/session_runner/role_driver.py::subscription_auth_env` (`:76-100`) blanks `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, and `ANTHROPIC_AUTH_TOKEN` at `:94-96` and then exports `CLAUDE_CODE_OAUTH_TOKEN` at `:97-99` into the `claude -p` subprocess. The blanking is deliberate doctrine, argued in the docstring at `:84-85` — "headless role turns must never fall back to metered API-key auth." So the sandbox's auth question is **not** "may the token exist there" but "**is the chosen runtime a remote host we operate, or a hosted service consuming the subscription on our behalf**," and that is undocumented rather than decided. Task 4 resolves it into one of three recorded verdicts and task 9 runs only under the one that is affirmatively supported. Supplying an API key instead is not a workaround: `:94` blanks it *before* the token lookup, so an API-key sandbox needs a code change to `subscription_auth_env`, which is a doctrine change and a tagged No-Go here.

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
- **Impact on plan**: Durable-state topology is a first-class design decision in this lane, not a deployment detail. It is task 7, it precedes acquisition, and "the sandbox writes evidence the dashboard can read" is a success criterion rather than an assumption.

### spike-5: Can a spend receipt be recorded today?
- **Assumption**: "Receipt-based metering is available as the fallback when a provider has no billing API."
- **Method**: code-read (`models/improvement_evidence.py:58-64`, `:219-223`)
- **Finding**: No. `EVIDENCE_KINDS` is `("correction", "inspiration", "shipped_work", "owner_liveness", "other")` on `main` and on the #3255 branch alike, and `record_once` silently rewrites any other kind to `"other"` after a `logger.warning`. A receipt would land in the same bucket as everything else uncategorized and be unqueryable as spend.
- **Confidence**: high
- **Impact on plan**: Adding `spend_receipt` to the vocabulary is this lane's, and it drags a TTL question with it: `ImprovementEvidence` expires on a 30-day window, which is shorter than the audit life of a budget week. Both are task 1, which now runs first so no earlier task records into `other`.

### spike-6: Does Cloudflare fit the $50/week unit for a 24/7 sandbox?
- **Assumption**: "Charter §8 names a funded Cloudflare account, so Cloudflare is the answer."
- **Method**: web-research
- **Finding**: Cloudflare is a *candidate*, and a metered one. Containers and Sandboxes are GA as of 2026-04-13 on a scale-to-zero model: $5/month Workers Paid including 25 GiB-hours of memory, 375 vCPU-minutes, and 200 GB-hours of disk, then per-second overage. A continuously-running instance blows through the included allowance in the first day or two, and instances cap at 4 GiB RAM and half a vCPU. Sandboxes sleep after 10 minutes idle unless `keepAlive` is set, and the SDK requires an explicit `sandbox.destroy()` or containers run indefinitely.
- **Confidence**: medium (rates partly from third-party calculators and community discussion; the live pricing page is the authority and is re-read in task 3)
- **Impact on plan**: The provider question stays open into the build with Cloudflare as the default rather than the foregone conclusion, and Gap D's "**a resource whose charge cannot be forecast is refused**" becomes the deciding rule between per-second metering and a flat monthly rate. Half a vCPU is separately a real risk for a `claude -p` subprocess and is a measured criterion in task 4.

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
4. **Persistence**: the session writes `AgentSession` state, `ImprovementEvidence` rows, and artifacts to the durable store chosen in task 7. This is the step that fails silently today if the sandbox runs its own Redis: the run succeeds and the evidence is invisible.
5. **Liveness and recovery**: the sandbox writes `worker:registered_pid:*`; a crash is detected and the sandbox restarts unattended, or the session is re-queued.
6. **Output**: the dashboard shows the session's evidence beside every local session's, and the §2 progress report can say *which* sessions ran where, on what, at what cost, and what stopped the rest.

The join between the flows is the fifth answer. Flow A's remaining budget and Flow B's completed-unattended count are two different measurements, and neither one alone says whether the operating model moved.

## Architectural Impact

- **New dependencies**: one infrastructure provider account (Cloudflare by default under charter §8, decided in task 3), its CLI (`wrangler`, currently `absent` on the probing machine), and a container image definition for the worker. No new Python runtime dependency is expected; the budget meter is stdlib plus Popoto.
- **Interface changes**: `ImprovementEvidence.EVIDENCE_KINDS` gains `spend_receipt` and `resource_probe` — additive changes to a module constant that is read by `record_once` and asserted by tests. The constant's comment calls it "low-cardinality on purpose" because it backs an index, so each addition is an index-cardinality decision argued in the docstring, not a free append. `ImprovementSettings` gains no new fields if #3255 lands as written; the only candidate change is relaxing the `le=4` bound on `max_concurrent_research_sessions`, and that happens only if task 10's evidence supports it.
- **Coupling**: this lane deliberately **decreases** coupling in one place and increases it in another. It decreases it by proving the worker is separable from the bridge and from launchd, which the codebase asserts in a docstring and has never demonstrated. It increases it by making durable state a network dependency: today a worker that loses Redis has lost localhost, which does not happen; tomorrow it has lost a network hop, which does.
- **Data ownership**: unchanged for sessions and evidence — Redis and Popoto stay authoritative. New: the infrastructure ledger (reservations, settlements, credit expiries) is owned by this lane's meter as ordinary Popoto models, and receipts are owned by `ImprovementEvidence`. One exception, declared rather than discovered: the unit-3 window counter is a plain Redis key, `improvement:budget:unit3:{window_key}`, outside Popoto so it can be reserved atomically; it migrates into lane 3's control namespace when #3215 lands. The vault stays the sole owner of credentials.
- **Retention root**: this lane takes ownership of where improvement artifacts persist. `POPOTO_IMPROVEMENT_CONTENT_PATH` currently defaults to `data/improvement_content` inside the checkout (`models/verifying_artifact_store.py:45-56`), which a container rebuild destroys; task 7 supersedes it and publishes the destination contract lane 3's export/import will write against.
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

The `op` check is the one that must run **on the machine that owns the `valor` project**, not on whichever machine the builder happens to be on. A probe re-run somewhere else measures a different machine and answers a different question. `wrangler` is deliberately absent from this table: whether it is needed is task 3's output, and requiring it up front presumes the provider decision.

## Solution

### Key Elements

- **Probe re-run under service-account auth** — the same `tools/improvement_resources.py::probe`, run on the machine that owns `valor` with `OP_SERVICE_ACCOUNT_TOKEN` set, with its result recorded as durable evidence rather than pasted into a report. spike-4 in lane 2b showed all four vault `unknown`s come from **one** failed listing, so one successful run resolves all four together.
- **`tools/infrastructure_budget.py`** — unit 3's meter and admission gate. Computes the ISO-week window from `budget_week_start` and `budget_day_boundary`, forecasts a recurring charge for the remainder of the window and the next, refuses what it cannot forecast, tracks credits with their expiry and the paid rate that follows, and settles from a billing API or a `spend_receipt` row. It reads `weekly_infrastructure_usd` and nothing else; the paid-inference pool is not visible to it.
- **`spend_receipt` and `resource_probe` as first-class evidence kinds** — added to `EVIDENCE_KINDS` with a TTL decision that outlives a budget week, so a settled dollar can still be audited when someone asks in month two, and a probe result is a queryable record rather than one more `other` row. Lane 3 owns `resource_acquired` and adds it itself; the ownership split is declared in the module so the two lanes rebase instead of colliding.
- **The teardown policy** — a written, tested ladder that says what a budget-exhausted window actually does, given that tearing down a running trial is the wrong answer. Encoded as code, not as prose in a doc.
- **A cloud sandbox trial** — one worker-only sandbox that claims a research session from the shared queue, runs it through the official `claude` CLI, writes its evidence where the dashboard can read it, and survives a crash unattended. One is enough to convert an expectation into a measurement.
- **`tools/improvement_operating_report.py`** — generates charter §2's five answers from records, including the fifth. Machine-generated so it cannot drift into optimistic prose between runs, and re-runnable so the next report is comparable to this one.
- **The concurrency revisit** — a recorded, evidence-backed decision on `max_concurrent_research_sessions`, which may well be "unchanged, and here is why."

### Flow

**Charter §2 expectation** → Verify what we actually have (probe under service-account auth) → **Known resource position** → Decide the provider against a forecastable rate → **Admitted, reserved acquisition** → Run one session in the sandbox unattended → **Measured operating position** → Generate the five answers from records → **Progress report on #3177, including what still prevents the result**

### Technical Approach

**1. Verify before acquiring, and record the verification.** The probe re-run is the lane's first task and gates every acquisition task. Charter §8's "verify availability before relying on it" is satisfied by a recorded probe result, not by a builder's recollection. Any resource still `unknown` after the service-account run is treated as **absent for acquisition purposes and unknown for reporting purposes** — the probe's own asymmetry, carried forward: we do not acquire against an uncertain credential, and we do not report a resource missing on uncertain evidence.

**2. The provider is chosen on forecastability, not on price alone.** Gap D already refuses a resource whose charge cannot be forecast. spike-6 makes that rule decisive rather than decorative: Cloudflare's Containers model is per-second metered with a small included allowance and a documented failure mode where a container runs indefinitely unless explicitly destroyed. A flat monthly rate is trivially forecastable; a per-second rate is forecastable only with a bounded duty cycle. The build records the comparison and the decision, and charter §8 names Cloudflare, so a decision *against* it needs its evidence written down.

**3. Unit 3 has one reader, no bridge to unit 2, and a named key namespace.** `tools/infrastructure_budget.py` imports `weekly_infrastructure_usd`, `budget_week_start`, and `budget_day_boundary`, and never `daily_paid_inference_usd`. Charter §8 forbids moving spend between categories to evade a limit; the enforcement is that no code exists to do it, and an anti-criterion in Verification asserts the absence.

The window counter that admission reserves against is **not a Popoto model**. It is a plain Redis string key, `improvement:budget:unit3:{window_key}`, owned by this lane and declared here so nobody has to infer it. That placement is the whole point: CLAUDE.md's rule is "never use raw Redis on **Popoto-managed** keys," and Popoto offers no compare-and-set, so a window total stored as a Popoto model could not be reserved atomically at all. A non-Popoto key sits outside the rule and may be reserved with a Lua `EVAL`, which is the shape Gap D specifies for unit 2. The **decision records** — reservations, settlements, credit expiries — stay Popoto models read and written through the ORM; only the counter is raw, and only because atomicity requires it. Lane 3 (#3215) owns "the control namespace and Lua transition" (`docs/plans/recursive-self-improvement.md:800`), and when it lands this counter and its script move into that namespace as a named migration. **This lane does not block on #3215 for the counter**, because a lane that cannot meter a dollar cannot refuse one either.

**Every reservation has a paired release, and the window key has a bounded expiry.** `admit()` returns a reservation id alongside its decision, and the acquisition path releases that id on every failure exit — from `except` and from `finally`, never from the caller's happy path. The release is the compensating `EVAL` (`INCRBYFLOAT key -amount`, floored at 0 inside the script so a double release cannot drive the counter negative), and it is idempotent by reservation id: the release is recorded on the Popoto reservation model and the script is a no-op once that model shows `released`. Idempotence is load-bearing rather than decorative — task 8 retries, and a double decrement silently *raises* headroom, which is the expensive direction. Without the pair, headroom shrinks monotonically on every failed acquisition (a provider error, the declined card the No-Gos already anticipate, a process that dies between reserve and acquire) until a window that spent nothing refuses everything. The window key carries an `EXPIRE` set past the audit horizon rather than to the window length: a counter expiring mid-window resets headroom to full, which is the same failure wearing the opposite sign. The plan already argues `ImprovementEvidence`'s 30-day TTL is shorter than a budget week's audit life, and the window key's expiry is set against that audit horizon, not against the week.

**4. The teardown policy, stated.** A budget-exhausted window:

- **closes admission** — no new acquisition, no renewal, no scale-up, for the remainder of the window;
- **classifies each running resource** as `trial` (attached to an open experiment that is still gathering evidence) or `standing` (no open trial depends on it);
- **tears down `standing` resources** at the end of the window, because idle capacity is not protecting evidence;
- **lets `trial` resources run** to the trial's end or the end of the following window, whichever comes first, and **records the continuation as a forecast overrun against the next window before it accrues**, so the next window opens already reserved against rather than discovering the charge later;
- **requires a verified evidence export as the precondition of every teardown.** A teardown that cannot confirm the export **fails closed, leaves the resource running, and escalates onto the named surface below.** Losing a trial's evidence to save its hosting cost is the trade this policy exists to refuse;
- **treats a resource it cannot confirm torn down as still running and still charging.** This mirrors the probe's `unknown` bias and answers spike-6 directly: with a provider whose documented default is to run forever unless destroyed, an unconfirmed teardown is the expensive failure, not the cheap one.

**"Escalates" is a record, not a verb.** A guard that leaves a paid resource running and reports to nobody is Risk 2's silent overrun arriving through the mechanism built to prevent it, so the surface is named here and asserted in test. Two outputs, both on surfaces that already exist:

1. **A durable `spend_receipt` row** booking the resource's continued charge as a forecast overrun against the next window, carrying the resource id, the teardown attempt, the verifier's failure mode (raised, or returned False), and the forecast amount. This is the surface of record, and it is available the moment task 1 lands: a resource that keeps running keeps charging, so the overrun *is* the receipt. **No third evidence kind is introduced** — task 1's index-cardinality argument for exactly two values does not survive a third added to carry a message.
2. **A charter §9-compatible Telegram status message** through the parent plan's `improvement-assumption-digest` reflection, which sends a status report that asks nothing and is therefore permitted where a question is not. That reflection is lane 5's and is not landed, so this leg is an **optional sink**: where the digest exists the escalation appears in it, and where it does not, the row in (1) is still the record. A surface that does not exist yet cannot be the only place an overrun is reported.

Both are asserted inside the existing mutation check in `tests/unit/test_teardown_policy.py`: the verifier-raises case and the verifier-returns-False case must **each** leave the resource running **and** produce the escalation row. A guard observable only in a log line nobody reads is not observable.

**5. The sandbox is worker-only.** It runs `python -m worker` and the official `claude` CLI; it does not run the Telegram bridge, does not appear in `projects.json`, and is not reached by `remote-update.sh` (spikes 2 and 3). Code reaches it by image rebuild and redeploy. This is a genuinely different update contract from the fleet's, and saying so is cheaper than discovering it when `/update` reports success against a host it never touched.

**5a. The sandbox's authentication mode is a decided, recorded output of task 4, and task 9 runs only under a mode task 4 affirmed.** This is the concrete gap the round-1 critique found: `python -m worker` builds its `claude -p` environment through `agent/session_runner/role_driver.py::subscription_auth_env` (`:76-100`), which blanks `ANTHROPIC_API_KEY` at `:94` *before* reading `CLAUDE_CODE_OAUTH_TOKEN` at `:97`. A sandbox with neither the token nor a code change runs an unauthenticated `claude -p`. Three modes, and task 4 picks exactly one:

- **Mode A — subscription on a host we operate (the default, and the only one that needs no code change).** The runtime is a container or VM we control, the vault's `CLAUDE_CODE_OAUTH_TOKEN` is injected as a provider secret at deploy time, `subscription_auth_env` runs unchanged, and the doctrine at `:84-85` stands. This is spike-1's supported shape. Its cost is stated plainly rather than hidden: the token cannot be minted inside the sandbox (`claude setup-token` opens a browser) and cannot arrive by iCloud, so the sandbox holds a credential it received from outside and cannot renew — see Risk 7, which is narrowed to match.
- **Mode B — unresolved, so the trial does not run on subscription auth.** Task 4 cannot affirm from vendor documentation that the chosen runtime is a host we operate rather than a hosted service consuming the subscription. Task 9 does not run under mode B. The finding goes straight into the fifth answer of the §2 progress report, which is a real deliverable and not a consolation prize.
- **Mode C — API-key auth, deferred.** Requires changing `subscription_auth_env` so a caller can opt into API-key auth explicitly (an argument, never deleting the blanking), plus a docstring change at `:84-85` and a budget unit that funds metered inference. It is a doctrine change with an argument to make, and it is a tagged No-Go in this lane rather than a fallback the builder can reach for at 2am.

No mode introduces a *new* consumer of the token. Mode A hands the existing consumer the credential it already expects, which is why the Verification anti-criterion is scoped to this lane's own Python files rather than to the repository.

**6. Durable state is decided before anything is acquired, and "durable state" includes the artifact retention root.** spike-4 leaves two options with real costs for sessions and evidence. The build picks one, writes down why, and proves the choice by the dashboard rendering a sandbox session's evidence beside a local one's. A sandbox whose evidence the dashboard cannot see has not moved the operating model, however well it ran.

The parent plan scopes one more thing to this lane that round 1 found silently dropped: lane 7 "supersedes the local retention root and migrates export/import" (`docs/plans/recursive-self-improvement.md:804`), and lanes 1 and 2 "keep the local retention root plus export/import so lane 7 has something to migrate" (`:572`). That root is real and already on `main`: `models/verifying_artifact_store.py::_default_base_path` (`:45-56`) resolves `POPOTO_IMPROVEMENT_CONTENT_PATH`, defaulting to `data/improvement_content` **inside the repo checkout** — a path a container rebuilds away on every redeploy. Two distinct pieces of work follow, and conflating them is how one of them gets lost:

- **Supersession** is a topology choice this lane owns end to end: the retention root moves off the checkout to whatever the topology decision above chose, exercised by a **cross-process** round trip that needs no provider account — `POPOTO_IMPROVEMENT_CONTENT_PATH` pointed at the new root, an artifact written through `VerifyingArtifactStore` from one process and loaded, hash-verified since the store re-hashes on every load, from a second process started in a different working directory. `_default_base_path` reads the env var fresh on every call for exactly this reason (its own docstring says so at `:45-56`), so a second local process is a faithful stand-in for the container. That is one acceptance test, it belongs here, and it is satisfiable in phase 2. The literal sandbox-to-local round trip is a stronger observation of the same property and is recorded as task 9 evidence; it is not the bar, because under mode B there is no sandbox and this lane's supersession would otherwise have an acceptance test that can never pass.
- **Migrating export/import** is data movement whose other end does not exist yet: `valor-improve export` / `import` is lane 3's (`:424`, `:800`), unwritten. This lane therefore ships the **destination contract** — the retention root's new location, its verification-on-load guarantee, and a documented archive path — and lane 3 writes against it. It does not write `valor-improve export`. Task 7 records this split so the parent plan's lane-7 success line is fully owned rather than half-owned, and so lane 3's export has a landing place named before it needs one.

**7. The report is generated, not written.** `tools/improvement_operating_report.py` reads records and emits the five answers. The fifth — what still prevents the intended result — is assembled from the recorded provisional assumptions, the `unknown` entries in the latest probe, and any resource refused by admission for want of a forecast. It is structurally impossible for it to come back empty while those inputs are non-empty, which is the property that makes the report worth reading.

**8. The concurrency revisit is a decision with evidence, and the null result is a real outcome.** The hypothesis to test is that a sandbox adds a *machine*, not subscription capacity — Gap D budgets Claude work as subscription concurrency precisely because the subscription is the constraint, and spike-1 confirms sandbox sessions draw on the same subscription under the same "ordinary, individual usage" standard. If that holds, the correct outcome is `max_concurrent_research_sessions` unchanged with the reasoning recorded, and the `le=4` bound at `config/settings.py:609` untouched. Raising it needs evidence that concurrency, not the subscription, was the binding constraint.

## Failure Path Test Strategy

This lane's failure paths are unusually consequential: one of them spends money and another loses evidence. Each gets an explicit test.

### Exception Handling Coverage
- [ ] `tools/infrastructure_budget.py` — every provider-facing call (billing API read, provider CLI invocation) is wrapped, and each handler is tested for an **observable** result: a `logger.warning` **and** a settlement that equals the forecast. Charter §8's "uncertain or missing metering is not zero cost" is a testable assertion, not a comment. Test: seed a billing reader that raises, assert settled == forecast and the warning fired.
- [ ] `tools/improvement_operating_report.py` — a record source that raises must degrade that one answer to an explicit "could not be determined" **and keep the other four**, never blank the report. Test: raise from each of the five sources in turn, assert four answers survive and the failed one says so.
- [ ] Teardown — a failure to *verify* the export must not fall through into teardown. Test: an export verifier that raises, and one that returns False, both leave the resource running **and write the escalation row** named in Technical Approach §4. Asserting the record, not just the survival, is what keeps "escalates" from being a verb with no output. This is the single most important negative test in the lane, and it is mutation-checked: invert the guard and both assertions must fail.
- [ ] Acquisition — every failure exit releases its reservation. Test: an acquisition that raises after a successful `admit()`, and one that dies between reserve and acquire, both return the window's headroom exactly once; a replayed release is a no-op. Without this the meter leaks headroom on every failure until a window that spent nothing refuses everything.
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

- [ ] `tests/unit/test_improvement_models.py` — UPDATE: the `EVIDENCE_KINDS` membership assertions gain **both** `spend_receipt` and `resource_probe`. Any test asserting the tuple's exact length or exact contents fails on the addition and must be updated rather than loosened.
- [ ] `tests/unit/test_settings.py` — UPDATE **only if** task 10 changes `max_concurrent_research_sessions`. The `le=4` bound is asserted there; if the revisit concludes "unchanged," this file is untouched and that is the expected outcome.
- [ ] `tests/unit/test_improvement_resources.py` — no change. This lane runs the probe; it does not modify it. Listed so a builder does not "improve" a file that #3255 owns while its PR is still open.
- [ ] `tests/unit/test_ui_app.py` — UPDATE only if an infrastructure-spend partial is added. It carries the route assertions for the improvement partials; a new route without a new assertion there is an untested route.

New test files, all greenfield:

- [ ] `tests/unit/test_infrastructure_budget.py` — CREATE: window computation across the Monday 00:00 UTC boundary, forecast refusal, credit expiry mid-window, no-transfer-between-units, settlement from receipt and from billing API, missing metering settling at forecast, concurrent admission against one window, **reservation release on the failure path and its idempotent replay**, **the window key's expiry outliving its window**, **the `max(settled, forecast)` live-window stop firing against a billing reader that reports zero**, and `test_no_acquisition_bypasses_admission` (task 8's `Validates`, described there).
- [ ] `tests/unit/test_teardown_policy.py` — CREATE: the full ladder, with the export-verification guard mutation-checked in both directions **and the escalation record asserted in each direction**.
- [ ] `tests/unit/test_improvement_operating_report.py` — CREATE: all five answers present with no sandbox sessions; per-source degradation; the fifth answer non-empty whenever its inputs are non-empty.
- [ ] `tests/unit/test_length_safe_content_store.py` — UPDATE if task 7 supersedes the retention root. `TestVerifyingArtifactStore` lives there and `test_retention_root_is_separate_from_the_shared_content_path` (`:224`) asserts the current `data/improvement_content` default through `_default_base_path`. A superseded root changes what that test asserts, and it must be updated rather than deleted — the separation it guards is the reason the root exists.

No integration test asserts that a real sandbox ran. That evidence is a recorded artifact from task 9, not a CI fixture — a test that provisions a paid sandbox on every run is a recurring charge disguised as a test, and Gap D would have to reserve for it.

## Rabbit Holes

- **Building a general multi-provider sandbox abstraction.** One provider, chosen on recorded evidence, running one session. An interface with two implementations and no second provider in sight is speculative generality, and it makes the "which sessions run where" answer harder to compute, not easier.
- **Migrating the whole SDLC pipeline to the cloud.** Charter §2 says *mostly cloud sandboxes* as a first-month expectation for RSI operation. This lane proves one unattended RSI session and reports the distance to "mostly." Moving ordinary lanes is a different mandate with different risks.
- **Making `remote-update.sh` work on Linux.** Tempting, because it looks like the missing piece. It is a rewrite of a script whose first forty lines assume iCloud and launchd, in service of a host that should be updated by image rebuild anyway. spike-3 exists so nobody spends a week here.
- **Securing a network-reachable Redis as a side quest.** If task 7 chooses shared Redis, transport security is real work with real scope — TLS, auth, network boundaries — and it belongs to a task with its own name, not to a bullet inside "make the sandbox work."
- **Litigating Anthropic's terms.** spike-1 gives a rule and an unresolved scale question. The response to the unresolved part is a recorded provisional assumption under charter §9 and an honest fifth answer, not a legal analysis and not a message to Tom.
- **Perfecting the cost model before spending a dollar.** A forecast good enough to admit or refuse is the bar. A model that predicts the bill to the cent is a research project, and the actual bill settles it anyway.
- **Reporting sandbox count, uptime, or token volume as progress.** Charter §2 names all three as things that do not establish improvement. The report generator has no function that returns them, for the same reason `ui/data/improvement.py` has no function returning experiment count.

## Risks

### Risk 1: Subscription capacity is not available to a sandbox at the scale §2 expects
**Impact:** The first-month expectation is unreachable by any amount of infrastructure work. A lane that acquires sandboxes without resolving this buys hosting for sessions that cannot authenticate.
**Mitigation:** spike-1 resolved the rule (official CLI on a remote host: supported; subscription OAuth in a hosted runtime: blocked since January 2026) and left only the scale question open. The build runs the official CLI and nothing else, records the scale question as a provisional assumption with the observation that would overturn it (a rate-limit or account action attributable to sandbox usage), and reports it as the fifth answer. **This risk materializing is a successful outcome for this lane**, because the charter asks for an honest account of what prevents the result, not for the result at any cost.

### Risk 2: A running resource keeps charging after the week is exhausted
**Impact:** Silent budget overrun, and charter §8's limit breached by inaction rather than by decision.
**Mitigation:** The teardown policy makes continuation an explicit, recorded forecast overrun against the next window, booked before it accrues. `standing` resources are torn down; only `trial` resources continue, and only to a bounded horizon. The fail-closed branch of the export guard escalates onto a **named** surface rather than into a log (Technical Approach §4): a `spend_receipt` row booking the continued charge, plus the digest status message where that reflection exists. This risk arriving *through* Risk 3's guard is the shape that naming closes.

### Risk 3: Teardown destroys the evidence the trial was gathering
**Impact:** The lane spends money to produce evidence and then deletes it — the exact failure Gap D's "does not tear down running resources" clause anticipates.
**Mitigation:** Verified export is the precondition of teardown, and the guard fails closed: a verifier that raises or returns False leaves the resource running and escalates onto the named surface in Technical Approach §4 — a durable overrun row, with the digest message as an optional second sink. Mutation-checked in both directions, and the escalation record is asserted in each, because a guard that never fires is indistinguishable from no guard and an escalation nobody records is indistinguishable from none.

### Risk 4: The sandbox runs and its evidence never reaches the dashboard
**Impact:** The most expensive kind of null result — a successful unattended run that cannot be reported, because §2 asks *which sessions* ran in sandboxes and the answer lives in records nobody can query.
**Mitigation:** spike-4 makes durable-state topology a decision (task 7) that precedes acquisition, and "the dashboard renders a sandbox session's evidence beside a local one's" is a success criterion with a Verification row.

### Risk 5: A metered provider's charge cannot be forecast, and admission refuses the charter's own named resource
**Impact:** Charter §8 names a funded Cloudflare account. Refusing it on forecastability grounds looks like the lane ignoring the charter.
**Mitigation:** It is the opposite, and the plan says so where a reader will find it: Gap D's refusal rule is charter-derived, and a decision against Cloudflare is recorded with its evidence and its arithmetic. §8 grants authority to use the account; it does not require spending through it on an unforecastable rate. If a bounded duty cycle makes the rate forecastable, the refusal does not arise.

### Risk 6: The lane is blocked behind two unlanded lanes and stalls entirely
**Impact:** #3255 is an open PR and #3215 has no PR and no plan. A lane that waits for both does nothing for weeks.
**Mitigation:** The task graph was re-cut in revision so the free deliverable is not held hostage by the expensive one. **The §2 progress report (`build-operating-report`) depends on `build-infrastructure-budget` and `spike-probe-rerun` only** — the cost answer and the fifth answer's `unknown` entries. It does not depend on the acquisition, the trial, or the concurrency revisit, and folds each of those in only if it has run. That is not an optimistic re-wiring; the plan already requires the report to work from a zero-sandbox position ("this is the state the first report will actually be generated in, so it is the primary case, not the edge case"), and Verification tests exactly that.

Phase 1 (evidence kinds, probe re-run, provider decision, sandbox feasibility) and phase 2 (meter and teardown policy, the report, state topology) need only #3255 and spend nothing. Phase 3 (acquisition, trial, concurrency revisit) needs #3215's vault writer. **If #3215 never lands, the lane still ships a metered unit 3, a teardown policy, a resolved resource position, a provider decision, an auth verdict, and the charter §2 report** — with an unusually well-evidenced fifth answer. Documentation (task 11) describes what landed; the report is regenerated after any later task lands, since it is re-runnable by design.

### Risk 7: "Unattended" is claimed on a run too short to have tested it
**Impact:** A four-hour green run reported as unattended operation, when the known headless failure mode is an OAuth refresh 401 at token expiry (spike-2's finding on issue #50743).
**Mitigation:** The trial's duration criterion is defined by **crossing a credential-refresh boundary and surviving at least one induced crash**, not by wall-clock hours. A run that has not crossed both has produced a different, lesser finding, and the report says which. Task 9 carries a default bound (72 hours of continuous availability, hard-capped, and hard-stopped at the earlier of that or $15 of unit-3 settlement) so a builder is never waiting on a preference answer to start. The dollar leg of that stop reads `max(settled, forecast)`, never settled alone, because the default provider meters per second and settles late.

**And the claim itself is narrower than it first reads.** Under mode A the sandbox holds a `CLAUDE_CODE_OAUTH_TOKEN` it received from outside: `docs/infra/granite-oauth-token.md` records that the token is minted by `claude setup-token`, which "opens a browser window; the resulting token must be copied manually to the vault," and distributed by iCloud vault propagation — neither of which a Linux container has. So what the trial tests is **unattended operation across an access-token refresh within the long-lived token's life**, which is the failure mode issue #50743 reports and the one that actually bites in a 72-hour window. What it does **not** test, and the report must not claim, is unattended *rotation* of the long-lived token itself, which stays a manual, browser-bound, roughly annual act performed elsewhere. Task 9 states which claim it is making.

### Risk 8: The progress report drifts into optimism between runs
**Impact:** Charter §2's reporting obligation is discharged in form and defeated in substance — the specific dishonesty the parent plan names.
**Mitigation:** The report is generated from records rather than written, the fifth answer is assembled from recorded assumptions, `unknown` probe entries, and refused acquisitions, and the generator has no function returning sandbox count, uptime, or token volume. A Verification anti-criterion asserts those functions do not exist.

## Race Conditions

### Race 1: Concurrent admission against the same budget window
**Location:** `tools/infrastructure_budget.py`, the admission path
**Trigger:** Two acquisitions admitted in the same tick, or a controller tick overlapping an operator-initiated acquisition. Each reads the window's remaining headroom, each sees room, both are admitted, and the sum exceeds `weekly_infrastructure_usd`.
**Data prerequisite:** The window's reserved total must reflect every prior admission before the next admission reads it.
**State prerequisite:** No two admissions may hold a read-modify-write on the same window concurrently.
**Mitigation:** Reserve-then-check in a single atomic step against the window key `improvement:budget:unit3:{window_key}`, the same shape Gap D specifies for unit 2's `outstanding_slots + 1 <= max` check. A read followed by a separate write is the bug; the test drives two concurrent admissions whose sum exceeds the limit and asserts exactly one is admitted. **Reservation and release are paired** (Technical Approach §3): the reserve is a Lua `EVAL`, the release is its compensating `EVAL` floored at 0 and idempotent by reservation id, and the window key carries an expiry set past the audit horizon rather than to the window length. A second test drives a failed acquisition after a successful admission and asserts the headroom returns exactly once — without it, this mitigation trades a double-spend for a monotonic leak.
**Why this does not wait on lane 3:** the counter is a plain Redis string key, not a Popoto model (Technical Approach §3), so CLAUDE.md's "never use raw Redis on Popoto-managed keys" does not reach it and a Lua `EVAL` is available today. Lane 3 (#3215) owns the eventual control namespace and Lua transition (`docs/plans/recursive-self-improvement.md:800`); when it lands, the counter and script migrate into it. Had the counter been a Popoto model, it could not have been reserved atomically at all — Popoto offers no compare-and-set — so this is a placement decision made deliberately rather than a rule bent.

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
**Location:** task 2's recorded probe evidence
**Trigger:** The report or an admission reads the latest probe row while a fresh probe is being recorded, and sees a partial state.
**Data prerequisite:** A probe result is consumed only as a complete, single record.
**State prerequisite:** None.
**Mitigation:** The probe's result is written as **one immutable record per run** and readers take the newest complete one. `probe()` already returns a complete dict for all six resources or an `unknown` entry per resource, so there is no partial shape to write — the record is written once or not at all, never patched field by field.

## No-Gos (Out of Scope)

- `[EXTERNAL]` **Granting the `valor-local` service account write access to the `m-valor` vault.** Tom's action; the parent plan already names it as his one remaining manual step. Until it lands, this lane can read the vault under the service account but cannot store an issued credential, so tasks 8 and 9 stop at the boundary rather than working around it.
- `[EXTERNAL]` **Funding the provider account and any card authorization it needs.** Charter §8 grants authority to spend within the limit and names a virtual debit card; it does not make a card work. A declined card is a human/world condition this lane reports rather than routes around.
- `[ORDERED]` **Every acquisition task waits on #3215's `tools/vault_write.py`.** Lane 3 has no PR and no plan as of this writing. Storing a credential any other way violates charter §8's vault rule, and there is no acceptable interim shape — a credential in `.env`, in a log, or in a commit is the failure the rule exists to prevent.
- `[ORDERED]` **The whole lane waits on PR #3275.** The probe, the three budget units, and the window-boundary settings all live there. Building against the branch would fork the settings vocabulary a second time, which is the exact cost lane 2b was created to avoid paying four times.
- `[SEPARATE-SLUG #3218]` **Production promotion and rollback of anything this lane builds.** Lane 6 owns promotion, rollback drills, and the recursive comparison. A sandbox trial produces evidence; it does not promote a release.
- `[SEPARATE-SLUG #3217]` **Running the first complete research cycle in the sandbox.** Lane 5 owns the first full cycle and runs it on this machine by the parent plan's own sequencing. This lane proves one session runs unattended remotely; it does not take lane 5's cycle hostage to that.
- `[EXTERNAL]` **Making the Telegram bridge run in a sandbox.** Bridge hosting requires a `projects.json` machine-roster entry and an owner decision about inbound routing, and `~/Desktop/Valor/projects.json` is Tom's iCloud-private file, invisible from a sandbox. The sandbox target is worker-only (spike-2), and this stays out by design rather than by omission.
- `[SEPARATE-SLUG #3216]` **Anything about evaluation inputs, blinding, or statistics for work produced in a sandbox.** Lane 4 owns evaluation; a sandbox is where a session runs, not how its output is judged.

- `[SEPARATE-ISSUE]` **Teaching `subscription_auth_env` an API-key mode (auth mode C).** `agent/session_runner/role_driver.py:94` blanks `ANTHROPIC_API_KEY` before the token lookup, and its docstring at `:84-85` argues the blanking on purpose: "headless role turns must never fall back to metered API-key auth." Adding an explicit opt-in is a doctrine change that needs its own argument, its own budget unit to fund metered inference, and its own issue. If task 4 returns mode B, that is the honest fifth answer, **not** a cue to reach for mode C mid-build.

Also deliberately not done, and needing no tag because they are not deferrals — they are refusals with anti-criteria in Verification:

- **No new consumer of `CLAUDE_CODE_OAUTH_TOKEN` is introduced by this lane.** Round 1 caught the round-0 phrasing ("no token anywhere in the repository") as both wrong and unachievable: the token appears 34 times on `main` at `191bd42a1` — `.env.example`, `tools/doctor.py`, `agent/session_runner/role_driver.py`, `agent/session_runner/harness/claude_diagnostics.py`, and four test files — because it *is* the repo's sanctioned headless auth path, and driving that count to zero would mean deleting it. spike-1's rule blocks the token with a **third-party harness** while explicitly supporting the **official CLI** on a remote host, which is what `subscription_auth_env` already builds. So the refusal is scoped to this lane's delta: no Python file this lane adds or changes references the token, and the credential reaches the sandbox as a deploy-time provider secret consumed by the existing code path, never as a new reader and never in a commit. The Verification row is diff-scoped to match, so it starts green and can be shown red.
- **No code path reads the paid-inference pool while computing infrastructure headroom** (charter §8's no-transfer rule).
- **The report generator contains no function returning sandbox count, uptime, or token volume** (charter §2's explicit non-measures).

## Update System

Two changes, and one deliberate exclusion that matters more than either.

**Changes to the fleet path:**

- `.env.example` gains the provider's credential key with a real comment block, plus `# @passthrough wrangler` if the provider CLI reads a key this codebase never does. Every declaration is required unless its block carries a bare `# @optional`, so an optional key gets that marker deliberately rather than by whatever the env-completeness check happens to flag on one machine.
- If a Popoto model is added for the infrastructure ledger, `scripts/update/migrations.py` gains an idempotent migration registered in the `MIGRATIONS` dict — required, since `run_pending_migrations()` iterates that dict and an unregistered function never runs. The `spend_receipt` and `resource_probe` additions are values in a module constant, not a schema change, and need no migration; the reader should not go looking for one.
- If task 7's retention-root supersession changes `POPOTO_IMPROVEMENT_CONTENT_PATH`'s effective default rather than only its override, existing artifacts under `data/improvement_content` need a move, and that move is an idempotent registered migration like any other. A path change that silently orphans artifacts a `VerifyingArtifactStore` would otherwise re-hash is the quiet version of losing evidence.

**The exclusion:** **the sandbox is not a fleet machine and `/update` must not try to reach it.** spike-3 found three macOS assumptions in the first forty lines of `scripts/remote-update.sh` and `scripts/valor-service.sh` — an iCloud-synced `~/Desktop/Valor/.env`, `launchctl` for every service operation, and `worker/__main__.py`'s `VALOR_LAUNCHD` branch. A Linux container has none of them. Code reaches the sandbox by **image rebuild and redeploy**, a separate contract documented in `docs/infra/`, and the sandbox is absent from the machine roster in `projects.json`.

This distinction is worth writing down precisely because the failure is quiet. The prior-art cluster (#1898, #2013, #2089, #2104, #2161) is five separate issues in which the update path reported success while the target was running old code or was down. Adding a host the path cannot see and never claims to have updated is the safe shape; adding one it claims to have updated is the sixth issue in that series.

## Agent Integration

The agent reaches new `tools/` code through a CLI entry point in `pyproject.toml [project.scripts]` or through a direct import from the bridge. Nothing here is bridge-internal, so it is all CLI.

- **Preferred surface: subcommands on lane 3's `valor-improve`** (#3215). This lane adds `budget`, `teardown`, and `report` subcommands rather than three new console scripts. Three sibling entry points for one subsystem is the fragmentation the single CLI exists to prevent, and `docs/tools-reference.md` would have to carry all three.
- **If `valor-improve` has not landed when this lane builds**, the tools are invoked as module paths (`python -m tools.infrastructure_budget`, `python -m tools.improvement_operating_report`) and the subcommands are wired when lane 3 arrives. **No standalone `[project.scripts]` entry is added** — a console script created as a stopgap outlives the stopgap, and the No-Legacy rule then makes someone delete it later.
- **The bridge imports none of this.** The controller tick calls the budget module directly, in-process, the same way it calls the probe.
- **Every new subcommand or module entry point ships informative `--help`.** The repo's stated convention is that `--help` substitutes for memorized invocations, and a tool that spends money is a poor place to break it.
- **Integration test:** a test invokes the report path through the same entry point the agent would use and asserts all five answers appear. A tool the agent cannot actually invoke is a tool that does not exist, and this one exists to be run.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/improvement-controller.md` with unit 3's metering, the window boundaries as they are actually computed, the `improvement:budget:unit3:{window_key}` key namespace and why it sits outside Popoto, and the teardown policy. The file already documents units 1 and 2; a third unit that lives only in a plan is a unit nobody will honor.
- [ ] Create `docs/features/improvement-cloud-execution.md` describing the sandbox topology (worker-only, official CLI, the recorded authentication mode and how the token reaches and leaves the sandbox, the durable-state choice and the superseded retention root), how it is updated, and how its evidence reaches the dashboard.
- [ ] Add both entries to the `docs/features/README.md` index table.

### Infrastructure Documentation
- [ ] Create `docs/infra/improvement-cloud-execution.md` — the durable infra record, using the Current State / New Requirements / Rules & Constraints / Rollback Plan structure. It carries the provider decision and its arithmetic under a named duty cycle, the **auth verdict** and the token's distribution and rotation story, the **export/import destination contract** lane 3 writes against, the rate and quota constraints, the redeploy contract that replaces `/update` for this host, and the teardown-and-destroy-account rollback. Three Verification rows grep this file for those first three, so they are content requirements rather than suggestions. Infra docs are never archived when plans ship, which is the point: the next person to ask "why not Cloudflare" (or "why Cloudflare") finds the answer here rather than in an archived plan.

### External Documentation Site
- [ ] Not applicable. This repo publishes no external documentation site.

### Inline Documentation
- [ ] `tools/infrastructure_budget.py` — a module docstring that states the window computation, the refusal rule, and the no-transfer rule, in the style the sibling improvement modules already use. `tools/improvement_resources.py`'s docstring is the model: it explains *why* `unknown` is the default, which is why nobody has since "fixed" it to `absent`.
- [ ] The teardown policy's ladder is documented at its implementation, not only in this plan. A plan is archived; the code is read.
- [ ] `tools/improvement_operating_report.py` — a docstring naming the five answers and stating that sandbox count, uptime, and token volume are deliberately absent, so a future contributor adding one meets the reason first.

## Success Criteria

- [ ] The resource probe has been re-run on the machine that owns `valor` with `OP_SERVICE_ACCOUNT_TOKEN` set, and its result is recorded as **one immutable `resource_probe` evidence row**. No resource is reported `absent` on the strength of a run that could not read the vault.
- [ ] The provider decision is recorded with its arithmetic against the $50/week unit under a named duty cycle, including the case against the options not chosen. Charter §8 names Cloudflare, so a decision away from it carries its evidence.
- [ ] **The sandbox's authentication mode is a recorded verdict with its evidence** — mode A (subscription on a host we operate, existing code path unchanged), mode B (unresolved, so no subscription-auth trial), or mode C (deferred to its own issue). A trial that ran without a recorded verdict does not satisfy this lane, and neither does a verdict inferred rather than evidenced.
- [ ] **The artifact retention root is superseded off the checkout**, proven by a cross-process round trip that needs no provider account (`POPOTO_IMPROVEMENT_CONTENT_PATH` at the chosen durable root, written from one process, loaded hash-verified from a second started in a different working directory), and the export/import destination contract is documented for lane 3 to write against. The literal sandbox-to-local round trip is task 9's recorded evidence, not this criterion's bar — this is a phase-2 criterion and phase 2 does not presume a sandbox.
- [ ] `tools/infrastructure_budget.py` admits and refuses against unit 3, computes the window from `budget_week_start` and `budget_day_boundary`, discloses both on every decision, refuses any resource whose charge cannot be forecast, and settles missing metering at the forecast rather than at zero.
- [ ] `spend_receipt` and `resource_probe` are recognized `EVIDENCE_KINDS` with a TTL that outlives a budget week, and a row of each round-trips through `record_once` without being coerced to `"other"`. `resource_acquired` is left to lane 3, and the ownership split is written in `models/improvement_evidence.py` beside the constant.
- [ ] The teardown policy is implemented and tested: admission closes, `standing` resources are torn down, `trial` resources continue to a bounded horizon with the overrun booked against the next window before it accrues, and **every teardown is gated on a verified evidence export that fails closed**.
- [ ] At least one RSI session has run **unattended in a cloud sandbox**, crossing a credential-refresh boundary and surviving at least one induced crash, with its evidence, its budget settlement, and its recovery recorded — **or, if #3215 never landed or task 4 recorded mode B, recorded as not reached with the auth verdict (or the missing vault writer) named as its cause.**
- [ ] The dashboard renders that sandbox session's evidence beside a local session's. A run whose evidence the dashboard cannot see does not satisfy this lane — **or, under the same two conditions, recorded as not reached with its cause.**
- [ ] `max_concurrent_research_sessions` has a recorded, evidence-backed decision. "Unchanged, because the subscription and not the machine is the binding constraint" is a passing outcome; leaving the question *unrecorded* is not — **and under mode B or an unlanded #3215 the recorded disposition is "not reached, because no trial ran," which is a disposition rather than an open question.**
- [ ] The charter §2 progress report is posted on #3177 **as soon as phase 2 completes, without waiting on the trial**, and answers all five questions — which sessions run in cloud sandboxes, whether the loop continues unattended, what resources sustain it, what they cost against the $50/week unit with both window boundaries disclosed, and **what still prevents mostly-cloud operation**. The fifth answer is non-empty.
- [ ] Every unresolved factual claim from this lane is recorded as a provisional assumption with its evidence, confidence, consequence, and the observation that would overturn it — charter §9's shape, not a hedge in prose.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`), including the `docs/infra/` record
- [ ] The report generator is invocable through the same entry point the agent would use, and a test proves it

## Team Orchestration

The lane splits cleanly into three phases with different risk profiles, and the split is real rather than cosmetic: phase 1 spends nothing and can start the moment #3275 merges, phase 2 spends money and blocks on #3215, phase 3 is honest bookkeeping over whatever the first two produced.

### Team Members

- **Spike agent (decisions)**
  - Name: `capacity-spiker`
  - Role: Tasks 2 through 4. Re-runs the probe, reads live provider pricing, and answers the sandbox-feasibility and authentication-mode questions. Produces recorded findings and decisions; writes no production code.
  - Agent Type: general-purpose, worktree isolation
  - Resume: true

- **Builder (budget, policy, records)**
  - Name: `budget-builder`
  - Role: Tasks 1, 5, 6, 7, and 10. The evidence-kind additions, the meter and teardown policy, the §2 report, the durable-state topology and retention root, and the concurrency revisit.
  - Agent Type: builder. Domain: Redis/Popoto data — no raw Redis operations, model changes carry an idempotent migration registered in `MIGRATIONS`, index cardinality respected.
  - Resume: true

- **Builder (sandbox trial)**
  - Name: `sandbox-builder`
  - Role: Tasks 8 and 9. Acquisition under admission, credential storage through lane 3's vault writer, and the unattended trial. Runs only when #3215 has landed and task 4 returned auth mode A.
  - Agent Type: builder. Domain: security/untrusted-input — no credential in a log, a message, an argv, or a commit; compare by SHA-256 fingerprint and never echo a secret or any prefix of one.
  - Resume: true

- **Validator**
  - Name: `capacity-validator`
  - Role: Runs the Verification table and every mutation check. Reports pass/fail per row and edits no code.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `capacity-scribe`
  - Role: Task 11. Feature docs, the `docs/infra/` record, and the index entries — describing what actually landed, including a phase 3 that did not run.
  - Agent Type: documentarian
  - Resume: true

**Two builders, and they must not share a worktree.** `budget-builder` and `sandbox-builder` are sequenced by dependency, not run in parallel: task 8 admits through task 5's meter. Shared-worktree builders have livelocked on this repository before, converging on each other's design simultaneously. If the phases are ever genuinely overlapped, the file-level ownership split is `tools/infrastructure_budget.py`, `tools/improvement_operating_report.py`, `models/improvement_evidence.py`, and `models/verifying_artifact_store.py` for the first, and the deployment artifacts for the second, declared before either starts.

The validator gets its own worktree. A mutation review whose author is editing the same checkout corrupts both directions of the measurement.

### Available Agent Types

- **general-purpose**: spikes, web research, code reads. Returns findings, not code.
- **builder**: implementation and tests in the lane worktree, committed in small logical checkpoints with explicit paths.
- **validator**: read-only verification, no Write or Edit.
- **documentarian**: documentation only.

## Step by Step Tasks

**Task IDs carry the graph; the numbers are reading order and the two now agree.** Round 1 found the numbering and the dependencies out of step, in a way that hid a real defect: the §2 progress report — this lane's zero-cost, charter-due deliverable — sat four edges behind a lane with no PR. The edges below are re-cut so it does not.

**Four phases.** Phase 1 (tasks 1-4) needs only PR #3275 merged and spends nothing. **Phase 2 (tasks 5-7) needs nothing further and produces both the meter and the progress report** — if #3215 never lands, the lane still ships everything through here. Phase 3 (tasks 8-10) additionally needs #3215's `tools/vault_write.py`, and is the only phase that spends money. Phase 4 (tasks 11-12) documents and validates whatever landed.

### 1. Add this lane's evidence kinds to the vocabulary
- **Task ID**: build-evidence-kinds
- **Depends On**: none
- **Validates**: `tests/unit/test_improvement_models.py` (UPDATE)
- **Informed By**: spike-5 (`record_once` coerces an unrecognized kind to `"other"` after a warning, `models/improvement_evidence.py:219-223`)
- **Assigned To**: `budget-builder`
- **Agent Type**: builder — Domain: Redis/Popoto data
- **Parallel**: false
- **This runs first, and that placement is the fix for a round-1 finding.** `EVIDENCE_KINDS` is `(correction, inspiration, shipped_work, owner_liveness, other)` on both `main` and `origin/session/sdlc-3255` (`models/improvement_evidence.py:58-64`). Every task that records durable evidence — starting with task 2's probe — silently degrades to `"other"` until this lands, and this plan's own Problem section calls an `other` row indistinguishable from every other `other` row.
- Add **two** values: `spend_receipt` (the budget's fallback settlement path, Gap D unit 3) and `resource_probe` (task 2's recorded probe result, which Race 4 requires be one immutable record per run).
- **Declare kind ownership across lanes, in the module, beside the constant.** This lane owns `spend_receipt` and `resource_probe`. **Lane 3 (#3215) owns `resource_acquired`** (`docs/plans/recursive-self-improvement.md:646`, `:800`) and adds it itself; this lane does not add it and does not reserve it. Both lanes edit `models/improvement_evidence.py`, so whoever lands second rebases onto an already-extended tuple rather than discovering a conflict in CI.
- Honor the constant's own comment at `:57` — "Low-cardinality on purpose — this is an index." Each addition is an index-cardinality decision. Two is the whole ask; state in the module docstring why each earns an index partition, so the next contributor meets the reason before appending a third.
- Resolve the TTL question explicitly: `ImprovementEvidence` expires on a 30-day window, shorter than the audit life of a budget week. State the decision and its reasoning in the module docstring, where the next reader meets it.
- Update the existing membership and cardinality assertions rather than loosening them.
- Adding values to a module constant is not a schema change and needs no migration. If the TTL decision forces a model change, that change **does** need an idempotent migration registered in the `MIGRATIONS` dict.

### 2. Re-run the resource probe under service-account authentication
- **Task ID**: spike-probe-rerun
- **Depends On**: build-evidence-kinds
- **Validates**: one recorded `resource_probe` evidence row that round-trips through `record_once` without coercion, asserted in `tests/unit/test_improvement_models.py`; `tests/unit/test_improvement_resources.py` unchanged and still passing
- **Informed By**: lane 2b's measured position; the code-read showing all four vault `unknown`s share one failed listing
- **Assigned To**: `capacity-spiker`
- **Agent Type**: general-purpose
- **Parallel**: false
- Run on the machine that owns the `valor` project, with `OP_SERVICE_ACCOUNT_TOKEN` from the vault `.env` and `OP_CACHE=false`. A run elsewhere measures a different machine.
- Record the full six-resource result as **one immutable `resource_probe` row per run** (Race 4), not as prose in a report and not as an `other` row.
- Report each resource's state change from lane 2b's baseline. A resource still `unknown` stays `unknown`; do not promote it to `absent` on a second inconclusive run.
- Do not modify `tools/improvement_resources.py`. #3255 owns it.

### 3. Decide the provider against unit 3, on forecastability
- **Task ID**: spike-provider
- **Depends On**: spike-probe-rerun
- **Validates**: the decision record in `docs/infra/improvement-cloud-execution.md` under Rules & Constraints, carrying the arithmetic for every candidate including the ones refused; task 12 checks it exists and names a duty cycle
- **Informed By**: spike-6 (Cloudflare GA pricing, scale-to-zero, 4 GiB / 0.5 vCPU cap, `keepAlive` plus explicit `destroy()`)
- **Assigned To**: `capacity-spiker`
- **Agent Type**: general-purpose
- **Parallel**: false
- Re-read the live Cloudflare Containers pricing page; spike-6's rates came partly from third-party calculators and the vendor page is the authority.
- Compute the weekly cost of a continuously-available worker-only instance under each candidate, showing the arithmetic against $50/week.
- **Use task 9's default duty cycle as the arithmetic's input**: 72 hours of continuous availability inside one ISO week, idle otherwise. That figure is pinned in task 9 precisely so this task is not blocked on a preference answer, and spike-6's finding that Cloudflare's included allowance is consumed "in the first day or two" of continuous running is what makes the number decisive rather than decorative.
- Apply Gap D's rule as the deciding criterion: **a resource whose charge cannot be forecast is refused.** State for each candidate whether the charge is forecastable and under what duty-cycle assumption.
- Enumerate free tiers and credits with expiry dates and the paid rate that follows each.
- Produce a decision with its evidence. Charter §8 names Cloudflare, so a decision against it must show its arithmetic.
- **This task creates `docs/infra/improvement-cloud-execution.md`.** It is the file's first writer and therefore its owner-of-record; task 4 appends the auth verdict, task 7 appends the export/import destination contract, and task 11 completes it and creates the *feature* doc. Round 2 found three tasks describing the same file with no stated creator, which is how a doc ends up written twice or not at all.

### 4. Establish sandbox feasibility, and decide the authentication mode
- **Task ID**: spike-sandbox-feasibility
- **Depends On**: spike-provider
- **Validates**: a recorded auth verdict (mode A, B, or C) plus its evidence in `docs/infra/improvement-cloud-execution.md`, and a recorded provisional assumption for the §9 scale question; task 12 checks both exist and task 9 refuses to run under any mode but A
- **Informed By**: spike-1 (official CLI supported remotely; token plus third-party harness blocked; the scale question unresolved), spike-2 (worker-only topology needs no bridge identity), `docs/infra/granite-oauth-token.md`
- **Assigned To**: `capacity-spiker`
- **Agent Type**: general-purpose
- **Parallel**: false
- **Decide the authentication mode and record which one, with its evidence.** This is the task that closes round 1's first blocker. `python -m worker` builds its `claude -p` environment through `agent/session_runner/role_driver.py::subscription_auth_env` (`:76-100`); without a decision here, task 9's happy path is an unauthenticated `claude -p`. The three modes are defined in Technical Approach §5a. **Mode A** (the default) needs no code change: the vault's `CLAUDE_CODE_OAUTH_TOKEN` is injected as a deploy-time provider secret and the existing code path consumes it unchanged. **Mode B** is the honest stop and feeds the fifth answer. **Mode C** is a tagged No-Go.
- Mode A requires an affirmative finding from vendor documentation that the chosen runtime is **a remote host we operate** rather than a hosted service consuming the subscription on our behalf. Absent that finding, the verdict is mode B. Do not resolve ambiguity in our own favor; that is the exact move charter §2's fifth answer exists to catch.
- **Answer the token-distribution sub-question explicitly.** `docs/infra/granite-oauth-token.md` records the repo's only mechanism: mint via `claude setup-token` (which opens a browser) and propagate by iCloud vault sync. A Linux container has neither. State how the token reaches the sandbox (a deploy-time secret, never a commit, never a log, never an argv), how it is rotated (manually, from a browser-capable machine, roughly annually), and what happens at expiry with nobody watching.
- Confirm the official `claude` CLI installs and authenticates in the chosen runtime, on the mode-A path only.
- Measure whether half a vCPU (Cloudflare's per-instance cap) actually carries a `claude -p` subprocess plus a worker, or whether it does not. This is a measurement, and a negative result is a finding.
- Check the provider's datacenter IP reputation against the reported Hetzner-range blocking (spike-2's second finding).
- Record the "ordinary, individual usage" scale question as a **provisional assumption** under charter §9: evidence, confidence, consequence, and the observation that would overturn it.
- Do not acquire anything. This task decides whether acquisition is worth attempting.

### 5. Build the unit-3 meter and the teardown policy
- **Task ID**: build-infrastructure-budget
- **Depends On**: build-evidence-kinds
- **Validates**: `tests/unit/test_infrastructure_budget.py` (create), `tests/unit/test_teardown_policy.py` (create)
- **Informed By**: Gap D's unit-3 rules; spike-6's `keepAlive`-plus-`destroy()` failure mode
- **Assigned To**: `budget-builder`
- **Agent Type**: builder
- **Parallel**: true (with task 3 and task 4, which are the spiker's)
- Window computation from `budget_week_start` and `budget_day_boundary`, with both disclosed on every decision record.
- Admission reserves the remainder of the current window and forecasts the next. **Refuse any resource whose charge cannot be forecast** — test the `None`, empty, and non-numeric rate shapes separately.
- Credits and free tiers carry an expiry and the paid rate that follows; a credit expiring inside the window converts to a forecast charge on its expiry day.
- Settlement from a billing API where one exists, otherwise from a `spend_receipt` row. **Missing or uncertain metering settles at the forecast, never at zero.**
- **Expose the live-window stop rule that task 9's trial keys on**: `stop_now = max(settled_usd, forecast_usd_to_date) >= cap`, evaluated on the caller's own watchdog tick rather than on settlement arrival. This is the settlement asymmetry above pointed at the live window: with a per-second metered provider, settlement lags and a stop keyed on settled spend alone can only fire after the money is gone. `admit()` already computes the forecast for the duty cycle, so this adds no new machinery. Test it with a billing reader that reports zero for the whole window; the stop must still fire from the forecast alone.
- **Reserve-then-check atomically against `improvement:budget:unit3:{window_key}`, a plain Redis string key that is deliberately not a Popoto model** (Technical Approach §3, Race 1). Reserve with a Lua `EVAL`. Popoto has no compare-and-set, so a counter modeled in the ORM could not be reserved atomically at all; a non-Popoto key sits outside CLAUDE.md's "never use raw Redis on Popoto-managed keys." **Every reservation, settlement, and credit record around it is an ordinary Popoto model, read and written through the ORM.** Document the namespace in the module docstring and note that it migrates into lane 3's control namespace when #3215 lands. Test two concurrent admissions whose sum exceeds the limit; exactly one is admitted.
- **Pair every reservation with an idempotent release, and expire the window key** (Technical Approach §3). `admit()` returns a reservation id beside its decision; the acquisition path releases from `except` and `finally`, never from the happy path. The release is the compensating `EVAL` (`INCRBYFLOAT key -amount`) floored at 0 inside the script, recorded on the Popoto reservation model, and a no-op once that model shows `released` — task 8 retries, and a double decrement raises headroom, which is the expensive direction. `EXPIRE` the window key past the audit horizon, never to the window length. Test: a failed acquisition after a successful admission returns the headroom exactly once; a replayed release changes nothing; and a window key outlives its own window.
- Implement the teardown ladder from Technical Approach §4 in full: admission closes, `standing` torn down, `trial` continued to a bounded horizon with the overrun booked forward, **verified export as the precondition of every teardown, failing closed**, and an unconfirmed teardown treated as still running and still charging.
- **Emit the escalation as a record on the fail-closed branch** — a `spend_receipt` row booking the continued charge as a forecast overrun against the next window, plus the digest status message where that reflection exists (Technical Approach §4). No new evidence kind, no new outbound seam.
- Mutation-check the export-verification guard in both directions, and assert the escalation record in each direction. A guard that never fires is indistinguishable from no guard; a guard whose escalation goes nowhere is indistinguishable from no escalation.
- Import nothing from the paid-inference pool. There must be no code path from unit 2's headroom to unit 3's.

### 6. Generate the charter §2 progress report
- **Task ID**: build-operating-report
- **Depends On**: build-infrastructure-budget, spike-probe-rerun
- **Validates**: `tests/unit/test_improvement_operating_report.py` (create)
- **Assigned To**: `budget-builder`
- **Agent Type**: builder
- **Parallel**: false
- **This task deliberately does not depend on the acquisition, the trial, or the concurrency revisit.** Round 1 found the report chained behind all three and therefore behind #3215, a lane with no PR — which would have cost the lane its one free, charter-due deliverable if #3215 slipped. Its two real inputs are the cost answer (task 5's meter) and the fifth answer's `unknown` entries (task 2's probe row). Everything from phase 3 folds in **if it has run** and is reported as absent if it has not.
- `tools/improvement_operating_report.py` generates the five answers from records: which sessions ran in cloud sandboxes, whether the loop continued unattended, what resources sustain it, what they cost against unit 3 with both window boundaries disclosed, and what still prevents the intended result.
- The fifth answer is assembled from recorded provisional assumptions, `unknown` entries in the latest `resource_probe` row, any acquisition refused for want of a forecast, and **task 4's auth verdict when it is mode B**. Test that it is non-empty whenever those inputs are non-empty.
- Publish reserved and settled figures **separately**, never a derived net (Race 3).
- **No function returns sandbox count, uptime, or token volume.** Charter §2 names all three as non-establishing, and their absence is asserted in Verification.
- The report must produce five real answers from a zero-sandbox position, since that is the state its first run will be in. This is the primary case, not the edge case.
- **Re-runnable by design.** Post the first run as a comment on #3177 as soon as phase 2 completes. Regenerate and post again after phase 3 lands, so the two are comparable and the movement is visible.

### 7. Decide and implement the durable-state topology, including the retention root
- **Task ID**: build-state-topology
- **Depends On**: spike-sandbox-feasibility
- **Validates**: a test proving a worker with a non-local Redis writes evidence the dashboard's read path returns; a **cross-process** retention-root round trip needing no provider account — `POPOTO_IMPROVEMENT_CONTENT_PATH` pointed at the chosen durable root, written through `VerifyingArtifactStore` from one process, loaded hash-verified from a second process started in a different working directory; `tests/unit/test_length_safe_content_store.py` (UPDATE, `test_retention_root_is_separate_from_the_shared_content_path` at `:224`)
- **Informed By**: spike-4 (`redis://localhost:6379/0` default, no TLS or auth in settings, `worker:registered_pid:*` presumes one shared Redis)
- **Assigned To**: `budget-builder`
- **Agent Type**: builder — Domain: Redis/Popoto data
- **Parallel**: false
- Choose between a sandbox-local Redis with an export path and a network-reachable shared Redis, and write down the cost of the option not chosen.
- If shared: transport security is in scope for this task and is named work, not a footnote.
- If local: the export path is in scope, and "the dashboard renders it" is the acceptance test either way.
- **Supersede the local artifact retention root.** The parent plan scopes this here (`docs/plans/recursive-self-improvement.md:804`, with `:572` explaining that lanes 1 and 2 kept it "so lane 7 has something to migrate"), and round 1 caught it being dropped. `models/verifying_artifact_store.py::_default_base_path` (`:45-56`) resolves `POPOTO_IMPROVEMENT_CONTENT_PATH`, defaulting to `data/improvement_content` **inside the repo checkout** — which an image rebuild destroys on every redeploy. Move it to the durable store chosen above, keep the separation from the shared popoto content directory that `_default_base_path` exists to enforce, and prove it **without needing a sandbox to exist**. `_default_base_path` (`:45-56`) reads `POPOTO_IMPROVEMENT_CONTENT_PATH` fresh on every call rather than through the cached settings singleton — its docstring says so, and that is exactly what lets a second process with a different working directory stand in for the container. So the testable acceptance is: point the env var at the chosen durable root, write through `VerifyingArtifactStore` from one process, load from a second process started elsewhere, and let the re-hash-on-every-load do the verifying. **The literal sandbox-to-local round trip stays as task 9's recorded evidence**, not as this phase-2 task's acceptance bar — task 7 is declared phase 2 and phase 2 ships without #3215 and without a provider account, so an acceptance test that presumes a running sandbox could never be satisfied under mode B, which is an outcome task 4 is explicitly instructed to return absent an affirmative vendor finding.
- **Ship the export/import destination contract; do not write export/import.** `valor-improve export` / `import` is lane 3's (`docs/plans/recursive-self-improvement.md:424`, `:800`) and does not exist yet, so there is nothing to migrate today. What this lane owes is the other end: the retention root's new location, its verification-on-load guarantee, and a documented archive path, written into `docs/infra/improvement-cloud-execution.md` so lane 3 writes against a named target instead of inventing one. Supersession and migration are two different pieces of work and this task says which one it is doing.
- All reads and writes go through the Popoto ORM. No raw Redis operations on Popoto-managed keys.

### 8. Acquire the resource under admission
- **Task ID**: build-acquisition
- **Depends On**: build-infrastructure-budget, spike-provider, spike-sandbox-feasibility, **#3215's `tools/vault_write.py`**
- **Validates**: `tests/unit/test_infrastructure_budget.py::test_no_acquisition_bypasses_admission` — an import-graph and AST check over this lane's modules asserting (a) every acquisition call site routes through `tools.infrastructure_budget.admit`, and (b) no module in this lane imports a provider SDK or invokes a provider CLI outside that path. **It spends nothing and needs no provider account**, which is the point: this task's real acceptance is that no charge bypassed the meter, and that is a structural property, not an outcome you have to buy to observe.
- **Assigned To**: `sandbox-builder`
- **Agent Type**: builder — Domain: security/untrusted-input
- **Parallel**: false
- Acquire only against resources the task-2 probe reports `verified`. A resource still `unknown` is not acquired against.
- Every charge passes through task 5's admission first. No acquisition path bypasses the meter.
- Any issued credential is written to `m-valor` through lane 3's vault writer. Never a log, a message, an argv, or a commit; compare by SHA-256 fingerprint and never echo a secret or any prefix of one.
- Record the acquisition, its forecast, its expiry, and any credit that applies.

### 9. Run one unattended session in the sandbox
- **Task ID**: build-sandbox-trial
- **Depends On**: build-acquisition, build-state-topology
- **Validates**: recorded trial evidence — the session's records, its budget settlement, and its recovery
- **Assigned To**: `sandbox-builder`
- **Agent Type**: builder
- **Parallel**: false
- **Runs only under task 4's mode A.** Under mode B this task does not run and its absence is reported as the fifth answer, which is a real result. Under no circumstance does it reach for mode C mid-build; that is a tagged No-Go with its own issue.
- Worker-only: `python -m worker` plus the official `claude` CLI. No Telegram bridge, no `projects.json` entry, no `remote-update.sh`. The `CLAUDE_CODE_OAUTH_TOKEN` arrives as a deploy-time provider secret and `subscription_auth_env` consumes it unchanged.
- The session is claimed from the shared queue like any other. Nothing in the queue names a machine, and nothing should start to.
- **Default bound, so nobody waits on a preference answer to start: 72 hours of continuous availability, hard-capped, and hard-stopped at the earlier of that or `max(settled_usd, forecast_usd_to_date) >= $15` of unit-3 spend**, evaluated on the trial's own watchdog tick rather than on settlement arrival. The `max()` is the whole point: spike-6 records per-second metering with a billing API that reports after the fact, so a stop keyed on *settled* spend alone can only fire once the money is gone and $15 would not be a cap at all. The forecast is the only figure available in real time, and task 5's meter already computes it. Three days crosses several access-token refreshes — spike-2's known headless 401 (`anthropics/claude-code#50743`) is exactly the failure "unattended" has to survive — while staying well inside a $50 week. Task 3's forecast arithmetic uses this same duty cycle. Open Question 3 may shorten it; nothing is blocked waiting for that answer.
- **Induce at least one crash** and confirm unattended recovery: either the sandbox restarts and resumes, or the session is re-queued and another worker takes it. Both are acceptable; neither happening is not.
- **State the claim precisely in the recorded result.** What this trial tests is unattended operation **across access-token refreshes within the long-lived token's life**. It does **not** test unattended rotation of `CLAUDE_CODE_OAUTH_TOKEN` itself, which `docs/infra/granite-oauth-token.md` records as a manual, browser-bound, roughly annual act the sandbox cannot perform. Say so; the report inherits whatever this task claims.
- Confirm the dashboard renders this session's evidence beside a local session's.
- **Record the sandbox-to-local artifact round trip as trial evidence**: write an artifact from the sandbox through `VerifyingArtifactStore` against task 7's superseded retention root, load it hash-verified from a local process, and record the result. This is the stronger observation of the property task 7 already proved cross-process; it lives here because it needs a sandbox, and task 7's acceptance deliberately does not.
- If the trial fails, **record why and stop**. A failed trial with a recorded cause is a valid input to task 6's regeneration and is worth more than a retried trial with a lost cause.

### 10. Revisit `max_concurrent_research_sessions`
- **Task ID**: build-concurrency-revisit
- **Depends On**: build-sandbox-trial
- **Validates**: `tests/unit/test_settings.py` (UPDATE only if the value or bound changes)
- **Assigned To**: `budget-builder`
- **Agent Type**: builder
- **Parallel**: false
- Test the hypothesis that a sandbox adds a machine and not subscription capacity, against what the trial actually showed.
- Record the decision with its evidence. **"Unchanged, because the subscription and not the machine is the binding constraint" is a valid and likely outcome**, and recording it closes the question Gap D left open.
- Raising the value past 4 is a change to the `le` bound at `config/settings.py:609`, not an env override. Do it only if the evidence supports it, and say so in the commit.
- **Under mode B, or with #3215 unlanded, this task does not run.** Its criterion says leaving the question open is not a passing outcome, so the mode-B path records the explicit disposition instead: "not reached, because no trial ran; cause = task 4's auth verdict / absent vault writer." A recorded not-reached is a disposition, not an open question, and it is what keeps mode B from forcing a stated non-passing outcome on a task that was correctly prevented from running.

### 11. Documentation
- **Task ID**: document-feature
- **Depends On**: build-operating-report, spike-sandbox-feasibility, build-state-topology — the three artifacts this task's own bullets have to write down, with `spike-provider` reached transitively through `spike-sandbox-feasibility`. Phase-3 inputs (the trial, the concurrency revisit) fold in **if they have run**, the same way task 6 already folds phase 3 in. Round 2 caught this task depending on the report alone, which let a builder following the graph document a provider decision, an auth verdict, and a retention-root supersession before any of them existed.
- **Validates**: the four Verification rows that check documentation artifacts exist and are indexed (`docs/infra/improvement-cloud-execution.md` present, `docs/features/README.md` carries both entries)
- **Assigned To**: `capacity-scribe`
- **Agent Type**: documentarian
- **Parallel**: false
- **Document what landed, not what was planned.** If phase 3 did not run, the feature doc says the sandbox topology is decided and unbuilt, and names the auth verdict that stopped it. A doc describing an unbuilt sandbox in the present tense is the drift Risk 8 exists to prevent.
- Update `docs/features/improvement-controller.md` with unit 3, its window computation, the `improvement:budget:unit3:{window_key}` namespace, and the teardown policy.
- Create `docs/features/improvement-cloud-execution.md`. **Complete** `docs/infra/improvement-cloud-execution.md`, which task 3 created and tasks 4 and 7 appended to — this task does not create it. The infra doc ends up carrying the provider decision and its arithmetic (task 3), the auth verdict (task 4), the retention-root supersession and the export/import destination contract (task 7), plus the redeploy contract that replaces `/update` for this host and the teardown-and-destroy-account rollback (this task).
- Task 12's grep rows over that file are the only checks that the auth verdict, the duty cycle, and the destination contract were written down rather than decided in a builder's head. Do not delete them to make validation easier.
- Add both feature entries to the `docs/features/README.md` index table.
- Describe the new status quo only. No migration narrative, no "previously we…".

### 12. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature, build-concurrency-revisit (satisfied-if-run: under mode B or with #3215 unlanded, `build-concurrency-revisit` does not run and its criteria are marked not reached rather than blocking validation)
- **Validates**: a per-row pass/fail report over the Verification table, posted to the PR; every anti-criterion additionally shown in its red state with the FAIL output captured
- **Assigned To**: `capacity-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification row and report pass/fail per row.
- **Demonstrate each anti-criterion red before trusting it green.** The OAuth row is diff-scoped precisely so this is possible; a row that starts red on `main` cannot be shown to bite.
- Re-run the mutation check on the export-verification guard; a green test that reaches no code is the failure mode this exists to catch.
- Confirm every Success Criterion, including the ones that are decisions rather than code. **Mark the phase-3 criteria not reached — rather than passed, and rather than failed — if `#3215 never landed` OR `task 4 recorded mode B`.** The condition is disjunctive on purpose: with #3215 landed and task 4 returning mode B, tasks 9 and 10 still do not run, and a rule conditioned only on #3215 would leave three Success Criteria with no recorded disposition at all. Record which arm of the disjunction fired, since the report's fifth answer already claims to cover the mode-B case.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `./scripts/pytest-clean.sh tests/unit/test_infrastructure_budget.py tests/unit/test_teardown_policy.py tests/unit/test_improvement_operating_report.py tests/unit/test_improvement_models.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Both new evidence kinds are real | `python -c "from models.improvement_evidence import EVIDENCE_KINDS as K; print(set(('spend_receipt','resource_probe')) <= set(K))"` | output contains True |
| A receipt is not coerced to `other` | `./scripts/pytest-clean.sh tests/unit/test_improvement_models.py -k spend_receipt -q` | exit code 0 |
| A probe row is not coerced to `other` | `./scripts/pytest-clean.sh tests/unit/test_improvement_models.py -k resource_probe -q` | exit code 0 |
| Unit 3 has a meter with a reader | `python -c "import tools.infrastructure_budget as m; print(hasattr(m,'admit'))"` | output contains True |
| Window boundaries disclosed on every decision | `./scripts/pytest-clean.sh tests/unit/test_infrastructure_budget.py -k boundary -q` | exit code 0 |
| Unforecastable charge is refused | `./scripts/pytest-clean.sh tests/unit/test_infrastructure_budget.py -k forecast_refus -q` | exit code 0 |
| Missing metering settles at forecast, not zero | `./scripts/pytest-clean.sh tests/unit/test_infrastructure_budget.py -k missing_metering -q` | exit code 0 |
| Concurrent admission cannot double-spend a window | `./scripts/pytest-clean.sh tests/unit/test_infrastructure_budget.py -k concurrent_admission -q` | exit code 0 |
| A failed acquisition returns its reservation, once | `./scripts/pytest-clean.sh tests/unit/test_infrastructure_budget.py -k reservation_release -q` | exit code 0 |
| The window key outlives its window | `./scripts/pytest-clean.sh tests/unit/test_infrastructure_budget.py -k window_key_expiry -q` | exit code 0 |
| The trial's hard stop fires on forecast alone | `./scripts/pytest-clean.sh tests/unit/test_infrastructure_budget.py -k forecast_hard_stop -q` | exit code 0 |
| No acquisition path bypasses the meter | `./scripts/pytest-clean.sh tests/unit/test_infrastructure_budget.py -k no_acquisition_bypasses -q` | exit code 0 |
| Teardown fails closed without a verified export | `./scripts/pytest-clean.sh tests/unit/test_teardown_policy.py -k export_verification -q` | exit code 0 |
| A fail-closed teardown escalates onto a record | `./scripts/pytest-clean.sh tests/unit/test_teardown_policy.py -k escalation_record -q` | exit code 0 |
| Report answers all five §2 questions from a zero-sandbox position | `./scripts/pytest-clean.sh tests/unit/test_improvement_operating_report.py -k five_answers -q` | exit code 0 |
| The fifth answer is non-empty when its inputs are | `./scripts/pytest-clean.sh tests/unit/test_improvement_operating_report.py -k what_prevents -q` | exit code 0 |
| The retention root is superseded and still verifies on load (phase 2) | `./scripts/pytest-clean.sh tests/unit/test_length_safe_content_store.py -k retention_root -q` | exit code 0 |
| The auth verdict is recorded, not implied | `grep -c "Auth verdict" docs/infra/improvement-cloud-execution.md` | prints a number > 0 |
| The provider decision names its duty cycle | `grep -c "duty cycle" docs/infra/improvement-cloud-execution.md` | prints a number > 0 |
| The export/import destination contract is written down | `grep -c "export/import destination" docs/infra/improvement-cloud-execution.md` | prints a number > 0 |
| Infra record exists and is not archived | `test -f docs/infra/improvement-cloud-execution.md` | exit code 0 |
| Feature docs indexed | `grep -c "improvement-cloud-execution" docs/features/README.md` | prints a number > 0 |

### Anti-criteria

These live in a code block rather than in the table above, and that is a fix from critique round 1 rather than a style choice. A markdown table cell cannot carry a literal `|`, so every alternation and every shell pipe inside one has to be escaped — and round 1 measured what the escaping actually did: `grep -cE "a\|b" file` treats the backslash-pipe as a **literal pipe character** in ERE and matches nothing, returning `0` with exit 1. Read raw or read as table-escaping, three of the five anti-criteria round 1 measured were passing vacuously. A check that cannot fail is worse than no check, which is the precise thing the paragraph below claims to prevent, so the checks moved somewhere pipes are unambiguous and the patterns were rewritten to avoid alternation entirely.

Two mechanical traps these commands are written around, both verified on this machine: **`grep -c` given more than one file prints `path:count` per file, not a single number**, so every count check below takes exactly one file; and **`grep -c` exits 1 when the count is 0**, so the expectation is on the printed number, never on the exit code.

```bash
# 1. No NEW consumer of the subscription OAuth token.
#    Diff-scoped on purpose. Repo-wide this is red before the lane starts: 34 matches
#    on main at 191bd42a1, across .env.example, tools/doctor.py, role_driver.py,
#    claude_diagnostics.py and four test files -- because the token IS the repo's
#    sanctioned headless auth path. spike-1 blocks token + THIRD-PARTY HARNESS and
#    supports token + OFFICIAL CLI, which is what subscription_auth_env already builds.
#    Deployment artifacts (Dockerfile, wrangler config) legitimately inject it as a
#    secret and are excluded by the '*.py' pathspec.
git diff --name-only origin/main...HEAD -- '*.py' | xargs -r grep -l CLAUDE_CODE_OAUTH_TOKEN | wc -l
# expected: 0
# red state: echo agent/session_runner/role_driver.py | xargs -r grep -l CLAUDE_CODE_OAUTH_TOKEN | wc -l  ->  1

# 2. No transfer between budget units (charter SS8).
grep -c -e 'daily_paid_inference_usd' -e 'daily_external_llm_usd' tools/infrastructure_budget.py
# expected: prints 0 (exit status 1 -- that is grep's no-match exit, not a failure)

# 3. The report returns no sandbox count, uptime, or token volume (charter SS2 non-measures).
grep -c -e 'def .*sandbox_count' -e 'def .*uptime' -e 'def .*token_volume' -e 'def .*token_count' tools/improvement_operating_report.py
# expected: prints 0

# 4. No silently swallowed exceptions in this lane's modules. One file per invocation.
grep -cE 'except Exception: *pass' tools/infrastructure_budget.py
grep -cE 'except Exception: *pass' tools/improvement_operating_report.py
# expected: each prints 0

# 5. The sandbox is not a fleet machine (phase 3 only; skipped under auth mode B).
#    Checked against the deployment artifact rather than against prose in a doc --
#    the doc mentions launchd and remote-update.sh precisely in order to disown them,
#    so grepping it needs a negation filter that is itself untestable.
grep -c -e 'launchctl' -e 'remote-update.sh' -e 'VALOR_LAUNCHD' deploy/sandbox/entrypoint.sh
# expected: prints 0

# 6. This lane did not claim lane 3's evidence kind.
#    Diff-scoped, and it lives here rather than in the table for the same reason
#    checks 1-5 do: the honest form needs a shell pipe, and a markdown cell cannot
#    carry a literal one. It compares the tuple's contents across this lane's diff
#    instead of testing membership on the current tuple, so it keeps biting after
#    #3215 lands -- once resource_acquired is on main, a membership test passes for
#    a reason that has nothing to do with this lane, which is the vacuous shape
#    round 1 measured. Comparing sets also survives task 1 naming resource_acquired
#    in the module's ownership declaration, which a text grep would false-positive on.
python - <<'EOF'
import ast, subprocess
def kinds(ref):
    src = subprocess.run(["git", "show", ref + ":models/improvement_evidence.py"],
                         capture_output=True, text=True).stdout
    for node in ast.walk(ast.parse(src)):
        # EVIDENCE_KINDS is an annotated assignment (AnnAssign), not Assign --
        # verified against models/improvement_evidence.py:58. Handle both so the
        # check survives the annotation being dropped.
        targets = [node.target] if isinstance(node, ast.AnnAssign) else getattr(node, "targets", [])
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and any(
            getattr(t, "id", None) == "EVIDENCE_KINDS" for t in targets
        ):
            return set(ast.literal_eval(node.value))
    return set()
print(kinds("HEAD") - kinds("origin/main") == {"spend_receipt", "resource_probe"})
EOF
# expected: output contains True
# red state: add 'resource_acquired' to the tuple on this branch  ->  False
```

Every anti-criterion above must be demonstrated in its red state before it is trusted. Introduce the violation deliberately, capture the FAIL output, revert, and paste the FAIL into the PR description. An anti-criterion that has only ever passed is a check nobody has proven can fail, and this repo has shipped several of those. Check 1 carries its red-state command inline because it is the one round 1 found unsatisfiable — a criterion that starts red on `main` can never be shown to bite.

## Critique Results

**Round 1** — FULL depth (`appetite: Large` force-FULL). Roster: Risk & Robustness, Scope & Value, History & Consistency.
**Mode**: sequential lenses (Agent tool unavailable: not in tool list). No finding below was independently corroborated by a second critic.
**Verdict**: NEEDS REVISION — 3 blockers, 7 concerns, 0 nits.
**Revision applied**: all 3 blockers and all 7 concerns addressed; none deferred, none disputed. Every file:line the critique cited was re-derived against `main` before use, and one set was wrong: `subscription_auth_env`'s internals sit one line earlier than reported (`:94-96` and `:97-99`, not `:95-97` and `:98-100`). The rest checked out, including the 34-match OAuth count and the vacuous-grep measurement, both reproduced here.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| BLOCKER | Risk & Robustness | Task 8 runs `python -m worker`, whose `claude -p` subprocess is built by `agent/session_runner/role_driver.py::subscription_auth_env` (`:76-101`). That function blanks `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, and `ANTHROPIC_AUTH_TOKEN` at `:95-97`, then exports `CLAUDE_CODE_OAUTH_TOKEN` at `:98-100`. The plan forbids that token in a hosted runtime, names no alternative, and the blanking removes the API-key fallback, so the trial's happy path ends in an unauthenticated `claude -p` and no task changes it. | **Technical Approach §5a** — three named auth modes. Mode A (default) needs no code change: the vault's token is injected as a deploy-time provider secret and `subscription_auth_env` consumes it unchanged, which is spike-1's supported official-CLI shape. Mode B is the honest stop and feeds the fifth answer. Mode C (API-key) is a `[SEPARATE-ISSUE]` No-Go. **Task 4** records the verdict with its evidence and **task 9** runs only under mode A. Line numbers re-derived on `main`: the function is `:76-100`, the blanking `:94-96` with `ANTHROPIC_API_KEY` at `:94`, the token read at `:97` and export at `:98-99`, doctrine docstring at `:84-85` — round 1's citations were each off by one. spike-1's Impact bullet now states the token-plus-official-CLI carve-out explicitly. | Make the sandbox's auth mode an explicit output of task 3 and an explicit change in task 8, naming which of `subscription_auth_env`'s three behaviors is altered and why that stays inside spike-1's rule. `ANTHROPIC_API_KEY` is blanked at line 95 BEFORE the token lookup, so supplying an API key to the sandbox is not a workaround; it needs a code change. The docstring at `:81-88` says the blanking is deliberate ("headless role turns must never fall back to metered API-key auth"), so altering it is a doctrine change that must be argued. spike-1's actual rule is token plus THIRD-PARTY HARNESS is blocked, while token plus OFFICIAL CLI is supported, which is what this function already does, so the likely resolution narrows the plan's No-Go rather than changing the code. |
| BLOCKER | Scope & Value | Risk 6's mitigation states tasks 9 and 10 "need whatever has landed by then and are written to produce a truthful report from a partial position," but the task graph hard-serializes task 10 behind 9 behind 8 behind 7, and task 7 depends on #3215's `tools/vault_write.py` (a lane with no PR and no plan). Tasks 11 and 12 chain behind 10. If #3215 does not land, the lane produces nothing, including the charter §2 progress report, which costs no money and is due regardless. | **Task graph re-cut and renumbered so reading order matches the edges.** `build-operating-report` is now task 6, depending only on `build-infrastructure-budget` and `spike-probe-rerun` — the cost answer and the fifth answer's `unknown` entries. It does not depend on the acquisition, the trial, or the concurrency revisit, and folds each in only if it ran. Phase 2 now ends with the report, so **if #3215 never lands the lane still ships a metered unit 3, a teardown policy, a resource position, a provider decision, an auth verdict, and the charter §2 report.** Risk 6 rewritten to match, task 11 documents what landed, and task 12 marks phase-3 criteria not-reached rather than failed. The report is re-runnable and posts again after phase 3. | Re-cut the `Depends On` edges so the report is reachable from phase 1. The plan already contains the test proving the report works without the trial: "The report with no sandbox sessions must produce five real answers, one of which is 'none'... this is the state the first report will actually be generated in, so it is the primary case, not the edge case." Set `build-operating-report` to depend on `build-infrastructure-budget` (cost answer) plus `spike-probe-rerun` (the fifth answer's `unknown` entries), folding task 9's outcome in only if it has run. Do not gate the report on evidence it is explicitly designed to produce without. |
| BLOCKER | History & Consistency | The anti-criterion "no subscription OAuth token anywhere in the repo" expects a repo-wide grep count of 0. Measured on `main` at `191bd42a1`: 34 matches across `.env.example:81`, `tools/doctor.py:1902-1964`, `agent/session_runner/role_driver.py:16,19,81,97,99`, `agent/session_runner/harness/claude_diagnostics.py:110,115`, and four test files. The criterion is red before the lane starts and can only go green by deleting the repo's sanctioned headless auth path. The No-Go prose also mis-states spike-1, which blocks the token with a third-party harness while explicitly supporting the official CLI on a remote host. | **Restated as a delta and moved where it can be shown red.** Verification's anti-criteria left the table for a fenced code block; check 1 is now a diff-scoped pipeline (`git diff --name-only origin/main...HEAD -- '*.py'` piped through `xargs -r grep -l CLAUDE_CODE_OAUTH_TOKEN` and `wc -l`; the exact command is in the Anti-criteria block, where a pipe needs no escaping), measured `0` today, with its red-state command inline (measured `1`). Deployment artifacts are excluded by the `'*.py'` pathspec because mode A legitimately injects the secret there. The No-Go was rewritten from "no token anywhere in the repository" to "**no new consumer of the token**", carrying round 1's own 34-match measurement on `main` at `191bd42a1` and spike-1's actual rule — third-party harness blocked, official CLI supported. | Restate the anti-criterion as a delta over this lane's own new files and narrow the No-Go from "the token is forbidden" to "no non-official-CLI consumer of the token is introduced." Scope it with `grep -c -e CLAUDE_CODE_OAUTH_TOKEN tools/infrastructure_budget.py tools/improvement_operating_report.py` plus the deployment artifacts, or diff-scope with `git diff --name-only origin/main...HEAD` piped to `xargs -r grep -l`. An absolute repo-wide count cannot distinguish "this lane introduced it" from "the session runner has always used it," and the plan's own rule that every anti-criterion be shown in its red state is unsatisfiable for a check that starts red. |
| CONCERN | Risk & Robustness | Race 1's mitigation is "reserve-then-check in a single atomic step against the window key, the same shape Gap D specifies for unit 2." Gap D's shape is a Lua admission script in lane 3's control namespace (`docs/plans/recursive-self-improvement.md:804` assigns lane 3 the "Control namespace and Lua transition"). Task 6 lists #3215 nowhere in its `Depends On`, and Risk 6 asserts tasks 4-6 need only #3255, but this repo forbids raw Redis on Popoto-managed keys and Popoto offers no compare-and-set. | **The window counter is declared non-Popoto, so lane 3 is not a dependency.** Technical Approach §3, Race 1, task 5, and Architectural Impact all name `improvement:budget:unit3:{window_key}` as a plain Redis string key reserved by Lua `EVAL`, outside CLAUDE.md's Popoto-managed-keys rule, with every reservation, settlement, and credit record staying an ORM model. The rationale is stated rather than assumed: Popoto has no compare-and-set, so a counter modeled in the ORM could not be reserved atomically at all. Lane 3's control namespace (`docs/plans/recursive-self-improvement.md:800`) is the declared future home, with a named migration when #3215 lands. Task 5's `Depends On` is `build-evidence-kinds` only. | Either add lane 3's control namespace to task 6's `Depends On` and re-phase it, or state in task 6 which non-Popoto key namespace the window counter lives in and who owns its Lua script. The guard is CLAUDE.md's "Never use raw Redis on Popoto-managed keys," enforced by `.claude/hooks/validators/validate_no_raw_redis_delete.py`. A window counter that is NOT a Popoto model sits outside that rule and may use a Lua `EVAL`; a window stored as a Popoto model sits inside it and cannot be reserved atomically at all. Pick one before the builder writes `admit()`, because the two lead to different modules and different tests. |
| CONCERN | Risk & Robustness | The three grep-based anti-criteria are ambiguous between markdown-table escaping and regex syntax, and one is broken under either reading. Read raw, `grep -cE` with a backslash-escaped pipe treats it as a LITERAL pipe in ERE and never matches (verified: against a file containing `daily_paid_inference_usd` it returns count 0, exit 1). Read as table-escaped, the last row becomes a BRE `grep -rn` with a bare pipe, which is also literal and never matches. Either way a check the plan calls load-bearing passes vacuously, the exact failure the paragraph beneath it says it exists to prevent. | **All five anti-criteria rewritten without pipes and relocated to a fenced `bash` block**, where a pipe character is unambiguous. Alternation is gone in favor of repeated `-e` patterns, each check takes exactly one file, and both mechanical traps are documented after being measured on this machine: `grep -c` given two files prints `path:count` per file, and `grep -c` exits 1 on a zero count, so every expectation is on the printed number rather than the exit code. Round 1's finding that `grep -cE 'a\|b'` matches a literal pipe and returns `0` is quoted as the reason the checks moved. | Avoid pipes inside the patterns entirely: `grep -c -e 'daily_paid_inference_usd' -e 'daily_external_llm_usd' <file>` needs no escaping and reads identically raw or rendered, and add `-E` to every row that keeps alternation. Separately, `grep -c` given TWO file arguments prints `path:count` per file rather than one number, so the "match count == 0" expectation on the `except Exception: pass` row (which passes two paths) needs a `grep -v ':0$'` filter or one row per file. |
| CONCERN | Scope & Value | The parent plan assigns lane 7 more than this plan claims. `docs/plans/recursive-self-improvement.md:804` scopes lane 7 to "where evidence, the control namespace, and artifacts persist (this lane supersedes the local retention root and migrates export/import)." Task 4 decides the durable-state topology but says nothing about superseding the retention root or migrating export/import, and no No-Go defers it. The scope is dropped silently rather than refused. | **Taken, not deferred, and split into the two pieces of work it actually is.** Task 7 (formerly 4) owns **supersession** end to end, citing `models/verifying_artifact_store.py::_default_base_path` (`:45-56`) and `POPOTO_IMPROVEMENT_CONTENT_PATH` defaulting to `data/improvement_content` inside the checkout — a path an image rebuild destroys — with acceptance by writing an artifact from the sandbox and loading it hash-verified locally. **Migrating export/import** is not taken, because its other end is lane 3's unwritten `valor-improve export` (`:424`, `:800`); this lane ships the **destination contract** instead so lane 3 writes against a named target. Technical Approach §6, Success Criteria, Test Impact (`tests/unit/test_length_safe_content_store.py:224`), Update System, Documentation, and a Verification row all follow. | Either add the retention-root supersession and export/import migration to task 4 with their own acceptance, or add a tagged No-Go naming what is deferred and to which lane. These are not one decision: "where does a sandbox write evidence" is a topology choice, while "supersede the local retention root and migrate export/import" is data movement with an existing local store on the other end. If deferred, the parent plan's lane-7 success line becomes half-owned and lane 3's export/import (named at `:800`) has no landing place. |
| CONCERN | Scope & Value | Open Question 3 asks whether a multi-day paid trial is within appetite, and unlike questions 1, 2, and 4 the plan states no default. It is not a preference question: task 8's success bar sets the trial's duty cycle, and the duty cycle is the input task 2's forecast needs. spike-6 found Cloudflare's included allowance is consumed "in the first day or two" by a continuously-running instance, so the unanswered question determines whether the charter's own named provider survives admission. | **A default is stated so nothing waits on the answer.** Task 9 is bounded at 72 hours of continuous availability, hard-capped and hard-stopped at the earlier of that or $15 of settled unit-3 spend, and **task 3's forecast arithmetic uses that same duty cycle** — which was the real coupling round 1 identified. Three days crosses several access-token refreshes (`anthropics/claude-code#50743`) while staying well inside a $50 week. Open Question 3 is restated as a tuning question that changes a number and unblocks nothing. | State a default duration bound in task 8, and therefore a duty-cycle assumption for task 2's arithmetic, that the builder proceeds on absent an answer. The credential-refresh boundary is the binding constraint and is knowable in advance: `docs/infra/granite-oauth-token.md` documents short-lived session tokens expiring "after roughly an hour" and `CLAUDE_CODE_OAUTH_TOKEN` as the ~1-year prevention credential. Which boundary task 8 must cross therefore follows from the auth mode chosen in task 3 (hours, not days, if it is the session token). Pin that first and the question largely answers itself. |
| CONCERN | History & Consistency | Prior Art concludes "Nothing has failed here before, because nothing has been tried" and omits the `## Why Previous Fixes Failed` section on that basis, but it misses `docs/infra/granite-oauth-token.md`, a durable non-archived infra record on this lane's central question. It documents the headless token-expiry failure the plan rediscovers from an external GitHub issue, and names a distribution mechanism a Linux container does not have: "A single token is shared across all machines via iCloud vault propagation," minted by an interactive browser flow. spike-3 already established the sandbox has no iCloud. | **Added to Prior Art with the two facts that bind this lane**: `claude setup-token` "opens a browser window; the resulting token must be copied manually to the vault," and "a single token is shared across all machines via iCloud vault propagation" — neither available to a Linux container (spike-3). **Task 4 gains an explicit token-distribution and rotation sub-question**: how the token reaches the sandbox, how it rotates, and what happens at expiry with nobody watching. **Risk 7 is narrowed**: the trial tests unattended operation across access-token refreshes within the long-lived token's life and explicitly does not test unattended rotation of the token itself, and task 9 must state which claim it is making. | Add the infra doc to Prior Art and make token acquisition and re-acquisition in the sandbox an explicit sub-question of task 3, since the repo's existing answer is unavailable there. `claude setup-token` "opens a browser window; the resulting token must be copied manually to the vault," so a sandbox cannot mint its own and rotation is a manual, browser-bound, roughly annual act. That makes "unattended" in Risk 7 conditional on a token the sandbox received from outside and cannot renew, a smaller claim than the plan currently makes. Say which claim task 8 is testing. |
| CONCERN | History & Consistency | Task 1 requires the probe result be recorded "as durable evidence, not as prose in a report," and Race 4 calls it "one immutable record per run." But `EVIDENCE_KINDS` is `(correction, inspiration, shipped_work, owner_liveness, other)` on both `main` and `origin/session/sdlc-3255` (`models/improvement_evidence.py:58-64`), and `record_once` coerces anything else to `other` after a warning (`:219-223`). Task 5 adds only `spend_receipt`, and it sits in phase 2 after task 1, so task 1 as written can only record the probe as `other`, which this plan itself calls indistinguishable from every other `other` row. | **The vocabulary addition moved to task 1, ahead of the probe, and gained a second kind.** `build-evidence-kinds` adds both `spend_receipt` and `resource_probe`, so task 2's probe records a queryable row instead of an `other` row. **Cross-lane ownership is declared in the module beside the constant**: this lane owns those two, lane 3 owns `resource_acquired` (`docs/plans/recursive-self-improvement.md:646`, `:800`) and adds it itself, so whichever lands second rebases onto an extended tuple rather than colliding. The comment at `models/improvement_evidence.py:57` ("low-cardinality on purpose — this is an index") is cited as the reason each addition is argued in the docstring. Verification, Test Impact, Success Criteria, Key Elements, and Architectural Impact updated. | Add the probe's evidence kind to task 5 alongside `spend_receipt`, move that vocabulary addition ahead of task 1, or state that the probe result is a different record type and name it. The same gap sits one lane over: `docs/plans/recursive-self-improvement.md:800` has lane 3's `tools/vault_write.py` writing a `resource_acquired` evidence row, another kind absent from `EVIDENCE_KINDS`, so the two lanes will collide on `models/improvement_evidence.py` unless this plan says which kinds it owns. Note the constant's own comment, "Low-cardinality on purpose, this is an index," which makes each addition an index-cardinality decision rather than a free append. |
| CONCERN | Structural check | Five of the twelve tasks carry no `Validates` field: task 2 (spike-provider), task 3 (spike-sandbox-feasibility), task 7 (build-acquisition), task 11 (document-feature), and task 12 (validate-all). Task 7 is the task that spends money, and it is one of the two with neither a `Validates` row nor a Verification-table row naming it. | **All twelve tasks now carry a `Validates` row.** Task 8 (acquisition) gets `tests/unit/test_infrastructure_budget.py::test_no_acquisition_bypasses_admission` — an import-graph and AST check asserting every acquisition call site routes through `tools.infrastructure_budget.admit` and that no lane module touches a provider SDK or CLI outside that path. **It spends nothing and needs no provider account**, which is the point round 1 made: the real acceptance is structural, not purchased. It also gains a Verification row. Tasks 3, 4, 11, and 12 get recorded-artifact paths, backed by three new Verification rows that grep the infra doc for the auth verdict, the named duty cycle, and the export/import destination contract. | Give task 7 a `Validates` entry that is checkable without a provider account, since its real acceptance is that no charge bypassed the meter. A concrete shape: a test asserting every acquisition call site routes through `tools/infrastructure_budget.admit` and that no module in this lane calls a provider SDK or CLI outside that path, which is testable by import-graph inspection rather than by spending. Tasks 2, 3, 11, and 12 produce decisions and documents, so a recorded-artifact path is the appropriate `Validates` value for each. |

**Round 2** — FULL depth (`appetite: Large` force-FULL). Roster: Risk & Robustness, Scope & Value, History & Consistency.
**Mode**: sequential lenses (Agent tool unavailable: not in tool list). No finding below was independently corroborated by a second critic.
**Verdict**: READY TO BUILD (with concerns) — 0 blockers, 6 concerns, 1 nit.

All three round-1 blockers verified closed against `main` at `017e03d93`, by re-derivation rather than by reading the revision's claim. `subscription_auth_env` is `agent/session_runner/role_driver.py:76-100` with the blanking at `:94-96`, `ANTHROPIC_API_KEY` at `:94`, the token read at `:97`, the export at `:98-99`, and the doctrine docstring at `:84-85` — every corrected citation is exact. The diff-scoped OAuth anti-criterion was executed here: it prints 0 today and its inline red-state command prints 1, and `xargs -r` is supported on this machine. All three of the plan's stated grep traps reproduce exactly. Every parent-plan citation (`:363-367`, `:424`, `:572`, `:646`, `:650`, `:800`, `:804`) and every quotation from `docs/infra/granite-oauth-token.md` checked out verbatim. The Race 1 placement argument also survives adversarial checking: `.claude/hooks/validators/validate_no_raw_redis_delete.py::_BLOCK_PATTERNS` carries no `eval`, `incrbyfloat`, `set`, or `expire` pattern, and `worker/__main__.py:672-685` already performs a raw `zadd`/`zremrangebyscore`/`expire` on `worker:starts:{host}` through `POPOTO_REDIS_DB`, which is the same shape and the same precedent.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| CONCERN | Risk & Robustness | Task 5's admission reserves atomically against `improvement:budget:unit3:{window_key}` but nothing describes releasing a reservation. If acquisition fails after admission (provider error, the declined card the No-Gos already anticipate) or the process dies between reserve and acquire, the reserved dollars stay booked forever, so unit-3 headroom shrinks monotonically with every failure until a window that spent nothing refuses everything. The window key also has no stated expiry, so windows accumulate indefinitely. | pending | Give `admit()` a paired, idempotent release exercised on the failure path, and put a bounded expiry on the window key. Return a reservation id alongside the decision and write the compensating `EVAL` (`INCRBYFLOAT key -amount`, floored at 0 inside the script so a double release cannot drive the counter negative) from the acquisition path's `except` and `finally`, never from the caller's happy path. Make it idempotent by reservation id: record the release on the Popoto reservation model and make the script a no-op when that model already shows `released`, because task 8 retries and a double-decrement silently RAISES headroom, which is the expensive direction. `EXPIRE` the window key well past the audit horizon (the plan already argues the 30-day `ImprovementEvidence` TTL is shorter than a budget week's audit life), never to the window length: a counter expiring mid-window resets headroom to full. |
| CONCERN | Risk & Robustness | The teardown ladder's fail-closed branch leaves the resource running "and escalates," and Risk 2's mitigation rests on continuation being recorded rather than silent, but no section names an escalation surface, a recipient, or a record. As written, the failure mode is a paid resource running past its window with the escalation going nowhere. That is Risk 2's silent budget overrun arriving through the guard built to prevent it. | pending | Name the surface in Technical Approach §4 and assert it in `tests/unit/test_teardown_policy.py` so "escalates" is a tested output rather than a verb. Two surfaces already exist and need no new infrastructure: an `ImprovementEvidence` row (task 1 is already adding kinds, so a durable escalation record is one more argued index-cardinality decision or reuses `spend_receipt` for the booked overrun), and the charter §9-compatible Telegram status message the parent plan's `improvement-assumption-digest` reflection already uses, since a status message that asks nothing is permitted where a question is not. Assert it inside the existing mutation check: the verifier-raises and verifier-returns-False cases must EACH leave the resource running AND produce the escalation record, or the guard is observable only in a log line nobody reads. |
| CONCERN | Risk & Robustness | Task 9's trial is "hard-stopped at the earlier of that or $15 of settled unit-3 spend," but for the per-second metered provider the plan treats as its default, settlement lags: spike-6 records per-second metering and a billing API reports after the fact. A stop keyed on settled spend can only fire after the money is gone, so the $15 figure is not a cap on a metered provider. | pending | Key the hard stop on `stop_now = max(settled_usd, forecast_usd_to_date) >= 15.0`, evaluated on the trial's own watchdog tick rather than on settlement arrival. The forecast is the only figure available in real time and `admit()` already computes it for the duty cycle, so this adds no new machinery. State it beside the rule it mirrors: "missing or uncertain metering settles at the forecast, never at zero" already governs settlement, and this is the same asymmetry pointed at the live window. Test it with a billing reader that reports zero for the whole trial; the stop must still fire from the forecast alone. |
| CONCERN | Scope & Value | Task 7 is declared phase 2 and the plan promises phase 2 ships without #3215, but its acceptance is "a test loading a sandbox-written artifact from a local process" and the matching Success Criterion reads "an artifact written from the sandbox loads hash-verified from a local process." A sandbox exists only after task 9, which is phase 3 and runs only under auth mode A. Under mode B, an outcome §5a makes first-class and task 4 is instructed to return absent an affirmative vendor finding, the retention-root supersession this lane was told to own has an acceptance test that can never be satisfied. | pending | Split the acceptance: prove supersession against a remote-shaped store needing no provider account, and keep the literal sandbox round trip as recorded task-9 evidence. `models/verifying_artifact_store.py::_default_base_path` (`:45-56`) is already written to be redirected at runtime, reading the env var fresh on every call rather than through the cached settings singleton, which is exactly what lets a second process with a different cwd stand in for the container. So task 7's testable acceptance is: point `POPOTO_IMPROVEMENT_CONTENT_PATH` at the chosen durable root, write through `VerifyingArtifactStore` from one process, load from a second process started in a different working directory, and let the re-hash-on-every-load verify. Update `tests/unit/test_length_safe_content_store.py::test_retention_root_is_separate_from_the_shared_content_path` (`:224`), already flagged for UPDATE. Move "written from the sandbox" out of task 7's `Validates` and out of the Success Criterion into task 9's recorded evidence, or that criterion cannot be marked reached under mode B. |
| CONCERN | Scope & Value | `document-feature` depends only on `build-operating-report`, and `validate-all` only on `document-feature`, so neither has `spike-provider`, `spike-sandbox-feasibility`, `build-state-topology`, or `build-concurrency-revisit` as an ancestor. Yet task 11 must write the provider decision, the auth verdict, the retention-root supersession, and the export/import destination contract into the infra doc, and three of task 12's Verification rows grep that file for exactly those artifacts. The plan states the task IDs carry the graph and the numbers are only reading order, so a builder following the graph runs phase 4 before the work it documents exists. | pending | Set `document-feature`'s `Depends On` to `build-operating-report, spike-sandbox-feasibility, build-state-topology` (the three artifacts its own bullets require) and `validate-all`'s to `document-feature, build-concurrency-revisit`, marking the phase-3 members satisfied-if-run the same way task 6 already folds phase 3 in "if it has run." Do NOT instead delete the grep rows: they are the only checks that the auth verdict, the duty cycle, and the export/import destination contract were written down rather than decided in a builder's head. Separately, task 3 and task 4 both name `docs/infra/improvement-cloud-execution.md` in their own `Validates` while task 11 says "Create" it, so say which task creates the file; as written two owners describe the same file. |
| CONCERN | History & Consistency | The revision made mode B first-class (task 4 returns it absent an affirmative vendor finding; task 9 "does not run under mode B") but did not propagate it to the disposition rule. Task 12 marks phase-3 criteria not reached "if #3215 never landed," which is the wrong condition: with #3215 landed and task 4 returning mode B, tasks 9 and 10 still do not run and three Success Criteria are left with no recorded disposition. Task 10 also becomes unreachable under mode B since it depends on `build-sandbox-trial`, while its own criterion says "leaving the question open is not" a passing outcome, so mode B currently forces a stated non-passing outcome. | pending | Make task 12's condition disjunctive: mark phase-3 criteria not reached if `#3215 never landed OR task 4 recorded mode B`. Add the matching clause to the three phase-3 Success Criteria (the unattended sandbox session, the dashboard rendering it, and the `max_concurrent_research_sessions` decision): "or, under mode B, recorded as not reached with the auth verdict as its cause." This is not cosmetic. Task 6's fifth answer is already specified to include "task 4's auth verdict when it is mode B," so under mode B the report claims to cover ground that no rule requires the validator to mark. |
| NIT | History & Consistency | The Verification row "This lane did not claim lane 3's kind" expects "False until #3215 lands; either value passes afterward" — a check that cannot fail once #3215 merges, which is the same vacuous shape round 1 measured for the alternation grep and that the plan itself calls worse than no check. | pending | Scope it to this lane's diff so it keeps biting after #3215 lands: `git diff --name-only origin/main...HEAD -- models/improvement_evidence.py` piped through `xargs -r grep -c resource_acquired` reads 0 whether or not #3215 has landed, because it measures this lane's delta rather than the tuple's contents. Or drop the row and rely on the module-level ownership declaration task 1 already requires. |

---

## Open Questions

1. **The scale question in spike-1 is the lane's central unknown, and it is not mine to answer.** Anthropic permits the official Claude Code CLI on a remote host and blocks subscription OAuth in hosted runtimes; what is undocumented is whether a continuously-running sandbox consuming subscription concurrency counts as the "ordinary, individual usage" subscription limits are written against. Charter §9 forbids routing research questions to Tom, so the plan's answer is to record a provisional assumption and report it as the fifth answer. **Is that the disposition you want, or is this the rare case where an evidence-backed amendment request under §9 is the right move — since it touches authority and budget rather than a research choice?** This is the one question in the plan I genuinely cannot resolve from evidence.

2. **Charter §8 names a funded Cloudflare account, and spike-6 suggests Cloudflare's per-second Containers pricing may not survive Gap D's forecastability rule for a 24/7 instance.** The plan lets the evidence decide and requires the arithmetic to be recorded either way. **Confirming: is spending unit 3 with a provider the charter does not name acceptable when the arithmetic supports it, or does §8's naming carry a preference the arithmetic should not override?**

3. **Task 9's trial spends real money for one measurement, and the plan now proceeds on a default rather than waiting for this answer.** The success bar is deliberately strict — cross a credential-refresh boundary and survive an induced crash. Round 1 was right that this could not stay an open preference: the duty cycle is task 3's forecast input, so an unanswered question would have stalled the provider decision too. **The stated default is 72 hours of continuous availability, hard-capped, and hard-stopped at the earlier of that or $15 of settled unit-3 spend**, and task 3's arithmetic uses that same figure. Three days crosses several access-token refreshes, which is the failure `anthropics/claude-code#50743` reports, while staying well inside a $50 week. **Is that bound right, or should the first trial be shorter and produce a lesser, cheaper finding?** Answering changes a number; it no longer unblocks anything.

4. **The plan predicts task 10 concludes "`max_concurrent_research_sessions` unchanged."** If sandboxes add machines and not subscription capacity, raising it changes nothing except how fast the subscription is exhausted. **Is a recorded null result an acceptable outcome for that task, or do you want the revisit to carry a bias toward raising it?**

