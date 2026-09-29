# cowork context — this repo (ai)

This repo already has a **local scheduled-task system** — the reflections framework
(`reflections/`, `config/reflections.yaml`, `agent/reflection_scheduler.py`). A candidate
task is a fit for a Claude Code Routine only when it doesn't need that local machinery.

## Local-reflection-vs-Cowork decision rule (this repo)

Ask: does the task need **live local state** — the local Redis instance, the local
worker process, `~/Desktop/Valor/` vault files, or the local Telegram relay for
notification?

- **Yes, needs local state/relay** → stays a local reflection (`reflections/`,
  registered in `config/reflections.yaml`). Runs on the single machine that owns
  `project_key` for the relevant project (see the Single-Machine Ownership convention
  in the root `CLAUDE.md`).
- **No — it's a pure cloud-API judgment task** (read a cloud API, apply a rubric, write
  to a cloud API, e.g. file a GitHub issue) → a Claude Code Routine candidate. The filed
  issue (or equivalent cloud write) becomes the notification, since a routine cannot
  reach the local Telegram relay.

Worked example: `sentry-issue-triage` (Sentry API → A-E classification → `gh issue
create` for Class C, Sentry PUT for A/B/E auto-actions) fit the second bucket exactly —
see the routine-spec descriptor at `docs/infra/cowork-sentry-triage.md` and the pattern
doc at `docs/features/cowork-tasks.md`.

Second example: `pr-review-audit` (merged-PR review findings → `gh issue create` per PR)
also fits, but needed an audit-specific Redis-bypass shim rather than a copy of the sentry
guard; see `docs/infra/cowork-pr-review-audit.md` for its deployment status. Most other
candidates do **not** fit; see the Candidate Re-Triage table in
`docs/features/cowork-tasks.md` for the recorded dispositions.

## Checking what's currently scheduled locally

```bash
python -m reflections --dry-run
```

Loads the reflection registry, prints status, and exits 0. Use this to confirm a
candidate task is (or isn't) already a local reflection, and to confirm a cutover
actually removed an entry after migrating it to a routine. Note the resolution order:
`REFLECTIONS_YAML` env → `~/Desktop/Valor/reflections.yaml` (vault, the file that
actually fires on the owning machine) → `config/reflections.yaml` (tracked, in-repo
fallback). Editing only the tracked file does **not** stop a local reflection from
firing if the vault copy still has the entry — both copies need the edit on a real
cutover.

## Filing side (routine output in this repo)

- `gh issue create` / `gh issue list --search` — the native GitHub CLI, used both by the
  local `/sentry` on-demand recipe and by the cloud routine (via the GitHub connector or
  the cloned repo's own `gh`) for filing and dedup.
- `sdlc-tool stage-query --issue-number {N}` — once a routine files an issue, the normal
  SDLC pipeline (see `.claude/skills-global/do-sdlc/SKILL.md`) picks it up from there; the routine's
  job stops at "issue filed," it does not drive the pipeline.

## Recipes routine prompts delegate to

- `/sentry --apply` (`.claude/skills/sentry/SKILL.md`) for `sentry-issue-triage`; never
  re-implement the A-E rubric in a routine prompt.
- `python -m reflections.audits.pr_review_audit --apply` for `pr-review-audit`.
