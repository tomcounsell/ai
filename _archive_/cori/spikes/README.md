# Cori spikes

Proof-of-concept spikes behind the biggest assumptions in `docs/`. Each
directory is self-contained: a README with question, method, numbers,
surprises, and a recommendation, plus code and a `run.sh`. One uv project
(`pyproject.toml`, Python 3.14) holds the shared dependencies.

Spikes 1 and 2 start a private Postgres 18 cluster on port 5499 under
`spikes/.pgdata-01` (gitignored) and create databases named `cori_spike_*`.
Spikes 3 and 4 read `ANTHROPIC_API_KEY` from `~/src/ai/.env` and call
Haiku 4.5. Total API spend across all spikes was about $0.30. Spike 6
installs apple/container and builds `infra/sandbox/base.Dockerfile`.

## Results

| Spike | Question | Headline number | Verdict |
|---|---|---|---|
| [01 budget conservation](01-budget-conservation/) | Does an insert-only ledger plus a locked `delegate()` transaction conserve budget when 16 workers race on one parent? | 0 over-allocations in 960 racing delegations (control without the lock: 402 tokens over in 0.24 s); 1,642 delegate/s, p95 15 ms contended, 1.4 ms alone | holds with caveat: `SELECT ... FOR UPDATE` is refused under an INSERT+SELECT-only role, so the lock must be an advisory lock |
| [02 kill is lossless](02-kill-is-lossless/) | Does a loop that renders purely from the event store converge after SIGKILL at random points? | 146 of 146 killed runs converged in the event log; resume in 35 ms median. Effects: reconcile-by-key 0 duplicated and 0 lost; at-least-once duplicated in 22% of killed runs; at-most-once lost effects in 20% | holds with caveat: losslessness of the log does not give exactly-once effects; that needs idempotency keys the broker can query |
| [03 gateway kill and trace](03-gateway-kill/) | How fast does revoke stop an agent, and can the tool trace be rebuilt from the gateway log alone? | stream cut 41 ms after revoke, retry refused in 4.8 ms; tool sequence rebuilt from the log identical to the agent's own; usage matches provider to the token | holds; `count_tokens` as a pre-check costs 410 ms per call and a cut stream loses its output usage |
| [04 cache economics](04-cache-economics/) | What does a volatility-ordered render with two breakpoints save over 20 turns? | 81.9% of input billed as cache reads, 29% of uncached cost; naive importance order: 0% reads and 122% of uncached cost; Haiku 4.5 minimum cacheable prefix observed between 4,026 and 4,247 tokens | holds; pass bar of 60% cleared |
| [05 capability attenuation](05-capability-attenuation/) | Can `issue()` be a pure function that never widens, including through a three-child relay? | 4 properties, 2,000 examples each, 0 failures | holds |
| [06 apple/container](06-apple-container/) | Does apple/container give the six Sandbox port operations, the three profiles, and a network restriction set from outside the VM? | boot about 1 s, exec 65 to 110 ms, write 45 ms, read 30 ms; host-only network reaches the Mac and nothing else; all three profiles hold | holds; the builder needs Rosetta, the registry allowlist needs a proxy on the Mac, and snapshot the mount rather than the 285 MB rootfs |
| [07 stop](07-stop/) | When a sandbox is stopped, how long until its processes and its open request are dead, and does the disk survive? | `kill`: execution dead before the next 1 s tick, request reset at the server in 265 ms median; `stop -t 1`: one more write in 20 of 20 and 1.4 s to drop the request; disk retained 40 of 40 | holds with `kill`; nothing in the sandbox honors SIGTERM, and the CLI's return lags execution death, so confirm a stop by probing |
| [08 tool log and ask](08-tool-log-ask/) | Can the smallest PydanticAI loop with five tools against a real container write a tool log whose hashes match the retained disk, and block on `ask` so the gateway sees nothing until `answer()`? | 0 gateway requests while the worker waited, 3 for the run; artifact hash equal to the file on disk; abort during the wait left one terminal event | holds; tool calls in one turn run concurrently so the log pairs by seq; hash artifacts on the host side of the mount |

## Three most surprising things

1. **The tech-stack persistence recipe cannot run as written.** Postgres
   requires the UPDATE privilege for `SELECT ... FOR UPDATE`, so "kernel role
   has INSERT and SELECT and nothing else" and "FOR UPDATE on the parent node"
   are mutually exclusive on the same table. A transaction-scoped advisory
   lock keyed on the parent id gives the same conservation at the same
   throughput with the grant untouched. (Spike 01.)
2. **Prompt caching in the wrong order costs more than no caching.** With the
   same content and the same breakpoints, the naive order paid the 1.25x write
   price on every turn and read nothing back: 122% of the uncached bill. The
   gateway rule "turn on caching for everyone" is a 22% tax on any harness
   whose prefix is unstable, and there is no error or usage field that says
   so. Ordered, the same 20 turns cost 29%. (Spike 04.)
3. **Lossless is not the same as exactly-once, and the at-most-once policy in
   the brief loses effects one time in five.** The event log converged in
   every one of 146 SIGKILLed runs, but about 40% of kills landed between an
   effect's intent and outcome events. At-most-once then recorded `unknown`
   and never retried, so the world was missing the effect in half of those
   cases and the log could not tell which half. Only reconcile-by-idempotency-
   key, asking the target system whether the key exists, reached zero
   duplicates and zero losses. (Spike 02.)

Two smaller ones: a cut stream never delivers the provider's output usage, so
a killed call has to be charged its reserved `max_tokens` (Spike 03); and a
stateless loop is only as lossless as its projection, since my first version
re-ran completed effects because the scripted agent advanced by step counter
instead of by looking at the projected effects (Spike 02).

## What I would change in docs/

- **tech-stack §3, persistence rules.** Replace "`SELECT ... FOR UPDATE` on the
  parent node" with "`pg_advisory_xact_lock` keyed on the parent node id".
  Keep the trigger; the grant already refuses UPDATE, DELETE, TRUNCATE, and
  DROP TRIGGER before the trigger is reached.
- **tech-stack §4, gateway.** Item 2: the budget pre-check uses the previous
  call's billed usage plus the appended delta, with `count_tokens` only on a
  Brief's first call and periodic re-anchors (it is exact but costs 410 ms of
  a 1,540 ms call). Add: a call ended by revoke without a `message_delta` is
  charged its reserved `max_tokens`. Item 6: caching is enforced only for
  harnesses whose breakpoint prefix is stable across calls for the same
  Brief; the gateway should warn or refuse when the prefix changes every
  call, since that costs 22% more than no caching. Add: the gateway forces
  `stream: true` upstream so a revoke can cut a non-streaming client too.
  Add: request bodies stored by content hash, because every request carries
  the whole conversation and the audit log grows quadratically otherwise.
- **tech-stack §8, Context Builder.** Add: the first breakpoint must sit at
  or beyond the resolved model's minimum cacheable prefix (4,096 tokens on
  Haiku 4.5, 1,024 on Sonnet 5, 512 on Opus 5), and a seat policy that
  changes the model can move where breakpoints are allowed. Keep the two-tier
  breakpoint; it saved the persona prefix on every roll-up change.
- **tech-stack §9, cost.** Replace the hypothesis with the measurement: 82%
  cache reads, 29% of uncached cost over 20 turns; naive order 122%.
- **tech-stack §14, spike 3 pass criterion.** "Reconciles to provider usage
  within 1%" holds exactly for completed calls and is unmeasurable from the
  stream for a cut call; say so.
- **architecture §1 and tech-stack §7, effects.** State the effect protocol
  explicitly: `effect.intent` event, the action, `effect.outcome` event, and
  on restart with a dangling intent the broker reconciles by idempotency key
  against the target system before deciding to re-run. Every class 2 and 3
  action type therefore needs a key the target can be queried by (a PR by
  head branch, a message by client-supplied id). At-most-once should be
  named as the fallback for targets that cannot be queried, with "unknown
  outcome" as an escalation, which §7 already lists as a card type.
- **architecture §3.1, capabilities.** Define "subset" as the covers-relation
  on (name, effect class, scope), put the space on the scope axis, and say
  the kernel refuses rather than clips a request it cannot meet in full.
- **architecture §8, failure table.** Add a row: "Loop advances by counter
  instead of by projected state", caught by the chaos test, since that is
  the bug the harness actually found.
