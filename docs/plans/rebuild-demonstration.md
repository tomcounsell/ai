---
tracking: none
slug: rebuild-demonstration
type: record
status: setup
---

# Rebuild demonstration

The record of the setup phase plan's step 4 demonstration:
one meaningful request from Tom's actual work, carried by Valor inside the
minimal kernel to a usable result, with Tom doing nothing except answer the
questions Valor chose to ask.

## Setup

**The replay.** The request is the one Tom gave for psyoptimal PR #894. Valor
starts from the commit that request was made against,
`ebdbf645a0c3302a90b77652852142f45983e84b` of `yudame/psyoptimal` (a private
Django and Postgres app), with no knowledge of how it was answered. The
merged PR is the reference the result is compared against afterward; it
stays out of this record and out of Valor's reach.

**The workspace.** `scripts/demo_workspace.sh` builds it under
`/Users/tomcounsell/src/valor-demo/`:

- `psyoptimal/`: a clone holding only the history up to the base commit
  (no later commits, branches, or tags), on branch `valor/profile-completion`.
- `origin.git/`: a local bare repository, the clone's only remote. A push is
  an `act` effect: Valor requests it, the broker holds it, and it lands only
  after Tom approves and releases it. Nothing reaches GitHub.
- `pg/`: a Postgres cluster of the workspace's own, separate from the one
  holding the kernel's ledger, on `127.0.0.1:5439` with password auth. Its
  `test` role (password `test`, CREATEDB) is what the app's tests use; the
  turn's environment points them at it (`TEST_DB_*`, `DATABASE_URL`, `PG*`).
- The leak check found no plan doc, code, or note describing the feature.
  The base tree mentions the sports career start-date fields (the existing
  data the request refers to) and an existing "Complete your profile" link;
  both are the app as it stood, and nothing was deleted.

**Isolation.** Every turn runs `claude -p` in safe mode (no hooks, skills,
plugins, CLAUDE.md, or MCP servers from this machine), with web fetch and
web search off, an allowlisted environment carrying no tokens or agent
sockets, an empty gh config, a git config with no credential helper, and a
sandbox-exec profile. Under that profile a turn cannot read Tom's other
checkouts, notes, earlier Claude Code transcripts and plans, or keys; cannot
write the bare origin or run git's keychain helper; and on loopback reaches
only the gateway, the workspace's Postgres, and ports 8000 to 8009; this
Mac's own Postgres (port 5432 and its socket) is out of reach, and the
workspace cluster's data directory is neither readable nor writable. It can
reach the public internet, so package installs work. It runs as Tom's user,
so a deliberate keychain read through the `security` tool is not fenced.

**Authority and money.** Effect ceiling `act`, governance grant none,
budget $15.00, model Claude Opus 5.5 (`claude-opus-5-5`, $4 input and $20
output per million tokens). Every model call of every turn, including
Claude Code's own side calls and subagents, passes through the kernel's
gateway and is metered against that one budget. Correction 1 (the
governance-restraint paragraph, global, direct) renders into every turn's
Brief.

**How it runs.** `python -m core start` once; then `python -m core run`
until it prints a question, a delivery, or the budget's end. Each question
is answered with `python -m core answer`; a held push is approved with
`python -m core approve` and performed with `python -m core release`.

## The request, verbatim

> Home page notification if user profile settings not complete. Example: At
> least one of the **Sports Career Start Dates** should be completed.

## Attention log

Every question Valor asked, Tom's answer, and whether the answer changed the
outcome or the authority the work needed.

_Not yet run._

## Where Tom acted as project manager

_Not yet run._

## What was delivered

What Valor delivered, whether it was used, and what Tom would have done
differently.

_Not yet run._

## Money

What the task spent, by model and by turn, and what a judgement layer would
have taken off a frontier model.

_Not yet run._
