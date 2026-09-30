# Commit pass, 2026-09-19

Run after the blind-spot review and before build planning. The question put to the documents: where do they engineer optionality instead of committing to a direction? The architect's rule for the pass: commit in most cases, because refactoring will be cheap and simplicity gets to learning faster.

Three facts from the architect decided most of the items:

- **The first space is a live client engagement**, so row-level security, per-space credentials, and allowed targets stay in M0.
- **No client has ever requested deletion or restricted model vendors.** Encryption, key destruction, provider allowlists, and a second provider are removed until an agreement asks for them.
- **The first month's work is both code in the client's repositories and communication around it**, so M0 carries a container sandbox and one mail connector from the start.

One more: the CLI is the only surface at M0; the second is a FastHTML page served by FastAPI with websockets and HTMX; Telegram is a possible third.

Each item below has a disposition: **committed** (one direction chosen, section cited), **deferred** (designed, kept as a paragraph, no milestone), or **dropped**.

## Ports built before a second implementation

| # | Hedge | Disposition |
|---|---|---|
| 1 | Worker port with a three-arm Executor bake-off (Pi, Claude Agent SDK, PydanticAI) and a spike to decide | committed: one PydanticAI loop for every agent class, tools bridged to the Sandbox port; tech stack §5 |
| 2 | Sandbox port with three adapters (apple/container, exe.dev, Cloudflare) and two spikes on the hosted ones | committed: apple/container only, installed as the first task of M0; tech stack §6 |
| 3 | Approval Surface port with JSON Schema export for a UI in any language and the second adapter left open | committed: CLI at M0, FastHTML at M1, Telegram possible; all Python, no export; tech stack §13 |
| 4 | Gateway with two wire formats, a Verifier from a different family, a provider allowlist per space, and data class as a provider constraint | committed: Anthropic only; Verifier is the previous generation of the same family; data class is an agent-class cap; tech stack §4, §4.1; architecture §5 |
| 5 | Model registry as a Postgres schema with selectors, fallback selectors, and per-provider terms | committed: one YAML file with pinned ids and three seats, inside the trust boundary; tech stack §4.1 |

## Infrastructure carried for milestones that do not exist

| # | Hedge | Disposition |
|---|---|---|
| 6 | Five process boundaries becoming five processes at M1, plus a separate macOS user for the kernel | committed: one process with module boundaries and import rules; the container VM is the boundary between agents and the kernel; tech stack §2 |
| 7 | Per-space envelope encryption, key destruction, tamper-evident hash-chained export, restore rehearsal in CI | committed: ending a space deletes its rows under the migrator role with a tombstone; nightly `pg_dump` to a disk; encryption returns when an agreement asks; tech stack §3; architecture §9 |
| 8 | Popoto on Redis now, Postgres later, a spike to measure the gap | committed: Redis; memory has no authority so two stores need no joint transaction; tech stack §3.1; architecture §6 |
| 9 | Hosting analysis (Neon, Fargate, region immutability) and a region spike at M3 | committed: Tom's Mac; hosting decided when the Mac stops being enough; tech stack §11 |
| 10 | Stub gateway at M0, real gateway at M1 | committed: real gateway at M0, since it is the smallest component and every measurement depends on it; tech stack §4, §10 |

## Vocabulary designed before a task needs it

| # | Hedge | Disposition |
|---|---|---|
| 11 | Six org structures with NodeSpec and EdgeSpec, jury independence checks, contract and workspace nodes, explore mode, and M4 to build them | deferred: one Executor per leaf; the rest is one paragraph in architecture §3.2, built when an objective needs it; M4 dropped |
| 12 | The bandit over the ledger for structure selection | dropped: needs a structure library and a ledger volume that do not exist |
| 13 | Nine agent classes | committed: Framer, Planner, Executor, Verifier, Scribe; Researcher, Synthesizer, Adjudicator, Paraphraser arrive with the structures they serve; architecture §3.1 |
| 14 | The forcing case run as a gate at every milestone | committed: kept as the argument behind the design, run when someone wants to see it; architecture, forcing case |
| 15 | Calibration, four understanding metrics, rubber-stamp rate, and per-model pass rates specified as reported instruments before any outcome exists | committed: every raw event is logged from the first turn; metrics are computed when about a hundred outcomes exist and the data says which it can support; tech stack §9; architecture §5, §6 |
| 16 | Standing prompt cadence left to measurement | committed: compiled per turn as part of the deterministic render; architecture §6; tech stack §8 |
| 17 | Paraphrase before review and hidden criteria as OPEN with a spike | deferred: paraphrase waits for a measured collusion case; criteria are shown until audit labels can compare; architecture §5 |

## Decisions parked as someone's call

| # | Hedge | Disposition |
|---|---|---|
| 18 | pyright strict in CI | committed: no type gate in CI, the same preference as the linter rule; anyone may run it locally; tech stack §1 |
| 19 | Second surface timing and choice | committed: item 3 above |
| 20 | Cryptographic versus physical destruction | dropped as a question: no client has asked; item 7 above |

## What was kept strict

Effect classes, budget conservation in the database, append-only grants, row-level security, the gateway as the one control point, idempotency keys on effects, kernel-minted approvals, the Scribe as a proposal-only writer, blind verification, the human audit sample, and the Valor boundary. Each is cheap, and each is what the spikes proved or what the client space requires.

## Spikes

Of nine scheduled, five are removed with the options they served. The four that remain (gateway kill on the real loop, budget return on cancel, cache economics on the real loop, passkey round trip) are done by building M0 and M1 or are scheduled at M3. Tech stack §14.

## Not in this pass

An independent reviewer raised five design corrections that are about precision rather than optionality: the stop guarantee overclaims what token revocation achieves; the gateway records model requests rather than execution truth and three records should be named; an older Verifier is not automatically a trusted one and needs a fixture screen; data class has to propagate to derived text, which the Scribe's context slice currently violates; and ordinary work needs a fast path. Those are a separate pass, proposed and not yet applied.
