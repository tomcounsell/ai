---
status: Ready
type: bug
appetite: Large
owner: Valor Engels
created: 2026-08-07
tracking: https://github.com/tomcounsell/ai/issues/2645
last_comment_id: none
revision_applied: true
revision_applied_at: 2026-08-07T13:20:00Z
---

# Harden Production Redis Against Accidental Flush

## Problem

On 2026-08-07 06:24Z an ad-hoc Python debug script called `POPOTO_REDIS_DB.flushdb()`. It intended to
hit test db 15 and set the target with `os.environ.setdefault("REDIS_URL", ".../15")` — but the shell
already exported the production `REDIS_URL`, so `setdefault` was a silent no-op and the client went to
db 0. 25,825 keys of live agent state (AgentSession, Room, Job, subconscious memory, steering lists,
DM coverage epochs) were destroyed. Recovery took an AOF point-in-time restore, ~30s of Redis
downtime, and ~2 minutes of discarded writes.

**This was the second incident of this exact class.** On 2026-06-03 a db-0 flush wiped the same
dataset *unrecoverably* — AOF was off then. The fix that shipped afterwards was a monkeypatch guard in
`tests/conftest.py:103-150` that raises on `flushdb()` against db 0. Its own comment states the
limitation that let 2026-08-07 happen: *"This patch lives in conftest.py, so it only affects pytest
runs; production code is untouched."* The mechanism was right; the scope was wrong. An ad-hoc script
is not a pytest run.

**Current behavior:**

- A standalone script inherits a production-pointed `REDIS_URL` by default. Targeting a test db
  requires the author to *override*, and the most natural idiom (`setdefault`) silently fails to.
- Outside pytest there is nothing between `redis.Redis(...).flushdb()` and the production dataset.
- The Redis server accepts `FLUSHDB`/`FLUSHALL` from any client with no restriction. `ACL LIST` shows
  one user: `default on nopass sanitize-payload ~* &* +@all`.
- The PreToolUse hook layer blocks raw `delete`/`srem`/`sadd`/`zrem`/`hgetall` on Popoto-managed keys
  but has no rule for `flushdb`/`flushall` — the strictly more destructive operation.
- `CLAUDE.md` § Manual Testing Hygiene warns about raw Redis on Popoto keys but never names the
  env-var-inheritance foot-gun that actually caused both incidents.

**Desired outcome:** four independent layers, any one of which would have stopped the 2026-08-07
script.

| Layer | Boundary | Stops |
|---|---|---|
| 1. Process-wide flush guard | Every Python process using a repo venv | The incident exactly: any `.flushdb()` on db 0, any `.flushall()` |
| 2. Redis ACL | The Redis server | Non-Python clients (`redis-cli`, other languages, other checkouts) — **only after an operator runs the apply runbook by hand** (this PR ships the planner, the config template, and the runbook; it never mutates the live server) |
| 3. PreToolUse hook validator | Agent-issued Bash | An agent typing a flush before it ever reaches an interpreter |
| 4. `CLAUDE.md` | The agent's read-before-testing surface | The `setdefault` reasoning error at its source |

## Freshness Check

**Baseline commit:** `76a23e15a`
**Issue filed at:** 2026-08-07T06:31:27Z (same day)
**Disposition:** **Overlap** — an in-flight sibling plan owns part of the surface. No code drift.

**File:line references re-verified:**

- `.claude/hooks/validators/validate_no_raw_redis_delete.py` — issue claims it blocks raw
  delete/srem/sadd/zrem but has no flush rule — **still holds**, verified in full (162 lines,
  `_BLOCK_PATTERNS` has no `flush*` entry).
- `tests/conftest.py:103-150` — the pytest-only db-0 guard — **still holds** (the issue did not cite
  this; it is the decisive find of recon).
- `CLAUDE.md` § Manual Testing Hygiene — present, does not name `setdefault` — **still holds**.
- `/opt/homebrew/etc/redis.conf` — `aclfile` commented at line 1033, sole `user default …` line at
  2301 — **still holds**.

**Cited sibling issues/PRs re-checked:**

- **#2636** (the originating popoto work) — still OPEN. Nothing in this plan depends on it, as the
  issue states.
- **#2628** (`suite-failure-rotation-db-ownership`) — still OPEN, plan `status: Ready`, critique
  round 7, **no branch and no PR yet**. This is the overlap. See below.

**Commits on main since issue was filed (touching referenced files):** none. `git log --since` over
`validate_no_raw_redis_delete.py`, `CLAUDE.md`, `tests/conftest.py`, `config/settings.py`, and
`.claude/hooks/manifest.toml` returns only `76a23e15a`, which touches none of them.

**Active plans in `docs/plans/` overlapping this area:**
`suite-failure-rotation-db-ownership.md` (#2628). Its Task 3 rewrites `tests/conftest.py:103-150`
wholesale into an *ownership* guard keyed on a new `claimed_test_dbs()` in `tests/db_claim.py`, and it
addresses this issue by name six times, verbatim: *"Compose with #2645, do not parallel it… Two
independently-maintained flush guards is how one of them drifts."* It claims the entire test-side
guard and never claims Layers 1-4 here.

The overlap is resolved by a **hard file seam**, not by coordination: this plan's diff does not touch
`tests/conftest.py`, `tests/db_claim.py`, or any `redis.Redis(db=…)` site under `tests/`. That is
enforced by an anti-criterion in `## Verification`, not by discipline. The two guards are not
duplicates — #2628 guards *test* processes against flushing an *unowned test db*; this plan guards
*every* process against flushing *production*. They compose as nested wrappers (see Technical
Approach), and the plan proves the composition rather than assuming it.

**Notes:** Recon corrected two premises in the issue's own solution sketch; both corrections are
recorded in the issue's Recon Summary and drive the design below.

## Prior Art

- **2026-06-03 incident (no issue number recovered; fix is in-tree)** — added
  `_install_redis_db0_flush_guard` to `tests/conftest.py` plus
  `tests/unit/test_redis_flush_guard.py`. Succeeded at its stated scope, which excluded production.
  This plan promotes that mechanism to process scope; the code is the direct ancestor of Layer 1.
- **AOF durability hardening** — `scripts/update/redis_persistence.py` + `/update` Step 3.13 +
  `tools/doctor.py::_check_redis_durability`. Landed after 2026-06-03 and is why 2026-08-07 was
  recoverable at all. It is also the exact structural precedent this plan reuses for Layer 2: an
  idempotent, non-fatal, `/update`-driven Redis server-config step with a matching doctor check.
- **#2435 hook dispatcher consolidation** — replaced 7 PreToolUse validator stanzas with one
  in-process dispatcher (`.claude/hooks/dispatch/pre_tool_use_bash.py`). Means Layer 3 needs **no**
  `manifest.toml` stanza, only a `_VALIDATORS` entry.
- **#2448 `validate_no_destructive_git_in_shared_checkout` and #2562 `validate_no_broad_process_kill`**
  — the two validators added to `_VALIDATORS` *after* the #2435 consolidation. They are the working
  template for exactly what Task 4 does: new validator file, `_VALIDATORS` entry, **no** new
  `manifest.toml` stanza, manifest comment-count bump. **#2562 is the closest behavioural analogue** —
  a hard block on a destructive command family (`pkill -f pytest`) paired with a documented sanctioned
  alternative (`scripts/reap-xdist.sh --apply`) named in the block message. Task 4 copies its shape.
- **`scripts/update/redis_replication.py` (#1827)** — the decisive precedent for revised Layer 2. It is
  a Redis server-config `/update` step that **never mutates the live server**: it gates on an
  operator-created `data/redis-replication-enabled` marker, and even when opted in it only *stages a
  config template* for the operator to substitute and restart. Layer 2 adopts this posture wholesale
  (see D8) instead of the apply-on-`/update` shape `redis_persistence.py` uses.
- **#2606 / #2060 per-process test-db claim** (`tests/db_claim.py`) — the flock-based db claim.
  Referenced only; not modified.
- **#2628 `suite-failure-rotation-db-ownership`** — in flight, owns the test-side guard. See Freshness
  Check.

## Research

Purely internal to this machine's Redis, venv, and hook plumbing, plus Redis 8 ACL semantics which
were resolved empirically rather than by search (spike-1 below is more authoritative than
documentation for this machine's exact build). No WebSearch performed.

No relevant external findings — proceeding with codebase context and the four spikes.

## Spike Results

### spike-1: Can any server-side mechanism permit `FLUSHDB` on dbs 1-15 while denying db 0?

- **Assumption**: "Either `rename-command` or an ACL rule can discriminate by database number."
- **Method**: prototype (throwaway `redis-server` on ports 6399/6398, own dir under `/tmp`;
  production 6379 received read-only commands only)
- **Finding**: **No — definitively, for both mechanisms.**
  - `ACL SETUSER u db:1` → `ERR Error in ACL SETUSER modifier 'db:1': Syntax error`. There is no
    db-scoped ACL syntax.
  - ACL *selectors* accept a key pattern, but `FLUSHDB` takes no key arguments, so the pattern is
    vacuously satisfied. A user configured `-flushdb '(+flushdb ~onlythis:*)'` **successfully flushed
    db 0**. Selectors are a trap here, not a solution.
  - Plain denial works and is db-blind: `NOPERM User nof has no permissions to run the 'flushdb'
    command`, in every db.
  - `rename-command FLUSHDB ""` works in 8.6.2 but the shipped homebrew config marks it
    **`(DEPRECATED)`** and explicitly recommends ACLs instead; it is all-or-nothing across dbs and
    downgrades the client error to a generic `ResponseError: unknown command`.
  - `ACL SETUSER default … -flushall -flushdb` → redis-py raises
    `redis.exceptions.NoPermissionError` (a `ResponseError` subclass). All other commands unaffected.
  - A second user (`ACL SETUSER testrunner on >pw ~* &* +@all`) retains flush, reachable via
    `redis://testrunner:pw@host:port/1`, in the same process where a default-user client is denied.
  - Persistence needs `aclfile` in redis.conf: `ACL SAVE` errors without one, and `aclfile` is
    immutable at runtime (`CONFIG SET aclfile` → `can't set immutable config`). One restart required.
    `CONFIG REWRITE` is the alternative but rewrites the whole 107k-line homebrew config.
- **Confidence**: high
- **Impact on plan**: Kills the issue's open question. The only available discriminator is **user
  identity**, so Layer 2 is an ACL credential split — and the direction of the split is forced by
  spike-4 (see D3 in Technical Approach).

### spike-2: Can a guard be installed process-wide without the script author opting in?

- **Assumption**: "A `sitecustomize.py` in the venv will auto-install the guard for any Python
  process."
- **Method**: prototype (marker files planted and removed in the real venv; `uv` behavior tested in a
  throwaway `/tmp` venv)
- **Finding**:
  - **`sitecustomize.py` does NOT run.** Homebrew ships its own at
    `…/python@3.14/…/lib/python3.14/sitecustomize.py`, which precedes site-packages on `sys.path` and
    permanently shadows a venv-level copy. A guard installed this way would silently never run — the
    worst possible failure mode, and one that would have looked green in review.
  - **A `.pth` file DOES run** at interpreter startup, per site-directory, immune to that shadowing.
  - **The repo is already importable from site-packages**: `_editable_impl_valor_bridge.pth` (editable
    hatchling install, `packages = ["bridge","tools","scripts","agent","utils","ui"]`) puts the repo
    root on `sys.path`, and alphabetical `.pth` processing guarantees it runs before a `zzz_*.pth`.
    Verified by planting a `.pth` containing `import tools` and running from `/tmp`. So there can be
    exactly **one** guard implementation, in `tools/`.
  - **`uv` does not delete untracked site-packages files**: the planted `.pth` + module survived
    `uv sync`, `uv pip install requests`, and `uv sync --reinstall`. uv prunes only what it tracks in
    `RECORD`. The one destructive path is full venv recreation (`uv venv`).
  - **17 venvs** currently: `/Users/valorengels/src/ai/.venv` + 13 `.worktrees/*/.venv` + 3
    `.claude/worktrees/*/.venv`, plus one per new worktree.
- **Confidence**: high
- **Impact on plan**: Layer 1's install mechanism is a `.pth`; `sitecustomize.py` is a recorded No-Go.
  The engineering cost is propagation (17 venvs, recreated freely), not mechanism — hence an
  idempotent installer wired into both `/update` and worktree venv bootstrap, plus a doctor check that
  asserts the guard is **live**, not merely that the file exists.

### spike-3: Where does first-party code build Redis clients, and does any of it flush?

- **Assumption**: "Some production code legitimately flushes, so a blanket guard would break things."
- **Method**: code-read census
- **Finding**: **False — zero first-party non-test code calls `flushdb()` or `flushall()`.** Every hit
  in the repo is under `tests/` or `docs/`. Meanwhile **21 first-party sites** build a client from
  `os.environ["REDIS_URL"]` with a `redis://…/0` default (`agent/output_handler.py:438,447`,
  `agent/session_completion.py:424,604`, `bridge/{email_relay,routing,liveness,email_dead_letter,dedup,telegram_relay,email_bridge}.py`,
  `tools/{valor_email,valor_telegram,react_with_emoji,send_message,email_history}.py`,
  `monitoring/bridge_watchdog.py:195`, `reflections/pm_briefings/delivery.py:78`, `ui/app.py:380,426,622`),
  plus 2 pool-derived clients in `agent/agent_session_queue.py:1088,1194` and the bootstrap at
  `config/redis_bootstrap.py:106-132`, which hand-`urlparse`s the URL. **It extracts the password only**
  (`:112` `password = parsed.password or None`) and passes no `username=` into
  `set_REDIS_DB_settings(...)` — re-verified at `76a23e15a`. It is the sole first-party site that
  hand-parses; the other 20 use `redis.Redis.from_url`, which handles the username itself.
  Popoto reads **`REDIS_URL` only**, falling back to a hardcoded `127.0.0.1:6379` **db 0**;
  `popoto/pytest_plugin.py:171,194` flush the plugin's own test db (15 or `POPOTO_TEST_DB`).
- **Confidence**: high
- **Impact on plan**: (a) a blanket flush guard breaks nothing; (b) credentials placed in `REDIS_URL`
  reach **20 of the 21 sites free** (they all `from_url`), and the 21st — `config/redis_bootstrap.py`,
  which feeds popoto, the exact client the incident flushed — needs **one bootstrap edit** to forward
  the username. That edit lands in this PR (D9) and is inert until the #2661 rotation; (c) the
  issue's proposed *connection*-construction guard on `db=0` is refuted — production runs on db 0.

### spike-4: What exactly does the in-flight sibling plan own?

- **Assumption**: "#2628 and #2645 can be built independently."
- **Method**: code-read of `docs/plans/suite-failure-rotation-db-ownership.md` (1985 lines)
- **Finding**: True **only under a specific seam**. #2628 is `status: Ready`, critique round 7, no
  branch, no PR. Its Task 3 rewrites `tests/conftest.py:103-150` in place; Tasks 1/5 rewrite
  `tests/db_claim.py`; Task 4 converts 5 `redis.Redis(db=…)` sites under `tests/`; Task 10 creates
  `docs/features/test-db-ownership.md`. It changes **no** production code, **no** `redis.conf`, **no**
  hooks, **no** `CLAUDE.md`; it self-describes as "a pure-`tests/` PR". It leaves Layers 1-4 here
  entirely, constraining only that a server-side rule not encode a static per-db allowlist.
- **Confidence**: high
- **Impact on plan**: gives the hard seam (see No-Gos and the `## Verification` anti-criterion), and —
  decisively — means the ACL split must require **zero test-side changes**, which selects the
  direction of Layer 2 (D3 below).

## Data Flow

The incident path, and where each layer intercepts it:

1. **Entry point**: an agent writes `/tmp/debug.py` containing
   `os.environ.setdefault("REDIS_URL", "…/15")` then `POPOTO_REDIS_DB.flushdb()`, and runs it with
   Bash: `.venv/bin/python /tmp/debug.py`.
   → **Layer 3** inspects the Bash command string. If the flush is written inline (`python -c`, a
   heredoc, or `redis-cli … flushdb`) it blocks here. A flush hidden inside an already-written file is
   not visible to a Bash validator; that is Layer 1's job, and is why Layer 3 alone is insufficient.
2. **Interpreter startup**: `site` processes `.pth` files in site-packages alphabetically —
   `_editable_impl_valor_bridge.pth` puts the repo root on `sys.path`, then
   `zzz_redis_flush_guard.pth` imports the boot shim, which imports `tools.redis_flush_guard` and
   monkeypatches `flushdb`/`flushall` on `redis.Redis` and `redis.asyncio.Redis`.
   → **Layer 1** is now armed, before any user code runs.
3. **`import popoto`**: `popoto/redis_db.py` reads `REDIS_URL` (unchanged by `setdefault`, so
   production) and builds `POPOTO_REDIS_DB` on **db 0**.
4. **`POPOTO_REDIS_DB.flushdb()`**: the patched method reads
   `self.connection_pool.connection_kwargs["db"]` → `0` → raises `RuntimeError` **before any bytes
   reach the socket**.
   → **Layer 1** stops the incident. This is the load-bearing layer.
5. **Had the call reached the server** (a non-venv Python, another language, a bare `redis-cli`):
   the connection authenticated as `valor-app`, whose ACL lacks `flushdb`/`flushall`, so the server
   replies `NOPERM`.
   → **Layer 2**, effective for db-0 traffic once the production `REDIS_URL` is rotated to the
   credentialed form.
6. **Output**: `RuntimeError` (Layer 1) or `NoPermissionError` (Layer 2), each naming the override and
   the correct idiom. **Layer 4** is what makes an agent not write step 1 in the first place.

Test traffic takes a disjoint path: `tests/conftest.py` builds `redis.Redis(db=<claimed 1-15>)` with
no credentials → authenticates as `default`, which retains `flushdb` → permitted by Layer 2, then
permitted by Layer 1 (`db != 0`), then flushed. No test-side change is required at any step.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|---|---|---|
| 2026-06-03 conftest guard (`tests/conftest.py:103-150`) | Monkeypatched `flushdb`/`flushall` on sync + async `redis.Redis` to raise on db 0 | Correct mechanism, scoped to pytest **by explicit design** — its own comment says "only affects pytest runs; production code is untouched". The 2026-08-07 offender was a standalone script, so the guard was simply not loaded. |
| AOF durability (`/update` Step 3.13) | Enabled `appendonly yes` + `appendfsync everysec`, persisted to redis.conf | Did not fail — it is why 2026-08-07 was recoverable where 2026-06-03 was not. But it is a *recovery* control, not a *prevention* control: it still cost ~30s downtime and ~2 minutes of discarded writes. |
| `validate_no_raw_redis_delete.py` | PreToolUse block on raw `delete`/`srem`/`sadd`/`zrem`/`hgetall` against Popoto keys | Covers the *less* destructive operations and gates every rule behind a `_POPOTO_CONTEXT` token. A bare `redis.Redis().flushdb()` or `redis-cli -n 0 flushdb` carries no Popoto token, so even adding `flush*` to its existing pattern list would leave the most dangerous shape unblocked. |

**Root cause pattern:** every prior control was installed at a boundary the offending code did not
cross. The conftest guard lives in the test harness; the hook validator gates on Popoto vocabulary;
AOF acts after the damage. The fix is not a better rule, it is **moving the same rule to a boundary
every caller must cross** — the interpreter itself (Layer 1) and the server (Layer 2).

## Architectural Impact

- **New dependencies**: none. `redis` is already a direct dependency; the guard imports nothing else.
- **Interface changes**: none to any existing function. New public surface:
  `tools/redis_flush_guard.py::install()` / `::is_installed()`, one new `RedisSettings` field, one new
  `scripts/update/` module pair, one new hook validator.
- **Coupling**: Layer 1 adds a process-wide monkeypatch on a third-party class. That is real global
  coupling and is accepted deliberately — a guard that can be bypassed by not importing it is not a
  guard. It is mitigated by being lazily armed (no `import redis` in a process that does not use
  Redis — D2a), fully exception-wrapped, and idempotent via a class registry (D6a).
- **Startup cost**: bounded and measured against `_STARTUP_BUDGET_MS`, not asserted. See Risk 7.
- **Data ownership**: unchanged. No Popoto model changes, so **no schema migration is required**.
- **Reversibility**: high, and for Layer 2 total. Layer 1 = delete two files per venv (`/update` and
  the self-heal re-install, so removal means deleting the installer too). **Layer 2 = nothing to
  revert — this PR changes no server state (D8);** reverting the operator's later apply is
  `ACL SETUSER default … +@all`, `ACL DELUSER valor-app`, and dropping the `aclfile` line. Layer 3 =
  one line out of `_VALIDATORS`. Layer 4 = a doc revert.

## Appetite

**Size:** Large

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 2-3 (the Layer-2 credential rotation is a judgment call with a machine-fleet blast
  radius, and the seam against #2628 needs confirming if #2628 starts building concurrently)
- Review rounds: 2+

Four layers across four subsystems (interpreter bootstrap, Redis server config, hook harness, docs),
a fleet-propagation concern touching 17+ venvs and every machine in the roster, and a live seam
against an in-flight sibling plan. Large is honest.

## Prerequisites

| Requirement | Check Command | Purpose |
|---|---|---|
| Redis reachable | `redis-cli -h 127.0.0.1 -p 6379 PING` | Layer 2 provisioning and verification |
| Redis ≥ 6 (ACL support) | `redis-cli ACL WHOAMI` | ACL semantics the planner reports against. (`ACL WHOAMI` exists only on Redis ≥ 6, so it is the version check — and unlike a piped `INFO server \| grep` it survives the prerequisite checker's shell parsing.) |
| Editable repo install in venv | `python -c "import tools, sys; print(tools.__file__)"` | The `.pth` boot shim must be able to import `tools.redis_flush_guard` |

Run via `python scripts/check_prerequisites.py docs/plans/redis-flush-hardening.md`.

**Deliberately *not* prerequisites** (both were, and both blocked the build):

- **Write access to `/opt/homebrew/etc/redis.conf`.** Nothing in this PR writes redis.conf. The
  `aclfile` directive is staged as a template for the operator (D8), so the builder never needs the
  write bit.
- **`REDIS_APP_PASSWORD` in the vault `.env`.** The builder is forbidden from writing the vault
  (CLAUDE.md § Secrets), so requiring it here deadlocked the build before task 1. With the apply moved
  behind human sign-off the password is not needed at build time at all: `RedisSettings.app_password`
  defaults to `""` so `Settings()` never fails without it, and the **report** path plans all four
  commands regardless, substituting a literal `<REDIS_APP_PASSWORD>` placeholder token (D8a). Only the
  **apply** path skips on a missing password. The password requirement moves to the operator checklist
  in `docs/features/redis-flush-hardening.md`.

## Solution

### Key Elements

- **`tools/redis_flush_guard.py`** — the single implementation. Patches `flushdb`/`flushall` on
  `redis.Redis` and `redis.asyncio.Redis` so that any `flushall`, and any `flushdb` on db 0, raises
  `RuntimeError` unless `REDIS_PRODUCTION_FLUSH_OK=1` is set. Patching is **lazy** (armed on first
  `import redis`, not at every interpreter start — D2), idempotent via a module-level class registry
  (D6), and it never raises at import time.
- **`scripts/update/redis_flush_guard_pth.py`** — idempotent installer that writes
  `zzz_redis_flush_guard.pth` + a `_redis_flush_guard_boot.py` shim into every repo venv's
  site-packages. Exposes `install_into(venv_path)` for the single-venv self-heal path. Called from
  `/update`, from worktree venv bootstrap, and from the guard's own self-heal.
- **`scripts/update/redis_acl.py`** — a Redis ACL **planner**, report-only by default and modeled on
  `redis_replication.py` rather than `redis_persistence.py`. It reads the current `ACL LIST`, computes
  the four commands that would converge the server onto the target rule set, and **returns them**. It
  **never** writes `redis.conf` on any path — the `aclfile` directive is emitted as text in the result
  for the operator to add by hand. It issues no `ACL SETUSER` and no `ACL SAVE` unless *both* the
  operator marker `data/redis-acl-enabled` exists *and* `REDIS_ACL_APPLY=true` is set — a combination
  `/update` never supplies (D8). Non-fatal on every path.
- **`.claude/hooks/validators/validate_no_redis_flush.py`** — a *new* PreToolUse predicate (not an
  extension of the raw-delete one) that blocks flush call shapes in agent Bash **unconditionally**,
  with no Popoto-context gate.
- **Two `tools/doctor.py` checks** — one asserting the guard is *live* in this interpreter, one
  asserting the ACL state on this machine's Redis.
- **`CLAUDE.md` § Manual Testing Hygiene** — names the `setdefault` foot-gun and the sanctioned idiom.

### Flow

**Agent writes a debug script** → [Bash tool call] → **Layer 3 validator inspects the command** →
[inline flush? block with the correct idiom] → **interpreter starts** → [`.pth` arms Layer 1 before
user code] → **script calls `.flushdb()` on db 0** → [Layer 1 raises `RuntimeError` naming
`REDIS_PRODUCTION_FLUSH_OK` and the test-db idiom] → **agent reads the message and retargets** →
production intact.

Non-Python client → **Redis server** → [Layer 2: `NOPERM` for `valor-app`] → production intact.

### Technical Approach

**Index of decision blocks.** They are emitted out of numeric order because the revision-round
additions (D8/D8a/D9) were appended after D4. Read them in this listed order; the numbering is stable
and `Informed By` lists refer to these labels.

| Block | Subject |
|---|---|
| D1 | Guard the operation, not the connection |
| D2, D2a, D2a-i, D2a-ii | `.pth` install, lazy arming, the finder contract, startup measurement |
| D2b, D2b-i | Self-heal into venvs nothing provisions, and the trigger that fires there |
| D3, D4 | ACL split by identity; rotation sequenced behind fleet readiness |
| D5, D5a, D5b | Layer 3 validator, its escape, and keeping Verification runnable |
| D6, D6a | Coexistence with `tests/conftest.py`, and why idempotence keys on a registry |
| D7 | No verification may flush db 0 if the guard is absent |
| D8, D8a | Layer 2 ships report-only; the password gates the apply path only |
| D9 | Popoto username plumbing, inert until rotation |

**D1 — Guard the operation, not the connection. (Corrects the issue's Layer-1 sketch.)**
The issue proposes a helper that "refuses `db=0` unless `REDIS_PRODUCTION_OK=1`". Production *is* db 0
— spike-3 found 21 first-party sites that legitimately construct db-0 clients. A construction-time
guard would either break the entire system or be permanently overridden, and the override would then
live in exactly the shell an ad-hoc script inherits, reproducing the `setdefault` failure one level
up. Guard the destructive **operation** instead: connection construction stays unrestricted; `flushdb`
on db 0 and `flushall` anywhere raise. The override is named `REDIS_PRODUCTION_FLUSH_OK` (not
`…_OK`) so its meaning is unambiguous and it grants nothing else.

**D2 — Install via `.pth`, never `sitecustomize.py`.**
Per spike-2, homebrew's stdlib `sitecustomize.py` shadows any venv copy, so that route yields a guard
that silently never runs. The `.pth` route is verified working and orders correctly behind the
existing editable-install `.pth`. Two files per venv:

- `zzz_redis_flush_guard.pth`, a single line: `import _redis_flush_guard_boot`
- `_redis_flush_guard_boot.py`, a shim whose entire body is
  `try: import tools.redis_flush_guard; tools.redis_flush_guard.arm()` / `except Exception: pass`

The shim exists because `.pth` lines cannot express a `try`/`except` cleanly, and an uncaught
exception at startup prints a traceback into every `python -c`, launchd service, and hook invocation
on the machine. The `zzz_` prefix is load-bearing: `.pth` files are processed in sorted order and the
guard must run after `_editable_impl_valor_bridge.pth` has put the repo root on `sys.path`.

**D2a — Arm lazily; do not import `redis` at every interpreter start.**
`install()` must import `redis` *and* `redis.asyncio` to patch the classes, and `redis.asyncio`
transitively pulls in `asyncio` and `ssl`. The `.pth` runs in **every** Python process started from a
repo venv — every `python -c`, every CLI, every launchd start, and every PreToolUse hook invocation,
which fires on every single Bash tool call under the dispatcher's shared `timeout = 20`. Paying an
unmeasured `import redis` there is a real cost imposed on the machine's hottest path, and the original
"import-cheap" claim in `## Architectural Impact` was an assertion with no measurement behind it.

So the boot shim calls `arm()`, not `install()`. `arm()` inserts a `sys.meta_path` finder that calls
`install()` on the first *actual* import of `redis` or `redis.asyncio`. A process that never touches
Redis pays only the finder insertion. `install()` stays the eager public entry point for unit tests
and the doctor's subprocess probe — the probe imports `redis` anyway, so lazy arming is invisible to
it and the sentinel assertion is unchanged. The finder must be import-order-agnostic: `arm()` calls
`install()` immediately if `redis` is already in `sys.modules` when it runs.

**D2a-i — The finder contract is specified here, not left to the builder. (Revision-round fix.)**
The naive reading of "a finder that calls `install()` on import of `redis`" is broken in two ways, and
both land in the silent-inert class Risk 1 names as this design's primary hazard — reached, ironically,
through the mechanism added to fix Risk 7. `find_spec` runs **before** the module object exists, so
calling `install()` there patches a half-initialized module and arms nothing; and `install()` itself
does `import redis`, which re-enters the finder and recurses. The contract:

- The finder is a `MetaPathFinder` whose `find_spec` returns `None` for every name outside
  `{"redis", "redis.asyncio"}` — the overwhelmingly common case, and it must be cheap.
- For those two names it **removes itself from `sys.meta_path`**, calls `importlib.util.find_spec(fullname)`
  to get the real spec from the remaining finders, then **re-inserts itself**.
- It wraps `spec.loader.exec_module` so that `install()` runs **after** the original `exec_module`
  returns — i.e. against a fully initialized module, never a half-built one.
- A module-level `_ARMING = False` re-entrancy flag guards the wrapper, so `install()`'s own
  `import redis` cannot re-trigger it.
- The `install()` call inside the wrapper is wrapped in `try/except Exception: pass`. A guard failure
  must never break an unrelated `import redis` — the guard is a safety net, not a dependency.
- `redis.asyncio` imports `redis` as its parent, so both names can fire in one process. `install()`
  stays idempotent across them via the `_INSTALLED` registry (D6a).

Liveness for this mechanism is asserted in a **subprocess** probe, never in-process (D6a).

The startup cost is measured, not asserted, against `_STARTUP_BUDGET_MS` in
`tools/redis_flush_guard.py`:

```python
# Provisional, tunable — take with a grain of salt. Overridable via env for a slow
# machine; the point is that a regression here is loud, not that 15 is sacred.
_STARTUP_BUDGET_MS = float(os.environ.get("REDIS_FLUSH_GUARD_STARTUP_BUDGET_MS", "15"))
```

**D2a-ii — Measure by toggling this guard's own `.pth`, not against `-S`. (Revision-round fix.)**
The first draft specified the measurement as a diff of `python -X importtime -c pass` with and without
the `.pth`, but the Verification row said "cumulative delta vs. a `-S` baseline". Those are different
quantities: `-S` skips the entire `site` machinery — including `_editable_impl_valor_bridge.pth` and
every other `.pth` on the machine — so its delta measures the cost of `site`, and would blow a 15 ms
budget on its own with this guard contributing nothing. The row's mechanics were wrong too:
`-X importtime … | tail -1` prints the last individual import line, not a cumulative total.

Fix, **without adding a kill-switch env var** (a second, broader bypass of the load-bearing layer,
invisible to Layer 3's validator, is too high a price for a benchmark): `-X importtime` traces
`.pth`-driven imports and gives `_redis_flush_guard_boot` its own `import time:` line. So the
measurement parses the **cumulative** field of that specific line out of a single
`python -X importtime -c pass` run. That is exactly and only this guard's startup cost, on the real
hot path. On a venv the installer has not yet healed, the same parse runs against a fresh copy of the
shim on `PYTHONPATH`. Note the "final `import time:` line" wording of the first draft was also wrong:
the last line is whatever imported last (`linecache` on this machine), not a cumulative total.

Because `-X importtime` reports wall clock, a single sample on a machine running several parallel
agents measures contention as much as it measures the guard — the same import that costs ~6 ms idle
was observed at ~107 ms under a loaded xdist run. The case therefore takes the **minimum across five
trials**, the standard estimator for the uncontended cost, so the assertion is a budget gate rather
than a load sensor.

**That subtraction lives in a pytest case, not in a human's eyeball** — otherwise `_STARTUP_BUDGET_MS`
is a constant in a production module that no code reads, and a criterion no command evaluates is one a
validator pencil-whips. The test spawns the two subprocesses and asserts the delta, so a regression
fails CI; mark it `slow` if two spawns are unwelcome in the fast suite. The measured number still goes
in the PR body, but the assertion is what enforces it.

**D2b — The guard self-heals into venvs nothing provisions. (Revision-round fix.)**
The first draft wired the installer into `/update` and into `agent/worktree_manager.py`'s venv
bootstrap, and called propagation solved. It is not: `tools/venv_health.py:9` and `tools/doctor.py:246`
both document `.claude/worktrees/{agent}/` checkouts as **harness-created** — `provision_worktree_venv`
is not on that path, and `tools/doctor.py` says in as many words that "nothing provisions those."
spike-2 counted 3 such venvs. Those are precisely the checkouts where an agent writes an ad-hoc debug
script, so under the first draft the load-bearing layer was absent in the highest-risk location on the
machine, from worktree creation until whenever someone next ran `/update`. Two wiring points cannot
cover a checkout neither of them sees; the third has to be the guard itself.

`tools/redis_flush_guard.install()` therefore self-heals: when it runs in a venv whose `site-packages`
lacks `zzz_redis_flush_guard.pth`, it calls `scripts.update.redis_flush_guard_pth.install_into(venv_path)`
for that one venv. Constraints — this runs on an interpreter-startup path, so it must be invisible when
it cannot work:

- Wrapped in `try/except Exception: pass`. A failure to self-heal never propagates.
- Skipped on read-only site-packages, and skipped when not running from a venv at all.
- Reuses the write-temp + `os.replace()` path from Race 1, so a partial write is impossible even if
  two interpreters self-heal the same venv concurrently.
- Scoped to the **current** venv only. It never walks the machine — `/update` owns fleet-wide
  propagation, and a startup path that iterates 17 venvs is its own incident.

**D2b-i — The self-heal needs a trigger that actually fires in a harness worktree. (Revision-round
fix.)** The self-heal runs only from inside `install()`, and `install()` runs only when something
imports `tools.redis_flush_guard`. In an unhealed `.claude/worktrees/{agent}/` checkout, nothing does
— the `.pth` that would call `arm()` is precisely the file that is missing. The first draft conceded
this ("the self-heal lands whenever *something* imports `tools.redis_flush_guard`") without naming an
importer that runs there, which leaves the highest-risk checkout on the machine unguarded
**indefinitely**, not "until the next `/update`". Acknowledging the gap is not closing it.

Fix: give it a trigger on a path that always runs. `tools/__init__.py` (today a 16-line docstring, no
imports) gains:

```python
try:  # ambient production-flush guard; see docs/features/redis-flush-hardening.md
    from tools.redis_flush_guard import arm

    arm()
except Exception:  # never break `import tools`
    pass
```

This is safe on a hot path **precisely because `arm()` is lazy** (D2a): it imports no `redis`, it only
inserts the meta-path finder. And `import tools` genuinely happens in a harness worktree — every
`python -m tools.*` CLI, every `tools.doctor` run, and any first-party import in that checkout, since
the existing `_editable_impl_valor_bridge.pth` already puts the repo root on `sys.path` there. The
`except Exception: pass` is mandatory: a guard that can break `import tools` is worse than no guard.

Considered and rejected: hanging the trigger off `.claude/hooks/dispatch/pre_tool_use_bash.py`, which
fires on every agent Bash call. It has an established trigger but spends the dispatcher's shared
`timeout = 20` budget on every single tool call for a job `import tools` already covers.

Note the residual ordering subtlety: on the very first interpreter start in an unhealed venv, before
anything imports `tools`, the process is unguarded. This is a convergence mechanism, not a first-run
guarantee; the doctor check is what makes the remaining gap visible. Accordingly
`_check_redis_flush_guard`'s FAIL text names the per-venv installer invocation
(`python -m scripts.update.redis_flush_guard_pth --venv <path>`), not just `/update`, so an operator
can heal one harness worktree without a full update run.

**The silent-inert failure mode is the primary hazard of this design and is handled explicitly.** The
shim swallows exceptions, so a broken install looks identical to a working one. Therefore
`tools/doctor.py` must assert the guard is **live** — `getattr(redis.Redis.flushdb, "_prod_flush_guarded", False) is True` —
and must not merely `test -f` the `.pth`. That check is a Success Criterion, not a nicety.

**D3 — Layer 2 denies flush to the *production* identity, not to the test identity. (Resolves the
issue's open question, in the opposite direction from its sketch.)**
Spike-1 proves no server-side mechanism can see the database number, so the split must be by user.
The issue implies denying `default` and giving tests a privileged user. That inverts badly: every test
path would need credentials, forcing edits into `tests/conftest.py:616-617` and
`tests/db_claim.py::redis_test_url` — both owned by in-flight #2628, and a guaranteed conflict. Invert
it:

- Create **`valor-app`** with `+@all -flushdb -flushall` and a password from `REDIS_APP_PASSWORD`.
  The production `REDIS_URL` becomes `redis://valor-app:<pw>@localhost:6379/0`, which reaches **20 of
  the 21** call sites for free because they all read `REDIS_URL` through `redis.Redis.from_url`. The
  21st, `config/redis_bootstrap.py`, hand-parses and drops the username; **D9 lands the one-line fix
  in this PR** so that after rotation popoto authenticates as `valor-app` rather than failing.
- Leave **`default`** able to `flushdb`. Tests build bare `redis.Redis(db=N)` with no credentials,
  authenticate as `default`, and keep working with **zero changes** — the seam against #2628 holds.
- Remove **`flushall`** from `default` immediately. Spike-3 found zero first-party callers and
  `conftest` already blocks it universally, so this costs nothing and closes the wipe-every-db vector
  at the server this PR.

**D4 — The `REDIS_URL` rotation is sequenced behind fleet readiness, and so is the ACL apply itself.**
The rotation is fail-loud by construction: a machine whose Redis lacks `valor-app` gets `WRONGPASS`
and its bridge/worker go down noisily. That is the correct posture (silent degradation would be
worse), but it means the vault `.env` — iCloud-synced to every machine at once — must not be rotated
until every machine in the roster has had the ACL applied. Editing the vault `.env` is also an action
an agent must never take (CLAUDE.md § Secrets). So this plan ships the planner, the target rule set,
the runbook, and the doctor check that reports fleet readiness; the one-line rotation is an
`[EXTERNAL]` No-Go tracked as **#2661**.

Layers 1 and 3 already make a db-0 flush impossible for agent-driven clients, which is what
acceptance criterion 2 asks for. Layer 2 is the server-layer backstop for everything that is not a
repo-venv Python process, and it lands in two moves: this PR ships the mechanism, an operator applies
it.

**D8 — Layer 2 ships report-only. `/update` must never apply it; the apply is a human-signed runbook
step. (Revision-round decision.)**
The first draft wired `redis_acl.py` as an unconditional `/update` Step 3.135 that issued
`ACL SETUSER valor-app …`, `ACL SETUSER default … -flushall`, appended an `aclfile` directive to
`/opt/homebrew/etc/redis.conf`, and `ACL SAVE`d. Because `/update` is mandatory after every merge,
**merging the PR would have been the apply** — a live mutation of the production Redis ACL, on the
same instance the incident just disrupted, with no human in the loop. That is not authorized in this
work, and no amount of care inside the provisioner changes it. The layer is not descoped; its
*trigger* moves.

The shape follows `redis_replication.py` (#1827), which already solves this exact problem for a Redis
server-config `/update` step:

- **`apply_redis_acl(apply: bool = False) -> RedisAclResult`.** With `apply=False` — the default and
  the only value `/update` ever passes — it reads `ACL LIST`/`ACL GETUSER`, diffs against the target
  rule set, and returns `planned_commands: list[str]` plus a `drift: bool`. It issues **no**
  `ACL SETUSER`, **no** `ACL SAVE`, and writes **no** file. Report-only means report-only.
- **Two independent gates for `apply=True`**, both operator actions, neither of which any automation
  performs: the marker file `data/redis-acl-enabled` must exist (mirrors
  `data/redis-replication-enabled` and `data/auto-revert-enabled`) **and** `REDIS_ACL_APPLY=true` must
  be set in the invoking environment. Missing either → `action="skipped"` with the reason. This is the
  `MEMORY_DECAY_PRUNE_APPLY` posture: an irreversible-in-practice operation defaults off and is
  reachable only by explicit, deliberate opt-in.
- **`params.apply` is never inherited.** `/update` Step 3.135 calls `apply_redis_acl()` with no
  arguments, always. There is no code path by which a global apply flag, a config toggle, or a future
  `--apply` on `/update` reaches this function. A regression test asserts that
  `scripts/update/run.py` passes no `apply` argument at the call site.
- **The `aclfile` directive is staged, not written.** The planner emits the exact directive line and
  the target path into its report (and into the runbook) for the operator to add. `redis.conf` is not
  opened for writing by anything in this PR.
- **The runbook is the deliverable for the applied state.** `docs/features/redis-flush-hardening.md`
  carries a section headed **"Applying the Redis ACL — requires human sign-off, not performed by this
  PR"** containing the operator checklist: obtain/record `REDIS_APP_PASSWORD` in the vault `.env`,
  `touch data/redis-acl-enabled`, run `REDIS_ACL_APPLY=true python -m scripts.update.redis_acl --apply`,
  add the staged `aclfile` directive, and verify with `redis-cli ACL GETUSER valor-app`.

Consequently every assertion in this plan's `## Success Criteria` and `## Verification` is about the
**planner's output**, never about live server state: the acceptance evidence is
`python -m scripts.update.redis_acl --dry-run` printing the four planned commands. Rows that could
only pass against an already-mutated production server have been removed; their live-server
equivalents live in the runbook, where a human runs them after signing off.

**D8a — The password gate belongs inside the apply branch, not around the whole function.
(Revision-round fix.)** The first draft stated unconditionally that `REDIS_APP_PASSWORD` unset yields
`action="skipped", error="REDIS_APP_PASSWORD unset"` — while `## Prerequisites` deliberately makes the
password *not* a prerequisite, so the build machine will not have it. That contradicts the only
acceptance evidence Layer 2 has: a skipped run plans nothing, so `--dry-run` printing four commands
and the whole-function gate cannot both hold on the build machine. The password matters only where it
is substituted, in `ACL SETUSER valor-app on ><pw>`, which is the apply path alone.

Fix: evaluate the password gate **inside** the `apply=True` branch, after the marker and
`REDIS_ACL_APPLY` gates. The report path plans all four commands on every machine, emitting the
literal placeholder token `<REDIS_APP_PASSWORD>`:

```
ACL SETUSER valor-app on ><REDIS_APP_PASSWORD> ~* &* +@all -flushdb -flushall
```

The placeholder also keeps the real secret out of `/update` logs and the PR body, which CLAUDE.md
§ Secrets requires unconditionally — never echo a secret or any prefix of one to stdout.

**D9 — Land the popoto username plumbing now, inert until rotation. (Revision-round fix, BLOCKER.)**
D3 and spike-3 originally claimed credentials in `REDIS_URL` reach all 21 sites with zero call-site
edits. That is false for exactly one site, and it is the decisive one.
`config/redis_bootstrap.py:112` reads `password = parsed.password or None` and never reads
`parsed.username`; the `set_REDIS_DB_settings(...)` kwargs block at `:119-132` passes `password=` and
no `username=`. After the #2661 rotation, `POPOTO_REDIS_DB` — the client the 2026-08-07 script actually
flushed — would send a one-argument `AUTH <pw>`, which ACL-wise targets `default`. `default` is
deliberately left `nopass` (D3), so that AUTH **errors** and popoto cannot connect at all: bridge,
worker, and dashboard go down fleet-wide the moment the vault `.env` syncs. And in the counterfactual
where it did connect, it would connect **as `default`**, which D3 deliberately leaves able to
`flushdb` — Layer 2 would then protect every client except the one that caused the incident. Either
branch falsifies "this plan ships everything that makes the flip a one-line edit."

Fix, in this PR: beside `password = parsed.password or None` add `username = parsed.username or None`,
and pass `username=username` in the `set_REDIS_DB_settings(...)` kwargs block. This is verified safe —
popoto's `set_REDIS_DB_settings(env_partition_name="", *args, **kwargs)` forwards `**kwargs` straight
into `redis.Redis(*args, **kwargs)`, and `username=None` is redis-py's own default, so the pre-rotation
URL (no username) produces byte-identical behavior. The change is therefore **inert until rotation**:
it is not a Layer-2 apply, it does not touch the server, and it is safe to merge ahead of #2661.

Do **not** reach for `redis.Redis.from_url` here as a "cleaner" fix. Rewriting the bootstrap's parsing
is a separate refactor (see Rabbit Holes) and this site hand-parses for reasons — the `db`, `retry`,
and timeout kwargs are assembled around it.

**D5 — Layer 3 is a new sibling validator, not an extension of the raw-delete one.**
`validate_no_raw_redis_delete.find_violation` returns `None` unless a `_POPOTO_CONTEXT` token is
present. A flush is unconditionally destructive and the most dangerous shapes
(`redis.Redis().flushdb()`, `redis-cli -n 0 flushdb`) carry no Popoto vocabulary, so adding `flush*`
to its pattern list would inherit the wrong gate. New file
`.claude/hooks/validators/validate_no_redis_flush.py` exposing a pure
`find_violation(command) -> str | None`, plus a `_run_no_redis_flush` adapter appended to
`_VALIDATORS` in `.claude/hooks/dispatch/pre_tool_use_bash.py:183` with `fail_closed=False`.
**No `manifest.toml` stanza is needed** — #2435's consolidated `dispatch_pre_tool_use_bash` already
covers it — but the manifest's explanatory comment block (`:74-89`) must be updated to say 9
predicates and the `timeout = 20` budget re-confirmed.

Patterns must match **call shapes**, not the bare word, so that `grep flushdb …`, `rg flushdb`, and
reading this plan aloud are not blocked: `\.flushdb\s*\(`, `\.flushall\s*\(`,
`redis-cli\s+.*\bFLUSHDB\b`, `redis-cli\s+.*\bFLUSHALL\b` (case-insensitive for the CLI forms). Note
for the builder: `validate_no_raw_redis_delete` has **no** dedicated unit test today (only indirect
coverage in `test_pre_tool_use_dispatcher.py`); the new validator gets a real one.

**D5a — The escape named in the block message must be the escape that works. (Revision-round fix.)**
The first draft said the block message names `REDIS_PRODUCTION_FLUSH_OK=1` as "the deliberate escape",
and Risk 4's mitigation rested on that. But `find_violation(command)` sees only the command string;
no env-var branch was specified, so prefixing the variable did **not** disarm the hook. Layer 3 had no
escape at all and the message was telling the agent to do something that would not work — the failure
mode that burns turns and teaches agents to distrust block messages. Fix: `find_violation` checks the
command string for the prefix form and returns `None` before evaluating `_BLOCK_PATTERNS`:

```python
if re.search(r"\bREDIS_PRODUCTION_FLUSH_OK=1\b", command):
    return None
```

The block message quotes that exact prefix form (`REDIS_PRODUCTION_FLUSH_OK=1 python -c "…"`), so the
message and the behavior agree. This is deliberately the *same* variable Layer 1 reads: one override
name disarms both layers for one command, and the agent only has to learn one thing. A regression test
asserts `find_violation('REDIS_PRODUCTION_FLUSH_OK=1 python -c "r.flushdb()"') is None`.

**D5b — Layer 3 must not block this plan's own Verification rows. (Revision-round fix.)**
Three Verification rows were Bash commands literally containing `redis.Redis.flushdb(c)`,
`redis.Redis.flushall(c)`, and `v.find_violation('python -c "r.flushdb()"')`. Once Layer 3 is in
`_VALIDATORS`, the final-validator — an agent issuing Bash — could not run them: the plan's own
acceptance evidence was unreachable. This is a general hazard for any plan that adds a Bash validator,
and the fix is structural, not a one-off exemption: **assertions about blocked call shapes live in
pytest files, not in Bash command strings.** Both guard rows and the validator rows collapse into
`scripts/pytest-clean.sh tests/unit/test_redis_flush_guard_prod.py tests/unit/test_validate_no_redis_flush.py -q`,
which contains no matching shape. Where a Bash-level probe is genuinely wanted, construct the
attribute name so the regex cannot see it: `getattr(redis.Redis, "flush" + "db")(c)` calls the same
function. Do **not** paper over this by prefixing `REDIS_PRODUCTION_FLUSH_OK=1` onto verification
rows — that disarms the very layer under test and would make the row assert nothing.

**D6 — Coexistence with `tests/conftest.py` is proven, not assumed.**
Under pytest both guards install: Layer 1's at first `import redis` (sentinel `_prod_flush_guarded`),
and conftest's at collection (sentinel `_db0_guarded`), wrapping Layer 1's. The sentinels are distinct,
so neither suppresses the other. Behavior: db 0 → conftest's wrapper raises first (its message contains
`db=0`, which is what `tests/unit/test_redis_flush_guard.py` matches on); `flushall` → conftest's
raises (`match="flushall"`); a claimed db 1-15 → conftest permits, delegates to Layer 1's wrapper,
which permits (`db != 0`), and the real flush executes. All 7 existing cases keep passing without
modification. After #2628 lands, its stricter ownership guard becomes the outer wrapper and the same
delegation holds. This must be *demonstrated* by running that file with the guard installed — not
asserted.

**D6a — Idempotence cannot key on the sentinel attribute. (Revision-round fix.)**
The first draft claimed `install()` is idempotent because it checks
`getattr(cls.flushdb, "_prod_flush_guarded", False)`. That claim is false in exactly the nesting D6
describes. `tests/conftest.py::_install_redis_db0_flush_guard` wraps whatever `cls.flushdb` is at
conftest-import time and sets `_guarded_flushdb._db0_guarded = True` **only** — it does not carry
`_prod_flush_guarded` forward onto its wrapper. So once conftest has installed,
`getattr(redis.Redis.flushdb, "_prod_flush_guarded", False)` reads `False` even though Layer 1 is
armed underneath, and any later `install()` re-wraps into `prod(db0(prod(orig)))`. The same stale read
makes an *in-process* liveness assertion report `False` under pytest while the guard is in fact
working.

Fix: back idempotence with a module-level registry rather than an attribute walk.

```python
_INSTALLED: set[type] = set()   # keyed on the patched class object
```

`install()` returns early for any class already in `_INSTALLED`; `is_installed(cls)` consults it. The
`_prod_flush_guarded` attribute is **kept**, but demoted to a single job: the liveness signal for the
doctor's **subprocess** probe, which runs in a clean interpreter with no conftest wrapper on top, so
it stays accurate there (Risk 1 is unaffected). Nothing may use the attribute for an in-process
idempotence or liveness decision.

Regression test: install Layer 1, apply a conftest-shaped wrapper on top that sets only
`_db0_guarded`, call `install()` again, and assert the delegation chain did not grow — count closure
depth, or assert a call counter on the original callable increments exactly once per call.

**D7 — No verification or test command may flush db 0 if the guard is absent.**
Any check that "proves the guard blocks db 0" must drive the *unbound* patched function with a stub
client — `redis.Redis.flushdb(SimpleNamespace(connection_pool=SimpleNamespace(connection_kwargs={"db": 0})))` —
because the guard reads only `_db_of(client)` and raises before touching a socket. If the guard were
missing, that call raises `AttributeError` on the stub rather than executing a flush. Constructing a
real `redis.Redis(db=0)` and calling `.flushdb()` to "check the red state" would wipe production on
any branch where the guard is not yet installed. This is the same trap #2628's round-4 critique caught
in its own plan; it is a hard rule here.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `_redis_flush_guard_boot.py`'s `except Exception: pass` is deliberate (a `.pth` traceback would
  pollute every interpreter start). Its silence is compensated by the doctor liveness check, which is
  the observable behavior under test: assert `doctor` reports FAIL when the guard is not live.
- [ ] `tools/redis_flush_guard.py::install()` must not raise when `redis` is unimportable — test with
  `redis` absent from `sys.modules` and blocked from import; assert `install()` returns falsy and does
  not propagate.
- [ ] `scripts/update/redis_acl.py` follows the non-fatal contract: every failure path (redis-cli
  absent, Redis down, `ACL LIST` unparseable) returns a structured result with `success=False` and a
  populated `error`, and the `/update` step logs a WARNING and appends to `result.warnings`. Test each
  with a fake `redis-cli`.
- [ ] **`REDIS_APP_PASSWORD` gates the apply path only (D8a)**, in two explicit tests: (a)
  `apply_redis_acl()` with the password unset returns `len(planned_commands) == 4` and
  `action != "skipped"`, with the fourth command containing the literal `<REDIS_APP_PASSWORD>`
  placeholder; (b) the **apply** path with the password unset returns
  `action="skipped", error="REDIS_APP_PASSWORD unset"` — `skipped`, not `success=False`, matching
  `RedisPersistenceResult`'s existing action so a machine without the secret produces a quiet
  `/update` log line rather than a warning every run.
- [ ] **No secret reaches stdout**: assert the report path's `planned_commands` contains the
  placeholder token and never the value of `REDIS_APP_PASSWORD`, with the env var set to a sentinel.
- [ ] **Apply-gate tests, one per gate combination**: marker absent + `REDIS_ACL_APPLY` unset, marker
  present + flag unset, marker absent + flag set → all three return `action="skipped"` and issue **zero**
  `ACL SETUSER`/`ACL SAVE` calls (assert on the faked `redis-cli` invocation list, not just the result).
  Only marker present + `REDIS_ACL_APPLY=true` + `apply=True` reaches the write path.
- [ ] **`/update` never applies**: a test asserts `apply_redis_acl` is called from
  `scripts/update/run.py` with no `apply` argument, and that a run with the marker *and* the env flag
  both set still issues no writes through the `/update` path (D8).
- [ ] Dispatcher adapter `_run_no_redis_flush` is registered `fail_closed=False`; assert that a
  raising predicate is logged and the dispatcher continues (existing contract in
  `test_pre_tool_use_dispatcher.py`).

### Empty/Invalid Input Handling

- [ ] `find_violation("")` and `find_violation(None)` → `None`, no exception (mirrors the existing
  validator's contract).
- [ ] `_db_of(client)` when `connection_pool` / `connection_kwargs` / `db` are missing or non-integer
  → **assume db 0** (fail closed, matching `tests/conftest.py:110-115`). Explicit test.
- [ ] `REDIS_PRODUCTION_FLUSH_OK` set to `""`, `"0"`, `"false"`, `"no"` → guard stays **armed**. Only
  the exact value `"1"` disarms. Explicit test per value; this is the one place a truthiness bug
  silently disables the whole layer.
- [ ] Installer against a path that is not a venv, a venv with no site-packages, and a read-only
  site-packages → skipped with a reported reason, never a crash, never a partial write. Same three
  cases via the `install()` self-heal path (D2b), asserting the exception never escapes.

### Error State Rendering

- [ ] The `RuntimeError` text is the primary user-visible artifact of this feature. Assert it names:
  the attempted db, `REDIS_PRODUCTION_FLUSH_OK=1`, and where to point a test client. Assert the same
  for `flushall`, which must additionally say it wipes every db.
- [ ] The Layer-3 block reason must render through the dispatcher to the agent (assert via
  `dispatch()` returning the reason string, not just the predicate).
- [ ] The doctor checks must render a FAIL with a remediation command (`/update`, or the installer
  invocation) — not a bare boolean.

## Test Impact

- [ ] `tests/unit/test_redis_flush_guard.py` — **UPDATE: none.** All 7 cases must pass unmodified with
  Layer 1 installed (D6). This file is owned by #2628 Task 4; this plan must not edit it. Its unchanged
  passing is a Verification row, **and its absence from the diff is enforced by the seam
  anti-criterion** — it is the file most likely to be "fixed" under pressure if D6 coexistence does not
  come out clean, so it is protected mechanically rather than by this bullet.
- [ ] `tests/unit/test_pre_tool_use_dispatcher.py` — **UPDATE**: the ordering/first-block-wins tests
  enumerate `_VALIDATORS`; extend for the 9th predicate and assert its position and `fail_closed=False`.
- [ ] `tests/unit/test_hook_migration.py` — **UPDATE if it asserts a predicate count**; verify. It uses
  `validate_no_raw_redis_delete` as a fixture and may be indifferent.
- [ ] `tests/unit/test_doctor*.py` (whichever enumerate checks) — **UPDATE**: two new checks appear in
  `get_checks()`; any count- or name-list assertion needs extending.
- [ ] `tests/unit/test_update_run*.py` / update-step tests — **UPDATE**: two new `/update` steps.
- [ ] New: `tests/unit/test_redis_flush_guard_prod.py` — Layer 1 unit tests (stub-client driven, D7).
- [ ] New: `tests/unit/test_validate_no_redis_flush.py` — Layer 3 predicate tests, including the
  no-false-positive cases (`grep flushdb`, `rg -n flushall`, a doc string containing the word).
- [ ] New: `tests/unit/test_redis_flush_guard_pth_installer.py` — installer idempotence, non-venv
  handling, read-only handling. Uses `tmp_path`, never a real venv.
- [ ] New: `tests/unit/test_redis_acl.py` — planner logic and the apply-gate matrix against a faked
  `redis-cli`, never the live server. Includes the D8a password-gate pair and the placeholder-token
  no-secret-leak assertion.
- [ ] New: `tests/unit/test_redis_bootstrap_username.py` — D9. Asserts
  `redis://valor-app:pw@h:6379/0` yields `username="valor-app"` in the kwargs handed to
  `set_REDIS_DB_settings`, and that a URL with **no** username yields `username=None` (the
  pre-rotation no-op case). Patches `popoto.redis_db.set_REDIS_DB_settings`; connects to nothing.
- [ ] `tests/unit/test_redis_flush_guard_prod.py` also carries the D2a-ii startup-budget case:
  parsing the `cumulative` field of the `_redis_flush_guard_boot` line from `python -X importtime -c pass`, best of five trials, asserting it is under `_STARTUP_BUDGET_MS`. Mark `slow` for the
  subprocess spawns. This is what makes `_STARTUP_BUDGET_MS` a live constant rather than PR-body
  decoration.

## Rabbit Holes

- **Making Layer 2 discriminate by db.** Spike-1 closed this definitively. Any effort spent on ACL
  selectors, `rename-command` aliases, or a Redis module is wasted — the selector route *looks* like
  it works and silently does not, which is worse than not trying.
- **Migrating the 21 `REDIS_URL` call sites to a shared client factory.** Tempting while reading the
  census, genuinely valuable, and entirely orthogonal. It changes nothing about this incident because
  the guard is at the operation, not the constructor. Separate issue.
- **Rewriting `tests/conftest.py`'s guard to import the new module.** The obvious DRY move and the one
  thing guaranteed to conflict with #2628 Task 3. The duplication is temporary and deliberate; #2628
  converges it.
- **Blocking `SELECT`, `KEYS`, `DEBUG`, `SHUTDOWN`, `CONFIG`, and every other dangerous command.**
  Scope creep dressed as thoroughness. Two incidents, one command family. Ship that.
- **Building a `.pth` installer that also repairs the editable install.** The installer should detect
  and *report* a venv where `import tools` fails, not fix it. `/update` and `uv sync` own that.
- **Auto-rotating the vault `.env`.** Forbidden by CLAUDE.md § Secrets and dangerous across a
  fleet. It is a No-Go, not a stretch goal.

## Risks

### Risk 1: The `.pth` guard is installed but silently inert
**Impact:** The single most important layer looks green in review and provides zero protection —
precisely the `sitecustomize` failure mode spike-2 uncovered. Any of these produce it: the editable
install missing from a venv, the shim swallowing an `ImportError`, `.pth` sort order changing, or
`python -S`.
**Mitigation:** The doctor check asserts liveness (`redis.Redis.flushdb._prod_flush_guarded is True`)
in a freshly spawned subprocess, not file presence. `tools/doctor.py::_check_worktree_interpreters`
is the precedent for iterating every venv, so the check reports *which* venvs are unguarded. A
Verification row runs it. The `python -S` gap is accepted and documented — nothing in this repo runs
that way.

### Risk 2: `REDIS_URL` rotation takes down a machine that has not provisioned `valor-app`
**Impact:** `WRONGPASS` on every client; bridge, worker, and dashboard all fail to connect. The vault
`.env` is iCloud-synced, so a rotation propagates to every machine before any of them necessarily runs
`/update`.
**Mitigation:** The rotation is not in this plan's diff — it is an `[EXTERNAL]` No-Go with its own
issue. The client-side prerequisite that made it *unsafe at any time* — popoto dropping the URL
username, so rotation would have produced a fleet-wide `AUTH` failure regardless of ACL readiness —
is fixed in this PR by D9 and is inert until the flip. The flip itself is gated on the doctor check
reporting `valor-app` provisioned on every machine in the roster
(`docs/features/single-machine-ownership.md`) — tracked as **#2661**. The planner ships and reports
drift on every `/update`, so the doctor can tell you exactly which machines an operator has already
applied the ACL to and which have not; the flip waits until that reads clean everywhere. Failure is
loud and instantly diagnosable, and rollback is a one-line `.env` revert.

### Risk 3: #2628 starts building concurrently and collides
**Impact:** Merge conflicts in `tests/conftest.py`, or two guards that disagree.
**Mitigation:** The seam is mechanical, not social: an anti-criterion in `## Verification` fails the
build if this PR's diff touches `tests/conftest.py`, `tests/db_claim.py`, or
`docs/features/test-db-ownership.md`. D6 documents the nesting so whichever lands second inherits a
described contract rather than discovering one.

### Risk 4: Layer 3 false positives block legitimate agent work
**Impact:** An agent debugging a test db cannot flush it and burns turns fighting the hook.
**Mitigation:** Patterns match call shapes, not the bare word (D5), so search and read commands pass.
The block message names `REDIS_PRODUCTION_FLUSH_OK=1` and the ORM idiom, and per D5a that prefix is
now an escape `find_violation` actually honors — in the first draft it was named but not implemented,
which made this mitigation void. Explicit no-false-positive tests for `grep`/`rg`/prose, plus the D5a
regression test that the prefixed command returns `None`.

### Risk 5: The guard's monkeypatch breaks an unrelated consumer of `redis.Redis`
**Impact:** A library that introspects or re-wraps `flushdb`, or that calls `flushall` legitimately,
starts failing in production.
**Mitigation:** Spike-3 found zero first-party callers, and popoto's only flushes are in its pytest
plugin against its own test db. The patch preserves the original callable and delegates on the
permitted path, so signatures and return values are unchanged. The `_INSTALLED` registry (D6a)
prevents double-wrapping across repeated `install()` calls, including the pytest nesting where the
sentinel-attribute check would have failed.

### Risk 6: `aclfile` addition requires a Redis restart on the production instance
**Impact:** Brief downtime on the instance the whole system depends on — the same instance the
incident just disrupted.
**Mitigation:** Nothing in this PR touches the live server at all (D8), so the risk does not arise
during the build or on merge. When the operator later runs the apply runbook: the planner **never
restarts Redis** and never issues `SHUTDOWN`, `CONFIG REWRITE`, or `brew services restart`. Users are
applied via `ACL SETUSER` at runtime (immediate); the staged `aclfile` directive matters only for
persistence across the next restart, which happens on the operator's schedule. If `ACL SAVE` is
unavailable because the directive is not yet loaded, that is reported as a warning and the runtime
rules still hold. An anti-criterion greps the module for all three forbidden commands.

### Risk 7: The `.pth` slows every Python start on the machine
**Impact:** The guard runs in every interpreter from a repo venv — including every PreToolUse hook
invocation, which fires on every Bash tool call under the dispatcher's shared `timeout = 20` that
already covers a 15s out-of-process subprocess. An eager `import redis` + `import redis.asyncio` (which
pulls `asyncio` and `ssl`) on that path is a machine-wide tax, and the first draft asserted
"import-cheap" with no measurement.
**Mitigation:** Lazy arming via a `sys.meta_path` finder whose contract is pinned in D2a-i — a process
that never imports `redis` pays only the finder insertion. The cost is **asserted in a pytest case**,
not eyeballed (D2a-ii), by parsing the `cumulative` field of the `_redis_flush_guard_boot` line from `python -X importtime -c pass`, best of five trials, compared against the
env-overridable provisional `_STARTUP_BUDGET_MS`. Measuring against `-S` would measure the cost of
`site` itself, not of this guard, and no kill-switch env var is introduced to enable the benchmark.
The measured number also goes in the PR body. If the budget is exceeded the mechanism is wrong, not
the budget.

### Risk 8: An agent or a future `/update` change turns the ACL planner into an applier
**Impact:** The exact outcome D8 exists to prevent — a live production ACL mutation with no human in
the loop, arriving silently in an unrelated PR.
**Mitigation:** Defense in depth rather than a comment. `apply` defaults to `False`; the write path
additionally requires both `data/redis-acl-enabled` (untracked, operator-created) and
`REDIS_ACL_APPLY=true`; a regression test asserts `scripts/update/run.py` passes no `apply` argument;
and the module carries a header comment stating that `/update` must never apply and why. Reversibility
is total until the operator acts, because nothing has been changed.

## Race Conditions

### Race 1: `.pth` install races an interpreter that is starting
**Location:** `scripts/update/redis_flush_guard_pth.py`
**Trigger:** `/update` rewrites the shim while a launchd service is executing `site.py`.
**Data prerequisite:** Both files must be complete and mutually consistent before any interpreter
reads either.
**State prerequisite:** A partially written `_redis_flush_guard_boot.py` must never be importable.
**Mitigation:** Write to a temp file in the same directory and `os.replace()` (atomic on the same
filesystem). Write the shim **before** the `.pth`, so the `.pth` never references a missing module.
`install_into()` — the single-venv entry point the D2b self-heal calls — uses this same path, so two
interpreters self-healing one venv concurrently cannot produce a torn file either.

### Race 2: Guard install races `import redis` in the same process
**Location:** `tools/redis_flush_guard.py::install()`
**Trigger:** None in practice — `.pth` processing completes inside `site.py`, before any user code
imports `redis`. But `install()` patches the *class*, so a module that did `from redis import Redis`
earlier still sees the patched class (same object); only a caller that captured the *bound method*
before install would escape.
**Data prerequisite:** `redis.Redis` must be importable when `install()` runs.
**State prerequisite:** The patch must be idempotent under repeated `install()` (`.pth` plus an
explicit call).
**Mitigation:** Idempotence via the module-level `_INSTALLED` class registry, **not** the
`_prod_flush_guarded` attribute (D6a — the attribute is not carried forward by conftest's wrapper and
would allow double-wrapping under pytest). `install()` imports `redis` itself rather than assuming it
is loaded, and `arm()` installs immediately if `redis` is already in `sys.modules`. No pre-capture of
bound methods exists in first-party code (spike-3).

### Race 3: Two `/update` runs provision the ACL concurrently
**Location:** `scripts/update/redis_acl.py`
**Trigger:** Two operators, or two shells on one machine, running the apply runbook at once. (The
`/update` path cannot cause this — it is report-only, D8.)
**Data prerequisite:** The final `ACL LIST` must reflect the intended rule set regardless of
interleaving.
**State prerequisite:** `ACL SETUSER` is atomic per user and idempotent for identical arguments.
**Mitigation:** The apply path issues a full declarative `ACL SETUSER` (complete rule set, not a
delta), so any interleaving converges to the same state. It verifies by re-reading `ACL GETUSER`
after writing and reports a mismatch rather than retrying.

## No-Gos (Out of Scope)

- **[EXTERNAL]** **Applying the ACL to the live Redis server.** Creating `valor-app`, removing
  `flushall` from `default`, writing the `aclfile` directive into `/opt/homebrew/etc/redis.conf`, and
  `ACL SAVE` are all operator actions performed by hand from the runbook in
  `docs/features/redis-flush-hardening.md`, gated on `data/redis-acl-enabled` **and**
  `REDIS_ACL_APPLY=true` (D8). `/update` never applies. This PR ships the planner, the target rule set,
  the staged directive, the doctor check, and the runbook — everything except the mutation.
- **[EXTERNAL]** Rotating the production `REDIS_URL` in the vault `.env` to the
  `redis://valor-app:<pw>@…` form. Writing secrets to the vault `.env` is a human action by policy
  (CLAUDE.md § Secrets), and the flip must wait until the doctor check reports `valor-app` provisioned
  on every machine in the roster — a fleet-readiness condition no single PR can establish. Filed as
  **#2661**; this plan ships everything that makes the flip a one-line edit.
- **[SEPARATE-SLUG #2628]** `tests/conftest.py:103-150` (`_install_redis_db0_flush_guard`),
  `tests/db_claim.py`, every `redis.Redis(db=…)` site under `tests/`, and
  `docs/features/test-db-ownership.md`. Owned by #2628 Task 3/1/4/10, which rewrites them wholesale.
  Enforced by an anti-criterion below, not by discipline.
- **[SEPARATE-SLUG #2628]** A `claimed_test_dbs()`-aware, per-db server-side rule. #2628 declares the
  flock claim registry the authoritative by-db discriminator, and spike-1 proves a server cannot read
  it anyway. This plan's server rule is deliberately db-blind.
- Everything else is in scope: the guard module, the `.pth` installer, `/update` and worktree
  wiring, both doctor checks, the report-only ACL planner and its apply gates, the `valor-app` rule
  set, the `default -flushall` denial, the staged `aclfile` directive, the operator runbook, the hook
  validator and its tests, the `CLAUDE.md` paragraph, and the feature doc.

## Update System

`/update` changes are central here, not incidental — Layer 1's whole propagation story is `/update`.

- **New Step 3.05 (after the Step 3 dependency-sync block closes, before Step 3.5):** install the
  flush-guard `.pth` into every repo venv via `scripts/update/redis_flush_guard_pth.py`. Placed
  **after** dep sync precisely so a venv that `uv sync` creates or recreates is guarded within the
  same run — `uv sync` is what creates `.venv` when it is absent, so running the installer first would
  skip a missing venv with "not a venv / no site-packages" and leave the new venv unguarded until the
  next `/update` (a fresh silently-inert state, Risk 1). Step 3 is conditional on `should_sync`, so
  the installer runs **unconditionally, outside** the `if config.do_dep_sync:` block. Idempotent.
- **New step 3.135 (immediately after Step 3.13 Redis durability): `scripts/update/redis_acl.py`,
  REPORT-ONLY.** It calls `apply_redis_acl()` with **no arguments** — never `apply=True`, never a
  forwarded `params.apply` — and logs the planned commands and drift status. `/update` must never
  mutate the Redis ACL (D8, Risk 8); the apply is a human runbook step. Same non-fatal contract as
  `redis_persistence.py` — log, warn, continue. Durability, then ACL report, then replication (3.14).
- **New operator marker (not created by any automation):** `data/redis-acl-enabled`, mirroring
  `data/redis-replication-enabled` and `data/auto-revert-enabled`. Its absence is the normal state on
  every machine.
- **New config file propagated:** none. The `.pth` and shim are generated, not checked in.
- **New secret:** `REDIS_APP_PASSWORD` — a field on `RedisSettings` in `config/settings.py`
  **defaulted to `""`** so `Settings()` never fails on a machine that lacks it. **No `.env.example`
  placeholder ships in this PR.** `check_env_completeness` derives its key set solely from
  `.env.example` and never inspects `config/settings.py`, so omitting the entry keeps that check green
  on every machine with zero operator action, while the apply path still reads the value from
  `os.environ`. The placeholder ships with the apply / #2661 rotation PR. Adding the real value to the
  vault `~/Desktop/Valor/.env` is an operator step in the apply runbook.
- **`config/redis_bootstrap.py` username forwarding (D9)** — a one-line parse plus one kwarg, landing
  in this PR and inert until the #2661 rotation. It is not an `/update` step and changes no `/update`
  behavior; it is listed here because it is the client-side half of the Layer 2 rollout.
- **Migration for existing installations:** none required. Both new steps are idempotent and
  self-healing on first run. No Popoto model changes, so no `scripts/update/migrations.py` entry.
- **Worktree bootstrap:** `agent/worktree_manager.py` creates a venv per worktree; it must call the
  same installer so a new worktree is guarded before its first `python` invocation. This covers
  `.worktrees/{slug}/` only — **`.claude/worktrees/{agent}/` is harness-created and reaches neither
  wiring point**, which is why `install()` self-heals its own venv (D2b), why `tools/__init__.py`
  carries the `arm()` trigger that actually fires there (D2b-i), and why the doctor FAIL text names
  the per-venv installer invocation.
- **`/setup`:** a new machine gets both layers on its first `/update`; no separate setup step.

## Agent Integration

- **No new CLI entry point in `pyproject.toml [project.scripts]`.** The guard is not something an
  agent invokes — it is ambient, installed into the interpreter. Adding a CLI would suggest it is
  opt-in, which is the failure mode this plan exists to remove.
- **No bridge changes.** `bridge/telegram_bridge.py` imports nothing new; it inherits the guard via
  its venv's `.pth` like every other process.
- **The agent-facing surface is the hook validator (Layer 3)**, which reaches the agent through the
  existing consolidated PreToolUse dispatcher. Registration is a `_VALIDATORS` entry in
  `.claude/hooks/dispatch/pre_tool_use_bash.py`; **no `manifest.toml` stanza is required** (#2435),
  though the manifest's comment block and `timeout = 20` budget must be reviewed.
- **Integration test:** assert `dispatch({"tool_name": "Bash", "tool_input": {"command": "…flushdb()…"}})`
  returns a block reason — i.e. that the agent's actual tool path is gated, not merely that the
  predicate function works in isolation.
- **`tools/doctor.py`** gains two checks, reachable by the agent through the existing
  `python -m tools.doctor` entry point. No new wiring.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/redis-flush-hardening.md` — the four layers, what each stops and what it
  does not, both incident dates, the `REDIS_PRODUCTION_FLUSH_OK` override and when using it is
  legitimate, the `.pth` install mechanism and why `sitecustomize.py` is unusable here, the ACL user
  model and why the split is by identity rather than by db, and the recovery runbook pointer.
- [ ] In that same doc, a section headed **"Applying the Redis ACL — requires human sign-off, not
  performed by this PR"**: the operator checklist — only on the machine being applied: record the
  real `REDIS_APP_PASSWORD` in the vault `.env`, `touch data/redis-acl-enabled`, run
  `REDIS_ACL_APPLY=true python -m scripts.update.redis_acl --apply`, add the staged `aclfile`
  directive, restart on your own schedule — plus the live-server verification commands
  (`redis-cli ACL GETUSER valor-app`, `redis-cli ACL LIST`) that were removed from `## Verification`,
  and the rollback (`ACL SETUSER default … +@all`, `ACL DELUSER valor-app`). State plainly that
  `/update` never performs any of this.
- [ ] Add the entry to the `docs/features/README.md` index table.
- [ ] Cross-link from `docs/features/redis-flush-hardening.md` to #2628's
  `docs/features/test-db-ownership.md` **as a link only** — that page is #2628's to write.

### External Documentation Site
- [ ] Not applicable — this repo has no external docs site.

### Inline Documentation
- [ ] `CLAUDE.md` § Manual Testing Hygiene: a paragraph naming the `os.environ.setdefault` foot-gun
  (it is a no-op when the key is already exported, so "defaulting" to a test URL silently keeps the
  production one), stating that the flush guard is ambient and what its error means, and giving the
  sanctioned idiom for pointing a script at a test db.
- [ ] Docstrings on `tools/redis_flush_guard.py` carrying both incident dates and the reason the guard
  is installed at interpreter scope rather than in `conftest.py`.
- [ ] A comment in `scripts/update/redis_acl.py` recording spike-1's finding that ACL selectors do
  **not** restrict `FLUSHDB` by db, so a future agent does not "improve" the rule into a no-op.
- [ ] A comment beside the new `username=` kwarg in `config/redis_bootstrap.py` (D9) saying it is the
  client half of Layer 2, inert until #2661 rotates `REDIS_URL`, and that removing it re-breaks
  popoto's auth at rotation time.
- [ ] A comment on the `tools/__init__.py` `arm()` trigger (D2b-i) stating that it is cheap because
  `arm()` imports no `redis`, and that the bare `except Exception: pass` is mandatory because nothing
  may break `import tools`.

## Success Criteria

- [ ] A flush guard is armed in every Python process started from a repo venv, verified by a doctor
  check that asserts liveness in a fresh subprocess (not file presence) across all repo venvs.
- [ ] `flushdb()` on db 0 and `flushall()` anywhere raise `RuntimeError` naming
  `REDIS_PRODUCTION_FLUSH_OK=1`; setting that variable to exactly `1` permits both.
- [ ] `REDIS_PRODUCTION_FLUSH_OK` set to `""`, `"0"`, `"false"`, or `"no"` leaves the guard armed.
- [ ] `tests/unit/test_redis_flush_guard.py` passes **unmodified** with the guard installed (D6
  coexistence).
- [ ] Repeated `install()` under a conftest-shaped outer wrapper does not grow the delegation chain
  (D6a).
- [ ] **`python -m scripts.update.redis_acl --dry-run` prints the four planned ACL commands and makes
  no server mutation**, and a doctor check reports the machine's current ACL drift. No criterion in
  this plan asserts applied server state — that lives in the runbook behind human sign-off (D8).
- [ ] With `data/redis-acl-enabled` absent, or `REDIS_ACL_APPLY` unset, or both, `apply_redis_acl`
  issues zero `ACL SETUSER`/`ACL SAVE` calls; `/update`'s call site passes no `apply` argument.
- [ ] With `REDIS_APP_PASSWORD` unset, the **report** path still plans four commands
  (`action != "skipped"`) using the `<REDIS_APP_PASSWORD>` placeholder, while the **apply** path
  returns `action="skipped", error="REDIS_APP_PASSWORD unset"` (D8a). No planned command or log line
  ever contains the secret's value.
- [ ] `config/redis_bootstrap.py` forwards `username=` from `REDIS_URL` into
  `set_REDIS_DB_settings(...)`, asserted for both a credentialed URL (`username="valor-app"`) and a
  bare URL (`username=None`, the pre-rotation no-op) — D9. Without this, the #2661 rotation takes
  popoto down fleet-wide.
- [ ] `tools/__init__.py` calls `arm()` inside `try/except Exception: pass`, so a harness-created
  `.claude/worktrees/{agent}/` checkout self-heals on its first first-party import (D2b-i), and
  `import tools` still succeeds when the guard module is broken or absent.
- [ ] Interpreter startup overhead from the `.pth` is under `_STARTUP_BUDGET_MS`, **asserted by a
  pytest case** that measures it by parsing the `cumulative` field of the `_redis_flush_guard_boot` line from `python -X importtime -c pass`, best of five trials — not against a
  `-S` baseline, not eyeballed, and with no kill-switch env var (D2a-ii).
- [ ] Test-suite Redis behavior is unchanged: `tests/unit/` passes with no edits to `tests/conftest.py`
  or `tests/db_claim.py`.
- [ ] The PreToolUse dispatcher blocks the flush call shapes and `redis-cli -n 0 flushdb`, does **not**
  block `grep -rn flushdb tests/`, and does **not** block a command prefixed with
  `REDIS_PRODUCTION_FLUSH_OK=1` (D5a) — asserted in pytest, never in a Bash verification row (D5b).
- [ ] `CLAUDE.md` § Manual Testing Hygiene names the `setdefault` foot-gun.
- [ ] No file under `tests/conftest.py`, `tests/db_claim.py`, `tests/unit/test_redis_flush_guard.py`,
  or `docs/features/test-db-ownership.md` appears in this PR's diff.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] No xfail conversions apply — no expected-failure markers exist for this bug.

## Team Orchestration

### Team Members

- **Builder (guard core)**
  - Name: `guard-builder`
  - Role: `tools/redis_flush_guard.py` and its unit tests. Owns nothing outside `tools/` and `tests/unit/test_redis_flush_guard_prod.py`.
  - Agent Type: builder
  - Domain: redis-popoto-data
  - Resume: true

- **Builder (propagation)**
  - Name: `propagation-builder`
  - Role: the `.pth` installer, `/update` wiring, `agent/worktree_manager.py` wiring, and both `tools/doctor.py` checks. Owns `scripts/update/run.py` and `tools/doctor.py`.
  - Agent Type: builder
  - Resume: true

- **Builder (server layer)**
  - Name: `acl-builder`
  - Role: `scripts/update/redis_acl.py`, `config/settings.py` field, `.env.example` placeholder, and the `config/redis_bootstrap.py` username plumbing (D9). Touches `scripts/update/run.py` and `tools/doctor.py` **only after** `propagation-builder` finishes.
  - Agent Type: builder
  - Resume: true

- **Builder (harness)**
  - Name: `hook-builder`
  - Role: the new PreToolUse validator, its dispatcher registration, the manifest comment, and tests. Owns `.claude/hooks/**`.
  - Agent Type: builder
  - Resume: true

- **Validator (coexistence)**
  - Name: `coexistence-validator`
  - Role: proves D6 — the existing guard tests pass unmodified with Layer 1 live — and proves the seam anti-criterion holds.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `flush-documentarian`
  - Role: `docs/features/redis-flush-hardening.md`, the README index row, and the `CLAUDE.md` paragraph.
  - Agent Type: documentarian
  - Resume: true

- **Validator (final)**
  - Name: `final-validator`
  - Role: runs every `## Verification` row.
  - Agent Type: validator
  - Resume: true

### PR-body reporting duties

These are process obligations, not Success Criteria — `final-validator` runs commands and cannot
assert that a human pasted something. Each is owned by the member who produces the artifact:

- `coexistence-validator` — pastes the verbatim `tests/unit/test_redis_flush_guard.py` run output
  (D6 evidence).
- `guard-builder` — states the measured `python -X importtime` startup cost (the
  `_redis_flush_guard_boot` cumulative field, best of five trials) and the `_STARTUP_BUDGET_MS` it was
  compared against (D2a-ii). The number is a report; the pytest case is what enforces it.
- `hook-builder` — re-confirms the dispatcher's `timeout = 20` budget still holds with a 9th predicate
  and says so.
- `acl-builder` — pastes the `--dry-run` planned-command output (four commands, with the
  `<REDIS_APP_PASSWORD>` placeholder rather than any secret), states explicitly that no live server
  mutation was performed, and states that the D9 bootstrap change is inert pre-rotation and why.

## Step by Step Tasks

### 1. Guard module + unit tests
- **Task ID**: build-guard-core
- **Depends On**: none
- **Validates**: `tests/unit/test_redis_flush_guard_prod.py` (create)
- **Informed By**: spike-2 (`.pth` viable, `sitecustomize` dead), spike-3 (zero first-party flush callers), D1, D2a, D2b, D6a, D7
- **Assigned To**: guard-builder
- **Agent Type**: builder
- **Domain**: redis-popoto-data
- **Parallel**: true
- Create `tools/redis_flush_guard.py` with `arm() -> None`, `install() -> bool`,
  `is_installed(cls=None) -> bool`, and a private `_db_of(client) -> int` that returns **0** whenever
  the db cannot be determined (fail closed, mirroring `tests/conftest.py:110-115`).
- `arm()` (D2a) installs a `sys.meta_path` finder that calls `install()` on the first real import of
  `redis` or `redis.asyncio`, and calls `install()` immediately if either is already in `sys.modules`.
  This is what the `.pth` shim calls; `install()` stays the eager entry point for tests and the doctor
  probe. Define `_STARTUP_BUDGET_MS` as an env-overridable module constant with a comment marking it
  provisional and tunable.
- **Implement the finder exactly per D2a-i — do not improvise it.** `find_spec` returns `None` for any
  name outside `{"redis", "redis.asyncio"}`. For those two: remove self from `sys.meta_path`, call
  `importlib.util.find_spec(fullname)`, re-insert self, and wrap `spec.loader.exec_module` so
  `install()` runs **after** the original `exec_module` returns. Guard with a module-level
  `_ARMING = False` re-entrancy flag (`install()` itself does `import redis`), and wrap the `install()`
  call in `try/except Exception: pass`. Calling `install()` from `find_spec` directly is the wrong
  design: the module does not exist yet, so it patches nothing or recurses — the silent-inert failure
  Risk 1 is about.
- Add the `tools/__init__.py` trigger (D2b-i): `try: from tools.redis_flush_guard import arm; arm()` /
  `except Exception: pass`, with the explanatory comment from `## Documentation`. This is the only
  self-heal trigger that fires in a harness-created `.claude/worktrees/{agent}/` checkout. Keep it
  cheap — `arm()` must not import `redis`, which the D2a test below asserts.
- Idempotence is backed by a module-level `_INSTALLED: set[type]` keyed on the patched class (D6a).
  Do **not** gate on `getattr(cls.flushdb, "_prod_flush_guarded", False)` — conftest's wrapper does not
  carry that attribute forward, so an attribute check re-wraps under pytest.
- Self-heal (D2b): when running in a venv whose site-packages lacks `zzz_redis_flush_guard.pth`, call
  `scripts.update.redis_flush_guard_pth.install_into(<this venv>)` for that one venv only, inside
  `try/except Exception: pass`, skipped on read-only site-packages and when not in a venv. Never walk
  other venvs.
- Patch `flushdb` and `flushall` on `redis.Redis` and `redis.asyncio.Redis`. Block every `flushall`;
  block `flushdb` when `_db_of(self) == 0`. Delegate to the original callable otherwise, preserving
  `*args`/`**kwargs` and return value.
- Disarm **only** when `os.environ.get("REDIS_PRODUCTION_FLUSH_OK") == "1"`, read at call time (not
  import time) so a script cannot pre-set it after the guard loads and expect a stale decision.
- Mark patched callables with `_prod_flush_guarded = True`. That attribute exists **solely** as the
  liveness signal for the doctor's subprocess probe (a clean interpreter); it is not an idempotence or
  in-process liveness signal (D6a). Never raise on import; return falsy if `redis` is unimportable.
- Error messages must name the attempted db, `REDIS_PRODUCTION_FLUSH_OK=1`, the two incident dates,
  and the correct way to point a client at a test db. The `flushall` message must state that it wipes
  every db including production.
- Tests drive the **unbound** patched function with a `SimpleNamespace` stub client per D7. Do not
  construct a real `redis.Redis(db=0)` anywhere in this task.
- Cover: db 0 blocked, db 1-15 delegated, `flushall` blocked at any db, async variants, missing/
  malformed `connection_kwargs` → treated as db 0, the four falsy override values leaving the guard
  armed, `"1"` disarming both, idempotent double-`install()`, and `install()` with `redis` unimportable.
- Additionally cover (D6a regression): install, apply a conftest-shaped wrapper on top that sets only
  `_db0_guarded`, call `install()` again, assert the delegation chain did not grow (closure depth, or a
  call counter on the original incrementing exactly once per call).
- Additionally cover (D2a): `arm()` does not import `redis` — assert `"redis" not in sys.modules`
  after `arm()` in a subprocess that imports nothing else — and that importing `redis` afterwards
  leaves the guard live. Also assert `import redis.asyncio` **first** (without `import redis`) arms
  the guard, and that a deliberately broken `install()` does not break `import redis`.
- Additionally cover (D2b/D2b-i): self-heal into a `tmp_path` fake venv, self-heal skipped on
  read-only site-packages, self-heal exceptions never escaping `install()`, and `import tools`
  succeeding when `tools.redis_flush_guard` is unimportable (simulate by blocking the import).
- Additionally cover (D2a-ii): the startup-budget case — spawn `python -X importtime -c pass`, parse
  the `cumulative` field of the **`_redis_flush_guard_boot` line specifically** (not the final
  `import time:` line, which is whatever imported last), and assert it is under
  `_STARTUP_BUDGET_MS`. Take the **minimum across five trials**, since `-X importtime` reports wall
  clock and one sample on a loaded machine measures contention. On an unhealed venv, measure a fresh
  copy of the shim via `PYTHONPATH` instead. Do **not** use `-S` as the baseline, and do **not**
  introduce a kill-switch env var. Mark `slow`.

### 2. `.pth` installer, `/update` + worktree wiring, doctor checks
- **Task ID**: build-propagation
- **Depends On**: build-guard-core
- **Validates**: `tests/unit/test_redis_flush_guard_pth_installer.py` (create), existing doctor tests
- **Informed By**: spike-2 (`.pth` ordering, uv survival, 17 venvs), Risk 1, Race 1
- **Assigned To**: propagation-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `scripts/update/redis_flush_guard_pth.py` exposing **`install_into(venv_path)`** (one venv,
  reused by the D2b self-heal and by a `--venv <path>` CLI flag) and a fleet entry point that
  discovers `.venv`, `.worktrees/*/.venv`, and `.claude/worktrees/*/.venv`; for each, locate
  `lib/python*/site-packages` and write `_redis_flush_guard_boot.py` **then**
  `zzz_redis_flush_guard.pth`, both via write-temp + `os.replace()` (Race 1). Return a structured
  per-venv result.
- The shim's entire body is a `try: import tools.redis_flush_guard; …arm()` / `except Exception:
  pass` — `arm()`, not `install()`, so no interpreter start pays an `import redis` it does not need
  (D2a). The `.pth` is the single line `import _redis_flush_guard_boot`. Document why the `zzz_`
  prefix is load-bearing (must sort after `_editable_impl_valor_bridge.pth`).
- **The shim carries no kill-switch env var (D2a-ii).** `REDIS_PRODUCTION_FLUSH_OK` is the single
  override in this design; a second, blanket one would disarm Layer 1 for a whole process while being
  invisible to Layer 3's validator. The startup budget is measured by parsing the
  `_redis_flush_guard_boot` line's cumulative field out of `python -X importtime -c pass`, which needs
  no toggle.
- Skip-with-reason (never crash) for: not a venv, no site-packages, read-only site-packages.
  Idempotent: identical content is a no-op that reports `unchanged`.
- Wire into `scripts/update/run.py` after Step 1.5 and before Step 3, following the non-fatal
  log/warn/continue shape of Step 3.13.
- Wire into `agent/worktree_manager.py`'s venv bootstrap so a new worktree is guarded before its first
  `python` call. Note this covers `.worktrees/{slug}/` only; `.claude/worktrees/{agent}/` is
  harness-created and is covered by the D2b self-heal in Task 1, not here.
- Add `tools/doctor.py::_check_redis_flush_guard`: for **each** discovered venv, spawn
  `<venv>/bin/python -c` that imports `redis` and prints
  `getattr(redis.Redis.flushdb, "_prod_flush_guarded", False)`. FAIL naming every venv that reports
  False, with **the per-venv installer invocation** (`python -m scripts.update.redis_flush_guard_pth
  --venv <path>`) as remediation — not a bare `/update`, which does not help someone holding one
  unhealed harness worktree. Follow `_check_worktree_interpreters` for the iteration pattern and
  `CheckResult` shape; register in `get_checks()`.
- Report the startup number for the PR body by running the same measurement the Task 1 pytest case
  asserts (parsing the `cumulative` field of the `_redis_flush_guard_boot` line from `python -X importtime -c pass`, best of five trials). The assertion lives in the
  test; this task only reports the number (D2a-ii, Risk 7).
- Installer tests use `tmp_path` fake venvs only. Never write into a real venv from a test.

### 3. Redis ACL planner (report-only) + settings + secret placeholder + popoto username plumbing
- **Task ID**: build-acl
- **Depends On**: build-propagation
- **Validates**: `tests/unit/test_redis_acl.py` (create), `tests/unit/test_redis_bootstrap_username.py` (create)
- **Informed By**: spike-1 (no db discrimination; aclfile immutable; `NoPermissionError`), spike-3 (the bootstrap hand-parse), D3, D4, **D8**, **D8a**, **D9**, Risk 2, Risk 6, Risk 8, Race 3, `scripts/update/redis_replication.py` (#1827)
- **Assigned To**: acl-builder
- **Agent Type**: builder
- **Parallel**: false

**This task must not mutate the live Redis server, and neither must anything it wires into
`/update`.** Read D8 before writing a line. If a step here would change server state on merge, it is
wrong.

- Create `scripts/update/redis_acl.py` with `apply_redis_acl(apply: bool = False) -> RedisAclResult`,
  modeled on `scripts/update/redis_replication.py`'s marker-gated, never-mutate posture (not
  `redis_persistence.py`'s apply-on-update posture). Result dataclass mirrors
  `RedisPersistenceResult` (`success`, `action`, `warning`, `error`) plus `planned_commands: list[str]`
  and `drift: bool`.
- **Report path (`apply=False`, the default and the only value `/update` ever passes):** read
  `ACL LIST` / `ACL GETUSER`, diff against the target rule set, populate `planned_commands`, return.
  Issue no `ACL SETUSER`, no `ACL SAVE`, write no file. Expose `--dry-run` on the module CLI that
  prints the planned commands. **This path does not depend on `REDIS_APP_PASSWORD` (D8a)** — it always
  plans four commands, emitting the literal token `<REDIS_APP_PASSWORD>` where the secret would go, so
  the acceptance evidence holds on a build machine that has no secret and so no log or PR body ever
  carries the value.
- **Apply path** requires **all three**: `apply=True`, the marker file `data/redis-acl-enabled`
  present, and `REDIS_ACL_APPLY=true` in the environment. Any missing → `action="skipped"` with the
  reason naming which gate failed. **Then, and only inside this branch, check `REDIS_APP_PASSWORD`**
  (D8a) — unset → `action="skipped", error="REDIS_APP_PASSWORD unset"`. Never gate the whole function
  on the password. Only then: (a) `ACL SETUSER valor-app on ><REDIS_APP_PASSWORD> ~*
  &* +@all -flushdb -flushall`; (b) `ACL SETUSER default … -flushall`, preserving every other existing
  `default` rule; (c) `ACL SAVE`, downgrading to a warning if no aclfile is loaded yet; (d) re-read
  `ACL GETUSER` for both users and report a mismatch (Race 3).
- **The `aclfile` directive is staged, never written.** Emit the exact directive line and target path
  in the result for the runbook. Do not open `redis.conf` for writing on any path.
- **Never restart Redis** and never issue `SHUTDOWN`, `CONFIG REWRITE`, or `brew services restart`
  (Risk 6). Runtime `ACL SETUSER` is immediate; the aclfile is for persistence only.
- Issue each `ACL SETUSER` as a complete declarative rule set, never a delta, so concurrent runs
  converge (Race 3).
- When the apply path skips on a missing password, the action is `skipped`, **not** `success=False`,
  so `/update` logs quietly instead of appending a warning on every machine that lacks the secret.
- Module header comment records (a) spike-1's selector finding verbatim in substance, so nobody
  "optimizes" the rule into a vacuous selector, and (b) that `/update` must never apply, and why
  (D8/Risk 8).
- Wire as Step 3.135 in `scripts/update/run.py`, immediately after Step 3.13 and before Step 3.14,
  calling `apply_redis_acl()` **with no arguments**. Never forward `params.apply` or any global apply
  flag.
- Add `RedisSettings.app_password: str = Field(default="", description="…(env: REDIS_APP_PASSWORD)")`
  to `config/settings.py` — defaulted empty so `Settings()` never fails on a machine without it.
  **Do not add a `.env.example` placeholder in this PR** (round-3 finding): it would buy a fleet-wide
  manual `.env` chore for a credential nothing here reads, and omitting it keeps
  `check_env_completeness` green with zero operator action. **Do not write to `.env` or the vault**;
  the real value is an operator step in the runbook.
- Add the D9 bootstrap username fix and its test (see the D9 bullet in this task's list below).
- Add `tools/doctor.py::_check_redis_acl`: reports current drift (whether `valor-app` exists with
  flush denied, whether `default` still permits `flushall`) with the **runbook** as remediation, not
  `/update` — `/update` cannot fix this by design. Register in `get_checks()`.
- **D9 — the popoto username plumbing (BLOCKER fix, ships in this PR, inert until #2661).** In
  `config/redis_bootstrap.py`, beside `password = parsed.password or None` (`:112`) add
  `username = parsed.username or None`, and add `username=username` to the
  `set_REDIS_DB_settings(...)` kwargs block (`:119-132`). Without it, the rotation makes popoto send a
  one-argument `AUTH <pw>` against `nopass` `default` and the whole fleet loses Redis. Verified safe:
  popoto forwards `**kwargs` straight into `redis.Redis(...)` and `username=None` is redis-py's
  default, so pre-rotation behavior is byte-identical. Do **not** rewrite this site to use
  `redis.Redis.from_url` — that is a separate refactor (Rabbit Holes) and the surrounding `db`, `retry`,
  and timeout kwargs are assembled around the hand-parse.
- New `tests/unit/test_redis_bootstrap_username.py`: patch `popoto.redis_db.set_REDIS_DB_settings`,
  assert `redis://valor-app:pw@h:6379/0` yields `username="valor-app"` in the captured kwargs and that
  a bare `redis://h:6379/0` yields `username=None`. Connect to nothing.
- Tests fake `redis-cli` via a stub executable or by patching the subprocess call. Never touch the
  live server; never assert against production `ACL LIST` output in a test. Include the apply-gate
  matrix, the D8a password-gate pair, the placeholder no-secret-leak assertion, and the "`/update`
  passes no `apply`" regression test from `## Failure Path Test Strategy`.

### 4. PreToolUse flush validator
- **Task ID**: build-hook-validator
- **Depends On**: none
- **Validates**: `tests/unit/test_validate_no_redis_flush.py` (create), `tests/unit/test_pre_tool_use_dispatcher.py`
- **Informed By**: spike-3 (`_POPOTO_CONTEXT` gate is wrong for flush), D5, **D5a**, **D5b**, and
  **#2562's `validate_no_broad_process_kill`** — copy its shape: a hard block on a destructive command
  family whose message names the sanctioned alternative. #2448 is the second working example of the
  post-#2435 registration flow.
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/hooks/validators/validate_no_redis_flush.py` exposing a pure
  `find_violation(command: str) -> str | None` that never raises for well-formed input and returns
  `None` for empty/None input. **No `_POPOTO_CONTEXT` gate** — flush is unconditionally dangerous.
- **Implement the escape first (D5a):** before evaluating `_BLOCK_PATTERNS`, return `None` when
  `re.search(r"\bREDIS_PRODUCTION_FLUSH_OK=1\b", command)` matches. Without this the block message
  names an escape that does not exist and Risk 4's mitigation is void.
- Match call shapes only: `\.flushdb\s*\(`, `\.flushall\s*\(`, and `redis-cli\s+.*\bFLUSHDB\b` /
  `\bFLUSHALL\b` (case-insensitive for the CLI forms). Do not match the bare word.
- The block reason quotes the exact working prefix form
  (`REDIS_PRODUCTION_FLUSH_OK=1 python -c "…"`), points at the per-process test-db idiom, and cites
  both incident dates.
- Add `_run_no_redis_flush(command, cwd)` to `.claude/hooks/dispatch/pre_tool_use_bash.py` (lazy
  import inside the function, matching `_run_no_raw_redis_delete` at `:116-119`) and append to
  `_VALIDATORS` at `:183` with `fail_closed=False`.
- Update the `manifest.toml` comment block at `:74-89` to say 9 in-process predicates and name this
  one. **Add no new `[[hook]]` stanza.** Re-confirm the `timeout = 20` budget still holds (reporting
  duty listed under `## Team Orchestration`).
- Tests: each blocked shape blocks; `grep -rn flushdb tests/`, `rg flushall`, and prose containing the
  words do **not** block; **`find_violation('REDIS_PRODUCTION_FLUSH_OK=1 python -c "r.flushdb()"')`
  returns `None`** (D5a regression); empty and `None` input return `None`; the reason renders through
  `dispatch()` for a real `{"tool_name": "Bash", …}` payload.
- **Before landing, re-read `## Verification` and confirm no row is a Bash command containing a shape
  this validator blocks (D5b).** The plan's own acceptance evidence must remain runnable by an agent
  once this validator is live.

### 5. Coexistence + seam validation
- **Task ID**: validate-coexistence
- **Depends On**: build-guard-core, build-propagation
- **Assigned To**: coexistence-validator
- **Agent Type**: validator
- **Parallel**: false
- Install the guard into this worktree's venv, then run `tests/unit/test_redis_flush_guard.py`
  **unmodified**. All 7 cases must pass. Capture the output verbatim for the PR body (D6).
- Confirm both sentinels are present and distinct under pytest: `_prod_flush_guarded` on the inner
  callable, `_db0_guarded` on the outer — and confirm the D6a consequence, that
  `getattr(redis.Redis.flushdb, "_prod_flush_guarded", False)` reads `False` in-process under pytest
  while the guard is still armed. That is expected, not a defect; it is why idempotence keys on
  `_INSTALLED` and why the doctor probes a subprocess.
- Confirm a permitted flush still reaches Redis: within a pytest process, flush **this process's own
  claimed db** via the existing fixture path. Do not construct a client on any db this process does
  not own, and do not construct a db-0 client at all.
- Run the seam anti-criterion: the PR diff must contain no `tests/conftest.py`, `tests/db_claim.py`,
  `tests/unit/test_redis_flush_guard.py`, or `docs/features/test-db-ownership.md`.
- Report pass/fail with evidence; do not fix anything.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: build-guard-core, build-propagation, build-acl, build-hook-validator
- **Assigned To**: flush-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/redis-flush-hardening.md` per the Documentation section.
- Add the `docs/features/README.md` index row.
- Add the `CLAUDE.md` § Manual Testing Hygiene paragraph naming the `setdefault` foot-gun.
- Link to (do not create) `docs/features/test-db-ownership.md`.

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-guard-core, build-propagation, build-acl, build-hook-validator, validate-coexistence, document-feature
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every row in `## Verification`.
- Confirm every Success Criterion.
- Generate the final report.

## Verification

**D5b applies to every row in this table: no row may be a Bash command containing a shape Layer 3
blocks**, or the final-validator cannot run it once Layer 3 is live. Assertions about blocked call
shapes are therefore made in pytest files, not inline. Do not "fix" a blocked row by prefixing
`REDIS_PRODUCTION_FLUSH_OK=1` — that disarms the layer under test.

**Reading the `grep -c` anti-criteria.** `grep -c` exits **1** when the count is zero, so the *passing*
state of those rows is a non-zero exit. Judge them on the **printed number**, which must be `0`; never
on exit status. A validator that grades these on exit status will read every passing run as a failure
and will be tempted to "fix" it by inverting the check, which silently deletes the anti-criterion.

| Check | Command | Expected |
|-------|---------|----------|
| Guard blocks db 0 and flushall (stub-driven, D7; replaces the two inline `python -c` rows that Layer 3 would block — D5b) | `scripts/pytest-clean.sh tests/unit/test_redis_flush_guard_prod.py -q` | exit code 0 |
| Guard live at interpreter start | `.venv/bin/python -c "import redis;print(getattr(redis.Redis.flushdb,'_prod_flush_guarded',False))"` | output contains True |
| Lazy arming: no `import redis` from the `.pth` alone (D2a) | `.venv/bin/python -c "import sys;print('redis' in sys.modules)"` | output contains False |
| Startup overhead within budget, asserted not eyeballed (D2a-ii, Risk 7, replaces the unevaluatable `-S` row) | `scripts/pytest-clean.sh tests/unit/test_redis_flush_guard_prod.py -k startup_budget -q` | exit code 0; the case parses the `_redis_flush_guard_boot` cumulative field from `-X importtime`, takes the best of five trials, and asserts under `_STARTUP_BUDGET_MS`. Number also reported in the PR body |
| `import tools` arms without importing `redis` (D2a laziness via the D2b-i trigger). **The self-heal itself is covered only by `tests/unit/test_redis_flush_guard_prod.py`** — a genuine self-heal row would have to mutate a real venv, which no Verification row may do | `.venv/bin/python -c "import tools,sys;print('redis' in sys.modules)"` | output contains False |
| `.pth` + shim installed | `ls .venv/lib/python*/site-packages/zzz_redis_flush_guard.pth .venv/lib/python*/site-packages/_redis_flush_guard_boot.py` | exit code 0 |
| Doctor reports guard liveness | `python -m tools.doctor --json` | output contains redis_flush_guard |
| Doctor reports ACL drift | `python -m tools.doctor --json` | output contains redis_acl |
| ACL planner is report-only (D8) | `python -m scripts.update.redis_acl --dry-run` | prints the four planned ACL commands; exit code 0 |
| ACL planner made no server mutation | `redis-cli ACL LIST` | output is unchanged from before the run — still the single `user default …` line; **no `valor-app`** |
| ACL planner plans four commands with no secret present (D8a) | `python -m scripts.update.redis_acl --dry-run` | four planned commands, the `valor-app` one containing the literal `<REDIS_APP_PASSWORD>` placeholder; no secret value in the output |
| Apply gates hold, password gates apply-only, `/update` passes no `apply` | `scripts/pytest-clean.sh tests/unit/test_redis_acl.py -q` | exit code 0 |
| Popoto receives the URL username (D9, BLOCKER) | `scripts/pytest-clean.sh tests/unit/test_redis_bootstrap_username.py -q` | exit code 0 |
| D9 is actually wired, not just tested | `grep -c 'username=username' config/redis_bootstrap.py` | output > 0 |
| Existing guard tests pass unmodified | `scripts/pytest-clean.sh tests/unit/test_redis_flush_guard.py -q` | exit code 0 |
| New unit tests pass | `scripts/pytest-clean.sh tests/unit/test_redis_flush_guard_prod.py tests/unit/test_validate_no_redis_flush.py tests/unit/test_redis_flush_guard_pth_installer.py tests/unit/test_redis_acl.py tests/unit/test_redis_bootstrap_username.py -q` | exit code 0 |
| Dispatcher contract intact | `scripts/pytest-clean.sh tests/unit/test_pre_tool_use_dispatcher.py -q` | exit code 0 |
| Validator blocks flush shapes, passes grep/rg/prose, and honors the `REDIS_PRODUCTION_FLUSH_OK=1` escape (D5a; replaces the two inline `python -c` rows Layer 3 would block — D5b) | `scripts/pytest-clean.sh tests/unit/test_validate_no_redis_flush.py -q` | exit code 0 |
| CLAUDE.md names the foot-gun | `grep -c 'setdefault' CLAUDE.md` | output > 0 |
| Feature doc indexed | `grep -c 'redis-flush-hardening' docs/features/README.md` | output > 0 |
| Anti-criterion: seam with #2628 held | `git diff --name-only origin/main...HEAD \| grep -c -e 'tests/conftest.py' -e 'tests/db_claim.py' -e 'tests/unit/test_redis_flush_guard.py' -e 'docs/features/test-db-ownership.md'` | printed number is `0` (ignore exit status) |
| Anti-criterion: no sitecustomize install | `grep -rn 'sitecustomize' scripts/ tools/ agent/ \| grep -v '\.md:' \| grep -vc 'never\|not usable\|shadow'` | printed number is `0` (ignore exit status) |
| Anti-criterion: no Redis restart in planner | `grep -c -e 'SHUTDOWN' -e 'CONFIG REWRITE' -e 'brew services restart' scripts/update/redis_acl.py` | printed number is `0` (ignore exit status) |
| Anti-criterion: planner never writes redis.conf (D8) | `grep -c -e "open(.*redis.conf" -e "redis_conf.*write_text" scripts/update/redis_acl.py` | printed number is `0` (ignore exit status) |
| Anti-criterion: no `.env`/vault write (CLAUDE.md § Secrets) | `grep -rc -e "Desktop/Valor/.env" -e "\.env\"" scripts/update/redis_acl.py config/redis_bootstrap.py` | printed number is `0` for both files (ignore exit status) |
| Anti-criterion: no real db-0 client in tests | `grep -rn 'Redis(db=0)\|/0")' tests/unit/test_redis_flush_guard_prod.py` | exit code 1 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

**Round 3 — 9 findings, no BLOCKERs.** The war room found no defect that blocks the build. Seven
CONCERNs cluster in two places. First, the D2b/D2b-i self-heal: its trigger is still specified on
`install()` (which lazy arming defers to the first `import redis`) rather than on `arm()`, and its
import target drags the whole `/update` package in through `scripts/update/__init__.py`'s eager
`from .run import ...`. Second, the layer's own bootstrap edges: the `sys.meta_path` swap can drop
the finder permanently on an exception, and the `/update` installer is sequenced before the
`uv sync` that creates the venv it is meant to guard. The remaining two CONCERNs are scope: an
`.env.example` placeholder that imposes a fleet-wide manual `.env` edit for a secret this PR never
reads, and a second blanket bypass env var introduced only to enable a benchmark. Two NITs cover
decision-block ordering and a Verification row that does not assert what it is labelled. `revision_applied`
is already `true` from round 2, so no plan-revising lock is set; the concerns above are for the revision
pass or the builder to absorb.


| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness | D2b-i's self-heal trigger still does not fire on the path it names. D2b-i claims `tools/__init__.py`'s `arm()` call is "the only self-heal trigger that fires in a harness-created `.claude/worktrees/{agent}/` checkout", but Task 1 and D2b put the self-heal inside `install()`, and D2a defers `install()` to the first real `import redis`. `import tools` in a process that never touches Redis arms a meta-path finder and heals nothing, so the round-2 finding is re-specified rather than closed and the highest-risk checkout stays unhealed for every non-Redis CLI invocation. | **ABSORBED IN BUILD** — the self-heal probe now runs in `arm()` (`tools/redis_flush_guard.py::_self_heal`, called before the finder is inserted), gated on `sys.prefix != sys.base_prefix` and a single `os.path.exists` on the `.pth`. `install()` keeps an idempotent self-heal call, but `arm()` is the harness-worktree trigger. D2a's contract holds: `test_arm_does_not_import_redis` still passes. | Move the self-heal probe out of `install()` and into `arm()`, so it runs on the trigger D2b-i actually names. In `arm()`, before inserting the finder: resolve the current venv from `sys.prefix` (skip when `sys.prefix == sys.base_prefix`), `os.path.exists(<site-packages>/zzz_redis_flush_guard.pth)`, and only on a miss call `install_into(<venv>)` inside `try/except Exception: pass`. This is one `stat()` on the hit path, so it does not violate D2a's "arm() must not import redis" contract or the Task 1 test asserting `"redis" not in sys.modules` after `arm()`. Keep `install()`'s self-heal too (idempotent), but stop describing it as the harness-worktree trigger. |
| CONCERN | Risk & Robustness | The self-heal's import target cannot be imported cheaply. `scripts/update/__init__.py:10` is `from .run import UpdateConfig, run_update`, so `import scripts.update.redis_flush_guard_pth` executes `scripts/update/run.py`, which eagerly imports ~30 update-system submodules (`deps`, `git`, `verify`, `redis_persistence`, `redis_replication`, `service`, `kokoro`, ...) and at `scripts/update/run.py:22` runs `sys.path.insert(0, str(PROJECT_ROOT))` as an import side effect. D2b requires the self-heal to be invisible when it cannot work; as specified it drags the whole update system into every unhealed-venv interpreter start and silently mutates `sys.path[0]` for the process. | **ABSORBED IN BUILD** — `_load_pth_installer()` uses `spec_from_file_location("_rfg_pth_installer", _PTH_INSTALLER_PATH)` + `module_from_spec` + `exec_module`; `scripts/update/__init__.py` is untouched. `scripts/update/redis_flush_guard_pth.py` was written stdlib-only so it loads standalone. Regression test `test_self_heal_loads_installer_by_path_not_package_import` asserts `sys.path[0]` is unchanged and that the self-heal adds no `scripts.update*` entry to `sys.modules`. Mutation-verified: reintroducing a package import turns it red, naming the whole update system. | Load the installer by file path on the self-heal path instead of by package import: `importlib.util.spec_from_file_location("_rfg_pth_installer", <repo>/scripts/update/redis_flush_guard_pth.py)` + `module_from_spec` + `exec_module`, inside the existing `try/except Exception: pass`. That leaves `scripts/update/__init__.py` untouched (so `/update` is unchanged) and guarantees exactly one module import with no `sys.path` mutation. Add a regression test asserting a self-heal into a `tmp_path` fake venv leaves `sys.path[0]` unchanged and does not put `scripts.update.run` in `sys.modules`. |
| CONCERN | Risk & Robustness | The D2a-i finder's `sys.meta_path` swap is neither exception-safe nor thread-safe. It "removes itself from `sys.meta_path`, calls `importlib.util.find_spec(fullname)`, re-inserts itself" with no `finally` and no lock. If `find_spec` raises (renamed `redis.asyncio`, a broken third-party finder later in the chain, a partially-installed venv) the finder is never re-inserted and Layer 1 is silently disarmed for the rest of the process; concurrent imports of `redis` and `redis.asyncio` from two threads can duplicate or drop it. Both outcomes are the silent-inert failure Risk 1 names as the primary hazard, reached through the mechanism added to fix Risk 7, and neither appears in `## Race Conditions`. | **ABSORBED IN BUILD** — the swap is wrapped in `with _FINDER_LOCK:` / `try:` … `finally: if self not in sys.meta_path: sys.meta_path.insert(0, self)`, returning `None` when `find_spec` raises so the real import proceeds unguarded. `_FINDER_LOCK` is an **RLock**, not a Lock: it is held across `find_spec`, which imports the parent package for a dotted name and can re-enter on the same thread. Both prescribed tests exist; the exception test is mutation-verified (deleting the `finally` turns it red). | Guard the swap with a module-level `_FINDER_LOCK = threading.Lock()` and make re-insertion unconditional: `with _FINDER_LOCK:` / `try: sys.meta_path.remove(self); spec = importlib.util.find_spec(fullname)` / `finally: if self not in sys.meta_path: sys.meta_path.insert(0, self)`. The `not in` test prevents the duplicate-finder outcome and the `finally` prevents the permanent-disarm outcome; return `None` when `find_spec` raised so the real import proceeds unguarded rather than failing. Two tests: (a) monkeypatch `importlib.util.find_spec` to raise, assert the finder survives in `sys.meta_path` and a later `import redis` still arms; (b) re-entrant two-thread call asserting `sys.meta_path.count(finder) == 1`. Add a Race 4 entry. |
| CONCERN | Scope & Value | The `.env.example` `REDIS_APP_PASSWORD` placeholder buys a fleet-wide manual chore for a credential this PR never reads. D8b, an `## Update System` bullet, and runbook "step 0" exist solely to neutralise fallout the placeholder itself creates: a `REDIS_APP_PASSWORD=` blank-line edit to `~/Desktop/Valor/.env` on every machine at merge time. The report path emits the literal `<REDIS_APP_PASSWORD>` token instead of the secret (D8a), so nothing shipped here needs the value, and any machine whose operator misses step 0 gets a permanent `/update` missing-secret warning -- exactly the noise D8b exists to prevent. | **ABSORBED IN BUILD** — no `.env.example` change ships in this PR (`git diff` over `.env.example` is empty). `RedisSettings.app_password: str = Field(default="")` is kept, and the apply path still reads `os.environ`. D8b, its `## Update System` bullet, runbook step 0, and the `test_env_completeness.py` Verification row are all deleted from this plan. | Drop the `.env.example` stanza from this PR; add it in the PR that performs the apply / #2661 rotation. `check_env_completeness` (`scripts/update/verify.py:1088`) derives its key set solely from `.env.example` via `declared = _parse_env_example(env_example)` (`:1109`) and never inspects `config/settings.py`, so keeping `RedisSettings.app_password: str = Field(default="")` while omitting the `.env.example` entry leaves the check green on every machine with zero operator action, and `apply_redis_acl()` still reads the value from `os.environ` on the apply path. Concretely: delete the `.env.example` bullet from Task 3, delete D8b, delete its `## Update System` bullet, delete runbook step 0, and drop the `test_env_completeness.py` Verification row. |
| CONCERN | Scope & Value | `REDIS_FLUSH_GUARD_DISABLE` adds a second, broader bypass of the load-bearing layer purely to enable a benchmark. The repo then has two ways to run unguarded where the plan intends one: `REDIS_PRODUCTION_FLUSH_OK=1`, which the block message and error text teach, and a blanket kill-switch that disarms Layer 1 for a whole process and is invisible to Layer 3's validator (which only matches `REDIS_PRODUCTION_FLUSH_OK=1`). The plan's own hedge -- document it "not as a supported way to run unguarded" -- acknowledges the hazard without removing it. | **ABSORBED IN BUILD** — no such env var exists anywhere in the diff; `REDIS_PRODUCTION_FLUSH_OK` remains the single override. Measurement instead parses the `cumulative` field of the `_redis_flush_guard_boot` line from `python -X importtime -c pass` (verified empirically: `-X importtime` does trace `.pth` imports and gives the shim its own line). Note the plan's "final `import time:` line" was also wrong — the last line is `linecache`. Because that number is wall clock, the case takes the **best of five trials**; a single sample read 107 ms under a loaded xdist run versus ~6 ms idle. | Measure the shim directly instead: `cumulative` from the final `import time:` line of `python -X importtime -c "import _redis_flush_guard_boot"` minus the same field from `python -X importtime -c pass`. Both legs keep every other `.pth` and the difference is exactly this guard's cost, with no new env var; the assertion against `_STARTUP_BUDGET_MS` stays in the pytest case. If the kill-switch is kept anyway, D2a-ii and Task 2 must state that the check is the **first statement of `_redis_flush_guard_boot.py`, before `import tools.redis_flush_guard`** -- placing it after the import (which "short-circuit before it calls `arm()`" permits literally) makes the measured delta ~0 and the budget assertion pass vacuously. |
| CONCERN | History & Consistency | Solution / Key Elements contradicts D8, Task 3, Risk 6 and the Verification anti-criterion on whether the planner writes `redis.conf`. Key Elements says the planner "issues no `ACL SETUSER`, no `ACL SAVE`, and writes no redis.conf **unless** *both* the operator marker `data/redis-acl-enabled` exists *and* `REDIS_ACL_APPLY=true` is set" -- i.e. that with both gates satisfied it does write `redis.conf`. D8 says the directive is "staged, not written" and that `redis.conf` "is not opened for writing by anything in this PR"; Task 3 says "Do not open `redis.conf` for writing on any path"; and an anti-criterion greps the module for exactly that. A builder following Key Elements literally fails the plan's own anti-criterion. | **ABSORBED IN PLAN** — the Key Elements bullet now states the `redis.conf` clause unconditionally ("**never** writes `redis.conf` on any path") and gates only the ACL commands. The anti-criterion is unchanged and passes: `grep -c` over `scripts/update/redis_acl.py` prints 0. | Rewrite the Key Elements bullet so the `redis.conf` clause is unconditional and only the ACL commands are gated: "It **never** writes `redis.conf` on any path -- the `aclfile` directive is emitted as text in the result for the operator to add by hand. It issues no `ACL SETUSER` and no `ACL SAVE` unless *both* the operator marker `data/redis-acl-enabled` exists *and* `REDIS_ACL_APPLY=true` is set -- a combination `/update` never supplies (D8)." Leave the anti-criterion unchanged; it is the mechanical check this bullet must agree with. |
| CONCERN | History & Consistency | The `/update` installer placement rationale is inverted. `## Update System` says the step is "Placed before `uv sync` so a freshly recreated venv is guarded within the same run", but `uv sync` is what creates `.venv` when it is absent (`scripts/update/deps.py::sync_dependencies` -> `sync_with_uv`) and Step 3 sits at `scripts/update/run.py:993`, after the proposed insertion point. Running the installer first means it skips a missing venv with "not a venv / no site-packages", and the venv `uv sync` then creates stays unguarded until the next `/update` -- the opposite of the stated reason, and a fresh "installed but silently inert" state on a just-bootstrapped machine (Risk 1). | **ABSORBED IN BUILD** — the installer is Step 3.05 in `scripts/update/run.py`, placed after the `if config.do_dep_sync:` block closes and before Step 3.5, and it runs **unconditionally outside** that `if`. The `## Update System` bullet now states the corrected rationale. | Insert the step after the `if config.do_dep_sync:` block closes in `scripts/update/run.py` (between Step 3 at `:993` and Step 3.5 at `:1053`), numbered e.g. Step 3.05, following the non-fatal log/warn/continue shape of Step 3.13 (`:1299`). Step 3 is conditional (`should_sync` is only true when dep files changed or `force_dep_sync`), so the installer must run unconditionally **outside** that `if`, not inside it. Then correct the `## Update System` bullet to say the placement is after dep sync precisely so a venv created or recreated by `uv sync` is guarded within the same run. |
| NIT | Scope & Value | The Technical Approach decision blocks are emitted out of order -- D1, D2, D2a, D2a-i, D2a-ii, D2b, D2b-i, D3, D4, **D8, D8a, D8b, D9**, D5, D5a, D5b, D6, D6a, D7 -- because the revision-round additions were appended after D4 rather than in sequence. A builder reading top-to-bottom hits D8/D9 before D5-D7, and the "Informed By" lists on Tasks 3 and 4 read as out-of-order references. | **ABSORBED IN PLAN** — an index table of the decision blocks now heads `### Technical Approach`, listing them in reading order with their subjects. | Renumber or reorder the blocks so they run monotonically, or add a one-line index of the decision blocks at the top of Technical Approach. |
| NIT | History & Consistency | The Verification row labelled "Guard self-heals via `import tools` (D2b-i)" does not test the self-heal. It runs `.venv/bin/python -c "import tools,sys;print('redis' in sys.modules)"` expecting `False`, which is the D2a laziness property already asserted by the row directly above it; `.venv` is a healed venv, so no install path is exercised and the row would pass identically if the self-heal were deleted. D2b-i -- added specifically to close the harness-worktree gap -- therefore has no Verification row, only the `tmp_path` unit-test bullet in Task 1. | **ABSORBED IN PLAN** — the row is relabelled as the laziness check it actually is, and now states explicitly that the self-heal is covered by `tests/unit/test_redis_flush_guard_prod.py` alone, because a genuine self-heal row would have to mutate a real venv. | Relabel the row as the laziness check it is, and add a real self-heal row -- or state explicitly that the self-heal is covered by `tests/unit/test_redis_flush_guard_prod.py` only, since a genuine self-heal row would have to mutate a real venv. |

---
## Resolved Questions

All three open questions were answered in the revision round. None remain open; the plan is Ready.

1. **Layer 2 rotation sequencing — answered: keep it sequenced.** The `REDIS_URL` rotation stays an
   `[EXTERNAL]` No-Go tracked as #2661. The revision round strengthens the case: the ACL apply itself
   is now also human-gated (D8), so the ordering is planner ships → operator applies per machine →
   doctor reports fleet readiness → rotation. Layers 1 and 3 already cover agent-driven clients, which
   is what acceptance criterion 2 asks for.

2. **`default` keeps `flushdb` — answered: yes, keep it.** Denying it would force test-credential
   plumbing into `tests/conftest.py` and `tests/db_claim.py`, which #2628 owns and is about to
   rewrite; absorbing that conflict is not worth it. A bare `redis-cli -n 0 flushdb` typed at a human
   shell remains possible at the server layer until #2628 lands and a follow-up flips it. Layer 3
   blocks that exact command when an *agent* types it, which is the vector both incidents came from.

3. **Appetite — answered: stays Large, all four layers, no descoping.** The proposed cut (ship Layers
   1/3/4 and split Layer 2 into its own issue) is declined. Layer 2 is the only layer that covers
   non-Python and non-venv clients, and the BLOCKER-1 fix already removes what made it risky — it is
   now config plus a runbook, with the mutation behind an operator's hands (D8). Shipping the
   mechanism costs little and leaves the fleet one signed-off command from the applied state; deferring
   it would leave the server layer unbuilt for the sake of a smaller diff.
