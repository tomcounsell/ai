# Cori: Tech Stack

Companion to the [architecture](architecture.md). That document says what the system is. This one says what it is built from and why. Section references (§) point at the architecture document.

**Provenance.** This stack descends from a stack written for a different system. Cori is greenfield, so nothing arrived LOCKED. Every choice was reassessed and carries a status:

- **LOCKED**: assessed for Cori and holds. Reopening needs new evidence, not new taste.
- **PROVISIONAL**: a recommendation not yet confirmed by Tom or by a spike.
- **OPEN**: a known unknown, with the spike or decision that closes it.
- **DROPPED**: listed at the end with the reason.
- **PATCHED**: added by the blind-spot review of 2026-09-19 ([record](reviews/2026-09-19-blind-spots.md)).
- **COMMITTED**: an option was closed in favor of one direction by the commit pass of 2026-09-19 ([record](reviews/2026-09-19-commit-pass.md)). The rule of that pass: build one thing, add the port when the second implementation arrives, because refactoring is cheap and simplicity gets to learning faster.

Vendor facts marked *(verified)* were checked against vendor docs on 2026-09-19 by the source document's author. They are inherited claims and every spike below re-checks the ones it depends on. Sources are at the bottom.

## The selection rule

One rule produced most of the choices: **the scarce resource is human review, not authorship.** A frontier model writes Python, TypeScript, and Rust equally well. The control bet depends on a person being able to read the kernel at full speed and catch what the model got subtly wrong. So the kernel is written in the language its reviewer is expert in, enforcement lives in the most boring layer available (Postgres constraints, process boundaries, network policy), and anything we cannot audit sits behind a port so it can be swapped.

Corollary: the Worker, the Sandbox, and the Approval Surface are Python Protocols with one implementation each. A Protocol costs nothing; a second implementation is written when a real need arrives, and the ports are proven then. Nothing in the kernel names a vendor, and no vendor is integrated ahead of a need.

**Assessment: LOCKED, COMMITTED on the ports.** This rule is the reason the stack is boring, and boring is the point. The source built three ports with two adapters each on day one; the commit pass reduced that to one implementation per Protocol.

## Summary

| Layer | Choice | Status |
|---|---|---|
| Language | Python 3.14, uv, black, pytest + Hypothesis; no linter, no type gate in CI | LOCKED |
| Schemas | Pydantic v2 | LOCKED |
| Process boundaries | five modules with separate credentials and import rules; one process through M2 | LOCKED (boundaries), COMMITTED (one process) |
| Database | Postgres + pgvector, vanilla SQL only, local through M2 | LOCKED |
| DB driver / migrations | psycopg 3 async, hand-written SQL in the kernel; Alembic with raw `op.execute` | LOCKED |
| Queue | Postgres outbox + `SKIP LOCKED`; no second queue | LOCKED |
| LLM gateway | in-house streaming proxy, Anthropic wire format only; per-Brief tokens; budget, trace, kill, caching | LOCKED, COMMITTED (one provider) |
| Model registry | a YAML file of pinned ids and seat selectors, read by the kernel | COMMITTED |
| Worker harness | one PydanticAI loop for every agent class, tools bridged to the Sandbox port | COMMITTED |
| Worker harness, external | Valor adapter: instruction out through the broker, reports in as trace events | PROVISIONAL |
| Sandbox | apple/container, local, hypervisor-isolated; nothing hosted | COMMITTED |
| Action broker | typed effects, the only route out of the space; verifies the person's signature itself | LOCKED (design), PROVISIONAL (details) |
| Approval Surface | CLI first; FastHTML with FastAPI, websockets, and HTMX second; passkey step-up for `act` | COMMITTED |
| Context Builder | deterministic render, volatility-ordered, compiled per turn; cheapest-capable summarizer | PROVISIONAL |
| Memory layer | popoto on Redis, every model partitioned by space | COMMITTED |
| Embeddings | Voyage-4 family; model id stored per row | PROVISIONAL |
| Hosting | Tom's Mac; revisited when the Mac stops being enough | COMMITTED |
| Artifacts | local filesystem | COMMITTED |
| Telemetry | OpenTelemetry to Logfire; never the audit log | PROVISIONAL |
| Build regime | CODEOWNERS on the trust boundary; four milestones | PROVISIONAL |

## 1. Language and tooling

Python 3.14. `uv` for environments and lockfiles. `black` for formatting. `pytest` plus **Hypothesis** for the kernel.

Hypothesis is load-bearing. The kernel's invariants are properties, not examples: for any sequence of delegate, report, and re-plan operations, the sum of child budgets never exceeds the parent's remaining budget; no issued capability set is ever a non-subset of its issuer's; no approval is consumed twice; no stale contract revision is honored. Property tests state those directly. A coverage target is necessary and insufficient; the property suite is what makes it mean something.

Rejected: **Rust**, because it would be polyglot from day one and its compile-time guarantees cover invariants the design already places in Postgres. **TypeScript**, because its real advantage is shared types with a web UI, and the second surface is FastHTML, which is Python. The stack is one language end to end.

**Assessment: LOCKED, COMMITTED on tooling.** The source used ruff for lint and format. Tom's standing preference is formatting only, so black formats and no linter runs. pyright was left open as a possible CI type gate; the commit pass closed it in the same direction as the linter rule. Anyone may run pyright locally; CI runs black and the tests.

## 2. Process boundaries

Five boundaries. What each holds is the point.

| Boundary | Holds | Never holds |
|---|---|---|
| **Kernel** | DB credentials, sandbox-provider keys, capability signing key | model provider keys |
| **LLM gateway** | model provider keys | DB write credentials (append-only audit role only) |
| **Action broker** | credentials for external effects (GitHub App key, deploy, messaging, payments); the person's passkey public key | model keys; it writes the effect ledger through a connection the kernel opens and holds no database credential of its own |
| **Worker pool** | per-Brief tokens issued by the kernel | any long-lived credential |
| **Sandboxes** | nothing | everything |

"Authority lives in the kernel" (§ five decisions) becomes a code-level fact: a worker that wants to write to the tree has exactly one way to do it, an authenticated kernel API call carrying a Brief-scoped token. Enforced in the repo by an import-linter rule: `workers/` and `adapters/` may import `schemas/` and `ports/` and nothing from `kernel/`; the kernel API a worker calls is the fourth Protocol in `ports/`, implemented inside the kernel and handed to the adapter at construction.

Repo layout: `schemas/` (Pydantic models: Objective, Brief, Report, Belief, ApprovalRecord, TraceEvent; the contract), `kernel/`, `gateway/`, `broker/`, `workers/`, `ports/` (three Protocols), `adapters/`, `infra/`.

**Assessment: boundaries LOCKED, one process COMMITTED.** The source had five deployables from the start, and the blind-spot review added a separate macOS user for the kernel so an Executor with bash could not read the kernel's credentials. Both answered a threat that the sandbox already answers: every agent's tools execute inside an apple/container VM (§6), so no agent process shares a host, a user, or a filesystem with the kernel. The loop, the gateway, and the broker run as one Python process on Tom's Mac, with the five modules holding separate credentials, until something forces a split. The kill switch lives in that process, which no sandbox can reach. Native macOS execution (Xcode, iOS builds) waits for the same reason: it would put an agent on the kernel's host.

## 3. Persistence

Postgres with pgvector. Vanilla SQL only, so the host stays swappable.

Rules:

- **Append-only by grant, not by convention.** Event tables: the kernel role has INSERT and SELECT and nothing else. Migrations run under a separate role. A trigger rejecting UPDATE and DELETE is belt to the grant's suspenders.
- **Budget conservation in a transaction**: `pg_advisory_xact_lock` keyed on the parent node id, check remaining, insert allocation event, commit. The LLM is never asked to do arithmetic it could get wrong on purpose. Measured (spike 01): 0 over-allocations in 960 racing delegations from 16 workers, 1,642 delegations per second, p95 15 ms contended. The unlocked control overspent within a quarter of a second.
- **Three roles**: `kernel_rw` (insert-only on events), `context_ro` (Context Builder; SELECT on base tables through row-level security), `migrator` (the only role with DELETE). Row-level security does not apply to materialized views, so `context_ro` is never granted one; a materialized view may exist for speed only if it is per space or read only by the kernel.
- **Direct connection from the kernel.** One long-lived service needs no pooler.
- **Row-level security on every space-partitioned table**, keyed to a read token the kernel mints per render into a `read_tokens` table that `context_ro` has no privilege on. The policy resolves the token to a space through a `SECURITY DEFINER` function. Any role can set a session variable, so the claim is narrower than "cannot set it": `context_ro` cannot mint a token, and a guessed value reads nothing. The Context Builder stays outside the trust boundary because the grant, not the code, keeps spaces apart.
- **Ending a space deletes its rows.** The `migrator` role removes every row carrying the space id and appends a `space.destroyed` event with the counts. The append-only trigger blocks the migrator too, so that one migration disables the trigger inside its own transaction, deletes, re-enables it, and appends the tombstone. Deletion is done by a person running a migration, and the tombstone is what the ledger keeps.
- **Backups.** Nightly `pg_dump` to an external disk. Restore is rehearsed by hand once, when the first backup exists.

**Queue: Postgres outbox.** An event-sourced store plus a separate queue is a dual-write bug waiting for a bad day. Enqueue in the same transaction as the event; workers claim with `FOR UPDATE SKIP LOCKED` from a mutable claim table the kernel owns, never from an event table, because a row lock needs the UPDATE privilege the insert-only grant withholds (spike 01); poll at 250 to 1000 ms. The claim table arrives with the second consumer process; at M0 one process polls the event table with a projection check under single-flight. Rejected as a queue: Redis, and workflow engines such as Temporal or DBOS, because the objective tree already is the durable workflow state and a second state machine is a second source of truth. This rule is about queues and the system of record. The memory layer (§3.1) runs on Redis.

Driver: psycopg 3 async with hand-written SQL in the kernel, auditable line by line. Alembic for migrations using raw `op.execute`.

**Assessment: Postgres and the rules LOCKED; encryption and hosting COMMITTED away.** One rule was corrected by spike 01: the source's recipe took a `SELECT ... FOR UPDATE` row lock, and Postgres requires the UPDATE privilege for that, so it cannot run under an insert-only grant. The transaction-scoped advisory lock gives the same conservation at the same throughput and keeps the grant minimal. Granting UPDATE and relying on the trigger also works, but it demotes the grant from first lock to second.

The blind-spot review added per-space envelope encryption so that ending a client engagement could destroy a key without violating append-only grants. The commit pass removed it: no client has asked for a destruction certificate, so the design meets the real requirement, deletion on request, with a migration and a tombstone. Encryption returns when a client's agreement asks for it. Hosted Postgres was likewise removed from the plan (§11); the source's Neon analysis is in the git history if hosting is ever reopened.

### 3.1 Memory layer: popoto

Episodic memory and the operator record use [popoto](https://popoto.io), the in-house agent memory library (architecture §6). It is a library call inside the kernel, never a network service, so it sits inside the trust boundary and its data class is whatever the space's is.

Rules:

- **Space on every model.** `space` is a key field; decaying and ranked fields partition by it; every retrieval names exactly one space; global beliefs use a reserved partition.
- **Confidence recorded, never read for authority**, until calibration labels exist.
- **Redis is the backend.** Popoto runs on Redis or Valkey today, and Cori uses it as it is. Memory has no authority, so the objective tree, events, and audit log in Postgres and the memory rows in Redis never need a joint transaction. The Redis instance is local, bound to localhost, and reachable only from the kernel process.

**Assessment: COMMITTED.** Popoto is the architect's system and its primitives are the ones the operator record needs. The source tracked popoto's Postgres backend (issue 631) as an open convergence question with a spike to measure the gap. The commit pass closed it: two stores is fine when one of them holds no authority, and the convergence question is reopened only if a joint transaction is ever needed, which nothing in the design requires.

## 4. LLM gateway

Every model call from every harness goes through the gateway. It:

1. Authenticates a **per-Brief bearer token** issued by the kernel.
2. Checks the Brief's remaining money with the kernel before forwarding and converts it to a token allowance at the resolved seat's price, input and output priced separately, with `max_tokens` reserved at the output price (architecture §4). The pre-check uses the previous call's billed usage plus the appended delta; the provider's counting endpoint runs only on a Brief's first call and at periodic re-anchors, because as a per-call check it cost 410 ms of a 1,540 ms call (spike 03).
3. Forwards with the real provider key, which no worker or sandbox ever sees, streaming through.
4. Writes request, response, and usage to the audit log under the append-only role. Every usage row carries the Brief's space, so spend is attributable per client.
5. **Kill starts with token revocation.** The in-flight stream is cut and the next model call fails. Measured (spike 03): stream cut 41 ms after revoke, retry refused in under 5 ms with zero provider calls. The gateway forces streaming upstream so a non-streaming client can still be cut. A call ended by revoke never delivers the provider's final usage, so it is charged its reserved `max_tokens`. Revocation alone does not make the agent inert: a shell script or background process in its sandbox keeps running without another model call. So a stop is three things: in one kernel transaction the Brief's generation is incremented so the kernel and broker refuse every later request carrying the old one and the gateway token is revoked; then the sandbox's compute is stopped with its disk retained, confirmed by a probe from outside the VM, because a container operation cannot sit inside a database transaction. A replacement Brief gets a new generation only after the old compute is confirmed stopped, and the broker reconciles any dangling effect intent by idempotency key (§7) before the new one runs. What survives a stop is what is durable: events, artifacts on the retained disk, and effects the broker confirmed. Process memory and unfinished calculations do not, and "lossless" means exactly that much.
6. **Turns on prompt caching where the prefix is stable.** Caching with an unstable prefix costs more than no caching: the same content in a volatile order paid the write premium every turn and read nothing back, 122% of the uncached bill, with no error and no usage field that says so (spike 04). The gateway enables caching for a Brief whose breakpoint prefix repeats across calls; when the prefix changes on two consecutive calls it strips the breakpoints, records the request as `unstable`, and forwards, so the worker pays the uncached price rather than the write premium and is never refused over a cost problem.
7. **Stores request bodies by content hash.** Every request carries the whole conversation, so the log grows quadratically otherwise.

Exposes the Anthropic wire format only; the PydanticAI loop points at it by base URL. A few hundred lines of FastAPI and httpx. Built in-house because it is a control point and gets the same test standard as the kernel. It is real from M0, since it is the smallest component in the system and every measurement of the loop depends on it.

Measured (spike 03): the full tool sequence, inputs and results, was rebuilt from the gateway's JSONL log identical to the agent's own trace, and gateway-parsed usage matched provider usage to the token across every field for completed calls.

What it proves and does not: tool calls recorded at the gateway are authentic, since the model cannot act without asking. They are requests, not execution. One `bash` call can run many operations, and a harness can fabricate what it reports back. So there are three records and each is named for what it attests: the **gateway log** holds what the model asked for; the **tool log**, written by the PydanticAI loop in the kernel process before and after each Sandbox port call, holds what was invoked, its exit status, and hashes of its output and any artifact; the **effect ledger**, written by the broker, holds what touched the world. The Verifier reads the second and third and never the first. The tool log records each invocation and its returned output, not every syscall inside the sandbox, and independent re-execution in the `verify` sandbox covers the rest of that gap.

Billing *(verified)*: Agent SDK and headless `claude -p` usage no longer draws on subscription limits, so there is no subscription arbitrage to design around. Everything bills at API rates through org keys the gateway holds.

**Assessment: LOCKED, COMMITTED to one provider.** The source had this PROVISIONAL. It is promoted because it is the physical form of the README's stop button and of the "legible" property. Without it, kill is a request to the harness and the trace is the agent's own account. The source also exposed an OpenAI-format endpoint and a provider allowlist per space so that a client could restrict vendors and a Verifier could come from a different family. No client has restricted vendors, so there is one provider, Anthropic, under its data processing agreement, and one wire format. A second provider is added when a client asks or when a measurement says the Verifier needs one.

### 4.1 Model registry and seat policies

Model choice is data, not code. Design goal: **adopting a new model is one tap, same day.** There is no quarantine, no shadow period, and no earned-trust gate on adoption. We do not out-evaluate the labs on capability.

The registry is one YAML file in `infra/`, inside the trust boundary, that the kernel reads at start:

```yaml
models:                      # pinned snapshot ids, never floating aliases
  - id: <newest frontier snapshot>
    generation: n
  - id: <previous generation snapshot>
    generation: n-1
  - id: <cheapest capable snapshot>
seats:
  frontier:   newest ACTIVE          # supervisor, Executor, Planner
  verifier:   previous generation    # never the executor's snapshot; a newer model only after the fixture screen
  summarizer: cheapest capable       # Scribe, thread summaries
```

Rules the kernel enforces:

- **Prices live beside the ids.** Each model entry carries `usd_per_mtok` for input, output, cache write, and cache read, checked against the pricing page on a recorded date; the gateway meters money from them and refuses a seated model without prices.
- **Pinned ids only.** Providers silently re-point aliases; a ledger row must describe a fixed thing. From the 4.6 generation on, Anthropic's concrete ids carry no date (`claude-opus-5` is the snapshot), so the seat file records the Models API's `created_at` beside each id and a ledger row cites both.
- **The kernel picks, the gateway enforces.** `Brief.model_ref` is resolved at delegate time; the gateway rejects any call for a different model. Agents cannot upgrade themselves.
- **Editing the file is a change inside the trust boundary**, reviewed like kernel code (§10). Whoever can rewrite the Verifier's seat can defeat trusted monitoring without touching an agent.
- **Independence is a constraint.** If the previous generation is retired and no other generation exists, the Verifier seat fails closed and a card says so. It never falls back to the Executor's snapshot. Age is a default, not evidence: at M1 every Verifier seat is screened once against a small fixture set of known-defective and known-good artifacts before it judges real work, and a newer model may take the seat after the same screen and an edit to this file. The screen is the trusted-monitor argument [4] checked rather than assumed.
- **Data class is an agent-class cap, not a provider constraint.** Every context slice is tagged PROJECT or OPERATOR; the Brief's `max_data_class` (architecture §3.1) says which an agent may receive. With one provider under a data processing agreement, there is no per-provider term to check.

The ledger observes and does not gate: per-model verified pass rates by task class are recorded for regression detection, and read when there is enough of them to mean anything (§9).

**Assessment: COMMITTED.** The source designed a catalog and seat-policy schema in Postgres with selectors, fallback selectors, and per-provider terms. A YAML file with three ids and three seats does the whole job for one operator on one provider. The schema arrives if policies ever change at runtime.

## 5. Worker port and harnesses

```python
class Worker(Protocol):
    async def run(self, brief: Brief) -> AsyncIterator[TraceEvent]: ...
    async def answer(self, brief_id: BriefId, question_id: str, text: str) -> None: ...
    async def abort(self, brief_id: BriefId) -> None: ...
# a TraceEvent of kind `question` pauses the worker until answer() arrives;
# the terminal TraceEvent carries the Report, validated against brief.report_schema
```

`Brief.harness` names the adapter: `pydantic_ai` for every internal agent class, `valor` for the external delegate.

**One loop for every agent class.** Planner, Executor, Verifier, and Scribe all run on the same PydanticAI loop with the same four tools (read, write, edit, bash), because code execution is a reasoning tool. Each tool is a plain function that calls the Sandbox port, so the loop runs in the kernel process outside the sandbox, every invocation is recorded in the tool log (§4) before it executes, and the agent's account of a tool result is never the only record of it. `output_type=Report` makes the report a validated type with automatic retry. What differs between classes is the Brief: the prompt, the sandbox profile, the capabilities, and the seat. Framing runs in the supervisor's own turn (architecture §2) and has no loop of its own.

**A worker may ask.** A child that can only hand back a result has to guess or abort when its task is ambiguous, and the defense against that is a Brief long enough to pre-empt every ambiguity. Instead the loop has a fifth tool, `ask`, which emits a `question` trace event and blocks. The Verifier's Brief carries no `ask`, because the supervisor that would answer holds the Executor's account and blindness is a fact about what the Verifier can reach (architecture §5); a Verifier that cannot judge abstains. The supervisor answers from what it knows, or relays the question to the person as a card, and the answer arrives through `answer()`. A question is a `read`, costs what its model call costs, and is recorded with its answer in the tool log. `steer` from the source design is gone: the only messages into a running worker are an answer to its own question and abort, because an unsolicited message interrupts a worker mid-thought for no gain.

**External delegate: Valor.** The second adapter's `run()` sends the Brief as a typed `propose` instruction through the broker and yields Valor's reports as trace events, its `answer()` relays a reply to a question Valor asked, and its `abort()` sends a stop and returns without waiting. The transport is whatever Valor exposes for instruction (a session-creation command on the same machine, or a message on Valor's channels); the adapter is the only code that knows. The guarantees that do not cross the boundary are tabled in architecture §3.4.

**Assessment: COMMITTED.** The source ran Executors on Pi and left the Claude Agent SDK as a challenger; the first Cori draft made it a three-arm bake-off. The commit pass closed it. The PydanticAI loop is already required for every other class, it is Python inside the reviewer's fluency, and it captures the trace at the sandbox boundary, which is the property the other two candidates would have needed adapters and a spike to recover: Pi runs tools inside its own process unless every tool is rewritten as a bridge and its resource discovery is replaced, and the Agent SDK executes tools in-process where an Executor with bash can tamper with its own harness. If the loop turns out to complete leaf tasks poorly against a purpose-built harness, that is a measurement taken on real work, and the Worker Protocol is where the second adapter goes. Rejected: opencode, a product you configure rather than a component you embed.

## 6. Sandbox port

```python
class SandboxProvider(Protocol):
    async def create(self, profile: SandboxProfile) -> SandboxHandle: ...
    async def exec(self, h, cmd, *, timeout) -> ExecResult: ...
    async def read(self, h, path) -> bytes: ...
    async def write(self, h, path, data) -> None: ...
    async def snapshot(self, h) -> SnapshotRef: ...
    async def stop(self, h) -> StopReceipt: ...      # kill, then a probe from outside the VM; the disk stays
    async def destroy(self, h) -> None: ...
```

The tool set is identical across agent classes. **The profile is the capability**, issued by the kernel from the node's approved effect class:

Every profile mounts from exactly one space. A sandbox never sees two spaces' data.

| Profile | Effect class | Mounts | Network | Lifetime | Who |
|---|---|---|---|---|---|
| `scratch` | `read` | read-only slice of the space | host-only at M0; none when the runtime offers it | ephemeral | every class |
| `verify` | `read` | read-only artifact, **fresh VM, never the Executor's**, from a kernel-built image with dependencies installed from a lockfile through an allowlisted registry | host-only at M0; none when the runtime offers it | discarded after use | Verifier |
| `worktree` | `propose` | writable objective worktree | host-only: the Mac, which is the gateway | per Objective, keyed by node id | Executor |

Role bleed is prevented structurally: a Planner can prototype in `scratch` and nothing there can become the deliverable. Space secrets a task needs at test time (a staging database, a client API key) are injected by the kernel into the process environment of a `worktree` sandbox as class-labeled values from the space manifest, never as files.

**The one adapter: apple/container.** It runs each Linux container in its own lightweight VM on Apple silicon, so local sandboxes are hypervisor-isolated and share no kernel, user, or filesystem with the process that holds authority (§2). Spike 06 measured it: boot about a second, exec 65 to 110 ms, and a host-only network that reaches the Mac and nothing else, set from outside the VM. That host-only network is every profile's whole network at M0: it reaches the Mac and nothing else. Nothing inside a sandbox calls the model, since the loop runs in the kernel process (§5) and the gateway binds loopback; the network's shape is what is restricted, not a client. Dependencies come from the kernel-built image, which installs them from the lockfile at build time, so a sandbox never needs a package registry. The builder VM needs Rosetta installed even for arm64 images, and snapshots take the mount rather than the 285 MB rootfs.

TODO, when a task needs a host the image cannot pre-install: a small allowlisting proxy on the Mac beside the gateway, so the sandbox still sees one host and the proxy decides which registries answer. Not built until an Executor is blocked by its absence.

**Assessment: port and profiles LOCKED; adapter COMMITTED.** The source planned three adapters: local, exe.dev, and Cloudflare Sandboxes, with a spike on exe.dev's egress policy and its default LLM integration, and a shim around Cloudflare's TypeScript SDK. Nothing is hosted through the milestones that exist, and the Mac runs Linux VMs natively. A hosted sandbox is chosen when the kernel leaves the Mac, against whichever vendors exist then; the source's analysis of both is in the git history.

## 7. Action broker

The kernel refuses any action above a node's effect ceiling, but the kernel only sees what it mediates. An Executor with bash, a network, and a token can do whatever the token allows. **Credentials are the effect class**, and the ordinal is a comment until the broker makes it real.

- **Sandboxes hold no credential capable of any effect outside the space.** Read-only repo access and package registries. Pushing is a typed `propose` action, `push_branch`, that the broker performs outside the sandbox restricted to the objective's branch name, because a repository token cannot be scoped to one branch and a sandbox holding one could force-push or delete any unprotected branch. Nothing leaves a sandbox except through the broker, by construction.
- **Every effect that leaves the space is a typed action**, never a shell command: `push_branch`, `open_pr`, `post_message_draft`, `calendar_hold` (`propose`); `merge`, `send`, `deploy`, `pay` (`act`). Each is a Pydantic model with a fixed class. An agent requests one through the kernel; the kernel checks the node's ceiling and, for an `act` or a proposal the supervisor chose to ask about, the approval record, then forwards to the broker.
- **Credentials are the person's personal access tokens, few by design.** The system acts as the person, so the broker holds a small set of tokens the person issued: a GitHub personal token, mail and calendar for the person's own accounts, and whatever else the person chooses. It has no accounts of its own. Anything needing accounts beyond that is instructed to Valor.
- **Credentials are keyed by space and target.** Every action payload carries its space; the broker selects the credential for that space and target and refuses a target absent from the space manifest's `allowed_targets`. The same broker holds connector credentials for `read` actions (mail, calendar, client systems) and performs those reads outside the sandbox.
- **Payloads bind the target's state.** Every payload that leaves the space includes a digest of the target state it was approved against (for a repo effect, the head SHA), and the broker re-reads the target and refuses if it differs. An Executor cannot push a commit between the passkey tap and the merge.
- **The broker verifies every `act` itself.** An `act` approval carries a WebAuthn assertion over the hash of the exact action payload. The broker holds the person's public key and checks the signature before executing. A kernel bug, or a compromised kernel, still cannot merge, send, deploy, or pay. Only a passkey tap on that specific payload can.
- **Prefer the target platform's native controls as a second lock.** GitHub rulesets requiring a human CODEOWNER review on protected branches mean even the broker's token cannot merge unreviewed code.
- **Every action carries an idempotency key the target can be queried by.** The effect protocol is intent event, action, outcome event. On restart with a dangling intent, the broker asks the target whether the key already exists before re-running. Spike 02 measured why: about 40% of random kills landed between intent and outcome; at-least-once then duplicated effects, at-most-once lost one in five and recorded `unknown` in two of five, and only reconcile-by-key reached zero duplicates and zero losses. At-most-once is the fallback for targets that cannot be queried, and `unknown outcome` is the escalation card architecture §7 lists.
- Small and boring: one module per action, every request and result an audit event.

**Assessment: LOCKED.** This is the strongest idea in the source stack. It converts the effect ordinal from a promise into a fact, and it survives a compromised kernel. Details such as which actions ship first are PROVISIONAL; `open_pr` is first because it is the proposal code work needs.

## 8. Context Builder

- **Deterministic render.** Same store state in, byte-identical context out. Stable key order, stable slice order, no timestamps in the prefix. Required for cache hits and for replaying any turn from the event log.
- **Ordered by volatility.** Procedural and tool definitions, then the voice core, then operator digest, then objective roll-up, then thread summary, then recent turns, then inbox, then on-demand reads. Cache breakpoints after the voice core (long TTL) and after the roll-up (short TTL). The first breakpoint sits at or beyond the resolved model's minimum cacheable prefix, which spike 04 observed at about 4,096 tokens on Haiku 4.5 (documented as 1,024 on Sonnet 5 and 512 on Opus 5), so the seat file decides where breakpoints are allowed. Anthropic's cache is an exact-prefix cache; writes cost more than uncached input and reads cost a fraction of it *(verified)*, so ordering is worth more than any model-selection cleverness.
- **The cap is a ceiling, not a target.** Budget depends on the triggering event. Target a median turn well under the cap.
- **Token counting** by the provider's own counting endpoint where one exists, reached through the gateway since the kernel holds no provider key, otherwise a conservative estimate with a margin. Tokenizers differ across model generations, so caps are enforced per resolved model.
- **Summarizer seat**: cheapest capable, with the bash tool so it can check its own arithmetic. Summaries are events with provenance links to what they compress, so a bad summary is a diff you can find.
- **Embeddings**: Voyage-4 family, whose tiers share an embedding space *(secondary source)*. Store `embedding_model` and dimensions on every row regardless. Episodic retrieval is by structure first, vector second.
- **Evals**: `pydantic-evals` for agent-class prompts and summarizer fidelity, in CI. Procedural memory "changes through the same process as code" only means something if prompts have tests.
- **The standing prompt is compiled per turn**, as part of the deterministic render. The source left the cadence to measurement; a render that is byte-identical for identical store state makes a separate compile step redundant, and the cache absorbs the cost when nothing changed.

**Assessment: PROVISIONAL.** Nothing here is controversial; it is provisional because every number in it is replaced by running the real loop (§14, spike 3).

## 9. Cost and attention

Measured on the render path (spike 04, twenty consecutive turns, Haiku 4.5): volatility-ordered with two breakpoints billed 81.9% of input tokens as cache reads and cost 29% of the uncached price; the same content in a naive importance order read nothing back and cost 122%. The two-tier breakpoint kept the voice-core prefix as reads on the turns where the roll-up changed. Per-leaf and per-turn dollar figures remain hypotheses until measured on the real loop:

- Because the Verifier seat happens to be the previous generation, mandatory verification adds on the order of a third to a leaf's cost today. If the seat moves to an equally strong model from a second family, verification costs what execution costs, and that is the right price for the property.
- The cost to watch is the supervisor. At a hundred turns a day, rendering to the cap uncached versus a cache-ordered median turn is several times the daily spend. Event-scoped context budgets and volatility ordering are the lever.
- Caching is a multiple on Executors, which is why the gateway enforces it rather than trusting each harness.

**Human attention is measured, never budgeted** (architecture §4, §7). The kernel records cards per objective, time to decision, and whether each approval carried an edit; how many cards to raise and when is the supervisor's judgment from urgency and the person's presence. The earlier "ten batches a day" is kept only as the order of magnitude at which the rubber-stamp rate is expected to become visible.

**Metrics are logged now and computed later.** Verifier calibration, the four understanding metrics (architecture §6), the rubber-stamp rate, and per-model pass rates are all defined against raw events the kernel already writes: verdicts, audit labels, corrections, approvals with or without edits, questions and their replies. Nothing computes them until there are on the order of a hundred verified outcomes to compute over. At that point the data says which of the defined metrics it can support, and the dashboards are built from that, rather than from a design written before a single outcome existed.

**Assessment: PROVISIONAL, COMMITTED on the order of work.** Keep the reasoning, distrust the numbers until measured, and build no measurement infrastructure ahead of the data.

## 10. Build and review regime

A frontier model is building the harness whose premise is that the frontier model is not trusted. That is fine if the boundary is explicit.

**Trust boundary, human review via CODEOWNERS:** `kernel/`, `gateway/`, `broker/`, `schemas/`, `migrations/`, `infra/`, CI configuration, the CODEOWNERS file itself, and, from the blind-spot review: the supervisor's prompt, VOICE.md, every Verifier prompt and checklist, the seat file, and the space manifests. Executor and summarizer prompts stay agent-mergeable behind the `pydantic-evals` gate. Approval adapters stay outside the boundary only because the kernel mints every approval record itself (§13). Without the last two, an agent can edit its way out. Target: the boundary stays small enough that line-by-line review remains a thing a person actually does.

**Everything else** (workers, adapters, prompts, Context Builder, tests) is agent-mergeable on green CI. Builder agents never hold production credentials; Cori is built at `propose`, PRs only.

**What the ruleset enforces today** (`.github/rulesets/README.md`, re-applied 2026-09-20). The ruleset on `main` refuses branch deletion and force-push and nothing more: direct pushes are allowed and no pull request or review is required. GitHub refuses self-approval, so the earlier ruleset that required one CODEOWNER review deadlocked every pull request with one human on the repository (prereqs finding 7), and it was removed rather than worked around. CODEOWNERS still names the boundary, CI still runs on every push and pull request as information, and the review is a practice the person keeps. That is enough for a repository one person builds with agents at the keyboard.

TODO, when builders run unattended: give them an identity of their own, a machine user or a GitHub App, and re-apply a ruleset that requires a pull request and a CODEOWNER review, so the boundary binds. Not before.

**Milestones.** Greenfield: nothing runs alongside, so each milestone proves one property end to end. The first space is a live client engagement, and the first month's work is both code in the client's repositories and communication around it, so M0 carries a sandbox and one connector from the start.

| | Ships | Proves |
|---|---|---|
| **M0** | schemas; kernel (tree, events, budgets in money per objective with the estimate and the actual recorded, spaces as manifests, row-level security, generation fencing); real gateway with per-Brief tokens, kill, and the tool log; the effect ledger with `push_branch` and the Gmail connector; CLI approval surface with a space on every conversation and kernel-minted approvals; the PydanticAI loop with `ask` for Executor and Scribe and without it for the Verifier; the Verifier in a fresh `verify` sandbox with deterministic checks and verdict rows recorded; framing in the supervisor's turn; the rest of the prerequisites in `prereqs.md` are done | one objective end to end at `propose` inside one client space's sandbox, in code and in a drafted message; a stop leaves durable state intact and the sandbox's compute dead |
| **M1** | operator record with goals and scope, the supervisor framing against it, the morning brief composed per space, FastHTML surface with FastAPI, websockets, and HTMX so cards and the brief reach a phone, Planner for objectives with more than one leaf, the Verifier seat screened against fixtures with a threshold and audit labels recorded, chaos test in CI | Cori holds goals with provenance, frames against them, and cannot act on a poisoned belief; budget and kill are enforced outside the agent |
| **M2** | broker with `open_pr` and `post_message_draft`, skip-level and human audit samples, metrics computed once the outcomes exist (§9) | the first proposals leave the space with no credentials in any sandbox; calibration has its first labels |
| **M3** | passkey step-up, broker `act`, first autonomous `act` in one measured task class, Telegram surface if the FastHTML page turns out to be the wrong place for cards | an `act` needs a passkey; one lane of earned autonomy exists and the numbers show why |

Multi-agent structures (architecture §3.2) have no milestone. They are designed when a real objective needs more than one Executor per leaf.

**Cheap now, expensive later, so done at M0:**

- **Event versioning.** Every event has `type` and `schema_version`; old events are never rewritten; readers upcast.
- **Single-flight per thread and per objective** via Postgres advisory locks, so two triggers cannot run concurrent supervisor turns on the same state.
- **Secrets on the build machine.** Provider keys and broker credentials live in the macOS Keychain under service `cori`, loaded at process start by `infra/secrets.py`; nothing in a dotfile. The durable copy of every secret is the 1Password vault `m-tomcounsell` under `CORI_*` items. The day the kernel runs anywhere but this Mac, it reads them through a 1Password service account scoped to a Cori vault, never a personal session.
- **M0's first objective is a code change in the client's repository**: branch, edit, tests, commit, at `propose` inside the sandbox, with the Verifier reading the kernel's test record. The second is a drafted reply to a client mail, also inside the sandbox. Together they exercise the tree, budgets, sandbox, connector, and verification with the fewest new concepts; `open_pr` and `post_message_draft` at M2 are the first proposals that leave the space.

**Assessment: PROVISIONAL, COMMITTED on scope.** The operator record moved from M3 to M1 and calibration is threaded through M1 to M3, because the architect set the drive to understand as Cori's center and broad autonomy as the destination. The commit pass removed from M0 the envelope encryption, the separate OS user, the stub gateway (replaced by the real one), and the tamper-evident hash-chained export, which protected against a compromised kernel on a machine only Tom operates. It removed M4 outright and the bandit with it: both needed ledger volume and objectives that do not yet exist.

## 11. Hosting

Cori runs on Tom's Mac: one process, local Postgres, local Redis, local sandboxes via apple/container. Hosting is decided when the Mac stops being enough, against a measured requirement.

**Assessment: COMMITTED.** The source chose AWS Fargate in the same region as Neon and exe.dev and scheduled a region spike. That answered a question Cori does not need to ask, and would have locked the cloud through the database. The analysis is in the git history.

## 12. Observability

OpenTelemetry everywhere, exported to Logfire (PydanticAI instruments natively). This is operational telemetry: latency, errors, cost dashboards. It is never the audit log. The audit log is Postgres, append-only, and the only record with evidentiary standing. If the two disagree, Postgres is right and Logfire has a bug.

**Assessment: PROVISIONAL.**

## 13. Approval Surface port

Typed cards out (one Pydantic model per escalation type in §7), approval record in, raw human message attached. First adapter: the CLI, because it is the fastest way to exercise the state machine. Second, at M1: a FastHTML page served by FastAPI with websockets and HTMX, so cards and the morning brief reach a phone over an authenticated session, and the `act` passkey page is one more route on it. Telegram is a possible third if the page turns out to be the wrong place for cards. All three are Python against the same Pydantic models, so no schema export is needed.

**Authenticating the person.** Every line of authority ends at "the person," so the channel's authentication is the system's root credential. A chat account is not strong enough to be root; session hijack or SIM swap would inherit everything.

- The kernel authenticates the channel and mints every approval record; adapters render and relay over a kernel-owned session. On the local surface an approval of a proposal is an interactive confirmation the kernel attributes to a terminal session under the person's user, never a file or socket write a worker could make.
- `read` and `propose`: channel identity is sufficient once the kernel has authenticated it.
- **`act`: passkey step-up.** The card links to a route on the FastHTML surface; the person signs the hash of the exact action payload with WebAuthn. The assertion is stored in the approval record and re-verified by the broker (§7). WebAuthn binds a credential to a relying-party id, so the production id (a domain the person owns) is chosen at M0 and the local page is served under it; a passkey registered against a local origin would be useless the day the page is reachable from outside.
- Also passkey-gated: editing the seat file, acknowledging an anomalous operator record change.
- **Cards are rendered by the kernel from structured fields.** Agent prose appears only in a marked, length-capped note region.

**Assessment: port and passkey LOCKED; surfaces COMMITTED.** The source assumed Telegram second because it already existed there, and the first Cori draft left the second surface open. The commit pass named it: the CLI at M0, the FastHTML page at M1, Telegram only if the page fails at the job.

## 14. Spikes, in order

Five spikes ran before the commit pass and all held (spikes/README.md). Of the nine that were scheduled, the commit pass removed the five that existed to close options now closed: hosted sandbox lifecycle, the Executor bake-off, hosted sandbox hardening, the region check, and popoto on Postgres. What remains is done by building M0 or is scheduled at the milestone that needs it.

1. **Gateway kill.** Done once with a stub gateway (spike 03). Rerun on the PydanticAI loop when the real gateway exists at M0. *Pass:* zero further model calls; budget ledger matches provider usage exactly for completed calls; cut calls are charged reserved `max_tokens`.
2. **Budget conservation under concurrency.** Done (spike 01). Remaining, at M1 with the Planner: cancel and re-plan returning budget to the parent, and two loop processes on one objective, which the advisory lock design should also cover.
3. **Cache economics.** Done on the render path with synthetic slices (spike 04, 82% reads). Remaining: measure on the real loop at M0 with tool definitions in the cached prefix and the long TTL, and set the §1 caps from it.
4. **Passkey round trip**, at M3. An `act` card, WebAuthn route on the FastHTML surface, assertion in the approval record, broker verifies, `merge` on a throwaway repo. *Pass:* a forged approval record written directly to the database is rejected by the broker.

## Limits

- **The person is one person.** Passkeys authenticate Tom. Away state (architecture §7) covers Tom being unavailable; nothing here covers Tom being tired or wrong. The attention record and the rubber-stamp rate make that visible once computed.
- **Exfiltration through allowed channels.** A sandbox permitted to reach a package registry or push a branch can leak through them. Data classes limit what is mounted.
- **The gateway is a single point of failure** and a single point of control. Those are the same property. It gets the kernel's test standard and its own chaos test.
- **One provider.** The Verifier is the previous generation of the same family as the Executor. Trusted monitoring rests on that generation gap alone until a second provider exists.
- **One machine, one operator, no encryption at rest beyond the disk's.** The kernel's credentials are as safe as Tom's Mac. That is the right level for a system Tom alone runs, and the wrong level the day a second machine or a client's destruction clause appears; both are named triggers for revisiting §2 and §3.
- **Deletion is deletion.** Ending a space removes rows under the migrator role. The ledger keeps a tombstone with counts and nothing else, so there is no cryptographic proof of what was deleted.

## Dropped from the source

- **Neon as LOCKED, and hosting at all.** Nothing is hosted; the analysis is in the git history.
- **AWS Fargate as the host.** Same.
- **Telegram as the second approval adapter.** Inherited from the previous system's channel. FastHTML is second; Telegram is a possible third.
- **Pi and the Claude Agent SDK as Executor harnesses.** One PydanticAI loop for every class (§5).
- **exe.dev and Cloudflare Sandboxes.** apple/container is the one adapter (§6).
- **The OpenAI wire format, the provider allowlist per space, and data class as a provider constraint.** One provider (§4).
- **The model registry schema.** A YAML file (§4.1).
- **Envelope encryption per space and key destruction.** Deletion on request (§3).
- **The tamper-evident hash-chained export.** It defended against a compromised kernel on a machine only Tom operates.
- **The separate macOS user for the kernel.** The container VM is the boundary (§2).
- **pyright as a CI gate.** Formatting only, per the same preference as the linter rule (§1).
- **Popoto's Postgres backend as a tracked convergence.** Redis (§3.1).
- **ruff.** Formatting is black only, per Tom's standing preference.
- **The bandit and M4.** Both needed ledger volume and multi-agent objectives that do not exist.
- **"v1 keeps running throughout."** There is no v1.
- **The amendments table.** The amendments are folded directly into the architecture document.

## Sources

Checked 2026-09-19 by the source document's author. Re-check each at the milestone that depends on it. Sources for dropped vendors are in the git history.

- Claude Agent SDK billing: https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan
- Anthropic prompt caching: https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- Anthropic pricing: https://platform.claude.com/docs/en/about-claude/pricing
- apple/container: https://github.com/apple/container
