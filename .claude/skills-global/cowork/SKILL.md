---
name: cowork
description: "Create, review, or maintain Claude Code Routines (cloud scheduled agents, aka 'Cowork'). Triggered by 'set up a routine', 'schedule a cloud agent', 'cowork task', 'migrate to a routine'."
---

# Cowork — Claude Code Routines

If `.claude/skill-context/cowork.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.

The context file declares a repo's local-scheduling-vs-routine decision rule, the local
scheduler a task might migrate from, and tools for checking what is scheduled locally.

**Objective:** decide whether a task belongs in a Claude Code Routine, and if so produce a
routine that delegates to committed logic, uses least privilege, has an explicit
notification seam, and is recorded in git. Reference:
[code.claude.com/docs/en/routines](https://code.claude.com/docs/en/routines).

## Facts

- A routine = **prompt** + **repo(s)** cloned into a fresh cloud sandbox each run +
  **connectors** (Anthropic-hosted MCP, e.g. GitHub, Slack, Linear) + **trigger**
  (schedule, API call, or GitHub event). Each run is an independent cloud session that
  runs even when the local machine is off. No state persists between runs except what a
  run pushes (by default only to `claude/`-prefixed branches) or writes to an external
  system.
- Routines cannot reach the local machine: no local `.env` or vault, no local relay for
  notifications. Credentials come from a native connector (preferred) or a routine-scoped
  secret provisioned at creation, whose value is shown once.
- API-trigger calls need the beta header `experimental-cc-routine-2026-04-01` at time of
  writing; check the current docs for the live value.
- Creation needs a Claude.ai Pro+ account. Create and inspect routines (including run
  history) through the built-in `/schedule` skill where it is available; otherwise a human
  creates it at `claude.ai/code/routines`.

## Fit

A routine fits a **pure cloud-API judgment task**: read a cloud API, apply judgment, write
to a cloud API. It does not fit a task that needs live local state (a local database or
process, a local-only secret, a local notification relay). A repo context file's decision
rule overrides this generic test.

## Constraints

- **Confirm the full config with the human before creating a routine.** It runs
  unattended and spends cloud budget.
- **The prompt delegates; it does not re-implement.** If a committed skill or recipe holds
  the judgment logic, the prompt invokes it by name ("Run `/my-skill --flag` and follow
  its output") and nothing more; a second copy drifts.
- **Least privilege:** only the connectors and secrets the recipe needs.
- **Record a routine-spec descriptor in git** (e.g. `docs/infra/<routine-name>.md`):
  prompt, repo, connectors, trigger, and which secret maps to which env var (never the
  secret value). Update it in the same change as any routine change; treat drift between
  descriptor and live routine as a bug.
- **Make the notification seam explicit.** The recipe's terminal action (e.g. a filed
  GitHub issue) is the signal a human receives.
- **Silence is ambiguous.** A failed run (auth or connector expiry, quota) and a quiet
  successful run both produce nothing, so check the run history directly when maintaining
  a routine, and confirm the prompt still invokes the recipe correctly after the recipe
  changes.
- **Migrating a local scheduled task:** remove the local schedule only after the routine
  has a verified successful live run, and don't leave both running once it has (duplicate
  side effects such as duplicate issues).
