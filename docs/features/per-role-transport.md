# Per-Role Transport Hedge

Config-selectable transport per granite role (PM / Dev): interactive **PTY**
(flat-billed on the Claude subscription) or **headless** `claude -p` (metered
against the Agent SDK credit pool). The default reproduces today's behavior
exactly — both roles on PTY. Plan: `docs/plans/per-role-transport-hedge.md`
(issue #1842).

## Why this exists

The granite container drives every bridge-originated session through two Claude
Code sessions (PM + Dev). Interactive PTY sessions bill flat on the
subscription; every headless path (`claude -p`, Agent SDK) draws metered usage
credits. Anthropic's billing policy for programmatic use has moved once and can
move again. This feature is the hedge: flip a role to the transport that is
economically favorable at the time, with the cost of the metered leg surfaced so
a flip is never silent.

## Configuration

Add an optional `transport` block to a project in `projects.json`:

```json
"projects": {
  "myproject": {
    "machine": "Some Machine",
    "transport": { "pm": "pty", "dev": "headless" }
  }
}
```

Values are `pty` or `headless`. Either role key may be omitted (it falls through
to the global default). Global defaults live on `GraniteSettings`:

- `GRANITE__PM_TRANSPORT` (default `pty`)
- `GRANITE__DEV_TRANSPORT` (default `pty`)

### Precedence (closest wins)

1. project block `projects.<key>.transport.{pm,dev}`
2. `settings.granite.pm_transport` / `dev_transport` (env `GRANITE__PM_TRANSPORT` / `GRANITE__DEV_TRANSPORT`)
3. literal `"pty"`

The resolved map is persisted onto the `AgentSession` (`role_transports`) once at
dispatch and is **immutable for the session's lifetime** — a config flip affects
only new sessions, and a revived/recovered session reuses its persisted map
rather than re-reading a since-flipped config (Race 2). A pre-feature session
with no persisted map degrades to both-PTY.

### Validation

`bridge/config_validation.py::validate_transport` rejects a non-dict `transport`
block, an unknown role key, and any value outside `{pty, headless}` — each error
names the offending project key. It is registered in `validate_projects_config`,
so a malformed block fails loud at update time (Step 4.6) and blocks the bridge
restart. The executor adds a defensive backstop: an invalid resolved value
finalizes the session `failed` with a reason naming the bad value.

## How the headless leg works

Each headless role runs one `claude -p` subprocess per turn via the preserved
harness (`agent/sdk_client.py::get_response_via_harness`) — the same subprocess
machinery the drafter uses, with `ANTHROPIC_API_KEY` stripped and stale-UUID
retry intact. `agent/granite_container/role_driver.py::HeadlessRoleDriver` adds:

- **Priming (first turn).** Two implemented branches selected at build time via
  `HeadlessRoleDriver(prime_path=...)`. The **default** is
  `PRIME_PATH_APPEND`: the role's prime command body
  (`.claude/commands/granite/prime-{pm,dev}-role.md`, frontmatter stripped) is
  injected via `--append-system-prompt`. The alternative `PRIME_PATH_SLASH`
  prepends the `/granite:prime-*` slash command to the first message. The append
  path is the default because slash-command *resolution* under `claude -p` is
  unverified on machines without Substrate B / ollama (Task 0 Probe B deferred);
  both branches are covered by unit tests.
- **Turn-end reconciliation.** The subprocess is spawned with the #1688
  `--settings` hook set writing to the per-session NDJSON edge file. The driver
  **prefers a `TURN_END` hook envelope** (parent `Stop`, filtered by session and
  postdating the pre-spawn snapshot) when it lands, and **falls back to the
  subprocess clean exit** (`result`) otherwise — a real, well-defined boundary
  for a single-shot invocation. This is probe-independent: it is correct whether
  or not `Stop` flushes before exit. `needs_human` / `compaction` edges are
  drained opportunistically for parity. The PTY leg stays hook-channel-only.
- **Race 4 guard.** The edge-file cursor is drained before each spawn and only
  envelopes postdating that snapshot are honored, so a stale `Stop` from a prior
  sequential headless turn cannot end the next.
- **Failure classification.** A hung subprocess is bounded-wait killed
  (`headless_turn_timeout`); a nonzero-exit corruption propagates an
  `exit_reason`; an empty result hits the empty-output guard.

The PTY leg is untouched — `PTYRoleDriver` is a mechanical wrapper over today's
`PTYDriver` + `HookEdgeConsumer`, byte-identical under the default config. Only
roles configured `pty` get a PTY process spawned (`_spawn_session_pair`); the
pool still bounds concurrency at one slot per session pair.

## Cost surfacing

Headless token/cost accounting is written to a **disjoint** field set so it can
never clobber the transcript tailer's absolute `total_*` writes (Race 1):

- `accumulate_session_tokens(..., metered=True)` writes `metered_input_tokens` /
  `metered_output_tokens` / `metered_cache_read_tokens` / `metered_cost_usd`
  additively and emits a `session.metered_cost_usd` ledger metric (dimensions
  `role`, `project`). It is called exactly once per headless turn (the single
  internal call inside `get_response_via_harness`), so cost is counted once.
- The transcript tailer keeps writing only `total_*`, and only for PTY roles
  (a headless role's transcript path is never populated).

Displayed grand total = `total_* + metered_*`, summed at read time:

- `dashboard.json` per session: `role_transports`, the four `metered_*` fields,
  and `total_cost_usd_combined`.
- Analytics summary: `metered_cost_today_usd` / `metered_cost_7d_usd`.
- `python -m tools.analytics export/summary`: the `session.metered_cost_usd`
  ledger metric flows through automatically.

## Runbook: flipping a transport

1. **Decide.** A flip is warranted when Anthropic's billing policy or your
   economics change (metered credit pool cheaper/more expensive than flat
   subscription for the workload). The trigger is rare, loud, and human-observed
   — there is no auto-flip.
2. **Edit config.** Set the `transport` block in `projects.json` (per project)
   or the `GRANITE__{PM,DEV}_TRANSPORT` env var (machine-global default).
3. **Validate.** Run `/update` (which gates on `validate_projects_config`), or
   validate directly:
   ```bash
   python -c "import json; from bridge.config_validation import validate_projects_config; validate_projects_config(json.load(open('/Users/you/Desktop/Valor/projects.json')))"
   ```
4. **Restart the worker** so new sessions pick up the change:
   ```bash
   ./scripts/valor-service.sh worker-restart
   ```
   Flips affect **new sessions only** — in-flight sessions keep their spawn-time
   transports.
5. **Watch after flipping:**
   - `curl -s localhost:8500/dashboard.json` — new sessions show
     `role_transports` with the flipped value.
   - `metered_cost_today_usd` in the analytics block — trending up as expected
     for the newly-headless role, and bounded by your monthly Agent SDK budget.
   - `python -m tools.valor_session telemetry --id <ID>` — `token_usage` events.
   - `tail -f logs/worker.log` — routing lines for the session.

### Notes

- **Both-headless** falls out of per-role selection and is covered by unit
  routing tests, but is flagged as untested beyond that (no E2E claim).
- **Slot accounting** is unchanged: one pool slot per session regardless of
  transport (concurrency bound = sessions).
- **Resume handles** (`resume_handles`, schema `{role, claude_session_id,
  transcript_path, transport}`) are persisted for both transports for future
  re-entry. This feature only *writes* them; consumption is #1721's scope.
