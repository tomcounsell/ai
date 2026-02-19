# Daydream Reactivation

The daydream system is an autonomous maintenance and self-reflection process that runs daily at 6 AM Pacific. It performs cleanup, log analysis, session quality assessment, LLM-powered reflection, and institutional memory management, then publishes per-project digests to GitHub Issues and Telegram.

## 11-Step Process

The daydream runner (`scripts/daydream.py`) executes these steps sequentially. Each step is independently failable -- a failure in one step does not block subsequent steps.

| Step | Name | Description |
|------|------|-------------|
| 1 | File Cleanup | Removes temp files, old logs, build artifacts |
| 2 | Log Review | Extracts structured errors from `logs/bridge.log` |
| 3 | Sentry Check | Queries Sentry for unresolved issues (skips gracefully if MCP unavailable) |
| 4 | Task Cleanup | Lists open bug issues via `gh issue list --label bug` |
| 5 | Audit Documentation | Weekly LLM-powered accuracy audit of `docs/`; KEEP / UPDATE / DELETE verdicts (see [Documentation Audit](documentation-audit.md)) |
| 6 | Report Generation | Writes local report to `logs/daydream/report_YYYY-MM-DD.md` |
| 7 | Session Analysis | Reads session logs, computes thrash ratio, detects corrections |
| 8 | LLM Reflection | Uses Claude Haiku via Anthropic SDK to categorize mistakes |
| 9 | Memory Consolidation | Appends lessons to `data/lessons_learned.jsonl`, deduplicates, prunes >90 days |
| 10 | GitHub Issue Creation | Posts daily digest issue via `gh` CLI (skips if no findings) |
| 11 | Telegram Post | Posts brief summary to project's Telegram group (per-project, added 2026-02-18) |

## Session Analysis (Step 7)

Session analysis reads chat logs from `logs/sessions/*/chat.json` and `tool_use.jsonl`, filtered to yesterday's sessions (capped at 10 most interesting).

### Thrash Ratio

Measures how much back-and-forth occurred relative to productive output:

```
thrash_ratio = correction_count / total_message_count
```

Sessions with a high thrash ratio are flagged for LLM reflection.

### Correction Detection

Scans session transcripts for patterns indicating the human corrected the agent:
- Explicit corrections ("no, I meant...", "that's wrong")
- Redirections ("actually", "instead")
- Repeated instructions

## LLM Reflection (Step 8)

Flagged sessions are sent to Claude Haiku (via `anthropic` Python SDK) for categorization into:

| Category | Description |
|----------|-------------|
| `misunderstanding` | Misinterpreted the user's intent |
| `code_bug` | Introduced a bug in generated code |
| `poor_planning` | Inadequate planning before implementation |
| `tool_misuse` | Used the wrong tool or used a tool incorrectly |
| `scope_creep` | Built more than was asked for |
| `integration_failure` | Failed to integrate with existing systems |

Each reflection includes category, summary, pattern, and prevention rule.

## Institutional Memory (Step 9)

Lessons are stored in `data/lessons_learned.jsonl`:

```json
{"date": "2026-02-17", "category": "misunderstanding", "summary": "Built OAuth when user asked for simple API key auth", "pattern": "minimizing qualifier + complex domain", "prevention": "When 'simple/basic/quick' precedes a complex domain, clarify scope first", "source_session": "tg_valor_-123_456", "validated": 0}
```

**Deduplication**: Checks existing entries for pattern similarity before appending.

**Pruning**: Entries older than 90 days are removed during each run.

## GitHub Issue Digest (Step 10)

When findings exist, a daily digest issue is created via `gh issue create --label daydream`. Format:

```
Daydream Report - 2026-02-17
```

Silent days (no findings) skip issue creation. Local report is always saved to `logs/daydream/`.

## Scheduling

| File | Purpose |
|------|---------|
| `com.valor.daydream.plist` | launchd job definition, 6 AM Pacific daily |
| `scripts/install_daydream.sh` | Installs plist and loads the service |

Install: `./scripts/install_daydream.sh`

## Key Files

| File | Purpose |
|------|---------|
| `scripts/daydream.py` | Main 10-step runner |
| `scripts/daydream_report.py` | GitHub issue creation module |
| `scripts/install_daydream.sh` | launchd installation |
| `com.valor.daydream.plist` | Schedule definition |
| `data/lessons_learned.jsonl` | Institutional memory store |
| `logs/daydream/` | Local report output directory |

## Troubleshooting

| Command | Purpose |
|---------|---------|
| `python scripts/daydream.py` | Run manually |
| `tail -f logs/daydream.log` | Stream logs |
| `cat data/lessons_learned.jsonl` | View institutional memory |
| `launchctl list \| grep daydream` | Check launchd status |
| `./scripts/install_daydream.sh` | Reinstall after plist changes |

## Multi-Repo Support

As of 2026-02-18, daydream supports analyzing multiple repositories per machine. It reads `config/projects.json`, filters to repos whose `working_directory` exists locally, and runs per-project analysis (log review, task cleanup, GitHub issue creation, Telegram posting) for each one. AI-only steps run once as before. See `docs/features/daydream-multi-repo.md` for the full architecture and configuration reference.
