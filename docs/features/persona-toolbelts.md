# Persona Toolbelts

Each persona the headless session runner drives (PM, Dev, Teammate) has a
committed manifest naming the tools it ships with. `config/toolbelts/{pm,dev,
teammate}.toml` is the version-pinned declaration; `agent/session_runner/
belt_resolver.py` compiles it into Claude CLI flags at turn start.

The feature ships **dark**: `TOOLBELTS_ENFORCE` defaults to `False`, so the
manifests, the resolver, and the measurement half are all in place while every
`claude -p` invocation still runs on the ambient tool surface. See
[Shipping dark](#shipping-dark) for why the order matters.

## What belts are, and what they are not

Belts give two things: ergonomics (one persona resolves the same tool surface
on every machine in the fleet, so a session's capabilities stop depending on
which host picked it up) and reproducibility (the manifest is a file you can
read cold, diff, and hand to a client).

Belts are **not a trust boundary**. The Dev persona keeps open Bash by
decision (#3081 No-Gos, recorded on the issue). Anything reachable from a
shell stays reachable regardless of what the manifest lists, so a narrower
belt narrows what the model is *offered*, not what a determined turn can
*do*.

**Belt absence is the sole enforcement layer headless.** Headless turns run
`--permission-mode bypassPermissions` (plan #2000 Task 2.2, reaffirmed by
#3081 Open Question 2, pinned by
`tests/unit/test_sdk_permissions.py::test_default_permission_mode_is_bypass`),
because a headless turn has nobody to answer a permission prompt. There is no
permission-mode safety net behind the belt: whatever a belt leaves in the
manifest runs without further gating.

## Manifest format

```toml
belt_version = 1
claude_cli_validated = "2.1.236"

[builtin]
tools = "default"

[mcp_servers.memory]
command = "/bin/sh"
args = ["-c", 'PYTHONPATH="$HOME/src/ai" exec python3 -m mcp_servers.memory_server']

[permissions]
allowed = []
disallowed = []
```

| Key | Meaning |
|-----|---------|
| `belt_version` | Positive integer, bumped on every edit. The discrete comparable value the resolver's version check compares against (#3081 Open Question 1). |
| `claude_cli_validated` | Optional string naming the Claude CLI version this belt's flag semantics were checked against. Currently `"2.1.236"` on all three manifests. |
| `[builtin].tools` | Either the string `"default"` (the CLI's full built-in set) or an explicit list of tool names. |
| `[mcp_servers.<name>]` | One table per MCP server: `command` (required, non-empty string), plus optional `args`, `env`, `type`. |
| `[permissions].allowed` / `.disallowed` | Lists of tool patterns, compiled to `--allowedTools` / `--disallowedTools` when non-empty. |

Unknown keys fail closed at every level: top-level, MCP server spec, and
permissions. A misspelled section that silently widens a belt is the exact
failure mode fail-closed validation exists to catch.

All three committed manifests currently declare `tools = "default"`, the
`memory` and `byob` MCP servers, and empty permission lists. That is a
faithful snapshot of each persona's actual ambient surface, so activating
enforcement changes nothing about what a session can reach. Narrowing is a
later, deliberate edit ranked by the baseline numbers, with a
`belt_version` bump.

### Server paths are resolved at launch, not at resolution

Both MCP entries launch through `/bin/sh -c` so the shell expands `$HOME` when
the server starts. This keeps the manifests byte-identical across hosts while
still reproducing what `/update` installs into `~/.claude.json`:

- **memory** — `scripts/update/mcp_memory.py::_expected_entry` writes the
  absolute repo root as `PYTHONPATH`. A session's working directory is usually
  some other project's checkout (`projects.json` maps a dozen repos), which holds
  no `mcp_servers` package, so a cwd-relative path would drop
  `mcp__memory__*` for most sessions.
- **byob** — `scripts/update/mcp_byob.py::_resolve_tsx_bin` accepts two layouts
  that `bun install` may produce, preferring the workspace-root
  `~/.byob/node_modules/.bin/tsx` and falling back to the package-local
  `~/.byob/packages/mcp-server/node_modules/.bin/tsx`. The belt's launch script
  probes the same two in the same order.

`tests/unit/test_belt_resolver.py::TestManifestsMatchInstalledSurface` pins both
against the installer modules.

### The `# why` convention

Every entry in a manifest carries a `# why:` comment above it explaining what
the entry buys the persona. The manifest is meant to be read cold by someone
who did not write it, including a client asking what Valor's Dev runs with, so
an entry without a stated reason is an entry nobody can evaluate for removal.
The header block of each file carries the same treatment for the file as a
whole, including the scope statement above.

## Resolution

`resolve_belt(persona, *, expected_belt_version=None, resumed_history_tools=None,
toolbelts_dir=None)` returns a frozen `ResolvedBelt` carrying the persona, the
manifest's `belt_version`, and the compiled `flags` tuple.

Resolution is pure. It reads the manifest bytes and nothing else: no
environment, no host inventory probe, no network. The same manifest yields
byte-identical flags in a fixed order on every machine, which is what makes
"the PM has the same belt everywhere" a checkable claim rather than an
aspiration.

### Fail-closed cases

`BeltResolutionError` carries a stable machine-readable `reason` and a message
that always contains the word `unresolvable`:

| `reason` | Trigger |
|----------|---------|
| `unknown_persona` | `persona` is outside `KNOWN_PERSONAS` (`pm`, `dev`, `teammate`). The message also contains `unknown persona`. |
| `missing_manifest` | No `{persona}.toml` in the manifest directory, or the read failed. |
| `malformed_manifest` | TOML that does not parse, or a manifest whose shape violates the schema above. |
| `version_mismatch` | `expected_belt_version` was supplied and the manifest declares a different one. |

Validation runs in full before compilation begins, so a partially valid
manifest can never half-compile into a partially applied belt.

The error propagates out of `get_response_via_harness` **before any subprocess
spawns**. A turn whose belt will not resolve costs zero `claude -p`
invocations, and the runner's terminal exception handling puts the reason on
`summary.exit_message`, so the refusal reaches the session output path rather
than only the logs.

### Flag composition and argv position

Compiled flags appear in a canonical order:

1. `--tools=<value>` where value is `default` or a comma-joined list.
2. `--mcp-config=<json>` carrying `{"mcpServers": {...}}`, serialized with
   sorted keys and compact separators so the JSON is byte-deterministic.
3. `--strict-mcp-config`.
4. `--allowedTools=<csv>`, present only when `[permissions].allowed` is non-empty.
5. `--disallowedTools=<csv>`, present only when `[permissions].disallowed` is non-empty.

The MCP config is emitted even when a manifest declares zero servers. Paired
with `--strict-mcp-config`, an empty config is what keeps host-local servers
in `~/.claude.json` out of an enforced turn: the ambient extras one machine
accumulated by hand are precisely the drift belts exist to remove.

Every value-carrying flag is a single `--flag=value` argv element. The CLI's `--tools`,
`--mcp-config`, `--allowedTools`, and `--disallowedTools` options are variadic
(`<tools...>`), so a space-separated form swallows the positional message the
harness appends last. Observed live on claude 2.1.236: `--tools Bash,Read
"msg"` fails with "Input must be provided".

Composition happens in `agent/session_runner/harness/claude.py`, in the same
run of `harness_cmd.extend(...)` calls that handle `--model` and `--settings`.
Belt flags land after `--model` and before `--settings`, which puts them ahead
of the positional message and any `--resume <uuid>` in the final argv
assembly. Resolution is gated on `role` being set, so role-driven session turns
carry belts while the drafter, probe, and drafter-review spawns do not.

### Across a resume boundary

`resumed_history_tools` lets a caller pass the tool names a resumed
transcript's `tool_use` blocks reference. The parameter is observational: when
the belt is narrower than the history, `resolve_belt` logs the narrowing at
INFO and keeps the narrow belt.

That choice follows the CLI's observed behavior, verified live on claude
2.1.236: a session whose replayed history contained a Bash `tool_use` block,
resumed with `--tools=Read`, exited 0 with `is_error: false` and the correct
result. Resume degrades gracefully, so the belt stays authoritative for the
turn that is running.

### The flag-off contract

With `TOOLBELTS_ENFORCE` off, `resolve_belt` is never called and no belt flag
enters `harness_cmd`. The argv is byte-identical to what the harness built
before belts existed, which is what makes the dark period a clean measurement
window rather than a partially-changed one.

The turn-start enforce-state stamp described below still runs on every role
turn while the flag is off, so the fleet's activation window is observable
before, during, and after the flip.

## Missing-capability escalation

A belt that is cut too tight shows up as an agent that cannot do its job.
Rather than infer that from failure patterns, the system asks for it directly.

Each role priming skill (`.claude/commands/roles/prime-{pm,dev,teammate}-role.md`)
instructs the agent to state a missing tool or capability plainly on its own
line starting with exactly `[missing-capability]`, for example
`[missing-capability] gh CLI unavailable, cannot query the PR`.

`SessionRunner._route_turn` calls `forward_capability_escalations` on each
turn's text before classification. For every line carrying the marker that this
run has not already forwarded, it records a `belt_escalation` telemetry event
and delivers the line on the open-question channel. The set of forwarded lines
lives on the runner instance, so a gap the agent restates every turn rides the
channel once per run.

The path is non-blocking by contract: it never changes the routing decision and
never raises. Telemetry recording and delivery are each individually
best-effort, so a failure in one still lets the other through.

`config/settings.py` carries the constants for the rollback gate that will
consume these events: `escalation_ceiling_multiplier` (default 2, env
`ESCALATION_CEILING_MULTIPLIER`) and `escalation_ceiling_floor` (default 3, env
`ESCALATION_CEILING_FLOOR`). Escalations per persona per week above
`max(multiplier × belt-relevant denial baseline, floor)` for two consecutive
weeks flips enforcement back off. The floor keeps a near-zero baseline, which
is plausible for PM and Dev under `bypassPermissions`, from tripping the gate
on the first escalation line. The gate itself is armed at activation time and
is not built yet (plan task 4).

## Fleet activation observability

`TOOLBELTS_ENFORCE` propagates per machine through git sync and `/update`, not
atomically. Mid-window, the same session can resolve a different tool surface
depending on which host takes its next turn.

`check_and_stamp_belt_state(session_id, *, enforce, belt_version)` runs at turn
start on every role turn. It reads the newest `AgentSession` for the session id,
compares the prior turn's `belt_enforce_state` stamp against the state this host
resolved, and on a mismatch emits a WARNING-level `belt_enforce_skew` telemetry
event carrying the prior and current enforce-states, both belt versions, and the
hostname. It then stamps the current state and belt version through a narrow ORM
save, and it is the sole writer of those two fields.

A `None` prior stamp is a pre-belt legacy read, never a mismatch. The whole
function is fail-quiet: belt observability never crashes a turn.

Read the events across the fleet with `python -m tools.belt_skew_report`
(documented in [`docs/tools-reference.md`](../tools-reference.md#toolbelt-skew-report-toolsbelt_skew_report)).
`read_session_timeline` answers one session at a time, which is the wrong shape
for the question an operator asks during an activation window: is the fleet
converged, and which host is behind.

The event type string is pinned in two places that must agree,
`BELT_SKEW_EVENT_TYPE` in the resolver and `BELT_ENFORCE_SKEW_EVENT` in
`agent/session_telemetry.py`, with a test holding them equal.

## Instrumentation

The measurement half answers "which tools are eating the context window" so
belt narrowing can be ranked by what tools actually cost.

### Per-tool cost attribution

`claude -p --output-format stream-json` reports usage per assistant message,
never per tool call. `agent/tool_cost_attribution.py` derives a per-tool ledger
from that stream under the method tag `assistant-usage-delta/v1`:

- The prompt prefix size at an assistant message is `input_tokens +
  cache_creation_input_tokens + cache_read_input_tokens`. Summing all three
  keeps the number continuous across a caching boundary, where any single field
  jumps.
- The growth of that prefix between consecutive assistant messages is
  attributed to the tools the previous message invoked, split evenly across
  them. A shrinking prefix (compaction, cache eviction) clamps to zero.
- The `output_tokens` of the message issuing the `tool_use` blocks is
  attributed to those same tools, as the cost of asking.

These numbers are a **ranking aid, not billing**. Model prose between tool
calls and compaction both leak into the delta. The bar the plan sets is
"consistent enough to rank tools", and a tool whose results dominate the prefix
outranks one whose results are small, run after run. `total_cost_usd` on the
`result` event remains the only authority on spend.

Wiring: `_run_harness_subprocess` folds every parsed event into a
`ToolCostAttributor` before type branching, so neither the `result` event's
`break` nor the `assistant` branch's `continue` can skip a sample. One snapshot
per subprocess rides an `on_tool_cost` callback;
`get_response_via_harness` merges the snapshots across the primary invocation
and up to two fallbacks, emits a `tool_cost` telemetry event, and accumulates
the result onto `AgentSession.tool_cost_json` in the same narrow
`update_fields` save that already writes `turn_count` and `tool_call_count`.

`session_tool_cost_summary(session)` reads that field and returns a compact
aggregate (method, tool calls, attributed tokens, top three tools).
`models/session_lifecycle.py` stamps it as the `tool_cost` key on every
`status_transition` telemetry event, so context spend is readable per stage
without replaying a session timeline. It returns `None` on a session with no
recorded attribution, which keeps an absence of measurement distinguishable
from a measured zero.

`tool_cost_json` is nullable with a `None` default. Popoto is schema-on-read,
so records written before this field existed read back as "no attribution
recorded" with no migration.

Every entry point in the module swallows its own exceptions and increments
`dropped_samples`, and that counter rides into the telemetry event so a
silently degraded stream looks degraded rather than quiet.

### Denial taxonomy

`record_pre_tool_use_denial` (`agent/session_telemetry.py`) writes a
`pre_tool_use_denial` event carrying a `cause`, and optionally the tool name,
the deny reason, and the session's `tool_call_count` / `total_cost_usd` at deny
time. It never raises.

Three causes are instrumented today:

| Cause | Emitted by |
|-------|-----------|
| `tool_budget` | `agent/tool_budget.py::record_budget_trip`, before the per-session dedup gate so the count matches `denied_calls`. Covers both PreToolUse surfaces, since both actuate their budget deny through that function. |
| `sensitive_path` | `agent/hooks/pre_tool_use.py::_record_denial`, on the Write/Edit sensitive-path branch and the two Bash branches (redirect, and `cp`/`mv`/`tee`). |
| `teammate_write` | The same `_record_denial`, on the teammate write-restriction branch. |

The taps are telemetry only. Each is called alongside a deny that was already
decided, in a position where it cannot change one, and every tap swallows its
own errors. Deny decisions, block messages, and exit shapes are exactly what
they were.

### Belt-relevant denials

`tools/belt_baseline.py` splits denial counts into belt-relevant and
belt-irrelevant. `BELT_IRRELEVANT_CAUSES` holds exactly two entries:

- `sensitive_path`, because a belt chooses which tools are offered and no belt
  makes `.env` writable. A narrower belt cannot move this count.
- `teammate_write`, temporarily. Lane B's `valor-docs-write` wrapper retires
  that hook branch, at which point the restriction becomes belt-expressible and
  this entry comes out, leaving `sensitive_path` alone.

`denials_belt_relevant` counts everything else, `tool_budget` included: a
narrower belt means fewer tool calls, hence fewer per-session spend-cap trips,
so budget denials move with belt width and are exactly the signal being
measured. An unrecognized cause lands in this bucket too, so a cause added
later is in the baseline until someone amends the plan to exclude it.

That field is what the activation-time escalation ceiling is sized from, which
is why the set stays this narrow. Each additional exclusion subtracts signal
the ceiling exists to watch, and enough of them drive the denominator to a
structural zero. `tests/unit/test_belt_reports.py` pins the membership.

### Reports

`python -m tools.belt_baseline` publishes the pre-activation measurement, and
`python -m tools.belt_skew_report` gives the cross-session skew view. Flags,
exit codes, and usage live in
[`docs/tools-reference.md`](../tools-reference.md#toolbelt-baseline-toolsbelt_baseline).

Both read only the per-session JSONL under `logs/session_telemetry/`, never
Redis or GitHub, so the stream that produced a number can always reproduce it.
The baseline reports an empty window as an absence of measurement with its own
exit code rather than printing zeros: a zero baseline would make the −40%
context target trivially satisfiable.

## Shipping dark

`TOOLBELTS_ENFORCE` is `False` in `config/settings.py`, and that committed
default is the fleet-wide state.

The targets on this work are context spent on tool surface per merged PR down
40% and tool-call turns per merged PR down 25%, both against a measured
baseline. The baseline comes from the instrumentation above, which needs a
window of real merged PRs to accumulate. Enforcing belts before that window
closes would leave the targets with no pre-change measurement to compare
against, so both halves of the feature merge early and the flip waits.

Activation is its own commit (plan task 4), gated on the baseline report being
published. Keeping the flip isolated also makes it the obvious suspect if a
live session stalls on a too-tight belt. The env override on the flag exists as
break-glass rollback, not as a per-machine activation switch: the fleet
activates together through git sync and `/update`.

## Not built yet

The design above assumes work that follows in later lanes. Naming it here keeps
the shipped surface honest:

- **Activation** (plan task 4): flipping `TOOLBELTS_ENFORCE`, arming the
  escalation rollback gate that consumes `ESCALATION_CEILING_*`, and the
  `/update` doctor check for CLI belt-flag support.
- **AXI wrappers and `valor-docs-write`** (plan task 5, Lane B): the wrappers
  that let the Teammate write restriction move out of the PreToolUse hook and
  into the belt as tool absence. Until then the restriction stays hook-enforced
  and `teammate_write` stays in `BELT_IRRELEVANT_CAUSES`, and
  `config/toolbelts/teammate.toml` is a faithful snapshot of the offered
  surface rather than the effective one.
- **Hook retirement**: the deny branches those wrappers replace.
- **Post-rollout re-measurement** (plan task 7): re-running the baseline with
  the same window flags and publishing the before/after.

## Key Files

| File | Purpose |
|------|---------|
| `config/toolbelts/{pm,dev,teammate}.toml` | The committed manifests |
| `agent/session_runner/belt_resolver.py` | `resolve_belt`, `BeltResolutionError`, `check_and_stamp_belt_state`, `forward_capability_escalations` |
| `agent/session_runner/harness/claude.py` | Belt flag composition into `harness_cmd`; `ToolCostAttributor` wiring on the stream-json parse path |
| `agent/session_runner/runner.py` | `[missing-capability]` forwarding at turn routing |
| `agent/tool_cost_attribution.py` | `ToolCostAttributor`, `merge_tool_cost_snapshots`, `session_tool_cost_summary` |
| `agent/session_telemetry.py` | `record_pre_tool_use_denial`, `BELT_ENFORCE_SKEW_EVENT`, the `tool_cost` event schema |
| `agent/hooks/pre_tool_use.py` | `_record_denial` taps on the four deny branches |
| `agent/tool_budget.py` | The `tool_budget` denial tap in `record_budget_trip` |
| `tools/belt_baseline.py`, `tools/belt_skew_report.py` | The two report CLIs |
| `config/settings.py` | `toolbelts_enforce`, `escalation_ceiling_multiplier`, `escalation_ceiling_floor` |
| `models/agent_session.py` | `tool_cost_json`, `belt_version`, `belt_enforce_state` |
| `.claude/commands/roles/prime-{pm,dev,teammate}-role.md` | The `[missing-capability]` instruction |

## See Also

- [Headless Session Runner](headless-session-runner.md): the turn loop belts resolve into
- [HarnessAdapter Seam](harness-adapter.md): the argv and stream-json layer that carries the flags
- [Tools Reference](../tools-reference.md): `tools.belt_baseline` and `tools.belt_skew_report` usage and exit codes
- [Teammate Session Permissions](teammate-session-permissions.md): the hook-enforced write restriction the Teammate belt snapshots
- [Personas](personas.md): the identity layer the belts pair with
- [Subconscious Memory](subconscious-memory.md): the `memory` MCP server every belt declares
