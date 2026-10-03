# persona

Everything that defines Valor as one identity.

## Scope

- Identity, voice, and conduct.
- What acting as Valor means for tone and for what may be sent under Valor's name.
- One persona: `identity.toml`, `turn.md`, `voice.md`, `conduct.md`, `governance.md`, and `delivery.md`. `core/persona.py` renders them, with the governance paragraph and the "Tests are not governance." paragraph read from `CLAUDE.md` under `governance.md`'s heading, at the head of every turn's text. Every fresh session (critique, review, docs) gets the same persona.
- This README is not rendered.

Every outbound message and PR leaves as Valor. There is no per-space identity and no mode that sends as anyone else.

Governed by [docs/persona.md](../docs/persona.md).

## Imports

- Imports nothing. It holds text, not code.
- Read by: `core/persona.py`, which `core/tasks.py` calls when it renders a turn. Nothing else reads these files.

## Effect classes

Holds none. The persona shapes what is said; the broker decides whether it may leave.

## Not here

**Governance is restrained by structure, not sentiment.** Before any check, gate, hook, validator, review round, or approval step is added, the change names the mission item it serves and the incident that already happened without it; missing either, it is not added. A bug fix never adds a guard; it fixes the code. Adding governance is an `act`-class effect: the Brief carries a `governance_grant` field, default none, and a diff that adds any of the above needs Tom's tap, one approval per instance, through the same approval surface as a merge or a send. Every guard is ledgered with the incident it prevents, the mission item it serves, and a ninety-day expiry; a guard that has not fired by expiry is deleted by default. The blind verifier asks one Jev-class boolean over every diff, "does this add a check, gate, hook, round, or review step", and a yes with no grant is a refused merge. The same paragraph, in the same words, sits at the top of `CLAUDE.md`, in the persona rendered into every turn, and in the Not-here section of every directory README. No restraint skill, no hook that blocks hooks, no governance dashboard: each is the disease presenting as the cure.

- Per-channel or per-space personas. There is one.
- Rules that decide what may be sent. Sending is an `act` gated in `core/`.
- Harness-specific prompt plumbing. That is `harnesses/`.
- Skill bodies. Those are `skills/`.
