---
tracking: none
slug: valor-rebuild
type: plan
status: draft
---

# Valor rebuild: what exists

The rebuild's milestones are built and merged, and the cutover is done
(2026-10-14, on the Cowboy: `main` is this system, the old one is the tag
`old-system`). The per-milestone plans, records and runbooks are in git
history. [strip.md](strip.md) then took the restrictions down; this page is
what the system does after it. Tom's rulings are in
[valor-rebuild-feedback.md](valor-rebuild-feedback.md) and the calls made
without asking are in [valor-rebuild-decided.md](valor-rebuild-decided.md).

## What the system does

- **Kernel.** One resident process (`python -m core serve`) over a local
  Postgres: an append-only ledger, settings, and a fold from a task's rows to
  its state (judge, clarify if thin, plan, critique, build, test, review,
  patch, merge, stop, waiting on a question). Governance is text plus the
  four seeded guards in `core/guards.py` with their ninety-day expiry.
- **Merge.** The kernel merges to the Brief's target from the mirror when one
  candidate is current, its test passed (or gaps with rounds spent), and a
  fresh-session review passed. Docs still run and no longer hold the merge.
  It never force-pushes onto the target.
- **Turns.** `claude -p` with JSON output, session resume, the persona and
  Brief appended, and the gateway as the model endpoint, which meters every
  call (Anthropic and OpenAI routes). A turn has Valor's own `gh`, git, SSH
  and `sentry-cli` access, the project's own CLAUDE.md and skills, the
  `yudame` and `yudame-dev` plugins, the operator's user MCP servers (the
  logged-in browser among them) and the web tools, in one
  sandbox profile that denies only the kernel key directory, the Postgres
  data directory, the backup disk and loopback 5432. Everything a turn
  started is reaped, and background tasks are off. Concurrency is a limit
  read from settings, default 3.
- **Bridges.** Telegram, email, Slack and a local chat bridge. Telegram
  carries messages both ways and is the one tested bridge. Reactions mark
  arrival and the shape of the reply. Each team chat, each email thread,
  and Tom's own chat (his Telegram and Slack DMs, the operator chat, the
  local chat) has one
  long-lived chat session, and every message there that does not bind to
  running work goes to it. It reads and dispatches: it replies, stays
  quiet, starts a subagent for something small (`valor:web` for the web),
  or starts tasks that report to it, and only it speaks in its chat.
- **Harnesses.** Claude Code, plus Pi and the OpenAI route in code.
- **Persona and routines.** The persona is rendered into every turn;
  routines (status page, the 90-day expiry list, which reports to Tom and
  never deletes) run from `routines/`.
- **Effects.** `act` for every task Tom starts. Valor acts as itself on
  its own accounts, fully free, with no spend ceiling (Tom, 2026-10-16).
- **Suite.** No line count (Tom, 2026-10-16); `tests/test_bloat.py`
  fails on production code nothing in production uses; it covers the state machine, the merge predicate,
  the ledger, the resident kernel and Telegram.

## Open items

- Orchestration's done bar ([orchestration.md](orchestration.md)): Tom
  uses an answer from the web through his DM's session, and judges a team
  chat where a task's question and merge reach the chat through the
  session.
- Valor's own skill set, beside the yudame plugins and the project's own
  skills (Tom, 2026-10-16), and with it the full-operator capabilities of
  issues #3636 to #3642, all wanted, in no order.

- The strip's waves 3 and 4 and its done bar: a real task on popoto goes
  from Tom's Telegram message to a merge Tom uses
  ([strip.md](strip.md)).
- Valor's Sentry login in the turn ([c19-sentry-route.md](c19-sentry-route.md)).
- Asks reconciled before they start, in shadow, and run in start order
  ([ask-reconciliation.md](ask-reconciliation.md)).
- Calibrate `intake.overlap` and build the ask hold: `shape.proposed` waits
  on the writer, `ask.folded` folds a covered ask into its task, and a
  confirmed `supersedes` stops the narrower task (Valor confirms it as PM,
  Tom 2026-10-15). It is a guard and needs Tom's grant in its plan file:
  mission items 6 and 1; incidents `cfb11735ee36`/`5172ae8d8b4f` fixed one
  cause (rows 32505, 33201-33498, 34466-34468) and
  `d734763cd94e`/`85abb1026556` both build approve-by-reply (row 33265).
- Three small fixes ([c4-small-fixes.md](c4-small-fixes.md)).
- The GitHub token's reach (Tom's), the other three Macs, and the old
  system's features this one lacks, which Tom picks and orders, one task each.
