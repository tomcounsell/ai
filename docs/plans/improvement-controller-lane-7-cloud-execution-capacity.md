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

<!-- skeleton -->

## Data Flow

<!-- skeleton -->

## Architectural Impact

<!-- skeleton -->

## Appetite

<!-- skeleton -->

## Prerequisites

<!-- skeleton -->

## Solution

<!-- skeleton -->

## Failure Path Test Strategy

<!-- skeleton -->

## Test Impact

<!-- skeleton -->

## Rabbit Holes

<!-- skeleton -->

## Risks

<!-- skeleton -->

## Race Conditions

<!-- skeleton -->

## No-Gos (Out of Scope)

<!-- skeleton -->

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
