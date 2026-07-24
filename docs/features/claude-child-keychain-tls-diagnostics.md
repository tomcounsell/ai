# Claude Child Keychain/TLS Diagnostics & Containment

Operator runbook for issue #2100. This feature makes a worker-spawned Claude
Code CLI child process **attributable, classifiable, and containable** when it
triggers a macOS Keychain/Security trust-evaluation failure — the kind that
surfaces a *destructive* operator dialog whose `Reset to Defaults` action would
delete the login keychain.

> **DANGER — read this first.** If a macOS dialog appears saying
> *"a keychain cannot be found to store `<username>`"* (or any Keychain
> trust/reset prompt) while the worker is running: **do NOT press
> `Reset to Defaults`.** That action deletes the login keychain. It is never
> the correct response to this failure. Inspect the `[harness-spawn]` diagnostic
> in the worker log first (see below). The login keychain is healthy; the
> actionable defect is diagnosis, attribution, and containment — not keychain
> repair.

## Why the process is named `2.1.202`

On some machines the `claude` binary resolves through a symlink to a
version-named target, e.g.
`/Users/valorengels/.local/share/claude/versions/2.1.202`. Because macOS names a
process by its executable basename, the OS logs, Activity Monitor, `securityd`
audit trails, and the destructive Keychain dialog all show the child as the bare
process name **`2.1.202`** — with no obvious link back to Claude Code.

This feature closes that attribution gap. Whenever the harness resolves the
binary it renders a bare-version basename (matched by `^\d+\.\d+\.\d+`) as
`Claude Code CLI 2.1.202` alongside the symlink and realpath, so `2.1.202` in a
macOS dialog is immediately recognizable as the Claude Code child.

The version is read from the **path basename only** — the code never runs
`claude --version`, which can hang under launchd TCC.

## The `[harness-spawn]` diagnostic

Immediately before every `claude` subprocess spawn, the harness emits one
sanitized JSON record on a `[harness-spawn]` log line
(`agent/session_runner/harness/claude.py`, built by
`agent/session_runner/harness/claude_diagnostics.py::build_spawn_diagnostic`).
It contains:

| Field | Meaning |
|---|---|
| `binary.which` | The on-PATH `claude` symlink path (`shutil.which`). |
| `binary.realpath` | The symlink target (`os.path.realpath`). |
| `binary.basename` | Basename of the realpath (e.g. `2.1.202`). |
| `binary.version` | The bare version when the basename matches `^\d+\.\d+\.\d+`, else `null`. |
| `binary.display` | `Claude Code CLI {version}` for a version basename, else the basename. |
| `worker_label` | Which worker spawned the child (from `VALOR_WORKER_MODE` / hostname). |
| `working_dir` | The child's cwd. |
| `session_id` | The in-flight session id. |
| `auth_mode` | `oauth` when `CLAUDE_CODE_OAUTH_TOKEN` is present, `api_key` when `ANTHROPIC_API_KEY` is present (should never happen post-strip), else `unknown`. **Presence only — never the value.** |
| `trust_env` | Presence + value of the TLS trust-material env vars `SSL_CERT_FILE`, `SSL_CERT_DIR`, `NODE_EXTRA_CA_CERTS`, `REQUESTS_CA_BUNDLE`, `NODE_TLS_REJECT_UNAUTHORIZED` (these are filesystem paths / a `0|1` flag, not secrets). |

**No secret ever appears in the record.** The prompt (`cmd[-1]`), API keys, and
the OAuth token value are never logged. Auth mode is reported present/absent
only — so the diagnostic itself proves the no-leak guarantee.

### Env-strip guarantee

Every `claude` spawn (both the primary harness invocation and
`verify_harness_health`) runs through a shared `stripped_harness_env(base)`
helper that pops all three of `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, and
`ANTHROPIC_AUTH_TOKEN` before exec. The subscription-auth posture must never
inherit an API-key base URL or auth token from the parent environment.

To confirm on a running machine:

```bash
grep '\[harness-spawn\]' logs/worker.log | tail -1
```

## Failure classes

When a harness subprocess exits early (before a normal result event), the exit
is classified by
`claude_diagnostics.py::classify_harness_early_exit` into one of six classes,
checked in this order:

| Class | Trigger |
|---|---|
| `BINARY_MISSING` | `returncode is None` (FileNotFoundError path — `claude` not on PATH). |
| `TLS_TRUST` | stderr matches a TLS/trust token (`MissingIntermediate`, `AnchorTrusted`, `unable to get local issuer`, `self-signed certificate`, `certificate verify failed`, `tls`, …). This is the **destructive-dialog class**. |
| `AUTH_UNAVAILABLE` | stderr matches an auth token (`invalid api key`, `authentication`, `oauth`, `401`, `unauthorized`, `credit balance`) **and not** a TLS token. |
| `STALE_UUID` | no `system/init` seen and no TLS/auth match (a stale resume UUID) — returncode-independent, so this claims a `returncode=0` exit too. |
| `CLEAN_NO_OUTPUT` | `returncode == 0`, `init` was seen, no TLS/auth match (issue #2219) — a benign exit-0 empty turn, not a failure. Checked **after** `STALE_UUID` so an `init_seen=False` exit-0 still classifies as the error-level `STALE_UUID`, never gets silently downgraded. |
| `GENERIC_NONZERO` | nonzero exit, none of the above. |

### TLS wins over auth (load-bearing precedence)

If stderr matches **both** a TLS token and an auth token, the classification is
`TLS_TRUST`, never `AUTH_UNAVAILABLE`. TLS is the class that can trigger the
destructive Keychain dialog, so it takes precedence. This precedence and the
enum membership are the load-bearing contracts; the exact token lists are a
tunable implementation detail.

On a `TLS_TRUST` classification the harness emits a WARNING that names the class
and points at the `[harness-spawn]` diagnostic and the certificate chain, and
**never** mentions Keychain reset/repair.

## Sentry bucket split by exit class (issue #2219)

BRANCH C in `claude.py` (no result event and no accumulated text — the
terminal case after classification) used to emit one bare
`logger.error("Harness exited without a result event and no accumulated
text")` for every exit class. Sentry's `LoggingIntegration` turned that into a
single un-tagged, un-fingerprinted issue (VALOR-2M) that collapsed every root
cause — a killed CLI child, a TLS-trust failure, an empty drafter turn — into
one un-triageable 682-event bucket.

`claude_diagnostics.py::describe_harness_exit_for_sentry(exit_class,
returncode, init_seen, stderr_snippet)` is a pure helper that returns
`(log_level, sentry_payload)` for BRANCH C:

- **`CLEAN_NO_OUTPUT` → `logging.WARNING`.** A benign exit-0 empty turn sits
  below Sentry's error threshold, so it produces no Sentry event at all —
  removing the dominant noise source from the bucket. The caller still
  returns `text=None` and handles the empty turn exactly as before; only the
  log level and Sentry visibility change.
- **Every other class → `logging.ERROR`**, emitted inside an isolated
  `sentry_sdk.new_scope()` carrying:
  - tags `harness_exit_class` (the class value) and `harness_returncode`;
  - a `harness_exit` context dict (`returncode`, `init_seen`,
    `stderr_snippet`);
  - `fingerprint = ["harness-exit-no-result", str(exit_class)]`.

The per-class fingerprint is what actually splits the bucket: Sentry groups
events by fingerprint, so `harness-exit-no-result:tls_trust`,
`…:generic_nonzero`, `…:stale_uuid`, etc. each become their own issue,
resolvable/ignorable independently. VALOR-2M itself stops accruing new events
once the fix ships — that's expected, not a regression, since the split *is*
the fix.

The scope/tagging is best-effort: it runs inside a `try/except` so a Sentry
import or tagging failure can never suppress the underlying `logger.error`
call. The BRANCH-C return tuple (`text=None`, `returncode`, `usage`, …) is
byte-for-byte unchanged — no caller behavior changes, only what Sentry
receives.

## Per-session TLS streak + consecutive suppression

A single `TLS_TRUST` exit is **not** treated as permanent — an intermittent
chain race at keychain unlock could self-heal on retry, and hard first-occurrence
suppression would convert a transient into a guaranteed dropped turn.

Instead the harness keeps a per-session streak in Redis:

- Key: `harness:tls_streak:{host}:{session_id}` (per-session, **not** per-host,
  so one session's reset can never clear another interleaved session's streak).
- `INCR` + `EXPIRE` (`HARNESS_TLS_STREAK_TTL_S`, default 300s) on each
  `TLS_TRUST` classification.
- `DELETE` on any non-`TLS_TRUST` classification (reset).

The stale-UUID fresh-session retry is suppressed **only** once the streak
reaches `HARNESS_TLS_CONSECUTIVE_SUPPRESS` (named module-level constant in
`claude_diagnostics.py`, env-overridable, provisional default **2**). The FIRST
`TLS_TRUST` exit still retries; suppression engages on the M-th consecutive
occurrence, because a repeated hard TLS failure only re-triggers the same dialog.

`python -m tools.doctor` surfaces the current streak values for the host
**read-only** via the `claude_binary_attribution` check (it scans
`harness:tls_streak:{host}:*` and reports the values, or "no active TLS streak").

## Worker respawn circuit breaker

launchd runs `com.valor.worker` with `KeepAlive=true` and a fixed
`ThrottleInterval=10`, so a worker that crashes on startup re-spawns every ~10
seconds indefinitely — a tight crash-loop that, if each spawn re-triggers the
Keychain/TLS path, means a mis-click on the destructive dialog is only ever 10
seconds away. The circuit breaker halts that loop.

- **Start beacon** (`worker/__main__.py`): on each startup the worker records a
  timestamp in the `worker:starts:{host}` Redis **sorted set** (`ZADD` +
  `ZREMRANGEBYSCORE` to trim outside the window + bounded `EXPIRE`).
- **Breaker** (`monitoring/worker_watchdog.py`): on each watchdog pass, `main()`
  reads the beacon via `ZCOUNT` **before** the missing-worker dispatch. When
  starts in the window reach `WORKER_RESPAWN_CIRCUIT_THRESHOLD` (default **5**)
  within `WORKER_RESPAWN_CIRCUIT_WINDOW_S` (default **120s**), it trips:
  `launchctl disable` the worker (stopping the fixed-cadence loop) and writes a
  **dedicated** `worker:watchdog:critical:breaker:{host}` key (reason + count +
  timestamp), then returns immediately (so the same-tick L3 `_enable_worker()`
  cannot undo the disable).
- The breaker key is **distinct** from the U-state hung path's
  `worker:watchdog:critical:{host}` — the two states never clobber each other.
- The down-tick ladder stays gated on the **persistent** launchctl load-state
  (via `_is_operator_disabled()`), NOT on the Redis key, so a tripped breaker
  holds the worker disabled even after the breaker key's TTL expires — the
  ladder can never `kickstart` a launchctl-disabled service.
- The breaker is **cause-agnostic**: it trips on any tight whole-worker
  crash-loop, not only keychain/TLS deaths. A tight crash-loop is worth halting
  and alerting on regardless of cause. Child TLS failures do NOT trip it — only
  whole-worker *death rate* does; child failures back off session spawns and
  warn.

### Operator-restart suppression (false-trip guard)

`./scripts/valor-service.sh` (worker-restart / restart) and
`scripts/install_worker.sh` write a short-lived `worker:restart_suppress:{host}`
marker (TTL ~= the window, 120s) **before** they cycle the worker. The breaker
reads it first and skips tripping while it is set, so a scripted deploy/restart
or a manual debug restart never masquerades as a crash-loop.

### Break-glass recovery for a tripped breaker

A tripped breaker holds the worker persistently launchctl-disabled. To recover:

```bash
# 1. Re-enable launchd auto-respawn (persistent — survives the breaker key TTL).
launchctl enable gui/$(id -u)/com.valor.worker

# 2. Start the worker.
./scripts/valor-service.sh worker-start

# 3. Delete the dedicated breaker key so doctor/logs stop reporting a trip.
python -c "import socket; from popoto.redis_db import POPOTO_REDIS_DB as r; r.delete(f'worker:watchdog:critical:breaker:{socket.gethostname()}')"
```

Only do this **after** you have identified and fixed the root cause of the
crash-loop (inspect `logs/worker.log` and the `[harness-spawn]` diagnostic).
Re-enabling a still-broken worker just re-trips the breaker.

## `.worktrees/` install guard

`scripts/install_worker.sh` refuses to install the global `com.valor.worker`
launchd service when `PROJECT_DIR` contains `.worktrees/` — a worktree checkout
silently becoming the global worker install target was correlated with the
incident's plist-rewrite. The guard prints a loud multi-line WARNING and
`exit 1`.

Override for the rare intentional case (only if the main checkout is
unavailable):

```bash
ALLOW_WORKTREE_WORKER_INSTALL=1 ./scripts/install_worker.sh
```

`ALLOW_WORKTREE_WORKER_INSTALL` is a one-shot install-time shell override read
only by `install_worker.sh` — never a persistent worker runtime var.

## What this feature deliberately does NOT do

- **No macOS Keychain read/write/reset/repair.** The login keychain is healthy;
  the code never invokes `security`, `SecItem*`, or any "reset to defaults"
  language.
- **No live TLS/network failure in tests.** Classification is exercised with
  synthetic stderr strings only.
- **No secret logging.** Prompts, API keys, and OAuth token values never appear
  in a diagnostic — auth mode is presence/absent only.
- **No attempt to identify the failing TLS endpoint** (redacted in the incident,
  unrecoverable from repo config). The failure *class* is what gets classified.

## Related

- `agent/session_runner/harness/claude_diagnostics.py` — attribution +
  classification primitives, plus `describe_harness_exit_for_sentry`
  (issue #2219).
- `agent/session_runner/harness/claude.py` — spawn diagnostic emission,
  per-session TLS streak, BRANCH-C Sentry scope/tagging (issue #2219).
- `monitoring/worker_watchdog.py` — respawn circuit breaker.
- `worker/__main__.py` — start beacon + startup binary attribution.
- `scripts/install_worker.sh`, `scripts/valor-service.sh` — install guard +
  restart-suppression marker.
- `tools/doctor.py::_check_claude_binary_attribution` — operator-facing
  attribution + read-only TLS streak surface.
