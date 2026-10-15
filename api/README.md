# api

Programmatic interfaces.

## Scope

Reserved for MCP servers and any HTTP surface. In this phase the directory holds this README and nothing else.

Governed by [docs/architecture.md](../docs/architecture.md): any interface reaches authority only through the broker.

## Imports

- May import: `core/`.
- Imported by: nothing.

## Effect classes

Holds none until an interface exists. When it does, each endpoint reaches the world only through the broker with a declared class.

## Not here

**Governance is restrained by structure, not sentiment.** Before any check, gate, hook, validator, review round, approval step, isolation, sandbox rule, denial, allowlist, refusal, capability removal, or verification layer is added, the change names the mission item it serves and the incident that already happened without it; missing either, it is not added. A bug fix never adds a guard; it fixes the code. Adding governance needs Tom's grant, one per instance, written in the plan file that adds it. Every guard is ledgered with the incident it prevents, the mission item it serves, and a ninety-day expiry; a guard that has not fired by expiry is listed for Tom, and deleted unless he keeps it. The reviewer asks whether the diff adds governance; a yes with no grant in the plan file is a refused merge. The same paragraph, in the same words, sits at the top of `CLAUDE.md`, in the persona rendered into every turn, and in the Not-here section of every directory README. No restraint skill, no hook that blocks hooks, no governance dashboard: each is the disease presenting as the cure.

- The dashboard. That is `ui/`.
- A second path to authority that bypasses the broker.
