---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-03-12
tracking: https://github.com/tomcounsell/ai/issues/307
last_comment_id:
---

# Poll GitHub Issues for Automatic SDLC Kickoff and Deduplication

## Problem

SDLC work only starts when a human explicitly sends "SDLC issue 123" via Telegram. New GitHub issues sit idle until someone notices them and manually triggers planning.

**Current behavior:**
Issues are created on GitHub but nothing happens until Valor manually sends a Telegram message to kick off the SDLC pipeline. This creates latency and requires human attention for every new issue.

**Desired outcome:**
New GitHub issues are automatically detected, checked for duplicates, and get a draft plan created -- all without human intervention. The human is notified and reviews when ready, rather than initiating every step.

## Prior Art

- **Issue #258**: Job queue: agent self-scheduling, batch dispatch, and deferred execution -- CLOSED. Established the job queue architecture (`agent/job_queue.py`) with per-project sequential workers. The poller can enqueue draft-plan jobs through this existing infrastructure.

No prior PRs found addressing issue polling specifically. This is greenfield work building on existing scheduling and job queue infrastructure.

## Data Flow

1. **Entry point**: Cron/launchd triggers `scripts/issue_poller.py` on a schedule (every 5 minutes)
2. **GitHub API**: `gh issue list` fetches open issues, filtered to exclude already-seen issues
3. **Seen-issue tracker**: Redis set (`issue_poller:seen:{repo}`) stores issue numbers already processed
4. **Deduplication check**: For each new issue, compare title+body against other open issues using LLM similarity scoring (Claude Haiku)
5. **Dispatch**: If not a duplicate, enqueue a `/do-plan` job via the job queue OR invoke `claude -p` directly to create a draft plan
6. **Notification**: Send Telegram message to the relevant project group with issue link and plan status
7. **Labeling**: Apply GitHub labels (`auto-planned`, `needs-review`, `possible-duplicate`) to track state

## Architectural Impact

- **New dependencies**: None -- uses existing `gh` CLI, Redis (via Popoto), Claude Haiku API, and Telegram bridge
- **Interface changes**: New script entry point; new Redis keys for seen-issue tracking
- **Coupling**: Low coupling -- the poller is a standalone script that uses existing plan creation and notification infrastructure
- **Data ownership**: Redis owns the seen-issue state; GitHub labels provide a secondary indicator
- **Reversibility**: Fully reversible -- remove the cron job and script, labels can be cleaned up

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1-2 (scope alignment on polling frequency, dedup threshold, notification format)
- Review rounds: 1 (code review)

The core polling loop is straightforward. The dedup logic using LLM similarity and the multi-project support add complexity that pushes this beyond Small.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ANTHROPIC_API_KEY` | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('ANTHROPIC_API_KEY')"` | Haiku API for dedup similarity |
| Redis running | `python -c "import redis; redis.Redis().ping()"` | Seen-issue tracking |
| `gh` authenticated | `gh auth status` | GitHub issue access |

Run all checks: `python scripts/check_prerequisites.py docs/plans/issue_poller_sdlc_kickoff.md`

## Solution

### Key Elements

- **Issue Poller Script**: Standalone script that polls GitHub issues across configured projects and detects new ones
- **Seen-Issue Tracker**: Redis-backed set per repo tracking which issues have been processed
- **Dedup Engine**: LLM-based similarity check against open issues and existing plan docs before creating new plans
- **Plan Dispatcher**: Invokes `claude -p` with `/do-plan` to create draft plans for valid new issues
- **Notification Layer**: Sends Telegram messages for all automated actions (new plan, duplicate detected, issue too vague)

### Flow

**Cron fires** -> Poll `gh issue list` per project -> Filter out seen issues -> **For each new issue:** -> Dedup check (LLM similarity) -> If duplicate: label + comment + notify -> If valid: invoke `/do-plan` -> Label `auto-planned` -> Notify via Telegram

### Technical Approach

- **Standalone script** (`scripts/issue_poller.py`) rather than integrating into reflections.py -- reflections runs daily at 6 AM, but polling needs to run every 5 minutes
- **Dedicated launchd plist** (`com.valor.issue-poller.plist`) with 5-minute `StartInterval`
- **Redis set** for seen-issue tracking (`issue_poller:seen:{org}/{repo}`) -- lightweight and fast
- **Claude Haiku** for similarity scoring -- compare new issue title+body against open issues, threshold at 0.8 for duplicate, 0.5-0.8 for related
- **Multi-project support** -- iterate over `config/projects.json` entries that have a `github` key
- **Rate limit awareness** -- track `gh` API calls, back off if approaching GitHub's rate limits
- **Plan creation via subprocess** -- invoke `claude -p "Create a plan for issue #{N} in {repo}"` to leverage existing `/do-plan` skill rather than reimplementing plan creation logic

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `gh` CLI failures (network, auth expired) -- log warning, skip this polling cycle, do not crash
- [ ] Redis connection failures -- log error, skip cycle (cannot track seen issues without Redis)
- [ ] Claude API failures during dedup -- skip dedup, still create plan (dedup is best-effort)
- [ ] Telegram notification failures -- log warning, do not block plan creation

### Empty/Invalid Input Handling
- [ ] Issues with empty body -- flag as "needs-review" instead of auto-planning
- [ ] Issues with only a title -- attempt planning but note in notification that context is thin
- [ ] Malformed JSON from `gh` CLI -- catch and log, skip cycle

### Error State Rendering
- [ ] All errors are logged to `logs/issue_poller.log`
- [ ] Telegram notification on persistent failures (3+ consecutive cycles failing)

## Rabbit Holes

- **Real-time webhooks instead of polling** -- GitHub webhooks require a public endpoint, our system runs on a laptop. Polling is the right approach here.
- **Semantic embedding-based dedup** -- Using Ollama or embedding models for similarity is tempting but Claude Haiku with a simple prompt is sufficient and avoids a new dependency.
- **Auto-building after auto-planning** -- The issue explicitly says no auto-build. Plans are drafts that need human review.
- **Cross-project dedup** -- Detecting duplicates across different repos adds complexity for little value. Keep dedup within each repo.

## Risks

### Risk 1: Notification noise
**Impact:** Too many Telegram messages from auto-planning creates alert fatigue
**Mitigation:** Batch notifications if multiple issues arrive in same cycle. Add a daily summary mode option. Start with conservative polling (every 5 min) and tune.

### Risk 2: GitHub API rate limits
**Impact:** Polling 7+ repos every 5 minutes could hit rate limits (5000 requests/hour for authenticated)
**Mitigation:** Each poll is ~1 request per repo (7 per cycle, 84/hour -- well within limits). Add rate limit header checking and back-off logic.

### Risk 3: LLM dedup false positives
**Impact:** Legitimate new issues flagged as duplicates and not auto-planned
**Mitigation:** When flagging as duplicate, always comment with the suspected duplicate link and label as `possible-duplicate` (not `duplicate`) so the human can override. Err on the side of creating plans.

## Race Conditions

### Race 1: Concurrent poller executions
**Location:** `scripts/issue_poller.py` entry point
**Trigger:** Launchd fires a new cycle before the previous one finishes (slow API responses)
**Data prerequisite:** Seen-issue set must reflect completed processing, not in-flight
**State prerequisite:** Only one poller instance should run at a time per project
**Mitigation:** Use a Redis lock (`issue_poller:lock`) with TTL. If lock exists, skip this cycle.

### Race 2: Manual SDLC trigger during auto-planning
**Location:** Job queue and plan creation
**Trigger:** Human sends "SDLC issue 42" while poller is creating a plan for issue 42
**Data prerequisite:** Plan file and branch must not already exist
**State prerequisite:** Only one plan creation should run per issue
**Mitigation:** Check for existing plan doc referencing the issue number before dispatching. The seen-issue set also prevents re-processing.

## No-Gos (Out of Scope)

- Do NOT auto-merge or auto-build -- only draft plans
- Do NOT replace Telegram as the primary interface -- this supplements it
- Do NOT poll more frequently than every 2 minutes (GitHub API rate limits)
- Do NOT implement webhook-based triggering -- polling is sufficient for our laptop-based deployment
- Do NOT add cross-project duplicate detection -- keep dedup within each repo
- Do NOT modify the existing reflections.py scheduler -- this is a separate service

## Update System

- New launchd plist (`com.valor.issue-poller.plist`) needs to be installed on all machines
- Add install step to `scripts/install_reflections.sh` (or create dedicated `scripts/install_issue_poller.sh`)
- The `/update` skill should install the new launchd service as part of updates
- No new Python dependencies required -- uses existing `anthropic`, `redis`, `subprocess` for `gh`

## Agent Integration

- No MCP server changes needed -- the poller runs as a standalone cron script, not as an agent tool
- The poller invokes `claude -p` as a subprocess to create plans, leveraging existing `/do-plan` skill
- Telegram notifications use the existing bridge notification mechanism (or direct Telethon API if bridge is not running)
- No changes to `.mcp.json` required
- Integration test: verify the poller script can detect a new issue and enqueue a plan creation job

## Documentation

- [ ] Create `docs/features/issue-poller.md` describing the polling service, configuration, and troubleshooting
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update `docs/deployment.md` with issue poller launchd setup
- [ ] Code comments on dedup threshold tuning and rate limit handling

## Success Criteria

- [ ] New GitHub issues are detected within 5 minutes of creation
- [ ] Draft plans are automatically created for valid new issues via `/do-plan`
- [ ] Duplicate issues are detected (>0.8 similarity) and flagged with `possible-duplicate` label
- [ ] Related issues (0.5-0.8 similarity) are noted as dependencies in draft plans
- [ ] Human is notified via Telegram of all auto-planning activity
- [ ] Polling handles multi-project setup (all repos in `config/projects.json`)
- [ ] Concurrent execution is prevented via Redis lock
- [ ] Issues with insufficient context are flagged as `needs-review` instead of auto-planned
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (poller-core)**
  - Name: poller-builder
  - Role: Implement the polling script, seen-issue tracker, and launchd plist
  - Agent Type: builder
  - Resume: true

- **Builder (dedup-engine)**
  - Name: dedup-builder
  - Role: Implement LLM-based dedup logic and GitHub labeling
  - Agent Type: builder
  - Resume: true

- **Builder (notifications)**
  - Name: notification-builder
  - Role: Implement Telegram notification layer for poller events
  - Agent Type: builder
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: Verify end-to-end flow from issue creation to plan creation
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Build Poller Core
- **Task ID**: build-poller-core
- **Depends On**: none
- **Assigned To**: poller-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `scripts/issue_poller.py` with main polling loop
- Implement Redis-backed seen-issue tracker (`issue_poller:seen:{org}/{repo}`)
- Implement Redis lock for concurrent execution prevention
- Add multi-project support reading from `config/projects.json`
- Add rate limit header checking for `gh` CLI calls
- Create `com.valor.issue-poller.plist` with 5-minute StartInterval
- Create `scripts/install_issue_poller.sh` for launchd installation

### 2. Build Dedup Engine
- **Task ID**: build-dedup
- **Depends On**: none
- **Assigned To**: dedup-builder
- **Agent Type**: builder
- **Parallel**: true
- Implement `scripts/issue_dedup.py` with LLM similarity scoring using Claude Haiku
- Accept issue title+body, compare against list of open issues
- Return similarity scores with duplicate/related/unique classification
- Implement GitHub label application (`possible-duplicate`, `auto-planned`, `needs-review`)
- Add comment on duplicate issues linking to the suspected original

### 3. Build Notification Layer
- **Task ID**: build-notifications
- **Depends On**: none
- **Assigned To**: notification-builder
- **Agent Type**: builder
- **Parallel**: true
- Implement Telegram notification for: new plan created, duplicate detected, issue needs review
- Support batched notifications when multiple issues arrive in one cycle
- Log all notifications to `logs/issue_poller.log`

### 4. Integrate Components
- **Task ID**: build-integration
- **Depends On**: build-poller-core, build-dedup, build-notifications
- **Assigned To**: poller-builder
- **Agent Type**: builder
- **Parallel**: false
- Wire dedup engine into poller loop (check before dispatching plan)
- Wire notification layer into poller loop (notify after each action)
- Implement plan dispatch via `claude -p` subprocess
- Add issue validation (empty body, title-only detection)

### 5. Validate Integration
- **Task ID**: validate-integration
- **Depends On**: build-integration
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify poller detects new issues correctly
- Verify dedup flags similar issues
- Verify seen-issue tracking prevents re-processing
- Verify Redis lock prevents concurrent execution
- Run lint and format checks

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: notification-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/issue-poller.md`
- Add entry to `docs/features/README.md`
- Update `docs/deployment.md` with launchd setup

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all verification checks
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Poller script exists | `test -f scripts/issue_poller.py` | exit code 0 |
| Dedup module exists | `test -f scripts/issue_dedup.py` | exit code 0 |
| Launchd plist exists | `test -f com.valor.issue-poller.plist` | exit code 0 |
| Feature docs exist | `test -f docs/features/issue-poller.md` | exit code 0 |

---

## Open Questions

1. **Plan dispatch mechanism**: Should the poller invoke `claude -p` directly (simpler, but spawns a full Claude session per issue) or enqueue a job via the existing job queue in `agent/job_queue.py` (more integrated, but requires the bridge to be running)? The bridge-based approach is more robust but creates a dependency on the bridge being up.

2. **Notification channel**: Should notifications go to the project-specific Telegram group (as configured in `config/projects.json`) or to a dedicated "automation" channel? Project-specific keeps context close but might be noisy.

3. **Dedup scope**: Should dedup also check against closed issues (to detect issues being re-opened or re-filed)? This increases API calls but catches more duplicates.

4. **Issue filter criteria**: Should the poller only process issues with specific labels (e.g., `needs-plan`) or process ALL new issues? Processing all is simpler but might auto-plan issues that were intentionally left as discussion items.
