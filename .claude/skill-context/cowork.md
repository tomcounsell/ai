# cowork context — this repo (ai)

> **Second migration exercised at the code level; deployment still pending** — see the banner
> in `.claude/skills-global/cowork/SKILL.md`. This addendum originally reflected the single
> live pilot migration (`sentry-issue-triage`); issue #2068 has now exercised the pattern a
> second time on `pr-review-audit`, proving the guard-shim generalizes (`GH_REPO` project
> synthesis, fixed lookback window, per-PR title dedup — see
> `docs/features/cowork-tasks.md`). `pr-review-audit`'s CMA deployment and live filing
> verification have not happened yet (`docs/infra/cowork-pr-review-audit.md`); the local
> reflection is still enabled and unchanged.

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

Second worked example (in progress): `pr-review-audit` (merged-PR review findings →
`gh issue create` per PR) also fits the second bucket, but required an audit-specific
Redis-bypass shim rather than a copy of the sentry guard — see
`docs/infra/cowork-pr-review-audit.md`. Its CMA deployment has not happened yet; the
local reflection remains the only thing running it today. Most other candidates do
**not** fit the second bucket — see the Candidate Re-Triage table in
`docs/features/cowork-tasks.md` for the recorded dispositions and why.

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
  SDLC pipeline (see `.claude/skills/sdlc/SKILL.md`) picks it up from there; the routine's
  job stops at "issue filed," it does not drive the pipeline.

## Reference implementation

- `docs/infra/cowork-sentry-triage.md` — the committed routine-spec descriptor for the
  pilot (prompt, cadence, trigger, connectors, Sentry auth mechanism, notification seam). LIVE.
- `docs/infra/cowork-pr-review-audit.md` — the routine-spec descriptor for the second
  migration (prompt, cadence, guards, Redis-bypass note). Code-complete, not yet deployed.
- `docs/features/cowork-tasks.md` — the reusable pattern doc, including this decision
  rule in its general (non-repo-specific) form and the Candidate Re-Triage table.
- `.claude/skills/sentry/SKILL.md` — the existing on-demand `/sentry` recipe the pilot
  routine's prompt delegates to (`/sentry --apply`); do not re-implement the A-E rubric
  in a routine prompt.
- `python -m reflections.audits.pr_review_audit --apply` — the CLI recipe entrypoint the
  `pr-review-audit` routine's prompt will delegate to once deployed.
