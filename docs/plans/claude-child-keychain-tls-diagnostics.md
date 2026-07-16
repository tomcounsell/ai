---
slug: claude-child-keychain-tls-diagnostics
type: bug
status: Ready
appetite: Medium-Large
tracking: https://github.com/tomcounsell/ai/issues/2100
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-16T03:17:55Z
---

# Claude Child Keychain/TLS Diagnostics & Containment

## Problem

A worker-spawned Claude Code CLI child process (resolved on this machine to
`/Users/valorengels/.local/share/claude/versions/2.1.202`, so macOS logs it as
bare process name `2.1.202`) can trigger macOS Keychain/Security trust
evaluation failures that surface as a **destructive** operator dialog:
`a keychain cannot be found to store <username>`, whose `Reset to Defaults`
action would delete the login keychain. The observed securityd sequence is
`SecItemCopyMatching` → TLS trust failures (`MissingIntermediate`,
`AnchorTrusted`) → a port-443 connection attributed to process `2.1.202`.

The system currently cannot map that version-named process back to Claude Code,
classify the trust/auth failure category, or stop the worker from tightly
re-spawning at launchd's fixed `ThrottleInterval=10` cadence — so a single
mis-click on the destructive dialog is the failure mode we must design out.

The login keychain is **healthy** (per incident evidence). The actionable
defect is **diagnosis, attribution, and containment** — not keychain repair.

## Freshness Check

Baseline commit: `de5cdbd6c` (main at plan time). Issue filed
2026-07-15T07:59:16Z.

| Reference | Disposition |
|---|---|
| `agent/session_runner/harness/claude.py` (`_run_harness_subprocess`, `get_response_via_harness`) | **Unchanged** — spawn path at `_run_harness_subprocess` (~line 823 `asyncio.create_subprocess_exec`); env-strip at lines 375–382. |
| `worker/__main__.py` CLI-harness startup check (lines 614–653) | **Unchanged** — `shutil.which("claude")` gate present; no realpath/version rendering. |
| `monitoring/worker_watchdog.py` (down-tick counter, `_record_critical_status`, `_handle_missing_worker`) | **Unchanged** — down-tick ladder exists but only counts *missing-at-tick*; no repeated-death/respawn circuit breaker. |
| `scripts/install_worker.sh` | **Unchanged** — no `.worktrees/` guard; `PROJECT_DIR` derived from script dir (line 11). |
| `#2103` (index-drift) landed since filing | **Minor drift** — touched worker startup/AgentSession indexing, not the harness spawn or keychain/TLS path. No root-cause change. |

Disposition: **Unchanged / Minor drift** — proceed. The bug is still present:
the harness spawns `claude` by PATH with no sanitized pre-exec diagnostic, no
early-exit TLS/trust classification, and no whole-worker respawn circuit breaker.

## Research

No relevant external findings needed — this is an internal diagnostics/
containment change over the repo's own harness, worker, and watchdog code.
macOS Security-framework error tokens (`MissingIntermediate`, `AnchorTrusted`)
and common OpenSSL/Node TLS stderr fragments (`unable to get local issuer
certificate`, `self-signed certificate in certificate chain`, `SSL
certificate problem`) are used only as substring match tokens for
classification; no live TLS failure is exercised in tests.

## Prior Art

- #1311 / #1331 / #1407 / #1767 / #1815 / #1816 / #1846 — worker watchdog &
  liveness recovery ladder. This plan **extends** the existing watchdog
  (`_record_critical_status`, down-tick counter) with a distinct
  repeated-respawn circuit breaker; it does not replace the missing-worker or
  stale-heartbeat ladders.
- #1099 Mode 1 — `stderr_snippet` capture + sentinel classification in the
  harness. This plan reuses the same `stderr_snippet` surface for TLS/trust
  classification, mirroring the `THINKING_BLOCK_SENTINEL` pattern.
- #1751 — Claude setup-token work explicitly avoided macOS Keychain
  integration. This plan keeps that boundary: **no** Keychain read/write/reset.
- #1245 / #1980 — exit-shape (`on_exit_status`, `result_event_fired`) plumbing
  the early-exit classifier will consume.

## Data Flow

```
launchd (com.valor.worker, KeepAlive=true, ThrottleInterval=10)
  → python -m worker  (startup: shutil.which("claude") gate, lines 614–629)
      → [NEW] describe_claude_binary() startup diagnostic + version-basename warning
      → [NEW] record worker start-beacon in Redis (respawn tracking)
  → session turn → ClaudeHarnessAdapter.run_turn
      → get_response_via_harness
          → _run_harness_subprocess (asyncio.create_subprocess_exec)
              → [NEW] sanitized pre-exec spawn diagnostic (symlink/realpath/
                 version/worker-label/cwd/session-id/auth-mode/trust-env presence)
              → claude -p ... (child = "2.1.202")  ── TLS trust failure here ──
          → [NEW] classify_harness_early_exit(returncode, stderr, init_seen,
                 result_event_fired) → {binary_missing | auth_unavailable |
                 tls_trust | stale_uuid | generic_nonzero}
              → TLS/trust: log classification + non-Keychain remediation;
                 retry once on first occurrence; suppress retry only after
                 M consecutive TLS_TRUST exits (a repeated hard failure only
                 re-triggers the same dialog)

worker_watchdog (StartInterval=300)
  → [NEW] read start-beacon respawn count in window
      → if repeated whole-worker deaths → trip circuit breaker:
         launchctl disable + _record_critical_status (stop the 10s loop)
```

## Solution

Five surfaces, split by layer (per the issue's Revised recon bucket:
child-process classification lives in the harness; whole-worker respawn
throttling lives in the watchdog).

### 1. Sanitized harness spawn diagnostic (AC1, AC2, AC7)

**Env-strip reconciliation (critique blocker).** Today `get_response_via_harness`
only pops `ANTHROPIC_API_KEY` (`claude.py:376,379`); `ANTHROPIC_BASE_URL` and
`ANTHROPIC_AUTH_TOKEN` inherit from `os.environ` untouched. AC7 asserts none of
the three leak, so this plan **extends the strip block to pop all three** at both
sites (subscription auth must never inherit an API-key base URL or auth token).
The spawn diagnostic then reports auth mode as OAuth-present/absent only and never
echoes any of the three values — so the diagnostic itself proves the guarantee.

New module `agent/session_runner/harness/claude_diagnostics.py`:

- `describe_claude_binary(cmd0: str) -> dict` — resolves `shutil.which(cmd0)`
  (symlink path), `os.path.realpath` (target), `os.path.basename(realpath)`.
  When the basename matches `^\d+\.\d+\.\d+`, sets
  `display = "Claude Code CLI {basename}"` and `version = basename`; otherwise
  `display = basename`, `version = None`. Never runs `claude --version`
  (can hang under launchd TCC). Returns `{which, realpath, basename, version,
  display}`.
- `describe_auth_mode(proc_env: dict) -> str` — returns `"oauth"` when
  `CLAUDE_CODE_OAUTH_TOKEN` is present, `"api_key"` when `ANTHROPIC_API_KEY`
  present (should never happen post-strip), else `"unknown"`. **Presence
  only** — never the value.
- `trust_env_presence(proc_env: dict) -> dict` — for `SSL_CERT_FILE`,
  `SSL_CERT_DIR`, `NODE_EXTRA_CA_CERTS`, `REQUESTS_CA_BUNDLE`,
  `NODE_TLS_REJECT_UNAUTHORIZED`: report **present + path/value** (these are
  filesystem paths / a 0|1 flag, not secrets) or absent.
- `build_spawn_diagnostic(cmd, proc_env, working_dir, session_id, worker_label)
  -> dict` — composes the above into one sanitized record. Never includes the
  prompt (`cmd[-1]`) or any secret value.

Call site: `_run_harness_subprocess`, immediately before
`asyncio.create_subprocess_exec`, emit
`logger.info("[harness-spawn] %s", json.dumps(diagnostic))`. Add a
`worker_label` kwarg to `_run_harness_subprocess` /
`get_response_via_harness` (default derived from `VALOR_WORKER_MODE` /
`socket.gethostname()`), threaded through the adapter.

### 2. Early-exit failure classification (AC3)

In `claude_diagnostics.py`:

```python
class HarnessExitClass(str, Enum):
    BINARY_MISSING = "binary_missing"
    AUTH_UNAVAILABLE = "auth_unavailable"
    TLS_TRUST = "tls_trust"
    STALE_UUID = "stale_uuid"
    GENERIC_NONZERO = "generic_nonzero"

def classify_harness_early_exit(*, returncode, stderr_snippet, init_seen,
                                result_event_fired) -> HarnessExitClass | None
```

- Returns `None` when the turn completed normally (`result_event_fired` True).
- `BINARY_MISSING`: `returncode is None` (FileNotFoundError path).
- `TLS_TRUST`: stderr contains any of a curated token set
  (`MissingIntermediate`, `AnchorTrusted`, `unable to get local issuer`,
  `self-signed certificate`, `self signed certificate`, `SSL certificate
  problem`, `CERT_`, `certificate verify failed`, `tls`), matched
  case-insensitively.
- `AUTH_UNAVAILABLE`: stderr contains auth tokens (`invalid api key`,
  `authentication`, `oauth`, `401`, `unauthorized`, `credit balance`) **and
  not** a TLS match (TLS wins — it is the destructive-dialog class).
- `STALE_UUID`: `init_seen` False on a resume path with nonzero exit and no
  TLS/auth match (retained for parity with the existing fallback).
- `GENERIC_NONZERO`: nonzero exit, none of the above.

The exact substring token sets above are **illustrative, not prescriptive**
(critique nit): the builder tunes them against captured stderr fixtures. The
load-bearing contracts the plan fixes are (a) the `HarnessExitClass` enum
membership and (b) TLS-wins-over-auth precedence — the token lists themselves
are an implementation detail.

Wire into `_run_harness_subprocess` (it already has `returncode`,
`stderr_snippet`, tracks whether `system/init` fired, and
`result_text is not None`). On `TLS_TRUST`, emit a WARNING with the
classification and a remediation line that **never** mentions Keychain
reset/repair — e.g. "TLS trust failure from Claude Code CLI child; inspect
the [harness-spawn] diagnostic and the certificate chain. Do NOT reset the
login keychain." Return the classification via a new `on_early_exit_class`
callback (additive; existing callers unaffected) so the caller can suppress
the fresh-session retry on TLS/trust (a retry only re-triggers the dialog).

**Retry suppression (critique concern — transient-safe):** we do NOT assume a
single `TLS_TRUST` exit is permanent — an intermittent chain race at keychain
unlock could self-heal on retry, and hard first-occurrence suppression would
convert a transient into a guaranteed dropped turn. Instead, in
`get_response_via_harness` the first `TLS_TRUST` exit still takes the normal
recovery path (the existing stale-UUID fresh-session fallback), incrementing the
`harness:tls_failures:{host}` beacon each time. Suppression engages only after
`HARNESS_TLS_CONSECUTIVE_SUPPRESS` (named env-overridable, provisional default 2)
consecutive `TLS_TRUST` classifications within the beacon window — a persisted
streak, not a single match — at which point the fresh-session retry is skipped
(a repeated hard TLS failure will only re-trigger the same dialog) and the
non-Keychain WARNING is emitted. A non-TLS classification resets the streak.

### 3. Worker startup + doctor binary attribution (AC4)

- `worker/__main__.py` (after line 629 `which("claude")` success): call
  `describe_claude_binary("claude")`, log
  `logger.info("[worker-startup] Claude binary: %s (realpath=%s)", display,
  realpath)`, and when `version is not None` (bare-version basename) also
  `logger.warning("[worker-startup] Claude binary basename is a bare version
  number (%s); macOS dialogs/logs will show this as the process name.",
  basename)`.
- `tools/doctor.py`: new `_check_claude_binary_attribution()` check —
  passed=True always (advisory), message renders the `display` + realpath, and
  raises a warning-level note when the basename is a bare version number. Wire
  into the check registry alongside `_check_claude_oauth_token`.

### 4. Repeated-failure containment (AC5)

Two persisted beacons in Redis (raw `POPOTO_REDIS_DB` keys with TTL, matching
`_record_critical_status`'s existing pattern — these are watchdog telemetry
keys, **not** Popoto-managed model keys, so raw Redis ops are the sanctioned
path here):

- **Worker respawn beacon** — `worker:starts:{host}`, an **atomic Redis sorted
  set** (critique concern): `worker/__main__.py` on each startup does
  `ZADD {ts: ts}` then `ZREMRANGEBYSCORE 0 (now - window)` and sets a bounded
  `EXPIRE`, reusing the same atomic idiom as `_increment_down_ticks`
  (`worker_watchdog.py` ~lines 372–380) instead of a non-atomic JSON RMW.
  `worker_watchdog.py` reads `ZCOUNT (now-window) now` in `check()`/`main()`:
  when ≥ `WORKER_RESPAWN_CIRCUIT_THRESHOLD` (named env-overridable, provisional
  default 5) starts within `WORKER_RESPAWN_CIRCUIT_WINDOW_S` (provisional 120s),
  trip the breaker: `launchctl disable` the worker (stop the fixed-cadence loop)
  and `_record_critical_status("respawn circuit breaker: N starts in Ws", ...)`.
  This is the operator-visible critical state that replaces the silent 10s loop.
  - **Operator-restart suppression (critique concern — false-trip guard):**
    `./scripts/valor-service.sh` (worker-restart / restart) and
    `install_worker.sh` write a short-lived suppression marker
    `worker:restart_suppress:{host}` (TTL ~= window) before cycling the worker.
    The breaker reads it first and skips tripping while set, so a scripted
    deploy/restart or manual debug restart never masquerades as a crash-loop.
- **Harness TLS-failure beacon** — `harness:tls_failures:{host}`, atomic
  `INCR` + `EXPIRE` on each `TLS_TRUST` classification (surface 2). **Scope
  (critique concern — no orphaned consumer): increment + doctor/dashboard read
  + a WARNING only.** The plan does NOT add a session-executor backoff consumer
  in this pass (that would need its own thresholds/tests); the counter is an
  operator-visible signal read by `_check_claude_binary_attribution`'s sibling
  doctor surface, not an actuator. Whole-worker death rate (the respawn beacon)
  remains the only auto-disable trigger.

**Breaker-vs-ladder precedence (critique concern — self-fight):** once the
breaker trips, `launchctl disable` makes the worker read as `status == "down"`
on the next tick, and the existing `_handle_missing_worker` L1–L4 ladder would
`launchctl kickstart` the very service the breaker just disabled. To prevent
the two watchdog paths fighting, `check()` reads the breaker-tripped critical
key (`worker:watchdog:critical:{host}` with a `respawn circuit breaker` reason)
at the top; while tripped, the down-tick escalation is short-circuited (no
kickstart/enable), and a WARNING states the worker is intentionally held
disabled pending operator clearance. Task 6 explicitly amends the ladder's
entry conditions, not just adds the breaker.

All beacon constants are named, env-overridable, and carry a
"provisional/tunable" grain-of-salt comment (per repo convention).

### 5. Worktree install guard (AC6)

`scripts/install_worker.sh`: after computing `PROJECT_DIR` (line 11), detect
`.worktrees/` in the path. When present, print a loud multi-line WARNING and
`exit 1` **unless** `ALLOW_WORKTREE_WORKER_INSTALL=1` is set (documented
override). This prevents a worktree checkout from silently becoming the global
`com.valor.worker` install target (the incident's plist-rewrite correlation).

### 6. Operator runbook (AC8)

New `docs/features/claude-child-keychain-tls-diagnostics.md`: explains the
`2.1.202`→Claude Code attribution, the `[harness-spawn]` diagnostic, the
failure classes, the respawn circuit breaker, and an explicit **"Do NOT press
`Reset to Defaults` on the macOS keychain dialog — inspect the harness
diagnostic first"** operator instruction.

## No-Gos

- **No macOS Keychain read/write/reset/repair.** The login keychain is healthy;
  we never invoke `security`, `SecItem*`, or any "reset to defaults" language.
- **No live TLS/network failure in automated tests.** Classification is tested
  with synthetic stderr strings only.
- **No secret logging.** Prompts (`cmd[-1]`), API keys, and OAuth token values
  never appear in diagnostics — auth mode is reported present/absent only.
- **No re-enabling the launchd startup smoke test** that spawns `claude` under
  `VALOR_LAUNCHD` (TCC/TTY hang risk).
- **No new SDLC/session behavior** beyond diagnostics + containment.

## Update System

`scripts/install_worker.sh` changes (the `.worktrees/` guard + restart-
suppression marker) and the `scripts/valor-service.sh` restart-suppression
marker are picked up automatically by `/update` (which re-runs the installer and
syncs scripts) — no `scripts/update/run.py` change required. No new dependencies, no Popoto model changes, so **no
`scripts/update/migrations.py` entry is required**. The new Redis beacon keys
are plain TTL'd string keys created lazily at runtime; no migration needed.
State explicitly in the PR: after merge, a `./scripts/valor-service.sh restart`
is required on bridge/worker machines because worker startup + harness spawn
runtime code changed.

## Agent Integration

No new MCP server, `.mcp.json`, or CLI entry point is required — every change
is internal to the worker/harness/watchdog/doctor path the agent already runs.
The doctor check surfaces via the existing `python -m tools.doctor` entry point
(already agent-invokable). Integration coverage: a unit test asserts
`_check_claude_binary_attribution` is registered and runs; the harness spawn
diagnostic is exercised by the existing harness subprocess tests (mocked
subprocess).

## Failure Path Test Strategy

- TLS/trust classification: feed synthetic stderr containing each token; assert
  `HarnessExitClass.TLS_TRUST` and that no Keychain-reset string appears in the
  emitted log record.
- Retry suppression: assert the FIRST `TLS_TRUST` exit still retries (fallback
  called), and only the M-th consecutive `TLS_TRUST` exit suppresses the retry;
  a non-TLS class between them resets the streak.
- Circuit breaker: seed `worker:starts:{host}` (sorted set) above threshold and
  assert the watchdog trips `launchctl disable` (mocked) + writes the critical
  key; below threshold no trip; with `worker:restart_suppress:{host}` set no
  trip even above threshold; with the breaker critical key set the down-tick
  ladder does not kickstart.
- Binary attribution: monkeypatch `shutil.which`/`os.path.realpath` to a
  `/versions/2.1.202` path; assert `display == "Claude Code CLI 2.1.202"` and
  the bare-version warning fires.
- Auth-env non-leak: assert the spawn diagnostic reports auth mode
  present/absent only and never contains the token value.

## Test Impact

- [ ] `tests/unit/test_update_install_worker.py` — UPDATE: add cases for the
  `.worktrees/` guard (main-checkout allowed; worktree blocked; worktree +
  `ALLOW_WORKTREE_WORKER_INSTALL=1` allowed). Existing install-flow assertions
  unchanged.
- [ ] `tests/unit/test_worker_watchdog.py` — UPDATE: add respawn-circuit-breaker
  cases (trip above threshold, no trip below, critical key written, restart-
  suppression marker prevents trip, breaker-tripped key short-circuits the
  down-tick kickstart ladder). Existing down-tick/missing-worker cases unchanged.
- [ ] `tests/unit/test_doctor.py` — UPDATE: add `_check_claude_binary_attribution`
  cases (bare-version warning; normal-name pass). Existing
  `_check_claude_oauth_token` cases unchanged.
- [ ] `tests/unit/test_sdk_client.py` — UPDATE: assert the extended env-strip
  pops all three (`ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`,
  `ANTHROPIC_AUTH_TOKEN`) from `proc_env`, and that the added `worker_label`
  kwarg + `[harness-spawn]` log line do not break existing argv/env assertions.
- [ ] NEW `tests/unit/test_claude_diagnostics.py` — classification (incl.
  TLS-wins-over-auth), binary attribution, auth-mode/trust-env presence,
  consecutive-streak retry-suppression logic.

## Rabbit Holes

- **Do not** try to parse the real Claude Code version by executing the binary
  (`claude --version`) — it can hang under launchd TCC. The path basename is
  the version; use it.
- **Do not** generalize the circuit breaker into the missing-worker/stale
  ladders — it is a distinct respawn-rate signal. Keep it additive.
- **Do not** attempt to identify the failing TLS endpoint (redacted in the
  incident, unrecoverable from repo config). Classify the failure class only.
- **Do not** add auto-disable of the whole worker for *child* TLS failures —
  only whole-worker *death* rate trips the launchctl-disable breaker; child
  failures back off session spawns and warn.

## Step by Step Tasks

1. Create `agent/session_runner/harness/claude_diagnostics.py`:
   `describe_claude_binary`, `describe_auth_mode`, `trust_env_presence`,
   `build_spawn_diagnostic`, `HarnessExitClass`, `classify_harness_early_exit`.
2. In `get_response_via_harness`, extend the env-strip block to pop all three of
   `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN` at both
   sites (blocker fix). Wire the spawn diagnostic + `worker_label` kwarg into
   `_run_harness_subprocess` and thread it through `get_response_via_harness` /
   `ClaudeHarnessAdapter`.
3. Wire `classify_harness_early_exit` into `_run_harness_subprocess`; emit the
   non-Keychain TLS/trust WARNING; add the `on_early_exit_class` callback.
4. In `get_response_via_harness`, increment the atomic
   `harness:tls_failures:{host}` beacon on each `TLS_TRUST`; suppress the
   stale-UUID fresh-session retry only after `HARNESS_TLS_CONSECUTIVE_SUPPRESS`
   consecutive `TLS_TRUST` exits within the window (reset on any non-TLS class).
   No session-executor consumer in this pass — beacon is read-only telemetry.
5. Add the atomic sorted-set start-beacon write (`ZADD`+`ZREMRANGEBYSCORE`+
   `EXPIRE`) in `worker/__main__.py`; add the `describe_claude_binary` startup
   diagnostic + bare-version warning.
6. Add the respawn circuit breaker to `monitoring/worker_watchdog.py` (read
   beacon via `ZCOUNT`, honor the `worker:restart_suppress:{host}` marker, trip
   `launchctl disable` + `_record_critical_status`), AND amend `check()` /
   `_handle_missing_worker` to short-circuit the down-tick ladder while the
   breaker-tripped critical key is set. Named env-overridable threshold/window
   constants.
7. Add the `worker:restart_suppress:{host}` marker write to
   `scripts/valor-service.sh` (worker-restart / restart) and
   `scripts/install_worker.sh` before they cycle the worker.
8. Add `_check_claude_binary_attribution` to `tools/doctor.py` and register it
   (also surfaces the `harness:tls_failures` count read-only).
9. Add the `.worktrees/` guard to `scripts/install_worker.sh` with the
   `ALLOW_WORKTREE_WORKER_INSTALL=1` override.
10. Write `docs/features/claude-child-keychain-tls-diagnostics.md` runbook +
    add it to `docs/features/README.md`.
11. Write `tests/unit/test_claude_diagnostics.py`; update
    `test_update_install_worker.py`, `test_worker_watchdog.py`,
    `test_doctor.py`, `test_sdk_client.py` per Test Impact.
12. Run `ruff format`/`ruff check` + the narrow test set; open PR with
    `Closes #2100` and the restart note.

## Documentation

- [ ] Create `docs/features/claude-child-keychain-tls-diagnostics.md` — operator
  runbook: `2.1.202`→Claude Code attribution, the `[harness-spawn]` diagnostic
  fields, the failure classes, the respawn circuit breaker, and the explicit
  "do NOT press Reset to Defaults; inspect the harness diagnostic first"
  instruction.
- [ ] Add an entry to `docs/features/README.md` index table.

## Success Criteria

- Every harness subprocess spawn emits one sanitized `[harness-spawn]`
  diagnostic (symlink, realpath, basename, version, worker label, cwd, session
  id, auth mode, trust-env presence). No prompt/secret in the record.
- A `/versions/2.1.202` binary renders as `Claude Code CLI 2.1.202` + realpath
  everywhere (spawn diagnostic, worker startup, doctor).
- Early exits classify into ≥5 classes; tests cover them with no network.
- Worker startup + doctor report the resolved binary path and warn on a
  bare-version basename.
- Repeated whole-worker deaths trip an operator-visible critical state
  (`launchctl disable` + critical Redis key) instead of a silent 10s loop;
  tests cover the persisted beacon/trip.
- `install_worker.sh` blocks a `.worktrees/` install unless
  `ALLOW_WORKTREE_WORKER_INSTALL=1`; tests cover allowed/blocked/override.
- All three of `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`
  are stripped from `proc_env`; auth mode reported present/absent only.
- A scripted `valor-service.sh restart` does NOT trip the respawn breaker
  (suppression marker); a tripped breaker holds the worker disabled without the
  down-tick ladder kickstarting it back up.
- TLS retry is suppressed only after M consecutive `TLS_TRUST` exits, not on the
  first occurrence.
- Runbook explicitly warns operators off `Reset to Defaults`.
