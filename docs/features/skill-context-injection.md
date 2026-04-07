# Skill Context Injection

Pre-resolved SDLC context variables injected as environment variables into Claude Code subprocesses, eliminating the need for skills to guess or derive PR numbers, branch names, and other session metadata at runtime.

## Problem

Skills are static Markdown templates with placeholder variables like `{pr_number}` and `{owner}/{repo}`. When the Observer dispatches a skill, the worker must derive these values from context clues (coaching messages, git state, `gh` CLI calls). This causes:

- **Context loss**: The agent fails to resolve a variable and hallucinates or skips steps (e.g., the PR review branch-checkout bug at d77d2b0d where the reviewer read files from the wrong branch)
- **Cognitive overload**: Large skills (300+ lines) mix mechanical setup with judgment work, causing lost-place errors

## Solution

### Part 1: SDLC Environment Variable Injection

`agent/sdk_client.py` extracts session fields from `AgentSession` (via Redis) and injects them as `SDLC_*` environment variables before spawning the Claude Code subprocess.

| Variable | Source Field | Example |
|----------|-------------|---------|
| `SDLC_PR_NUMBER` | `AgentSession.pr_url` | `220` |
| `SDLC_PR_BRANCH` | `AgentSession.branch_name` | `session/my-feature` |
| `SDLC_SLUG` | `AgentSession.work_item_slug` | `my-feature` |
| `SDLC_PLAN_PATH` | `AgentSession.plan_url` | `docs/plans/my-feature.md` |
| `SDLC_ISSUE_NUMBER` | `AgentSession.issue_url` | `415` |
| `SDLC_REPO` | `ValorAgent.gh_repo` | `tomcounsell/ai` |

**Key behaviors:**
- Variables are only set when the corresponding field is non-None and a valid string (`isinstance(str)` guard)
- Missing fields produce no env var (not empty string, not `"None"`)
- The function wraps all Redis access in try/except, returning an empty dict on failure
- `SDLC_REPO` complements `GH_REPO`, it does not replace it

**Implementation:** `_extract_sdlc_env_vars()` in `agent/sdk_client.py`, called from `_create_options()` after the existing `GH_REPO` injection block.

### Part 2: Observer Coaching Enrichment

`bridge/observer.py` includes a `sdlc_context` dict in the `read_session` tool response. This gives the Observer concrete values to include in coaching messages, providing redundancy — variables are available via both env vars and coaching message text.

**Implementation:** `_build_sdlc_context()` in `bridge/observer.py`, called from `_handle_read_session()`.

The Observer system prompt instructs it to append resolved variables to coaching messages:
```
Context: SDLC_PR_NUMBER=220, SDLC_SLUG=my-feature, SDLC_PR_BRANCH=session/my-feature
```

### Part 3: Sub-Skill Decomposition

`/do-pr-review` (383 lines) was decomposed into four focused sub-skills, each with a single responsibility:

| Sub-Skill | Type | Responsibility |
|-----------|------|----------------|
| `checkout.md` | Mechanical | Clean git state, checkout PR branch |
| `code-review.md` | Judgment | Read files, analyze diff, classify findings |
| `screenshot.md` | Mechanical | Start app, capture UI screenshots |
| `post-review.md` | Mechanical | Format findings, post review to GitHub |

Sub-skills are guidance documents in `.claude/skills/do-pr-review/sub-skills/`. The parent `SKILL.md` orchestrates them and passes context. Each sub-skill references `$SDLC_PR_NUMBER` instead of `{pr_number}`, with fallback instructions for when env vars are absent.

## Data Flow

```
AgentSession (Redis)
  ├─ pr_url ──────────► _extract_sdlc_env_vars() ──► SDLC_PR_NUMBER env var
  ├─ branch_name ─────► _extract_sdlc_env_vars() ──► SDLC_PR_BRANCH env var
  ├─ work_item_slug ──► _extract_sdlc_env_vars() ──► SDLC_SLUG env var
  ├─ plan_url ────────► _extract_sdlc_env_vars() ──► SDLC_PLAN_PATH env var
  └─ issue_url ───────► _extract_sdlc_env_vars() ──► SDLC_ISSUE_NUMBER env var

Observer (read_session)
  └─ _build_sdlc_context() ──► sdlc_context dict ──► coaching message text
```

## Backward Compatibility

- Skills still work when `SDLC_*` env vars are absent — fallback to manual resolution via `gh` CLI and git state
- The `SKILL.md` uses bash parameter expansion with fallback: `PR_NUMBER="${SDLC_PR_NUMBER:-$PR_NUMBER}"`
- Existing `GH_REPO` env var is unchanged; `SDLC_REPO` is additive

## Files

| File | Role |
|------|------|
| `agent/sdk_client.py` | `_extract_sdlc_env_vars()` — env var injection |
| `bridge/observer.py` | `_build_sdlc_context()` — coaching message enrichment |
| `.claude/skills/do-pr-review/SKILL.md` | Updated to use `$SDLC_*` with fallback |
| `.claude/skills/do-pr-review/sub-skills/` | 4 focused sub-skill files + README |
| `tests/unit/test_sdlc_env_vars.py` | 10 unit tests |

## Tracking

- Issue: #420
- PR: #428
- Plan: `docs/plans/skill_context_injection.md`
