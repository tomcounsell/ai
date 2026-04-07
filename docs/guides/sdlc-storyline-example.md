# The Revenue Analytics Plugin: An SDLC Pipeline Case Study

*How an autonomous development pipeline steers a feature from a casual Telegram message to a merged pull request -- and what happens when things go wrong along the way.*

---

## 1. The Spark

It starts with a message on Telegram at 2:14 PM on a Tuesday:

```
Valor: hey, the billing system needs revenue analytics.
       like a dashboard that shows MRR trends, churn rate,
       cohort analysis. the data's all in postgres already,
       just needs a plugin that pulls it together
```

The bridge receives this via Telethon, creates an `AgentSession` in Redis, and routes it to the worker. The classifier tags it as a `work_request` -- not a question, not a casual chat. This classification is what triggers the SDLC pipeline rather than a one-off response.

The worker's first output is a summary of what it understood:

```
I understand you want a Revenue Analytics Plugin for the billing system.
This would include MRR trend visualization, churn rate tracking, and
cohort analysis, pulling from the existing Postgres data. I'll create
a GitHub issue to scope this properly.
```

**What could go wrong here**: The worker could misclassify this as a casual question and just answer with a paragraph about analytics frameworks. The classifier uses a confidence threshold (`CLASSIFICATION_CONFIDENCE_THRESHOLD = 0.80`); below that, it defaults to `QUESTION` -- conservative, but it pauses for human input rather than running unattended with the wrong context. In this case, the confidence is 0.94 for `work_request`, so the pipeline engages.

**What the Observer decides**: After the worker stops, the Observer reads the session state via `read_session`. No stages are completed. No issue URL exists. The stage progress is all `pending`. The deterministic SDLC guard kicks in:

```python
# Phase 1.75: Deterministic SDLC stage guard
if (
    is_sdlc
    and has_remaining
    and not has_failed
    and not stop_is_terminal
    and not cap_reached
    and not needs_human
):
    next_stage_info = _next_sdlc_skill(self.session)
```

The guard routes to ISSUE stage, and the Observer steers with a coaching message:

```
Pipeline has remaining stages. Next: ISSUE. Continue with /do-issue.
If you encounter a critical blocker requiring human input, state it
clearly. Otherwise, press forward.
```

The human sees nothing yet. The pipeline is working silently.

---

## 2. Issue Creation

The worker invokes `/do-issue`. It structures the raw Telegram message into a proper GitHub issue:

```markdown
## Revenue Analytics Plugin

**Summary**: Add a data science analytics dashboard to the billing system
that visualizes MRR trends, churn rate, and cohort analysis from existing
Postgres data.

**Context**: All revenue data already lives in the `billing_transactions`,
`subscriptions`, and `customer_events` tables. The plugin should query
these directly and expose dashboard endpoints.

**Acceptance Criteria**:
- [ ] MRR trend chart (monthly, with growth rate)
- [ ] Churn rate calculation (logo churn and revenue churn)
- [ ] Cohort retention matrix (monthly cohorts, 12-month horizon)
- [ ] Dashboard accessible via `/analytics/revenue` endpoint
- [ ] Data refreshes on page load (no stale cache)
```

The worker runs:

```bash
gh issue create --title "Revenue Analytics Plugin" --body "..."
```

And the output contains:

```
https://github.com/tomcounsell/billing/issues/247
```

**What could go wrong**: The worker might emit a URL from the wrong repository. If the worker's context includes URLs from prior sessions (a different repo it was working on), it might report something like `https://github.com/tomcounsell/ai/issues/247` instead. This is exactly what the deterministic URL construction prevents.

When the Observer stores the issue URL, it does not save the worker's URL verbatim. It extracts the number and reconstructs it:

```python
def _construct_canonical_url(url, gh_repo):
    issue_match = _ISSUE_NUMBER_RE.search(url)
    if issue_match:
        number = issue_match.group(1)
        return f"https://github.com/{gh_repo}/issues/{number}"
```

With `GH_REPO=tomcounsell/billing`, the session always stores `https://github.com/tomcounsell/billing/issues/247` -- regardless of whatever the worker actually printed.

The stage detector picks up the completion:

```
[stage-detector] Checked 13 patterns, matched: ['ISSUE=completed']
[stage-detector] Applied ISSUE -> completed: ISSUE completion marker detected
```

The worker also emits a typed outcome:

```html
<!-- OUTCOME {"status":"success","stage":"ISSUE","artifacts":{"issue_url":"https://github.com/tomcounsell/billing/issues/247"},"notes":"Issue #247 created","next_skill":"/do-plan"} -->
```

**What the Observer decides**: The typed outcome parser fires first. It sees `status=success` and `has_remaining_stages()=True`. This is the fast path -- no LLM call needed:

```python
if outcome.status == "success" and self.session.has_remaining_stages():
    coaching = (
        f"{outcome.stage} completed successfully. "
        f"{outcome.notes} Continue with {next_skill}."
    )
    return {"action": "steer", "coaching_message": coaching, ...}
```

Log line:

```
[obs-a7c3] Typed outcome routing: steer (success, remaining stages)
```

The worker receives:

```
ISSUE completed successfully. Issue #247 created. Continue with /do-plan.
```

The human still sees nothing. Auto-continue count: 2.

---

## 3. Planning

The worker invokes `/do-plan revenue-analytics-plugin`. This creates a plan document at `docs/plans/revenue-analytics-plugin.md` and outputs it for review.

The plan includes:

```markdown
## Scope

### In Scope
- MRR calculation engine (net new, expansion, contraction, churn)
- Cohort retention matrix with configurable time windows
- REST API endpoints under `/api/analytics/`
- Simple frontend dashboard using Chart.js

### Out of Scope
- Real-time streaming analytics
- Custom report builder
- Export to PDF/CSV (future iteration)

## Appetite
- **Budget**: 2-day build (Medium)
- **Complexity**: Moderate — SQL aggregations are straightforward,
  but cohort logic has edge cases around trial periods

## Open Questions
1. Should trial-to-paid conversions count as "new MRR" in the month
   they convert, or the month they started their trial?
2. Do we want the dashboard behind authentication, or is it
   internal-only with no auth gate?
```

**What could go wrong**: The "Open Questions" section triggers the human input detection heuristic. The Observer's deterministic guard checks:

```python
_HUMAN_INPUT_PATTERNS = [
    re.compile(r"## Open Questions", re.IGNORECASE),
    re.compile(r"(?:Should I|Should we|Do you want|Would you prefer)", re.IGNORECASE),
    ...
]
```

Both patterns match. `needs_human` is `True`. The deterministic SDLC guard is bypassed:

```
[obs-a7c3] Deterministic SDLC guard bypassed: has_failed=False,
stop_reason=end_turn, cap_reached=False, needs_human=True
— falling through to LLM Observer
```

The LLM Observer reads the session state, sees the open questions, and decides to DELIVER:

```python
deliver_to_telegram(
    reason="Plan has open questions requiring human decisions",
    message_for_user="""Plan created for Revenue Analytics Plugin (issue #247).

Two questions before I proceed:

1. **Trial conversions**: Should trial-to-paid count as new MRR in the conversion month or the trial start month?
2. **Authentication**: Dashboard behind auth, or internal-only with no gate?

Plan: docs/plans/revenue-analytics-plugin.md"""
)
```

**What the human sees on Telegram**:

```
Plan created for Revenue Analytics Plugin (issue #247).

Two questions before I proceed:

1. Trial conversions: Should trial-to-paid count as new MRR in the
   conversion month or the trial start month?
2. Authentication: Dashboard behind auth, or internal-only with no gate?

Plan: docs/plans/revenue-analytics-plugin.md
```

The session enters DORMANT state, waiting for a human reply.

**What could go wrong here (human input detection)**: If the plan had said "I will handle trial conversions as new MRR in the conversion month" instead of asking a question, the `_output_needs_human_input` check would return `False` -- the statement "I will" is not in the heuristic patterns. The deterministic guard would force-steer to BUILD. This is correct behavior: statements of intent are not questions. The human can always interrupt via a reply-to message if they disagree.

Valor replies thirty minutes later:

```
Valor: conversion month for MRR. no auth, it's internal only
```

The bridge appends this to `queued_steering_messages` on the session. The worker resumes with the human's reply as context.

---

## 4. Building

The worker invokes `/do-build` with the plan path. The build stage is the longest -- it creates a git worktree, implements the code, writes tests, and opens a PR.

The worker creates the branch and worktree:

```bash
git worktree add .worktrees/revenue-analytics-plugin -b session/revenue-analytics-plugin
```

Then it implements the plugin across several files:

- `plugins/revenue_analytics/engine.py` -- MRR calculation, churn rate, cohort matrix
- `plugins/revenue_analytics/api.py` -- REST endpoints
- `plugins/revenue_analytics/dashboard.py` -- Chart.js frontend template
- `tests/test_revenue_analytics.py` -- Unit tests for the engine
- `tests/test_revenue_api.py` -- API endpoint tests

**What could go wrong (rate limited mid-build)**: Halfway through implementing the cohort matrix, the Claude API returns `stop_reason: rate_limited`. The worker process is interrupted.

The Observer receives the truncated output with `stop_reason="rate_limited"`. The deterministic stop_reason routing catches this before any LLM call and steers with a backoff instruction:

```python
# Phase 1: Deterministic routing based on stop_reason
if self.stop_reason == "rate_limited":
    logger.warning(f"{self._log_prefix} Worker stopped: rate_limited — steering with backoff")
    return {
        "action": "steer",
        "coaching_message": "Rate limited by API. Wait briefly and resume.",
        "stop_reason": self.stop_reason,
    }
```

The worker resumes after the backoff. The pipeline state file at `data/pipeline/revenue-analytics-plugin/state.json` shows where it left off:

```json
{
  "slug": "revenue-analytics-plugin",
  "branch": "session/revenue-analytics-plugin",
  "stage": "implement",
  "completed_stages": ["plan", "branch"],
  "patch_iterations": 0,
  "started_at": "2026-03-16T14:32:00Z",
  "updated_at": "2026-03-16T15:08:00Z"
}
```

The build resumes and completes. The worker creates the PR:

```bash
gh pr create --title "Add revenue analytics plugin" \
  --body "Closes #247 ..."
```

Output:

```
https://github.com/tomcounsell/billing/pull/251
```

The worker emits:

```html
<!-- OUTCOME {"status":"success","stage":"BUILD","artifacts":{"pr_url":"https://github.com/tomcounsell/billing/pull/251"},"notes":"PR #251 created with revenue analytics plugin","next_skill":"/do-test"} -->
```

The Observer saves the canonical PR URL (`https://github.com/tomcounsell/billing/pull/251`) and steers to TEST. Auto-continue count: 4.

---

## 5. Testing

The worker invokes `/do-test`. It runs the full test suite:

```bash
cd .worktrees/revenue-analytics-plugin && pytest tests/ -v
```

Output:

```
tests/test_revenue_analytics.py::test_mrr_calculation PASSED
tests/test_revenue_analytics.py::test_mrr_with_trial_conversion PASSED
tests/test_revenue_analytics.py::test_churn_rate_basic PASSED
tests/test_revenue_analytics.py::test_churn_rate_zero_customers FAILED
tests/test_revenue_analytics.py::test_cohort_matrix PASSED
tests/test_revenue_analytics.py::test_cohort_matrix_partial_month FAILED
tests/test_revenue_api.py::test_mrr_endpoint PASSED
tests/test_revenue_api.py::test_cohort_endpoint PASSED
tests/test_revenue_api.py::test_invalid_date_range PASSED

7 passed, 2 failed
```

Two failures:

1. `test_churn_rate_zero_customers` -- division by zero when there are no customers in the base period
2. `test_cohort_matrix_partial_month` -- off-by-one error when the current month has incomplete data

The stage detector picks up the test results:

```
[stage-detector] Checked 13 patterns, matched: ['TEST=completed']
```

But wait -- the typed outcome tells a different story:

```html
<!-- OUTCOME {"status":"fail","stage":"TEST","failure_reason":"2 test failures: division by zero in churn_rate, off-by-one in cohort_matrix_partial_month","notes":"7 passed, 2 failed"} -->
```

The cross-check mechanism in `apply_transitions` resolves this conflict:

```python
elif outcome.status == "fail" and outcome.stage in regex_stages:
    logger.warning(
        f"[stage-detector] Cross-check mismatch: typed outcome says "
        f"{outcome.stage} failed but regex detected it as completing. "
        f"Trusting typed outcome."
    )
```

The outcome (structured, from the worker) takes priority over the regex (which only saw "7 passed" and triggered the completion pattern). TEST is marked as `failed`, not `completed`.

**What the Observer decides**: The typed outcome has `status=fail`:

```python
if outcome.status == "fail":
    reason = f"{outcome.stage} failed: {outcome.failure_reason or outcome.notes}"
    return {"action": "deliver", "reason": reason, ...}
```

But hold on -- this is a test failure in an SDLC pipeline. The pipeline graph says `("TEST", "fail") -> "PATCH"`. Should the Observer deliver to the human, or steer to PATCH?

The typed outcome routing delivers failures. But the Observer's session state now shows TEST as failed, and the `_next_sdlc_skill` function would resolve to PATCH. The key insight: **test failures are not human blockers**. The pipeline is designed to self-heal through the PATCH cycle.

In practice, the stage detector marks TEST as failed. On the next Observer evaluation -- when the worker stops after emitting this outcome -- the Observer sees `has_failed_stage()=True`. The deterministic SDLC guard is bypassed (safety condition: `not has_failed`), and the LLM Observer gets involved.

The LLM Observer reads the session, sees the test failures are fixable (not a fundamental architecture problem), and decides to STEER:

```python
enqueue_continuation(
    coaching_message="TEST found 2 failures: division by zero in churn_rate "
    "and off-by-one in cohort_matrix. These are fixable bugs. Continue with "
    "/do-patch to fix both issues, then /do-test will re-verify. "
    "Success means all 9 tests passing."
)
```

The human sees nothing. The pipeline enters its first patch cycle.

---

## 6. Patching (The TEST -> PATCH -> TEST Cycle)

### Patch Cycle 1

The worker invokes `/do-patch`. It reads the test output, identifies the two bugs, and fixes them:

**Fix 1** -- `engine.py`, `churn_rate()`:

```python
def churn_rate(self, period_start, period_end):
    base_customers = self.count_customers(period_start)
    if base_customers == 0:
        return 0.0  # No customers = no churn (was: division by zero)
    churned = self.count_churned(period_start, period_end)
    return churned / base_customers
```

**Fix 2** -- `engine.py`, `cohort_matrix()`:

```python
def cohort_matrix(self, months=12):
    today = date.today()
    # Use the last completed month, not the current partial month
    end_month = today.replace(day=1) - timedelta(days=1)  # was: today
    end_month = end_month.replace(day=1)
    ...
```

The worker commits and pushes:

```bash
git add -A && git commit -m "Fix churn rate division by zero and cohort off-by-one" && git push
```

Then it runs the tests again:

```
9 passed, 0 failed
```

The typed outcome:

```html
<!-- OUTCOME {"status":"success","stage":"TEST","artifacts":{},"notes":"All 9 tests passing after patch","next_skill":"/do-pr-review"} -->
```

The pipeline graph edge `("PATCH", "success") -> "TEST"` fires, but since the worker already ran tests and they passed, the stage detector marks TEST as completed. The Observer sees success with remaining stages and steers to REVIEW.

**What could go wrong (stuck in a patch loop)**: Suppose the fix for the off-by-one introduced a new failure. The pipeline would cycle: TEST(fail) -> PATCH -> TEST(fail) -> PATCH -> TEST. The `cycle_count` parameter tracks this:

```python
# Count PATCH cycles from history for the max-cycle safety valve
cycle_count = 0
history = session.get_history_list()
for entry in history:
    if isinstance(entry, dict) and entry.get("stage") == "PATCH":
        cycle_count += 1
```

When `cycle_count >= MAX_PATCH_CYCLES` (which is 3), `get_next_stage` returns `None`:

```python
if current_stage == "PATCH" and cycle_count >= MAX_PATCH_CYCLES:
    logger.warning(
        f"Max patch cycle limit reached ({cycle_count}/{MAX_PATCH_CYCLES}). "
        f"Escalating to human review."
    )
    return None
```

The `_next_sdlc_skill` function returns `None`, the deterministic guard has no stage to route to, and the LLM Observer delivers to the human:

```
Tests are still failing after 3 patch attempts. The cohort matrix
off-by-one keeps recurring because the fix conflicts with the
timezone-aware date handling in the subscription model.

Failures:
- test_cohort_matrix_partial_month: Expected 11 rows, got 12

This might need a design decision about whether to use UTC or
local time for cohort boundaries.
```

But in our story, the first patch works. Onward to review.

---

## 7. Review (The REVIEW -> PATCH -> TEST -> REVIEW Cycle)

The Observer steers to REVIEW. But first, the goal gate checks fire. REVIEW requires `pr_url` to exist on the session:

```python
if next_stage == "REVIEW" and not getattr(session, "pr_url", None):
    review_status = progress.get("REVIEW", "pending")
    if review_status == "pending":
        logger.info(
            "SDLC routing: REVIEW is next but no pr_url on session — "
            "routing to BUILD to create PR first"
        )
        return ("BUILD", "/do-build")
```

The session has `pr_url = https://github.com/tomcounsell/billing/pull/251` (saved earlier with canonical URL construction), so the gate passes. The worker invokes `/do-pr-review 251`.

The review skill examines the PR diff, runs linting, checks test coverage, and looks for code quality issues. It finds two problems:

```markdown
## Review Results

### Issues Found

**1. SQL Injection Risk (severity: high)**
File: `plugins/revenue_analytics/api.py`, line 47

```python
# UNSAFE: date_from comes directly from query params
query = f"SELECT ... WHERE created_at >= '{date_from}'"
```

Use parameterized queries instead of string interpolation.

**2. Missing error handling (severity: medium)**
File: `plugins/revenue_analytics/engine.py`, line 112

The `cohort_matrix()` method doesn't handle the case where the
database connection drops mid-query. A 500 error would leak
the raw traceback to the API response.
```

The typed outcome:

```html
<!-- OUTCOME {"status":"fail","stage":"REVIEW","failure_reason":"SQL injection risk in api.py, missing error handling in engine.py","notes":"2 issues found, 1 high severity"} -->
```

**What the Observer decides**: REVIEW failed. The pipeline graph says `("REVIEW", "fail") -> "PATCH"`. The LLM Observer (engaged because `has_failed_stage()=True`) decides to steer:

```python
enqueue_continuation(
    coaching_message="Review found 2 issues: SQL injection in api.py "
    "(high severity) and missing error handling in engine.py. Continue "
    "with /do-patch to address both. The SQL injection fix is critical — "
    "use parameterized queries. After patching, /do-test will re-verify, "
    "then the review will re-run."
)
```

### The REVIEW -> PATCH -> TEST -> REVIEW Cycle

The worker patches both issues:

```python
# api.py -- parameterized query
cursor.execute(
    "SELECT ... WHERE created_at >= %s AND created_at < %s",
    (date_from, date_to)
)
```

```python
# engine.py -- error handling
def cohort_matrix(self, months=12):
    try:
        # ... query logic ...
    except DatabaseError as e:
        logger.error(f"Cohort matrix query failed: {e}")
        raise AnalyticsError("Failed to generate cohort matrix") from e
```

The worker commits, pushes, runs tests (all pass), and the Observer routes through PATCH -> TEST -> REVIEW again. The re-review passes clean:

```html
<!-- OUTCOME {"status":"success","stage":"REVIEW","artifacts":{},"notes":"All issues resolved. Code quality approved.","next_skill":"/do-docs"} -->
```

**What could go wrong (session context loss)**: Between the first review and the patch, suppose the bridge restarts. The `AgentSession` is in Redis, but the `_enqueue_continuation` function needs to preserve all metadata when re-enqueuing the job. This is where the session context preservation fix matters.

The `_enqueue_continuation` fallback ensures that when a continuation is enqueued, all session metadata -- `work_item_slug`, `issue_url`, `pr_url`, `stage_progress`, `classification_type` -- transfers to the new job. Without this, the re-enqueued job would lose its SDLC context and be treated as a fresh conversation. The session's `correlation_id` provides the thread that ties everything together.

The Observer's `_handle_update_session` also re-reads the session from Redis before writing, using deterministic record selection:

```python
all_sessions = list(AgentSession.query.filter(session_id=self.session.session_id))
active = [s for s in all_sessions if s.status in ("running", "active", "pending")]
candidates = active if active else all_sessions
candidates.sort(key=lambda s: s.created_at or 0, reverse=True)
self.session = candidates[0]
```

This avoids clobbering concurrent writes (e.g., the bridge appending a `queued_steering_message` while the Observer is deciding).

Auto-continue count: 8. We are within the `MAX_AUTO_CONTINUES_SDLC = 10` cap.

---

## 8. Documentation

The Observer steers to DOCS. The worker invokes `/do-docs`:

```bash
# Create the feature documentation
cat > docs/features/revenue-analytics.md << 'EOF'
# Revenue Analytics Plugin

## Overview

The revenue analytics plugin provides MRR tracking, churn rate
calculation, and cohort retention analysis for the billing system.

## Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/analytics/mrr` | GET | Monthly recurring revenue trends |
| `/api/analytics/churn` | GET | Churn rate for a given period |
| `/api/analytics/cohorts` | GET | Cohort retention matrix |

## Query Parameters

All endpoints accept:
- `date_from` (ISO 8601) -- Start of analysis period
- `date_to` (ISO 8601) -- End of analysis period
- `granularity` (optional) -- `monthly` (default) or `weekly`

## MRR Calculation

MRR is calculated as the sum of all active subscription values,
decomposed into:
- **New MRR**: First-time subscriptions
- **Expansion**: Upgrades from existing customers
- **Contraction**: Downgrades from existing customers
- **Churn**: Canceled subscriptions

Trial-to-paid conversions are counted as New MRR in the month
of conversion (per product decision, issue #247).

## Cohort Matrix

Cohorts are defined by the month of first paid subscription.
The matrix shows retention rates for each cohort over a
configurable time horizon (default: 12 months). Partial months
are excluded to avoid misleading retention percentages.
EOF
```

The worker commits the docs, pushes, and emits:

```html
<!-- OUTCOME {"status":"success","stage":"DOCS","artifacts":{"doc_path":"docs/features/revenue-analytics.md"},"notes":"Feature documentation created","next_skill":"/do-merge"} -->
```

**What could go wrong (stage detector missing a completion)**: Suppose the worker's output doesn't contain any of the regex completion patterns for DOCS -- maybe it phrased things as "wrote the feature guide" instead of "documentation created." The stage detector would miss it. But the typed outcome cross-check catches it:

```python
if outcome.status == "success" and outcome.stage and outcome.stage not in regex_stages:
    logger.info(
        f"[stage-detector] Stage {outcome.stage} merged from typed outcome "
        f"(regex missed). Regex detected: {regex_stages or 'none'}"
    )
    transitions.append({
        "stage": outcome.stage,
        "status": "completed",
        "reason": f"Typed outcome: {outcome.stage} succeeded (regex missed)",
    })
```

The DOCS stage is marked completed even though regex missed it. This is why the typed outcome contract exists -- it provides a structured fallback that doesn't depend on the worker using magic phrases.

The Observer steers to MERGE. Auto-continue count: 9.

---

## 9. Merge

### The Merge Guard

The worker reaches the MERGE stage. This is where the system's safety invariants are most visible. MERGE is a human-gated stage -- the agent cannot merge without explicit authorization.

But suppose the worker tries to be helpful and just runs the merge command directly:

```bash
gh pr merge 251 --squash
```

The `PreToolUse` hook fires. The merge guard validator at `.claude/hooks/validators/validate_merge_guard.py` intercepts the Bash tool call:

```python
if _MERGE_CMD_RE.search(command):
    if _HELP_FLAG_RE.search(command):
        return  # Allow --help queries
    print(json.dumps({
        "decision": "block",
        "reason": "PR merge requires human authorization. "
                  "Use /do-merge to check prerequisites and "
                  "request merge approval."
    }))
```

The tool call is blocked. The worker sees the block reason and adjusts -- it invokes `/do-merge` instead, which is the proper channel.

### The /do-merge Skill

`/do-merge` doesn't actually merge. It checks prerequisites and presents the merge to the human for authorization. It verifies:

1. REVIEW stage is completed (it is -- passed on re-review)
2. TEST stage is completed (it is -- 9/9 passing)
3. DOCS stage is completed (it is -- feature doc created)
4. CI checks are passing on the PR
5. No merge conflicts

All prerequisites are met. The worker emits:

```html
<!-- OUTCOME {"status":"success","stage":"MERGE","artifacts":{"pr_url":"https://github.com/tomcounsell/billing/pull/251"},"notes":"All prerequisites met. Ready for human merge authorization."} -->
```

**What the Observer decides**: All stages are complete. `has_remaining_stages()` returns `False`. The typed outcome routing delivers:

```python
if outcome.status == "success" and not self.session.has_remaining_stages():
    return {
        "action": "deliver",
        "reason": f"Pipeline complete. {outcome.notes}",
        ...
    }
```

**What the human sees on Telegram**:

```
Revenue Analytics Plugin is ready to merge.

PR #251: Add revenue analytics plugin
- All tests passing (9/9)
- Code review approved (SQL injection fixed, error handling added)
- Documentation created at docs/features/revenue-analytics.md

All pipeline stages complete:
  [x] Issue #247
  [x] Plan
  [x] Build
  [x] Test (2 failures fixed in patch cycle)
  [x] Review (2 issues fixed in review cycle)
  [x] Docs
  [ ] Merge (awaiting your authorization)

Approve merge? Reply 'merge' to proceed.
```

Valor replies:

```
Valor: merge
```

The bridge routes this reply to the session. The worker runs the merge through the proper `/do-merge` channel -- which now has human authorization. The PR is squash-merged. The worktree is cleaned up. The session status moves to `complete`.

```
Valor: [thumbs-up reaction]
```

The session is marked as done.

---

## 10. What the Observer Saw

Here's the complete decision log for this feature, from spark to merge. Each line is a routing decision the Observer made:

```
14:14:32 [obs-a7c3] steer  deterministic-sdlc-guard: ISSUE pending
14:14:58 [obs-a7c3] steer  typed-outcome: ISSUE success
14:16:42 [obs-a7c3] deliver plan has open questions (LLM judgment)
14:47:01 [obs-a7c3] steer  deterministic-sdlc-guard: BUILD pending
15:08:33 [obs-a7c3] steer  stop_reason: rate_limited (backoff)
15:12:44 [obs-a7c3] steer  deterministic-sdlc-guard: BUILD pending (resumed)
15:28:16 [obs-a7c3] steer  typed-outcome: BUILD success
15:29:02 [obs-a7c3] steer  typed-outcome: TEST fail (LLM: fixable, steer to PATCH)
15:30:45 [obs-a7c3] steer  deterministic-sdlc-guard: TEST pending (after PATCH)
15:31:18 [obs-a7c3] steer  typed-outcome: TEST success
15:33:44 [obs-a7c3] steer  typed-outcome: REVIEW fail (LLM: steer to PATCH)
15:35:22 [obs-a7c3] steer  deterministic-sdlc-guard: TEST pending (after PATCH)
15:35:58 [obs-a7c3] steer  typed-outcome: TEST success
15:37:30 [obs-a7c3] steer  typed-outcome: REVIEW success
15:38:45 [obs-a7c3] steer  typed-outcome: DOCS success
15:39:12 [obs-a7c3] deliver typed-outcome: MERGE success, pipeline complete
```

Sixteen decisions. Two were delivered to the human (plan questions, final merge readiness). Fourteen were silent steers. The human typed three messages total across 90 minutes: the original request, answers to plan questions, and "merge" at the end.

### The Interplay Between Deterministic Guards and LLM Judgment

The decision log reveals the system's layered architecture:

1. **Typed outcomes** (fastest): When the worker emits a structured `<!-- OUTCOME -->` block, routing is deterministic. No LLM call. This handled 10 of 16 decisions.

2. **Stop reason routing** (fast): When the SDK reports `rate_limited`, the Observer routes deterministically with a backoff. This handled 1 decision.

3. **Deterministic SDLC guard** (fast): When stages remain and no safety conditions are triggered, the guard force-steers to the next stage. This handled 3 decisions.

4. **LLM Observer** (slow but nuanced): For edge cases -- open questions in plans, deciding whether a test failure is fixable or a blocker -- the Sonnet-powered Observer makes a judgment call. This handled 2 decisions.

5. **Fallback to deliver** (safety net): If the Observer LLM itself fails (API error, timeout, malformed response), the system defaults to delivering output to the human. Better to surface confusion than to silently drop it:

```python
except Exception as e:
    logger.error(f"{self._log_prefix} Observer failed: {e}", exc_info=True)
    return {
        "action": "deliver",
        "reason": f"Observer error: {e}",
        ...
    }
```

This never fired in our story. But it fires in production about once a week, usually due to Anthropic API rate limits. The human sees the raw worker output with a note that the Observer had trouble, and work resumes normally on the next message.

---

## Epilogue: The System's Memory

After the merge, the session snapshot is saved to `logs/sessions/`. The pipeline state at `data/pipeline/revenue-analytics-plugin/state.json` shows the complete audit trail:

```json
{
  "slug": "revenue-analytics-plugin",
  "branch": "session/revenue-analytics-plugin",
  "stage": "pr",
  "completed_stages": ["plan", "branch", "implement", "test", "patch", "review", "document", "commit"],
  "patch_iterations": 2,
  "started_at": "2026-03-16T14:14:00Z",
  "updated_at": "2026-03-16T15:39:00Z"
}
```

Two patch iterations. One for test failures, one for review feedback. Both resolved autonomously. The human was involved exactly when they needed to be: to make a product decision (trial conversion accounting), to acknowledge a budget limit, and to authorize the merge. Everything else was handled by the pipeline.

The worktree at `.worktrees/revenue-analytics-plugin/` is cleaned up by the post-merge script. The branch `session/revenue-analytics-plugin` is deleted. Issue #247 is closed by the PR merge (via `Closes #247` in the PR body).

The feature ships.
