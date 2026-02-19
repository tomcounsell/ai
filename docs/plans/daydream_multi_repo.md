---
status: Shipped
type: feature
appetite: Small
owner: Valor Engels
created: 2026-02-18
tracking: https://github.com/tomcounsell/ai/issues/137
---

# Daydream: Multi-Repo Support Per Machine Config

## Problem

This morning three machines each created an identical "Daydream Report" GitHub issue for the `ai` repo. The duplication check (`issue_exists_for_date()`) exists but is defeated by the race condition: all machines fire at the same time (6 AM Pacific via launchd), all see no existing issue, and all create one.

The root cause is structural: `scripts/daydream.py` is hardcoded to analyze only the `ai` repo regardless of how many projects are configured in `config/projects.json`. The log paths (`LOGS_DIR`), `gh issue list` calls, and GitHub issue creation all assume the `ai` repo.

**Current behavior:**
- Daydream runs on every machine at 6 AM Pacific
- All analysis is scoped to the `ai` repo log files and GitHub repo
- All machines produce the same report and race to create the same GitHub issue
- Other configured projects (popoto, django-template, psyoptimal, etc.) are never analyzed

**Desired outcome:**
- Daydream iterates over each project in `config/projects.json` whose `working_directory` exists on the current machine
- For each project, daydream `chdir`s into that project's `working_directory` and runs analysis from there — logs, `gh` calls, and issue creation are all native to that repo's context
- Each project's GitHub issue (`Daydream Report - {date}`) is created in its own repo — no project name in the title needed, the repo is the context
- The daydream run ends with a short summary posted to the project's Telegram chat group (from `config/projects.json` → `telegram.groups[0]`)
- The `ai`-specific steps (file cleanup, Sentry, docs check) run once, as before
- No cross-machine deduplication needed — machine-to-repo assignment is ops-managed; each machine's active repos map to their Telegram chat groups

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 0

The scope is focused: load config, loop over projects in 3 steps, thread `repo` into `gh` calls. No new infrastructure, no new data models beyond what's already in `DaydreamState`.

## Prerequisites

No prerequisites — all dependencies (`config/projects.json`, `gh` CLI, `scripts/daydream.py`) are already in place.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `gh` CLI authenticated | `gh auth status` | Create per-project GitHub issues |
| `config/projects.json` exists | `python -c "import json; json.load(open('config/projects.json'))"` | Project list source |

Run all checks: `python scripts/check_prerequisites.py docs/plans/daydream_multi_repo.md`

## Solution

### Key Elements

- **Project loader**: Reads `config/projects.json`, filters to projects whose `working_directory` exists on this machine. Called once at `DaydreamRunner.__init__`.
- **Per-project runner**: For each local project, a `ProjectDaydreamRunner` (or equivalent loop) `os.chdir()`s into the project's `working_directory`, then runs the per-project steps using paths relative to that root.
- **Per-project log review** (step 2): Runs from the project's root — `logs/*.log` relative to `working_directory`.
- **Per-project task cleanup** (step 4): Runs `gh issue list` from inside the project's repo dir — `gh` auto-detects the remote.
- **Per-project issue creation** (step 10): Runs `gh issue create` from inside the project's repo dir — no `--repo` flag needed; `gh` resolves it from the git remote.
- **Per-project Telegram post** (new step 11): After creating the GitHub issue, posts a brief summary to `telegram.groups[0]` for that project using Telethon. The bridge session file is reused — no separate auth needed.
- **LLM reflection** (step 7/8): Collects findings across all projects and reflects on cross-project patterns.
- **Single-repo steps stay single-repo**: Steps 1 (file cleanup), 3 (Sentry), 5 (docs check) run once from the `ai` repo root, as before.

### Flow

```
main()
  → load_local_projects()          # filter config to locally-present repos
  → steps 1, 3, 5                  # run once, ai-repo context (no chdir)
  → for project in local_projects:
      os.chdir(project.working_directory)
      → step_review_logs()         # logs/*.log relative to project root
      → step_clean_tasks()         # gh issue list (gh auto-detects remote)
      → step_session_analysis()    # sessions in project's logs/sessions/
      → step_create_github_issue() # gh issue create (gh auto-detects remote)
      → step_post_to_telegram()    # post summary to telegram.groups[0]
  → os.chdir(AI_ROOT)              # restore
  → step_llm_reflection()          # cross-project patterns
  → step_memory_consolidation()
  → step_produce_report()
```

### Technical Approach

**`scripts/daydream.py`**

Add `AI_ROOT = PROJECT_ROOT` constant at the top to preserve the `ai` repo path before any `chdir`.

Add a `load_local_projects()` helper:
```python
def load_local_projects() -> list[dict]:
    config_path = AI_ROOT / "config" / "projects.json"
    data = json.loads(config_path.read_text())
    projects = []
    for slug, cfg in data.get("projects", {}).items():
        wd = Path(cfg.get("working_directory", ""))
        if wd.exists():
            projects.append({"slug": slug, **cfg})
    return projects
```

Restructure `DaydreamRunner.run()` as a loop:
1. Run ai-only steps (1, 3, 5) from `AI_ROOT`
2. For each project: `os.chdir(project["working_directory"])`, run steps 2, 4, 6 (session analysis), 10
3. Restore `os.chdir(AI_ROOT)`, run steps 7, 8, 9 (reflection, memory, report)

**`scripts/daydream_report.py`**

No changes needed to function signatures — `gh` CLI auto-detects the repo from the current directory's git remote. `issue_exists_for_date()` and `create_daydream_issue()` work as-is when called from inside the project's `working_directory`.

**Step 11 — Telegram post**: After `step_create_github_issue()` for each project, call `step_post_to_telegram(project)`. This posts a short summary (issue count, key findings, link to GitHub issue) to `project["telegram"]["groups"][0]`. Use Telethon with the existing bridge session file (`data/valor.session`). Keep the message short — one paragraph, no raw data dumps.

If a project has no `telegram.groups` configured, skip silently.

**State file location**: Keep at `AI_ROOT / "logs/daydream/state.json"` — use `AI_ROOT` explicitly so the state persists across `chdir` calls.

**Findings namespacing**: Use `"{project_slug}:{category}"` as the key in `state.findings` to keep per-project findings distinct in the consolidated report.

## Rabbit Holes

- **Session analysis project tagging**: Sessions in `logs/sessions/` don't currently carry a project tag. Implementing project-aware session analysis would require reading session metadata to determine which project a session belongs to. This is a separate enhancement — for this issue, treat all sessions as `ai`-project sessions (current behavior) and note the improvement opportunity.
- **Cross-machine deduplication locks**: Adding a distributed lock or S3-based mutex to prevent the race condition is out of scope. The race condition is resolved structurally by scoping each project's issue to its own repo (different repos = no collision).
- **Per-project lessons_learned**: Tagging `data/lessons_learned.jsonl` entries by project slug is a nice enhancement but out of scope here — keep it a single global file.
- **Machine name in issue titles**: Tempting, but unnecessary once each project posts to its own repo.

## Risks

### Risk 1: Projects without GitHub config
**Impact:** Some projects in `config/projects.json` may lack a `github` key. Daydream would crash or silently skip.
**Mitigation:** In `step_create_github_issue`, skip projects without a `github.org` + `github.repo`. Log a warning. Same check in `step_clean_tasks` for `gh issue list`.

### Risk 3: Projects without a `logs/` directory
**Impact:** Most non-`ai` repos won't have a `logs/` directory. Log review step would find nothing.
**Mitigation:** The step already guards `if not log_file.is_file()`. Add an explicit check: if `{wd}/logs/` doesn't exist, note "no logs directory found" and continue.

## No-Gos (Out of Scope)

- Machine-specific project assignment config (separate issue)
- Project-tagged sessions (separate enhancement)
- Per-project `lessons_learned.jsonl` files (separate enhancement)
- Cross-machine deduplication locks (separate issue)
- Parallel per-project execution (keep sequential for simplicity and legibility)

## Update System

The `config/projects.json` file is already synced across machines as part of the repo. No update script changes needed — running `/update` on any machine will pull the latest `daydream.py` changes.

No new dependencies or config files required.

## Agent Integration

No agent integration required — daydream runs as a standalone script via launchd, not through the Telegram bridge or MCP servers.

## Documentation

- [ ] Update `docs/features/daydream-reactivation.md` to describe multi-repo behavior (project loading, per-project steps)
- [ ] Create `docs/features/daydream-multi-repo.md` documenting the multi-repo extension
- [ ] Add entry to `docs/features/README.md` index table for the new doc

## Success Criteria

- [ ] `load_local_projects()` returns only projects with existing `working_directory`
- [ ] Step 2 (log review) analyzes logs from each local project's `logs/` dir
- [ ] Step 4 (task cleanup) runs `gh issue list --repo {org}/{repo}` per project
- [ ] Step 10 (GitHub issue) creates issue in each project's own repo (`--repo` flag)
- [ ] `ai`-specific steps (1, 3, 5) run once, unchanged
- [ ] Each project run ends with a Telegram post to `telegram.groups[0]`
- [ ] Projects without `telegram.groups` or `github` config are skipped gracefully
- [ ] `pytest tests/` passes
- [ ] Documentation updated

## Team Orchestration

### Team Members

- **Builder (daydream-multi-repo)**
  - Name: daydream-builder
  - Role: Modify `scripts/daydream.py` and `scripts/daydream_report.py` for multi-repo support
  - Agent Type: builder
  - Resume: true

- **Validator (daydream-multi-repo)**
  - Name: daydream-validator
  - Role: Verify multi-repo iteration, per-project scoping, graceful fallbacks
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: daydream-documentarian
  - Role: Update daydream feature docs
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Implement multi-repo support in daydream
- **Task ID**: build-daydream-multi-repo
- **Depends On**: none
- **Assigned To**: daydream-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `load_local_projects()` to `scripts/daydream.py`
- Wire `self.projects` into `DaydreamRunner.__init__`
- Update `step_review_logs` to iterate over local projects' log dirs
- Update `step_clean_tasks` to run `gh issue list --repo` per project
- Update `step_create_github_issue` to call `create_daydream_issue` per project (from inside project's `working_directory`)
- Add `step_post_to_telegram(project)` — post summary to `project["telegram"]["groups"][0]` via Telethon using `data/valor.session`
- No changes needed to `scripts/daydream_report.py` — `gh` auto-detects repo from cwd

### 2. Validate implementation
- **Task ID**: validate-daydream-multi-repo
- **Depends On**: build-daydream-multi-repo
- **Assigned To**: daydream-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `load_local_projects()` filters by `working_directory` existence
- Verify `gh` calls include `--repo` flag
- Verify projects without `github` config are skipped gracefully
- Verify `ai`-specific steps (1, 3, 5) are untouched
- Run `python -c "from scripts.daydream import load_local_projects; print(load_local_projects())"`
- Run `pytest tests/` and confirm passing

### 3. Update documentation
- **Task ID**: document-daydream-multi-repo
- **Depends On**: validate-daydream-multi-repo
- **Assigned To**: daydream-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/daydream-multi-repo.md`
- Update `docs/features/daydream-reactivation.md` — note multi-repo behavior
- Add entry to `docs/features/README.md`

### 4. Final validation
- **Task ID**: validate-all
- **Depends On**: document-daydream-multi-repo
- **Assigned To**: daydream-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm all success criteria are met
- Verify docs exist and are accurate
- Run `black . && ruff check .`

## Validation Commands

- `python -c "from scripts.daydream import load_local_projects; import json; print(json.dumps(load_local_projects(), indent=2))"` — verify project loading
- `python -c "from scripts.daydream_report import issue_exists_for_date; print(issue_exists_for_date('2026-02-18', repo='tomcounsell/ai'))"` — verify repo param works
- `pytest tests/` — full test suite
- `black . && ruff check .` — linting

