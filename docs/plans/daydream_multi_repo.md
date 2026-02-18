---
status: Planning
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
- Log review, task cleanup, and issue creation are scoped per-project
- Each project's GitHub issue is created in its own repo (`--repo org/repo`)
- The `ai`-specific steps (file cleanup, Sentry, docs check) run once, as before
- No cross-machine deduplication code needed — each machine's local project set is its natural scope

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
- **Per-project log review** (step 2): For each project, look for `*.log` files in `{working_directory}/logs/`. Report errors per-project.
- **Per-project task cleanup** (step 4): Run `gh issue list --repo {org}/{repo}` for each project's GitHub repo.
- **Project-aware issue creation** (step 10): `create_daydream_issue()` gains an optional `repo` parameter, passed as `--repo` to all `gh` calls.
- **LLM reflection** (step 7): Collects findings across all projects and reflects on cross-project patterns.
- **Single-repo steps stay single-repo**: Steps 1 (file cleanup), 3 (Sentry), 5 (docs check) remain `ai`-repo scoped.

### Flow

`DaydreamRunner.__init__` → `load_local_projects()` → filter by `working_directory` existence

Steps 2, 4: loop per-project → collect per-project findings into `self.state.findings["{project_slug}:{category}"]`

Step 10: loop per-project → `create_daydream_issue(findings, date, repo="{org}/{repo}")`

### Technical Approach

**`scripts/daydream.py`**

Add a `load_local_projects()` helper at module level:
```python
def load_local_projects() -> list[dict]:
    config_path = PROJECT_ROOT / "config" / "projects.json"
    data = json.loads(config_path.read_text())
    projects = []
    for slug, cfg in data.get("projects", {}).items():
        wd = Path(cfg.get("working_directory", ""))
        if wd.exists():
            projects.append({"slug": slug, **cfg})
    return projects
```

In `DaydreamRunner.__init__`, call `self.projects = load_local_projects()`.

In `step_review_logs`: loop over `self.projects`, check `{wd}/logs/*.log` for each.

In `step_clean_tasks`: loop over `self.projects`, run `gh issue list --repo {org}/{repo}` for each.

In `step_create_github_issue`: loop over `self.projects`, call `create_daydream_issue(project_findings, date, repo=f"{org}/{repo}")`.

**`scripts/daydream_report.py`**

`issue_exists_for_date(date, repo=None)` — when `repo` is provided, add `["--repo", repo]` to the `gh issue list` call.

`create_daydream_issue(findings, date, repo=None)` — when `repo` is provided, add `["--repo", repo]` to both calls.

**Findings key strategy**: Use `"{project_slug}:{category}"` as the key in `state.findings` to namespace per-project findings while keeping existing structure. The report generation groups by project first.

## Rabbit Holes

- **Session analysis project tagging**: Sessions in `logs/sessions/` don't currently carry a project tag. Implementing project-aware session analysis would require reading session metadata to determine which project a session belongs to. This is a separate enhancement — for this issue, treat all sessions as `ai`-project sessions (current behavior) and note the improvement opportunity.
- **Cross-machine deduplication locks**: Adding a distributed lock or S3-based mutex to prevent the race condition is out of scope. The race condition is resolved structurally by scoping each project's issue to its own repo (different repos = no collision).
- **Per-project lessons_learned**: Tagging `data/lessons_learned.jsonl` entries by project slug is a nice enhancement but out of scope here — keep it a single global file.
- **Machine name in issue titles**: Tempting, but unnecessary once each project posts to its own repo.

## Risks

### Risk 1: `ai` repo duplication persists
**Impact:** All machines run daydream from `/Users/valorengels/src/ai` — it's where the script lives. So `valor` will appear in every machine's local projects list. The race condition for the `ai`/`valor` repo issue is not fully eliminated.
**Mitigation:** The `issue_exists_for_date()` check catches all-but-simultaneous runs. For true simultaneous runs: document as known limitation; the practical frequency (4 machines all within ~30 seconds) is low and the noise is tolerable until a per-project machine assignment config exists.

### Risk 2: Projects without GitHub config
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
- [ ] Projects without `github` config or `logs/` dir are skipped gracefully
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
- Update `step_create_github_issue` to call `create_daydream_issue` per project
- Add optional `repo` parameter to `issue_exists_for_date()` and `create_daydream_issue()` in `scripts/daydream_report.py`

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

---

## Open Questions

1. **Issue title format per project**: Should the GitHub issue title be `Daydream Report - {date}` (same as now, but in the project's own repo) or include the project name, e.g., `Daydream Report - {date} - Popoto`? The latter is more readable when viewing issues across repos. Leaning toward including project name.

2. **`valor`/`ai` duplication**: All machines will include `valor` in their local projects (daydream lives there). Should we accept the occasional duplicate for the `ai` repo, or add a `primary_machine` flag to `projects.json` to designate which machine creates the `ai` report? The current dedup check handles non-simultaneous runs.
