# bridges

Self-contained comms modules.

## Scope

- One module per channel: `telegram/`, `email/`, `local/`, and later others.
- Each does I/O and the outbox only: receive, normalize, hand to `core/`; take an approved outbound message and deliver it.
- Each conforms to one port in `core/`, so a bridge can be replaced without touching anything else.
- Test accounts are configured per bridge for `tests/`.

Entry points: `python -m bridges.telegram run | login | keys | --plist`, `python -m bridges.email run | keys | --plist`, and `python -m bridges.local run | open | --plist`.

Governed by [docs/bridges/telegram.md](../docs/bridges/telegram.md), which owns the bridge port, [docs/bridges/email.md](../docs/bridges/email.md), and [docs/bridges/local.md](../docs/bridges/local.md).

## Imports

- May import: `core/` ports only.
- Imported by: nothing except the process that starts it and `tests/`. Bridges never import each other.

## Effect classes

- Inbound receipt is `read`.
- Delivering a message is `act` (send). A bridge delivers only what the broker releases with an approval on the ledger; it never decides that a message may go.
- Posting a withdrawable draft where the channel allows it is `propose`.
- A bridge declares, per operation, which class it performs, and reaches the world only through the broker.

## Not here

**Governance is restrained by structure, not sentiment.** Before any check, gate, hook, validator, review round, or approval step is added, the change names the mission item it serves and the incident that already happened without it; missing either, it is not added. A bug fix never adds a guard; it fixes the code. Adding governance is an `act`-class effect: the Brief carries a `governance_grant` field, default none, and a diff that adds any of the above needs Tom's tap, one approval per instance, through the same approval surface as a merge or a send. Every guard is ledgered with the incident it prevents, the mission item it serves, and a ninety-day expiry; a guard that has not fired by expiry is deleted by default. The blind verifier asks one Jev-class boolean over every diff, "does this add a check, gate, hook, round, or review step", and a yes with no grant is a refused merge. The same paragraph, in the same words, sits at the top of `CLAUDE.md`, in the persona rendered into every turn, and in the Not-here section of every directory README. No restraint skill, no hook that blocks hooks, no governance dashboard: each is the disease presenting as the cure.

- Routing, triage, and judgement about what a message means or who handles it. That is `core/`.
- Session creation, queueing, and turn execution. That is `core/` and `harnesses/`.
- Persona rendering or text rewriting before send. That is `persona/` through `core/`.
- Retry policies that resend without a new approval.
- Dashboards or status pages. That is `ui/`.
