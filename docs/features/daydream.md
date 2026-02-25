# Daydream: Autonomous Maintenance System

The daydream system is an autonomous daily maintenance and self-reflection process. It runs every morning at 6 AM Pacific via macOS launchd, performing cleanup, analysis, reflection, reporting, and institutional memory management through 14 sequential steps. All persistence is Redis-backed via Popoto models.

## How It Works

The runner (`scripts/daydream.py`) loads state from Redis, executes each step in order, and checkpoints after every step. If interrupted, the next run resumes from where it left off. Each step is independently failable — a crash in one step does not block the rest.

### 14-Step Pipeline

| Step | Name | Description | Scope | Failure Mode |
|------|------|-------------|-------|--------------|
| 1 | Clean Up Legacy Code | Scans for TODO comments, deprecated typing imports | AI repo only | Non-blocking |
| 2 | Review Logs | Extracts structured errors from log files and Redis BridgeEvent records | Per-project | Non-blocking, skips if `logs/bridge.log` missing |
| 3 | Check Error Logs (Sentry) | Queries Sentry for unresolved issues | AI repo only | Non-blocking, skips if MCP unavailable |
| 4 | Clean Up Task Management | Lists open bug issues via `gh issue list` per project | Per-project | Non-blocking, requires `gh` auth |
| 5 | Audit Documentation | Weekly LLM-powered accuracy audit of `docs/` (see [Documentation Audit](documentation-audit.md)) | AI repo only | Non-blocking, requires `ANTHROPIC_API_KEY` |
| 6 | Session Analysis | Queries Redis SessionLog and BridgeEvent; computes thrash ratio, detects user corrections | AI repo only | Non-blocking |
| 7 | LLM Reflection | Claude Haiku categorizes mistakes into 6 categories | AI repo only | Non-blocking, requires `ANTHROPIC_API_KEY` |
| 8 | Auto-Fix Bugs | For high-confidence `code_bug` reflections, spawns `/do-plan` + `/do-build` to open fix PRs | AI repo only | Non-blocking, requires `claude` CLI |
| 9 | Memory Consolidation | Persists LessonLearned entries to Redis; deduplicates by pattern; prunes entries >90 days | AI repo only | Non-blocking |
| 10 | Report Generation | Writes local markdown report to `logs/daydream/report_YYYY-MM-DD.md` | AI repo only | Non-blocking |
| 11 | GitHub Issue Creation | Posts daily digest issue per project via `gh` CLI; posts summary to Telegram | Per-project | Non-blocking, requires `gh` auth |
| 12 | Skills Audit | Validates all SKILL.md files against template standards (see [Skills Audit](do-skills-audit.md)) | AI repo only | Non-blocking |
| 13 | Redis TTL Cleanup | Prunes expired records across all Redis models | AI repo only | Non-blocking |
| 14 | Redis Data Quality | Surfaces data quality issues: unsummarized links, dead channels, error patterns | AI repo only | Non-blocking |

## State & Persistence

All daydream state lives in Redis via three Popoto models defined in `models/daydream.py`.

### DaydreamRun

One record per calendar date. Acts as the primary state checkpoint for resumability.

| Field | Type | Purpose |
|-------|------|---------|
| `date` | UniqueKeyField | YYYY-MM-DD, one run per day |
| `current_step` | IntField | Next step to execute (1-14) |
| `completed_steps` | ListField | Steps already finished, e.g. `[1, 2, 3]` |
| `daily_report` | ListField | Human-readable log lines per step |
| `findings` | DictField | `{category: [finding_strings]}` |
| `session_analysis` | DictField | Output from session analysis step |
| `reflections` | ListField | LLM reflection outputs |
| `auto_fix_attempts` | ListField | Auto-fix attempt records |
| `step_progress` | DictField | Per-step metrics, e.g. `{"clean_legacy": {"findings": 2}}` |
| `started_at` | SortedField(float) | Unix timestamp, used for cleanup |
| `dry_run` | Field(bool) | True if `--dry-run` mode |

**Checkpoint cycle**: After each step completes (or fails), the runner saves all state to Redis via `DaydreamRun.save_checkpoint()`. This deletes and recreates the record to handle Popoto's KeyField constraints.

**Resume scenario**: If daydream crashes during step 7, the next run loads the DaydreamRun for today, sees `completed_steps = [1,2,3,4,5,6]`, and continues from step 7.

### DaydreamIgnore

Suppresses auto-fix for specific patterns. Each entry has a TTL (default 14 days).

| Field | Type | Purpose |
|-------|------|---------|
| `ignore_id` | AutoKeyField | UUID |
| `pattern` | KeyField | Pattern string to match against reflections |
| `reason` | Field | Why this pattern is ignored |
| `created_at` | SortedField(float) | When the entry was created |
| `expires_at` | SortedField(float) | When it expires (created_at + 14 days) |

**Matching**: Case-insensitive substring match — if either the ignore pattern or the reflection pattern is a substring of the other, it's a match.

**Cleanup**: Expired entries are pruned at the start of each auto-fix step and during Redis TTL cleanup (step 13).

### LessonLearned

Institutional memory from LLM reflection. Queryable by date, category, and recency.

| Field | Type | Purpose |
|-------|------|---------|
| `lesson_id` | AutoKeyField | UUID |
| `date` | KeyField | YYYY-MM-DD when recorded |
| `category` | KeyField | misunderstanding, code_bug, poor_planning, tool_misuse, scope_creep, integration_failure |
| `summary` | Field | Brief description of the lesson |
| `pattern` | Field | Recurring pattern (used for deduplication) |
| `prevention` | Field | Specific rule to prevent recurrence |
| `source_session` | Field | Session ID where this was observed |
| `validated` | IntField | 0=unvalidated, 1+=validated N times |
| `created_at` | SortedField(float) | Unix timestamp, used for 90-day cleanup |

**Deduplication**: Before creating a new entry, `add_lesson()` checks all existing entries for an exact pattern match. Duplicates are silently skipped.

## Session Analysis (Step 6)

Queries Redis for recent sessions and computes quality metrics.

### Data Sources

- **SessionLog** — turn count, tool call count, log file path, session tags
- **BridgeEvent** — error events correlated to sessions

### Thrash Ratio

Measures how much agent effort was wasted:

```
failure_ratio = max(0.0, 1.0 - (turn_count / tool_call_count))
```

Sessions above `THRASH_RATIO_THRESHOLD = 0.5` (50% failure rate) are flagged for LLM reflection. The runner caps analysis at the 20 most interesting sessions (sorted by turn count).

### Correction Detection

Scans session transcripts for patterns indicating the human corrected the agent:

| Pattern | Example |
|---------|---------|
| Explicit correction | "no, I meant...", "that's wrong" |
| Redirection | "actually, ...", "not what I asked" |
| Stop and redirect | "stop... instead" |
| Repeated instruction | "I said..." |

These regex patterns are defined in `CORRECTION_PATTERNS` and applied to user messages extracted from session transcript files.

## LLM Reflection (Step 7)

Flagged sessions are sent to Claude Haiku (`claude-haiku-4-5-20251001`) for categorization:

| Category | Description |
|----------|-------------|
| `misunderstanding` | Misinterpreted the user's intent |
| `code_bug` | Introduced a bug in generated code |
| `poor_planning` | Inadequate planning before implementation |
| `tool_misuse` | Used the wrong tool or used a tool incorrectly |
| `scope_creep` | Built more than was asked for |
| `integration_failure` | Failed to integrate with existing systems |

Each reflection output includes: `category`, `summary`, `pattern`, `prevention`, and `source_session`.

**Skip conditions**: Reflection is skipped if there are no session findings, if `ANTHROPIC_API_KEY` is not set, or if the `anthropic` package is not installed.

## Auto-Fix Bugs (Step 8)

When a reflection is categorized as `code_bug` and meets the confidence threshold, daydream spawns a subprocess to open a fix PR. It never pushes directly to `main` — human review and merge are always required.

### Confidence Criteria

Auto-fix triggers when a reflection meets **2 of 3** criteria:

| Criterion | Condition |
|-----------|----------|
| Category | `category == "code_bug"` |
| Prevention | `prevention` field is non-empty |
| Pattern length | `pattern` field is at least 10 characters |

If fewer than 2 criteria are met, the issue is logged but no action is taken.

### Ignore Log

The ignore log (Redis `DaydreamIgnore` model) suppresses auto-fix for specific patterns for 14 days:

```bash
python scripts/daydream.py --ignore "pattern text here"
python scripts/daydream.py --ignore "pattern text here" --reason "Intentional design, not a bug"
```

### Safety Properties

- **PRs only** — Never pushes to `main`. Every fix requires human review.
- **Dedup** — If an open PR already exists for the pattern, no duplicate is created.
- **Ignore log** — Patterns can be silenced for 14 days with one CLI command.
- **Dry-run** — All logic is testable without external side effects.
- **Kill switch** — `DAYDREAM_AUTO_FIX_ENABLED=false` disables the feature entirely.
- **Timeout** — Each `/do-plan` + `/do-build` subprocess has a 10-minute timeout.

## Multi-Repo Support

Daydream reads `config/projects.json`, filters to repos present on the current machine via `load_local_projects()`, and runs per-project analysis. A machine with only `ai` checked out analyzes only `ai`; a machine with four repos analyzes all four.

**Per-project steps**: 2 (Log Review), 4 (Task Cleanup), 11 (GitHub Issues + Telegram)
**AI-only steps**: Everything else runs once from the AI repo root

### Configuration

Each project entry in `config/projects.json`:

```json
{
  "working_directory": "/Users/valorengels/src/my-project",
  "github": { "org": "myorg", "repo": "my-project" },
  "telegram": { "groups": ["@my_group"] }
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `working_directory` | Yes | Must exist on disk to be included |
| `github.org` / `github.repo` | For issues/tasks | Steps 4 and 11 skip if absent |
| `telegram.groups` | No | Step 11 skips if absent or empty |

### Subprocess Scoping

Per-project subprocess calls (`gh issue list`, `gh issue create`) use `cwd=project["working_directory"]` rather than `os.chdir`. `gh` auto-detects the GitHub repo from the git remote of the given directory.

### Findings Namespacing

`state.findings` keys use `"{slug}:category"` format (e.g., `"ai:log_review"`, `"popoto:tasks"`) to prevent per-project findings from colliding.

### Graceful Fallbacks

- `working_directory` absent from disk — project excluded from `load_local_projects()`
- `github` key missing — steps 4 and 11 log a warning and skip that project
- `telegram.groups` missing or empty — step 11 logs and skips
- `data/valor.session` missing — step 11 skips silently
- `TELEGRAM_API_ID`/`TELEGRAM_API_HASH` not set — step 11 skips silently
- `telethon` not installed — step 11 skips silently

## Findings System

Every step can record findings via `state.add_finding(category, finding_string)`. Findings accumulate in `state.findings` as `{category: [strings]}`.

### Findings Flow

1. **Collected** — Steps append findings throughout execution
2. **Checkpointed** — Saved to Redis DaydreamRun after each step
3. **Reported** — Step 10 writes a local markdown report to `logs/daydream/`
4. **Published** — Step 11 creates per-project GitHub issues and posts to Telegram

## Redis TTL Cleanup (Step 13)

Prunes expired records to keep Redis lean:

| Model | Max Age | Method |
|-------|---------|--------|
| TelegramMessage | 90 days | `cleanup_expired()` |
| Link | 90 days | `cleanup_expired()` |
| Chat | 90 days | `cleanup_expired()` |
| SessionLog | 90 days | `cleanup_expired()` |
| BridgeEvent | 7 days | `cleanup_old()` |
| DaydreamRun | 30 days | `cleanup_expired()` |
| DaydreamIgnore | Per-entry TTL | `cleanup_expired()` |
| LessonLearned | 90 days | `cleanup_expired()` |

## Redis Data Quality (Step 14)

| Check | What It Finds |
|-------|---------------|
| Unsummarized links | Links shared in the last 7 days with no `ai_summary` |
| Dead channels | Chats with no activity in 30+ days |
| Error patterns | Common error keywords recurring across recent session transcripts |
| Message volume | Messages per chat in the last 7 days |

## Operations

### Scheduling

| Component | Detail |
|-----------|--------|
| Plist | `com.valor.daydream.plist` |
| Schedule | Daily at 6:00 AM Pacific |
| Location | `~/Library/LaunchAgents/com.valor.daydream.plist` |
| Stdout | `logs/daydream.log` |
| Stderr | `logs/daydream_error.log` |
| Environment | Sources `.env` before execution (all API keys available) |

Install: `./scripts/install_daydream.sh`

Reload after changes:
```bash
launchctl unload ~/Library/LaunchAgents/com.valor.daydream.plist
launchctl load ~/Library/LaunchAgents/com.valor.daydream.plist
```

### Quick Commands

| Command | Description |
|---------|-------------|
| `python scripts/daydream.py` | Run all 14 steps manually |
| `python scripts/daydream.py --dry-run` | Run without side effects (no PRs, no Telegram) |
| `python scripts/daydream.py --ignore "pattern"` | Suppress auto-fix for pattern for 14 days |
| `python scripts/daydream.py --ignore "pattern" --reason "why"` | Suppress with reason |
| `./scripts/install_daydream.sh` | Install/update launchd schedule |
| `tail -f logs/daydream.log` | Stream daydream logs |
| `python -c "from models.daydream import LessonLearned; [print(f'{l.date} [{l.category}] {l.summary}') for l in LessonLearned.get_recent()]"` | View institutional memory |
| `python -c "from models.daydream import DaydreamIgnore; [print(f'{e.pattern} (expires {e.expires_at})') for e in DaydreamIgnore.get_active()]"` | View active ignore entries |
| `launchctl list \| grep daydream` | Check launchd status |

### Output Locations

| Path | Content |
|------|---------|
| `logs/daydream.log` | Runner stdout/stderr |
| `logs/daydream/` | Generated reports (one per run) |
| Redis: LessonLearned model | Institutional memory (pruned to 90 days) |

## Key Files

| File | Purpose |
|------|---------|
| `scripts/daydream.py` | Main 14-step runner |
| `scripts/daydream_report.py` | GitHub issue creation module |
| `models/daydream.py` | Redis models (DaydreamRun, DaydreamIgnore, LessonLearned) |
| `scripts/install_daydream.sh` | launchd installation script |
| `com.valor.daydream.plist` | Schedule definition |
| `config/projects.json` | Multi-repo project registry |
| `logs/daydream/` | Local report output directory |
| `tests/test_daydream.py` | Core daydream tests |
| `tests/test_daydream_scheduling.py` | Scheduling tests |
| `tests/test_daydream_multi_repo.py` | Multi-repo tests |
| `tests/test_daydream_report.py` | Report generation tests |
| `tests/test_daydream_redis.py` | Redis model tests |

## Dependencies

| Dependency | Used By | Required |
|------------|---------|----------|
| Redis (Popoto ORM) | All steps | Yes — all state persistence |
| `ANTHROPIC_API_KEY` | Steps 5, 7 | Conditional — LLM reflection and docs audit |
| `gh` CLI (authenticated) | Steps 4, 8, 11 | Conditional — task cleanup, dedup, issues |
| `claude` CLI | Step 8 | Conditional — auto-fix subprocess |
| `telethon` | Step 11 | Conditional — Telegram notifications |
| `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` | Step 11 | Conditional — Telegram auth |
| `data/valor.session` | Step 11 | Conditional — Telegram session file |
| `DAYDREAM_AUTO_FIX_ENABLED` | Step 8 | Env var, default `true` |
| `config/projects.json` | Multi-repo | Optional — defaults to AI repo only |

## Troubleshooting

| Symptom | Diagnosis | Fix |
|---------|-----------|-----|
| Daydream did not run | `launchctl list \| grep daydream` | `./scripts/install_daydream.sh` |
| No GitHub issue created | No findings, or `gh auth status` failed | Check `tail -20 logs/daydream.log` |
| LLM reflection skipped | `ANTHROPIC_API_KEY` not set | Add to `.env` |
| Telegram post failed | Missing `data/valor.session` | Run `python scripts/telegram_login.py` |
| Auto-fix not triggering | Confidence criteria not met | Check reflection has pattern >=10 chars and non-empty prevention |
| State not resuming | Redis connection issue | Verify Redis is running |
| Step stuck/timing out | Auto-fix subprocess hung | Check for 10-minute timeout; review `logs/daydream.log` |

## See Also

- [Documentation Audit](documentation-audit.md) — step 5 deep dive
- [Skills Audit](do-skills-audit.md) — step 12 deep dive
