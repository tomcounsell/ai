# Tech stack

What each part of Valor is built from, and the status of every choice. The
[architecture](architecture.md) says what the system is; this document says what it runs on. Where
the RAM goes on the target machine is [machine.md](machine.md). The schema and the document model
are [data.md](data.md). The harness port is [harnesses.md](harnesses.md), and the judgement tier is
[judgement-layer.md](judgement-layer.md).

Every choice carries one of three statuses:

- **in use**: the code on this branch runs on it today.
- **chosen, not built**: decided, by Tom or by a plan constraint, and not yet
  in the code.
- **open**: a known question, with what would close it.

Each entry also names the mission item it serves or the constraint it enforces. Mission items and
constraints are the numbered list and the four constraints in [mission.md](mission.md).

## The selection rule

The scarce resource is Tom's review, not authorship. A frontier model writes any mainstream language
well; what limits the system is whether a person can read the kernel at full speed and catch what
the model got subtly wrong. So:

- the kernel is Python, the language its reviewer reads fastest;
- enforcement lives in the most boring layer available: Postgres grants and
  triggers, process groups, the sandbox profile, the network shape;
- a port is a plain Python type with one implementation, and the second
  implementation is written when a second real need arrives.

The last point is Mission item 5 applied to the stack: extraction on a demonstrated second need,
never on a first. The first two serve the constraint "Bounded authority, metered spending", because
authority a reviewer cannot read is authority nobody checked. The control stance behind enforcing
outside the model is AI Control [4].

## Summary

| Part | Choice | Status |
|---|---|---|
| Language | Python 3.14, pinned in `.python-version` | in use |
| Environments and lockfile | uv, `uv.lock` | in use |
| Runtime dependencies | `psycopg[binary]` 3, `aiohttp` | in use |
| Tests | pytest, real Postgres, a `spend` marker on every live test | in use |
| Property tests | Hypothesis: the state machine's fold; the effect ceiling down the tree next | in use |
| Schemas | frozen dataclasses in `core/` | in use; Pydantic open |
| Kernel process | `python -m core`, one process per command, no daemon | in use; a resident process open |
| Database | Postgres 18, as a document store | in use |
| Driver and schema | psycopg 3 async, hand-written SQL, one idempotent `core/schema.sql` | in use |
| Queue and coordination | Postgres only: advisory locks, `LISTEN`/`NOTIFY` | in use |
| Redis | none | in use (absent by decision) |
| Memory | popoto over Postgres | chosen, not built |
| Model gateway | in-house aiohttp proxy, Anthropic Messages and OpenAI Responses routes | in use |
| Model prices | a dated table in `core/settings.py` | in use |
| Model seats | a pinned registry in `core/settings.py`, each seat a harness and a model: frontier, reviewer, light, reviewer_openai; the judgement legs pinned beside it | in use |
| Frontier provider | Anthropic; OpenAI through the gateway's second route | in use |
| Judgement tier | Jev (`jev-1.13.0`), with the open-weight fallback Qwen3-235B-A22B Instruct 2507 on Parasail fp8 through OpenRouter behind the same port; OpenAI's Decisions API for judgements that need images | in use; the images leg chosen, not built |
| Harness | the `claude` CLI, one `claude -p` per turn; Pi (`docs/pi.md`) as the second | in use |
| Other harnesses | Codex, Pi, behind the same `TurnCommand` port | open |
| Sandbox for turns | `sandbox-exec` profile per workspace | in use |
| Sandbox for the verifier | Apple `container` (hypervisor-isolated Linux VMs) | chosen, not built |
| Which sandbox for which work | owned by [architecture.md](architecture.md); containers for turns | open |
| Workspace services | a Postgres cluster (and Redis when asked) per task, scram auth, run under a service sandbox | in use |
| Broker performers | Python classes run in the kernel process; `push_branch` and `merge` over git | in use |
| Approval surface | the `python -m core` CLI | in use |
| Approval from a phone | Telegram or a web page | open |
| Bridges | Telegram and email modules | chosen, not built; libraries open |
| Scheduling | launchd | chosen, not built |
| Secrets | kernel-held secrets (the kernel databases' passwords, the judgement keys, the OpenAI key) in the kernel key directory, durable copy of the keys in the vault; the bridges' in the macOS Keychain | the key directory in use; the Keychain chosen, not built |
| Dashboard | read-only views over `core/` read models | chosen, not built; framework open |
| Run and view the app | `look`, a headless Chromium (Playwright's `chrome-headless-shell`) in the workspace | chosen, built |
| Machine | one install per Mac, designed for one machine; MacBook Air M4, 16 GB as the target | chosen; the experiments ran on a 64 GB Mac |

## 1. Language and tooling

**Python 3.14**, pinned in `.python-version`; `requires-python = ">=3.14"` in
`pyproject.toml`. **uv** builds the environment from `uv.lock`, which holds
twenty-one packages in all. The kernel's runtime dependencies are two:
`psycopg[binary]` for Postgres and `aiohttp` for the gateway. Everything else
is the standard library. Status: **in use**. Serves the selection rule: a
two-dependency kernel is one a person can read.

`ruff` is configured for a 110-character line. pytest and Hypothesis are the dev
dependencies. Status: **in use**.

**Tests.** pytest against real Postgres, real `claude -p` turns, and real
sandbox profiles, with no mocks (`tests/README.md`). Every live test declares
`@pytest.mark.spend(usd)`, the money one run is expected to cost. Status: **in use**.
Serves the constraint "Docs describe reality": a test that exercises the real
thing is evidence, and a mocked one is narration.

**Hypothesis.** The kernel's invariants are properties: for any sequence
of calls, charges, and stops, every call is opened once and charged once,
and a stopped task opens no call. Property tests state that
directly. Status: **in use** for the state machine: every prefix of a
generated ledger folds to exactly one state (`tests/test_machine.py`), a dev
dependency. The ceiling property arrives with the objective tree, since
conservation of the effect ceiling down a tree is where a sequence of
operations can break it and one task record barely can. Serves "Bounded authority, metered spending".

**Schemas.** The kernel's records (the Brief, `TurnCommand`, the gateway
`Grant`) are frozen dataclasses and JSON payloads. Status: **in use**.
Pydantic for validated schemas at the ports is **open**; it is added if a
port's input needs validation the dataclasses cannot give without hand code.

## 2. The kernel process

`python -m core` is the composition root. Each command (`start`, `run`,
`answer`, `feedback`, `approve`, `release`, `stop`, `status`, `ledger`,
`correct`) is one short process. `run` starts the gateway on loopback, runs
the task's turns until a question, a delivery, or a stop,
and exits. Nothing in the kernel is resident between commands; all state is
in Postgres. Status: **in use**.

A resident kernel process (the bridges hand it work and it schedules turns) is **open**. It becomes
necessary when a bridge delivers requests without Tom at a terminal. Whatever form it takes, the
gateway, the broker, and the turn runner stay in one process outside every sandbox, so the stop path
never crosses a process the turn can reach. Serves "Reliable stop, recovery, and correction".

**Credential boundaries.** The kernel holds the database connection as
`valor_kernel` and performs effects through the broker. The gateway sets the
Claude credential on every Anthropic call (`claude-token` in the kernel key
directory, else Claude Code's own Keychain login) and its OpenAI key on the
OpenAI route. A turn's
environment is an allowlist (`HOME`, `USER`, `PATH`, `SHELL`, `TMPDIR`,
locale, terminal) with no tokens, no agent sockets, and only a placeholder
Claude credential. Status: **in use**.

A turn can still read the machine's Claude login from the Keychain, so it could call the provider
around the gateway with that credential: the sandbox profile limits loopback but leaves the public
internet open (section 6). The gateway is the metered path, not the only path. Tom accepted this on
2026-10-01 and it is not to be closed: the gateway is for visibility and honest metering, not a hard
wall, and turns run as the machine's user with no separate macOS account.

## 3. Persistence

**Postgres 18** (Homebrew `postgresql@18`), used as a document store: a
`documents` table of JSONB bodies keyed by `(kind, id)` and an append-only
`events` table that is the ledger. No foreign keys; a row names what it
belongs to by id inside its payload. The schema, the event types, and why
there is no relational lattice are [data.md](data.md). Status: **in use**.
Tom's decision: Postgres only, document strategies.

What the stack contributes to the ledger's integrity:

- **Grants first, triggers second.** `valor_kernel` has `SELECT` and
  `INSERT` on both tables and nothing else. A trigger rejects `UPDATE`,
  `DELETE`, and `TRUNCATE` on `events`. Only `python -m core migrate`
  connects as the owner. Serves "A ledger the system cannot edit records
  every effect".
- **Partial unique indexes** make each fold over the ledger total: one
  opening and one charge per gateway call, one row of each kind per
  effect, an approval consumed by at most one intent, one stop per task, one
  correction per number.
- **Transaction-scoped advisory locks** (`pg_advisory_xact_lock`) serialize
  the stop check and the append that opens a call, per task. They need no table privilege, so the insert-only
  grant stays minimal. Serves "Bounded authority, metered spending".
- **`LISTEN`/`NOTIFY`** carries a stop to the running turn's process the
  moment `task.stopped` commits. Serves "Stop is immediate and lossless".

**Driver.** psycopg 3, async, autocommit by default so every
`conn.transaction()` block is a real transaction. SQL is hand-written and
lives in `core/`, readable line by line. Status: **in use**.

**Schema changes.** `core/schema.sql` is idempotent and applied by
`migrate`. Events are never rewritten; a reader that meets an older payload
upcasts it ([data.md](data.md)). An additive change (a nullable column, a
plain or partial unique index) applies over a populated ledger without
rewriting a row or a table: `tests/test_migrate_history.py` shows it on a
copy of the kernel database, row by row (`xmin`) and table by table
(`pg_relation_filenode`). A migration tool is **open** and is added
when a change to `documents` needs more than an idempotent statement.

**Which cluster.** The kernel connects over the Unix socket to the Mac's
own cluster on port 5432 (`core/settings.py`, every value overridable by a
`VALOR_*` variable). Turns never reach it: the sandbox profiles deny its
port, its socket, and its data directory, and every workspace that needs a database gets a
cluster of its own (section 7). This is the fix for the demonstration's
first incident, a machine cluster that trusted loopback and could have let a
turn write ledger rows (rebuild-demonstration.md, Kernel findings 1). The
kernel cluster's authentication method and role separation are
[data.md](data.md)'s.

**Backups.** `pg_dump` to an external disk with a manifest, 30 kept, and a
restore into a scratch cluster checked against the manifest
(`core/backup.py`; [machine.md](machine.md), Backups). Status: **in use**,
rehearsed once from the command line. The nightly launchd job is in the
code (`python -m core backup --plist`) and runs once Tom loads it
([machine.md](machine.md), Backups). Serves "Reliable stop, recovery, and
correction": a ledger nobody can edit is still lost with the disk.

**Queue.** Postgres is the only store and the only queue. A second queue
next to the system of record would be a dual write. Redis is replaced by
Postgres (Tom's decision); no Valor component runs Redis. An app under test
may need Redis, in which case its workspace starts one of its own, the way
the replays did (rebuild-baseline.md, Infrastructure fixed during the
series, item 5). Status: **in use**.

**pgvector.** Not used by the kernel. Built into one replay cluster only,
because the app under test needed it. Whether memory needs it is decided
with memory. Status: **open**.

### Memory

popoto [20] over Postgres, built last. It waits on popoto's Postgres backend (the issue cited in
[20]). Until then `memory/` holds its README and the port the kernel reads through. Status:
**chosen, not built**. Serves the Evidence items "Tom's feedback, both directions": the corrections
and exemplar ledgers live in the kernel's events table from the start, and episodic memory is what
arrives last. Retrieved content carries no source class and grants nothing [7].

## 4. The model gateway

Every model call a turn makes goes through the kernel's gateway: an aiohttp
server on `127.0.0.1` at an OS-assigned port, with an Anthropic Messages
route and an OpenAI Responses route under `openai/` (`core/gateway.py`). A
turn is pointed at it through `ANTHROPIC_BASE_URL` with a per-turn token in
the path, so Claude Code's own side calls and subagents are metered too.
Status: **in use**.

What it does per call (price, open, forward, charge) is
[architecture.md](architecture.md)'s (Metered spending, in [metered-spending.md](metered-spending.md)). The stack-specific parts:
input is estimated at two bytes per token (`settings.bytes_per_token`) for the worst-case estimate, the opening runs under the task's advisory lock, and the response streams back unchanged while the gateway reads the provider's usage.

Token counting and model lists pass unmetered; any other path, and on the
OpenAI route any method a listed path does not take, is a 403. `revoke` retires the task's tokens and
cancels its in-flight calls at once; the stop path calls it before killing
the turn's process group.

The worst-case estimate (the charge when usage goes unreported) comes from each request body's
`max_tokens`. Valor sets no output limit of its own on a workspace turn; a project spec's
`max_output_tokens` sets Claude Code's.

The gateway sets no connect or read timeout on the provider. It runs each
metered call behind a shield and cancels the connection handler when the
client leaves, so a call no client waits for is cut and charged: when the
client disconnects, when the turn exits (`cut`, before the drain), and on
`revoke`. Only a call registered as in flight is cancelled, so an opening or
a charge is never torn. A login is used until its stated expiry.

Serves "Bounded authority, metered spending" and "Stop is immediate and lossless". Evidence: across
69 calls in the demonstration the gateway's charge and Claude Code's own cost report agreed to
$0.000024 of $2.97 (rebuild-demonstration.md, Money). The baseline ran twelve more tasks and a rerun
through the same path for $18.37 (rebuild-baseline.md, Caveats).

**Prices.** A table of US dollars per million tokens per model, input,
output, cache write, and cache read, from the provider's public pricing
page. An Anthropic dated id matches its undated entry, longest match first; an OpenAI id matches only exactly or with a `-YYYY-MM-DD` suffix, since `-pro` variants cost more; any other id is unpriced. Each per-million charge is rounded up to a whole micro-dollar in integers, and a turn's spend is the numeric sum of its charges. Each price carries the day it was checked against that page, and every
`gateway.charged` row records it as `price_checked`, so a price change
upstream is visible in the ledger. Status: **in use**.

**Providers.** Anthropic, and OpenAI's Responses API through its own gateway
route. Status: **in use**. Another arrives when Tom asks, metered first.

### Model seats

Model choice is data the kernel reads, not code: a small registry of pinned
model ids with their prices and the seats they fill (`core/settings.py`,
`SEATS`): each seat is a harness and a pinned id. `python -m core start
--model` takes a seat name or a model id (run on Claude Code), and
`--harness` overrides the harness; the Brief records both. Status: **in use** for frontier, reviewer, light, and
reviewer_openai; the judgement legs are pinned beside the seats.

- **Frontier**: the newest model, for turns.
- **Reviewer**: Opus-class, never cheaper (Tom, 2026-10-01): the
  frontier model itself in a fresh blind session, or an Opus-class model
  from another vendor through another harness. What a weaker judge loses is
  [18]; the baseline's Sonnet reviewer accepted #191 at fidelity 1. A
  model's preference for its own output [16] is countered by blindness when
  the reviewer shares the builder's model.
- **Reviewer_openai**: the reviewer's second seat, GPT-6.1 on Pi through
  the gateway's OpenAI route ([pi.md](pi.md)).
- **Judgement**: the Jev-class seat (section 5).

Ids are pinned, never floating aliases, because a ledger row has to describe a fixed thing. Editing
the registry is a change inside the trust boundary, reviewed like kernel code: whoever can rewrite
the reviewer's seat can defeat verification without touching an agent. Serves "Bounded authority,
metered spending" and the Evidence item "Independent checks".

Adopting a new frontier model is one edit, the same day. The system does not out-evaluate the labs
on capability. A cheaper capable model does not lower any ceiling; it buys better outcomes on the
same terms (the constraint "Bounded authority, metered spending").

## 5. The judgement tier

A hosted Jev-class model handles every decision that is not authority:
classifying a request, routing, triage, cheap checks, the blind verifier's
governance boolean. An open-weight equivalent sits behind the same port as
the fallback. A low-confidence call takes its judgement task's abstain
route, which reaches Tom only through a step he would see anyway. A
classifier decides what a thing is; it never decides what a thing may do. The taxonomy, the port, and
confidence gating are [judgement-layer.md](judgement-layer.md). Status:
**in use** for the request judge; the breadth and governance calls are
built, and the runners that call them are milestone 1.4's.

What the stack fixes:

- **Hosted, not resident.** No local classifier model of any size worth
  running fits beside Postgres, a container runtime, a `claude -p` turn, and
  the bridges in 16 GB. Any design that assumes a resident local model is
  wrong for this machine.
- **Metered like every other call.** Judgement calls are opened and
  charged on the task, so their price shows in the same
  ledger: in the kernel process through `core/spending.py`, the gateway's
  rows with `route: judgement`, no HTTP route. Jev's estimate allows for
  the prompt it bills around the request (1.25 times bytes / 2 plus a fixed
  margin, sized from the calibration calls)
  ([judgement-layer.md](judgement-layer.md)).
- **First use: the request judge.** Each incoming request is read by the
  judgement tier before the first turn. An underspecified request (a
  one-line ask, an ask that leans on an example, an ask naming existing UI
  without scope) goes to a clarify turn; a precise one goes straight to
  build. This is a guard Tom granted, ledgered with its incidents and a
  ninety-day expiry. Incidents: the demonstration (psyoptimal #894, two PM
  rounds for three decisions; rebuild-demonstration.md, Attention log) and
  the replay baseline (popoto #191 and #188, where clarifying raised fidelity
  and bare building needed a PM round; rebuild-baseline.md, What this says
  about the old SDLC stages). Serves Mission items 3 and 6. The
  demonstration estimated the judge's cost at well under $0.05 a request
  against $1.40 to $1.80 of frontier spend it would have saved; an estimate,
  to be measured by replay. Calibration measured about $0.000033 (Jev) and
  $0.00023 (fallback) per call.

**Vendor.** Jev is the primary leg, including for client request text
(Tom, 2026-10-01). Jev takes no images, so a judgement that needs one goes
to OpenAI's Decisions API behind the same port (not built). The fallback is
hosted, never resident on this machine. Jev's base model is not published,
so it cannot be the same model on a second provider (Tom's ask,
2026-10-01): it is Qwen3-235B-A22B Instruct 2507, open weights and no
reasoning tokens, on Parasail at fp8 through OpenRouter with provider
fallback off ([judgement-layer.md](judgement-layer.md),
[machine.md](machine.md)).

| Model (as pinned) | Input $/Mtok | Output $/Mtok | Checked | Source |
|---|---|---|---|---|
| `jev-1.13.0` | 0.042 | 0 | 2026-10-02 | https://docs.typesafe.ai/models |
| `qwen/qwen3-235b-a22b-2507@parasail/fp8` | 0.14 | 0.80 | 2026-10-02 | OpenRouter's endpoints list for the model, Parasail fp8 |

The table is `JUDGEMENT_PRICES` in `core/settings.py`, kept apart from the
gateway's Anthropic prices.

## 6. Harness and sandbox

### Harness: the `claude` CLI

A turn is one `claude -p` subprocess (`harnesses/claude_code.py`). A
workspace turn keeps Claude Code's own system prompt and tools, appends the
dispatched text (persona, then Brief) re-rendered every turn
(`--system-prompt-snapshot off`), resumes the task's session, and runs with:

- `--safe-mode` and `--strict-mcp-config`: no hooks, skills, plugins,
  `CLAUDE.md`, or MCP servers from this machine;
- `--permission-mode bypassPermissions`: the kernel bounds the turn, not a
  permission prompt nobody is there to answer;
- `WebFetch` and `WebSearch` disallowed;
- `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`, because a `-p` turn that stops
  kills what it left running in the background
  (rebuild-baseline.md, Caveats, pso-a);
- the prompt after `--`, because a request starting with `-` was read as an
  option (rebuild-baseline.md, Infrastructure fixed during the series,
  item 3);
- `GIT_CONFIG_GLOBAL` and `GH_CONFIG_DIR` pointed at configs with no
  credential helper and no token.

Status: **in use**. Serves Mission item 1 (a turn can inspect, edit, test,
and commit without Tom) inside "Bounded authority, metered spending". The port is
`TurnCommand` in `core/runs.py`: argv, environment, working directory, and a
result parser. The kernel never knows which harness it runs. Session resume,
resume cost, and the `.valor/` signal files are
[harnesses.md](harnesses.md).

**Harness version.** The `claude` CLI is installed per machine and not
pinned by the repository; the version it runs as is recorded in
`turn.started`, so a replay compared across months says which release
produced each turn. Pi is pinned in `harnesses/pi.py`. Status: **in use**.

**Other harnesses.** Pi runs behind the same port on GPT-6.1 through the
gateway's OpenAI route ([pi.md](pi.md)): **in use**. Codex is **open**.

### Sandbox: what runs today

Every workspace turn runs under a **`sandbox-exec`** profile generated per workspace
(`core/workspace.py`; `scripts/demo_workspace.sh` for the demonstration). Status: **in use**.

The profile's rules (files, loopback, binding, the `valor.turn.<turn id>` mark the reaper uses) are
specified in [harnesses.md](harnesses.md) (The turn sandbox, Reaping what a turn leaves). Serves
"Bounded authority, metered spending" and "Stop is immediate and lossless".

What `sandbox-exec` gives: no RAM overhead, the Mac's native toolchains
(Homebrew Postgres, uv, Xcode), and a profile a person can read in a minute.
What it does not give: the turn runs as the machine's user, so a
deliberate Keychain read through the `security` tool is not fenced, and the
public internet is reachable so package installs work
(rebuild-demonstration.md, Setup, Isolation). Both are accepted (Tom,
2026-10-01). Apple's man page marks
`sandbox-exec` deprecated; whether that matters on the timescale of this
system is a gap to check, not a known risk.

### Sandbox: Apple containers

The plan's constraint names Apple containers (`container`, hypervisor
isolated Linux VMs on Apple silicon) for sandboxes. Each container is its
own lightweight VM, sharing no kernel, user, or filesystem with the process
that holds authority. Measured on Apple silicon: boot about one second,
`exec` 65 to 110 ms, a host-only network set from outside the VM that
reaches the Mac and nothing else, `kill` confirmed by a probe from outside
the VM, and the bind-mounted disk retained after a kill in 40 of 40 trials.
Building images needs Rosetta even for arm64. Status: **chosen, not
built**; the `container` CLI is not installed on the machine the
demonstration ran on.

What containers give that `sandbox-exec` does not: a separate user and filesystem, so a Keychain
read is impossible rather than unfenced; a network whose shape is set from outside; a fresh VM per
verification, so the blind verifier never runs in the executor's environment [4]. What they cost:
RAM per running VM, Linux toolchains only, and dependencies baked into an image from a lockfile,
since a host-only network reaches no package registry. Each of those costs lands on the 16 GB
machine ([machine.md](machine.md)).

### Which sandbox for which work

[architecture.md](architecture.md) owns the split: turns run under
`sandbox-exec`, which runs the app's own toolchain natively at no memory
cost, and the blind verifier re-executes in a fresh container, which must
never share the executor's environment [4]. Moving turns on a Linux
toolchain (Python, Node, Django with Postgres) into containers is
**open**; work that needs macOS-native tools (Xcode, iOS builds) stays
under `sandbox-exec`, since no Linux VM can run it. What closes it: one
replay item run in a container end to end, with its RAM measured beside a
turn on the 16 GB machine. Serves "Bounded authority, metered spending": a sandbox
is where the effect ceiling meets the operating system, since credentials
and reachable hosts are the effect class in practice [11].

## 7. Workspaces

The kernel provisions a task's workspace from a project spec
(`core/workspace.py`, `projects/`), never the turn:

- a clone of the repository holding only the history up to the base commit;
- a local bare repository as the clone's only remote, which the turn cannot
  write and the broker pushes to, and the kernel mirror the merge reads;
- when the app needs them, a Postgres cluster of the task's own on its own
  loopback port with `scram-sha-256` authentication and an `app` role that
  may create databases and nothing more (a superuser login would be a way
  out of the sandbox), and a Redis of its own;
- an empty gh config and a git config with no credential helper.

Status: **in use** (`python -m core start --project`). Serves Mission item 1 (the turn tests actual
use against a real database) inside "Bounded authority, metered spending". How a workspace is
provisioned and torn down as part of a task is [architecture.md](architecture.md); its memory cost
is [machine.md](machine.md).

**Running and viewing the app.** No turn in the demonstration or the baseline
looked at a page in a browser, including the two UI items
(rebuild-demonstration.md, What was delivered; rebuild-baseline.md, Browser
use). `look`, a headless Chromium in the workspace, reached on the dev ports
the profile already allows, is a capability for Mission item 1 ("testing
actual use"), not a gate. It takes about 372 MB at peak (machine.md);
[browser.md](browser.md) specifies it.

## 8. The broker's performers

A performer is a Python class with an `action_type`, a fixed
`effect_class`, `perform`, and `lookup` by idempotency key. It runs in the
kernel process, outside the turn's sandbox, which is what lets it write a
remote the turn cannot. Status: **in use**.

- **`push_branch`** (`act`, `tools/push_branch.py`): pushes one commit to
  one branch of the workspace's origin over git, never with `--force`,
  tags, submodules, or a signature. Git is the Command Line Tools' install
  (`VALOR_GIT` overrides), checked before each call to be root's alone and
  a real install, never Apple's `/usr/bin/git` shim (`core/binaries.py`),
  with a system-only PATH, no `DYLD_*` or global config, one deadline per
  perform (git in its own process group, killed whole on timeout), replace
  refs, grafts, commit-graph, and multi-pack-index ignored, and hooks,
  helpers, pagers, and transports pinned off, and refuses a workspace whose
  own config names a program, redirects a push, sets any `push.*` or
  `http.*`, or includes other config, because that config is the turn's to write (`core/git.py`). `lookup`
  answers present (the branch holds the commit, at its tip or below),
  absent, or unknown; `broker.reconcile` uses it to settle a dangling merge
  intent. It pushes to the task's `push_url` (else the origin URL recorded
  at start) and refuses the task's target branch.
- **`merge`** (`act`, `tools/push_branch.py`): the kernel's push of a
  passed candidate onto the target branch, from the kernel mirror when the
  task has one, released only when the merge predicate holds; no turn is
  offered it.
- **`WorkspaceWrite`** (`propose`) and **`OutboxAppend`** (`act`)
  (`tests/performers.py`), for the tests only: a file in the workspace, and
  a local outbox that stands where a bridge's send will.

Serves "Bounded authority, metered spending": a sandbox holds no credential capable of an effect
outside it, and every effect that leaves is a typed action [11]. The effect protocol and approvals
are [architecture.md](architecture.md).

## 9. Surfaces

**Approval surface.** The `python -m core` CLI: `pending`, `approve
EFFECT --note`, `release EFFECT`, `answer`, `feedback --by --role-played`,
`status`. Status: **in use**. Serves "every `act` needs Tom, per action" and
Mission item 6, since `status` shows the attention log.

**Approving from a phone.** Tom approves away from a terminal through a
bridge or a small web page. Status: **open**. Whether an `act` approval also
needs a passkey signature over the exact payload, verified by the broker, is
**open**; it matters the day an approval arrives over a channel that a
session hijack could forge.

**Bridges.** Telegram and email, each a self-contained module in `bridges/`
conforming to one port in `core/`, with sending as an `act` through the
broker. Status: **chosen, not built** in this tree. Tom's call is that the
existing bridges may survive close to unchanged; their libraries (Telethon
for Telegram, the standard library's `imaplib` and `smtplib` for email) are
the candidates, **open** until the bridges are rebuilt. What each bridge does
is [bridges/telegram.md](bridges/telegram.md) and
[bridges/email.md](bridges/email.md).

**Dashboard.** Read-only views over `core/` read models: tasks, spend, the
ledger, pending approvals, the attention log (`ui/README.md`). Status:
**chosen, not built**; the web framework is **open**.

## 10. Scheduling, secrets, telemetry

**Scheduling.** launchd, one plist per routine, each routine an
objective and never a bare script ([routines.md](routines.md)). Status:
**chosen, not built**. Serves "Bounded authority, metered spending": scheduled work
is metered like any other task.

**Secrets.** Kernel-held secrets live in the kernel key directory, which
both turn sandbox profiles deny: the kernel databases' passwords in a libpq
password file, the judgement keys in `judgement-keys` and the OpenAI key in `openai-key`
beside it, copied from the vault `.env` by `python -m core judgement-keys` and
`python -m core openai-key` ([machine.md](machine.md),
Keychain, for why not the Keychain). Status: **in use**. The bridges'
secrets go in the macOS Keychain: **chosen, not built**. Nothing secret is
in the repository; the frontier credential is Claude Code's, and the
workspace database password is a fixed test value.

**Telemetry.** None beyond the ledger. The events table is the only record
with evidentiary standing; `python -m core status` and `ledger` read it.
Status: **in use**. An operational telemetry stack (latency, error rates) is
**open** and never becomes the audit record.

## 11. The 16 GB M4 Air

Everything runs Mac native, one install per machine, with a MacBook Air M4 and 16 GB of RAM as the
target (Tom's decision). Valor's four Macs each run their own install for the projects they own,
with nothing shared between them. The demonstration and the baseline ran on a 64 GB Mac, so no
memory figure from them carries over; the RAM plan per component is [machine.md](machine.md). What
the target machine fixes in the stack:

- **One `claude -p` at a time.** The baseline ran replays concurrently, with
  slot locks and a Redis server and test database per run
  (rebuild-baseline.md, Infrastructure fixed during the series, item 5).
  That is a 64 GB practice and does not carry over.
- **One container runtime.** Apple's, and only while a sandbox is running.
- **One Postgres server for the kernel**, resident. Workspace clusters start
  with their task and stop with it.
- **No resident model.** Judgement is hosted (section 5). The open-weight
  fallback is hosted too (Parasail, through OpenRouter).
- **Bridges resident, everything else on demand.** The bridges are the only
  components that must be up when Tom is not at the machine. Routines start
  under launchd and exit.
- **No Linux assumptions.** launchd, not cron or systemd; Keychain, not a
  secrets daemon; `sandbox-exec` and Apple containers, not Docker.

## Limits

- **The turn holds the provider credential** (section 2), and the public
  internet is open to it (section 6). Metering covers every call that goes
  through the gateway; a turn that deliberately went around it would be
  visible only in the provider's invoice. An accepted risk (Tom,
  2026-10-01).
- **`sandbox-exec` runs as the machine's user.** It fences paths and
  loopback ports; it does not fence the Keychain or the internet.
- **One provider today.** Reviewer independence rests on blindness until
  another vendor's Opus-class model is metered [16].
- **One machine per install, no encryption at rest beyond the disk's.** Each
  kernel database is as safe as the Mac it runs on. A client's destruction
  clause reopens this.
- **The gateway is a single point of failure and a single point of control.**
  Those are the same property.

## Not in the stack

- **Redis.** Postgres is the only store and queue (Tom's decision).
- **A workflow engine** (Temporal, DBOS, or similar). The task record and the
  ledger are the durable workflow state; a second state machine is a second
  source of truth.
- **An in-house agent loop.** The harness is the `claude` CLI behind the
  `TurnCommand` port; a different loop is a second harness on a second need.
- **Hosted Postgres, hosted sandboxes, cloud hosting.** Everything runs on
  the Mac until the Mac is measured to be insufficient.
- **A resident local model.** It does not fit in 16 GB beside the rest.
- **Docker.** Apple's container runtime is the one Linux runtime.
