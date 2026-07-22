---
name: cowork
description: "Create/review/maintain Claude Code Routines (cloud scheduled agents, aka 'Cowork'). Triggered by 'set up a routine', 'schedule a cloud agent', 'cowork task', 'migrate to a routine'."
---

# Cowork — Claude Code Routines

> **PROVISIONAL.** This skill codifies a pattern exercised by exactly **one** pilot
> (`docs/infra/cowork-sentry-triage.md`), whose live cloud-routine behavior was still
> [EXTERNAL]/unexecuted at the time this skill merged. Treat the guidance below as a
> first draft, not settled doctrine. **Cleanup trigger:** if issue #2068 (the first real
> second migration using this pattern) stalls, or the pattern proves wrong on first reuse,
> revise or remove this skill — and if removed, add a `RENAMED_REMOVALS` entry in
> `scripts/update/hardlinks.py` so the stale hardlink is cleaned up on every machine.

## Repo Context Probe

If `.claude/skill-context/cowork.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.

The context file is where a repo declares its local-scheduling-vs-routine decision rule, any existing local reflection/cron system a candidate task might be migrating away from, and repo-specific executables for checking what's currently scheduled locally or filing the routine's output. When the file is absent (the common case in a foreign repo), follow the generic baseline below.

## What a routine is

A **Claude Code Routine** is Anthropic's cloud scheduled-agent capability (research
preview) — informally called "Cowork" in some conversations, but the concrete,
documented mechanism is **Routines**. Authoritative reference:
[code.claude.com/docs/en/routines](https://code.claude.com/docs/en/routines).

A routine is composed of four parts:

1. **Prompt** — the instructions each run executes.
2. **Repo(s)** — the routine clones one or more repos into a fresh cloud sandbox each run.
3. **Connectors** — Anthropic-hosted MCP connectors (e.g. GitHub, Slack, Linear) the run can call.
4. **Trigger** — schedule (cron-like cadence), API call, or GitHub event.

Each run is a full, independent Claude Code session on Anthropic-managed cloud infra —
it executes even when the local machine that "owns" the repo is off. There is no
persistent state between runs beyond whatever the run itself commits back to the repo
(by default, Claude may push only to `claude/`-prefixed branches) or leaves in an
external system (a filed issue, a Slack message, etc.).

## When to use a routine vs. local scheduling

A routine is a good fit when a task is a **pure cloud-API judgment task**: read a cloud
API, apply judgment, write to a cloud API — with no dependency on local machine state,
local files that aren't in the repo, or a local relay for notification. It is a poor fit
when the task needs live local state (a database only the local machine can reach, a
local process to signal, a local secret that can't be handed to a routine-scoped secret
or connector).

If the repo context file declares a specific decision rule for this repo, use it. In its
absence, apply that generic test directly.

## How to create a routine

Creation is **human-gated** — it requires a Claude.ai Pro+ account and manual creation,
either:

- via `/schedule` in the Claude Code CLI, or
- at `claude.ai/code/routines` (web console, OAuth).

A headless agent cannot create or verify a live routine autonomously. The agent's job is
to produce everything the human needs to create it in minutes:

- A minimal, precise **prompt** that delegates to a committed recipe (see below) rather
  than re-implementing judgment logic inline.
- The **repo** to clone.
- The **connectors** required (e.g. GitHub for filing issues).
- The **trigger** (cadence or event).
- The **auth mechanism** for any non-native-connector credential (see below).

Record all of the above as a committed **routine-spec descriptor** — a versioned markdown
file in the repo (e.g. `docs/infra/<routine-name>.md`) — so the cloud object is
auditable and reconstructable purely from what's in git, even though the routine itself
lives outside the repo.

## Author the prompt: delegate, don't re-implement

**Do not re-implement judgment logic in the routine's cloud-config prompt.** If the task
already has a committed skill or recipe that performs the judgment (classification,
triage rubric, decision tree), the routine's prompt should invoke that recipe by name and
nothing more. Re-encoding the logic as routine-prompt text creates a second copy that
drifts from the source of truth the moment either one changes.

Good prompt shape: "Run `/my-existing-skill --flag` and follow its output." Bad prompt
shape: a paragraph re-explaining what that skill already does, in the routine's own
words.

## Auth: connectors vs. routine-scoped secrets

Routines have **no access to the local machine** — they cannot read a local `.env`,
vault file, or any credential that only exists on disk locally. Two options:

1. **Native connector** — if Anthropic's connector catalog already covers the target
   service (GitHub is a native connector, for example), prefer it: no secret management,
   scoped by the connector's own auth flow.
2. **Routine-scoped secret** — for services without a connector, a secret can be
   provisioned directly to the routine at creation time. The token/secret value is shown
   once at creation — capture it into the routine-spec descriptor's auth section (record
   *which* secret is used and how it maps to an env var the run reads, never the secret
   value itself).

API-trigger calls (as opposed to schedule/event triggers) require the beta header
`experimental-cc-routine-2026-04-01` at time of writing — check the current docs for the
live value before use.

## The notification seam

A routine has **no local relay** — it cannot deliver a Telegram message, push to a local
queue, or write to anything the local machine watches. The routine's own **output** is
typically the notification: if the recipe files a GitHub issue on an actionable finding,
that filed issue *is* the notification (most engineers already get GitHub notifications).
Design the recipe's terminal action, not a side-channel push, as the signal a human
receives.

This has an important corollary for **auditing failure**:

> A routine that fails to run (auth expiry, connector failure, quota) files nothing —
> and a routine that runs successfully but finds nothing actionable *also* files
> nothing. These two states are **indistinguishable from the outside** unless you
> separately check the routine's own run history.

Don't assume "no output = healthy quiet day." Build the audit habit below into how the
routine is maintained.

## How to review a routine before/at creation

- Confirm the prompt delegates to an existing committed recipe rather than encoding new
  judgment logic.
- Confirm the connector/secret list is the minimum needed — least-privilege, not "grant
  everything the catalog offers."
- Confirm the routine-spec descriptor is committed and matches what's actually configured
  (prompt text, cadence, connectors, secret names) — treat drift between the descriptor
  and the live routine as a bug to fix immediately.
- Confirm the notification seam is explicit: what does a successful actionable run
  produce, and how will a human learn about it?
- If migrating an existing local scheduled task (reflection, cron job), confirm the
  cutover is ordered — don't remove the local schedule until the cloud routine has been
  verified live with an actual successful run, and don't leave both running in parallel
  once the cloud routine is verified (duplicate side effects, e.g. duplicate filed
  issues, are a real risk during any overlap window).

## How to audit and maintain a live routine

Because a failing routine and a quiet routine look identical from the outside, maintaining
a routine requires periodically checking its **run history** directly (via
`claude.ai/code/routines` or wherever the routine was created), not just watching for its
output to stop appearing. Watch specifically for:

- **Auth/connector expiry** — OAuth tokens and routine-scoped secrets can expire or be
  revoked; a run can fail silently at the auth step.
- **Bundled-connector failures** — early connector integrations have shown intermittent
  failures in unattended (non-interactive) runs; a run can start, hit a connector error,
  and produce no output without that being obvious from outside.
- **Prompt/recipe drift** — if the committed recipe the prompt delegates to changes
  behavior (new flags, renamed skill, changed rubric), confirm the routine's prompt still
  invokes it correctly; the routine-spec descriptor is the place to record this check.

If the recipe or trigger needs to change, update the committed routine-spec descriptor
in the same change, so the descriptor never drifts from the live object it describes.

## What this skill does not cover

- Every connector Anthropic offers — this skill covers the reflection-style-audit →
  routine pattern generally; connector-specific setup lives in each connector's own docs.
- Automating routine CRUD from a repo (a bidirectional descriptor ⇄ live-object sync
  tool) — routines are created and edited by a human; the descriptor is a versioned
  record, not a live sync target.
