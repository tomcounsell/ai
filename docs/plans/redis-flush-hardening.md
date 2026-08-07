---
status: Planning
type: bug
appetite: Large
owner: Valor Engels
created: 2026-08-07
tracking: https://github.com/tomcounsell/ai/issues/2645
last_comment_id: none
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
| 2. Redis ACL | The Redis server | Non-Python clients (`redis-cli`, other languages, other checkouts) once the production URL is rotated |
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
  `config/redis_bootstrap.py:106-132`, which already `urlparse`s username/password out of the URL.
  Popoto reads **`REDIS_URL` only**, falling back to a hardcoded `127.0.0.1:6379` **db 0**;
  `popoto/pytest_plugin.py:171,194` flush the plugin's own test db (15 or `POPOTO_TEST_DB`).
- **Confidence**: high
- **Impact on plan**: (a) a blanket flush guard breaks nothing; (b) credentials placed in `REDIS_URL`
  reach all 21 sites with **zero call-site edits**, which is what makes Layer 2 tractable; (c) the
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
  guard. It is mitigated by being import-cheap, fully exception-wrapped, and idempotent.
- **Data ownership**: unchanged. No Popoto model changes, so **no schema migration is required**.
- **Reversibility**: high. Layer 1 = delete two files per venv (`/update` re-installs, so removal
  means deleting the installer too). Layer 2 = `ACL SETUSER default … +@all` + revert the `aclfile`
  line. Layer 3 = one line out of `_VALIDATORS`. Layer 4 = a doc revert.

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
| Redis ≥ 6 (ACL support) | `redis-cli INFO server \| grep redis_version` | ACL users, `NOPERM` semantics |
| Write access to redis.conf | `test -w /opt/homebrew/etc/redis.conf` | Adding the `aclfile` directive |
| Editable repo install in venv | `python -c "import tools, sys; print(tools.__file__)"` | The `.pth` boot shim must be able to import `tools.redis_flush_guard` |
| `REDIS_APP_PASSWORD` in vault `.env` | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('REDIS_APP_PASSWORD')"` | Provisioning the `valor-app` ACL user |

Run via `python scripts/check_prerequisites.py docs/plans/redis-flush-hardening.md`.

## Solution

### Key Elements

- **`tools/redis_flush_guard.py`** — the single implementation. Patches `flushdb`/`flushall` on
  `redis.Redis` and `redis.asyncio.Redis` so that any `flushall`, and any `flushdb` on db 0, raises
  `RuntimeError` unless `REDIS_PRODUCTION_FLUSH_OK=1` is set. Idempotent, import-cheap, and it never
  raises at import time.
- **`scripts/update/redis_flush_guard_pth.py`** — idempotent installer that writes
  `zzz_redis_flush_guard.pth` + a `_redis_flush_guard_boot.py` shim into every repo venv's
  site-packages. Called from `/update` and from worktree venv bootstrap.
- **`scripts/update/redis_acl.py`** — idempotent Redis ACL provisioner: ensures an `aclfile`
  directive, creates the `valor-app` user without `flushdb`/`flushall`, removes `flushall` from
  `default`, and `ACL SAVE`s. Non-fatal, mirroring `redis_persistence.py`.
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
  `try: import tools.redis_flush_guard; tools.redis_flush_guard.install()` / `except Exception: pass`

The shim exists because `.pth` lines cannot express a `try`/`except` cleanly, and an uncaught
exception at startup prints a traceback into every `python -c`, launchd service, and hook invocation
on the machine. The `zzz_` prefix is load-bearing: `.pth` files are processed in sorted order and the
guard must run after `_editable_impl_valor_bridge.pth` has put the repo root on `sys.path`.

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
  The production `REDIS_URL` becomes `redis://valor-app:<pw>@localhost:6379/0`, which reaches all 21
  call sites for free because they all read `REDIS_URL` and `config/redis_bootstrap.py:106-132`
  already parses credentials out of it.
- Leave **`default`** able to `flushdb`. Tests build bare `redis.Redis(db=N)` with no credentials,
  authenticate as `default`, and keep working with **zero changes** — the seam against #2628 holds.
- Remove **`flushall`** from `default` immediately. Spike-3 found zero first-party callers and
  `conftest` already blocks it universally, so this costs nothing and closes the wipe-every-db vector
  at the server this PR.

**D4 — The `REDIS_URL` rotation is sequenced behind fleet readiness, and the rest of Layer 2 is not.**
The rotation is fail-loud by construction: a machine whose Redis lacks `valor-app` gets `WRONGPASS`
and its bridge/worker go down noisily. That is the correct posture (silent degradation would be
worse), but it means the vault `.env` — iCloud-synced to every machine at once — must not be rotated
until every machine in the roster has run `/update` with the provisioner in place. Editing the vault
`.env` is also an action an agent must never take (CLAUDE.md § Secrets). So this plan ships the
provisioner, the `valor-app` user, the `default -flushall` denial, and the doctor check that reports
fleet readiness; the one-line rotation is an `[EXTERNAL]` No-Go tracked as **#2661**.

Layers 1 and 3 already make a db-0 flush impossible for agent-driven clients, which is what
acceptance criterion 2 asks for; Layer 2 pre-rotation closes `flushall` for everyone and stages the
rest.

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
`redis-cli\s+.*\bFLUSHDB\b`, `redis-cli\s+.*\bFLUSHALL\b` (case-insensitive for the CLI forms). The
block message names `REDIS_PRODUCTION_FLUSH_OK=1` as the deliberate escape and points at the test-db
idiom. Note for the builder: `validate_no_raw_redis_delete` has **no** dedicated unit test today
(only indirect coverage in `test_pre_tool_use_dispatcher.py`); the new validator gets a real one.

**D6 — Coexistence with `tests/conftest.py` is proven, not assumed.**
Under pytest both guards install: Layer 1's at interpreter start (sentinel `_prod_flush_guarded`), and
conftest's at collection (sentinel `_db0_guarded`), wrapping Layer 1's. The sentinels are distinct, so
neither suppresses the other. Behavior: db 0 → conftest's wrapper raises first (its message contains
`db=0`, which is what `tests/unit/test_redis_flush_guard.py` matches on); `flushall` → conftest's
raises (`match="flushall"`); a claimed db 1-15 → conftest permits, delegates to Layer 1's wrapper,
which permits (`db != 0`), and the real flush executes. All 7 existing cases keep passing without
modification. After #2628 lands, its stricter ownership guard becomes the outer wrapper and the same
delegation holds. This must be *demonstrated* by running that file with the guard installed, and the
output pasted into the PR body — not asserted.

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
- [ ] `scripts/update/redis_acl.py` follows `redis_persistence.py`'s non-fatal contract: every failure
  path (redis-cli absent, Redis down, redis.conf unwritable, `ACL SAVE` refused) returns a structured
  result with `success=False` and a populated `error`, and the `/update` step logs a WARNING and
  appends to `result.warnings`. Test each of the four with a fake `redis-cli`.
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
  site-packages → skipped with a reported reason, never a crash, never a partial write.

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
  passing is a Verification row.
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
- [ ] New: `tests/unit/test_redis_acl.py` — provisioner logic against a faked `redis-cli`, never the
  live server.

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
issue, gated on the doctor check reporting `valor-app` provisioned on every machine in the roster
(`docs/features/single-machine-ownership.md`) — tracked as **#2661**. The provisioner ships and runs on every `/update`
first, so readiness accrues before the flip. Failure is loud and instantly diagnosable, and rollback
is a one-line `.env` revert.

### Risk 3: #2628 starts building concurrently and collides
**Impact:** Merge conflicts in `tests/conftest.py`, or two guards that disagree.
**Mitigation:** The seam is mechanical, not social: an anti-criterion in `## Verification` fails the
build if this PR's diff touches `tests/conftest.py`, `tests/db_claim.py`, or
`docs/features/test-db-ownership.md`. D6 documents the nesting so whichever lands second inherits a
described contract rather than discovering one.

### Risk 4: Layer 3 false positives block legitimate agent work
**Impact:** An agent debugging a test db cannot flush it and burns turns fighting the hook.
**Mitigation:** Patterns match call shapes, not the bare word (D5), so search and read commands pass.
The block message names `REDIS_PRODUCTION_FLUSH_OK=1` and the ORM idiom, so the escape is discoverable
in the failure itself. Explicit no-false-positive tests for `grep`/`rg`/prose.

### Risk 5: The guard's monkeypatch breaks an unrelated consumer of `redis.Redis`
**Impact:** A library that introspects or re-wraps `flushdb`, or that calls `flushall` legitimately,
starts failing in production.
**Mitigation:** Spike-3 found zero first-party callers, and popoto's only flushes are in its pytest
plugin against its own test db. The patch preserves the original callable and delegates on the
permitted path, so signatures and return values are unchanged. Idempotence via `_prod_flush_guarded`
prevents double-wrapping across repeated `install()` calls.

### Risk 6: `aclfile` addition requires a Redis restart on the production instance
**Impact:** Brief downtime on the instance the whole system depends on — the same instance the
incident just disrupted.
**Mitigation:** The provisioner **never restarts Redis**. It writes the `aclfile` directive and
applies users via `ACL SETUSER` at runtime (which takes effect immediately); the directive matters
only for persistence across the next restart, which happens on the operator's schedule. If `ACL SAVE`
is unavailable because the directive is not yet loaded, the step reports that as a warning and the
runtime rules still hold. No step in this plan issues `redis-cli SHUTDOWN` or `brew services restart
redis`.

## Race Conditions

### Race 1: `.pth` install races an interpreter that is starting
**Location:** `scripts/update/redis_flush_guard_pth.py`
**Trigger:** `/update` rewrites the shim while a launchd service is executing `site.py`.
**Data prerequisite:** Both files must be complete and mutually consistent before any interpreter
reads either.
**State prerequisite:** A partially written `_redis_flush_guard_boot.py` must never be importable.
**Mitigation:** Write to a temp file in the same directory and `os.replace()` (atomic on the same
filesystem). Write the shim **before** the `.pth`, so the `.pth` never references a missing module.

### Race 2: Guard install races `import redis` in the same process
**Location:** `tools/redis_flush_guard.py::install()`
**Trigger:** None in practice — `.pth` processing completes inside `site.py`, before any user code
imports `redis`. But `install()` patches the *class*, so a module that did `from redis import Redis`
earlier still sees the patched class (same object); only a caller that captured the *bound method*
before install would escape.
**Data prerequisite:** `redis.Redis` must be importable when `install()` runs.
**State prerequisite:** The patch must be idempotent under repeated `install()` (`.pth` plus an
explicit call).
**Mitigation:** Idempotence via the `_prod_flush_guarded` sentinel. `install()` imports `redis`
itself rather than assuming it is loaded. No pre-capture of bound methods exists in first-party code
(spike-3).

### Race 3: Two `/update` runs provision the ACL concurrently
**Location:** `scripts/update/redis_acl.py`
**Trigger:** Two machines, or two shells on one machine, running `/update` at once.
**Data prerequisite:** The final `ACL LIST` must reflect the intended rule set regardless of
interleaving.
**State prerequisite:** `ACL SETUSER` is atomic per user and idempotent for identical arguments.
**Mitigation:** The provisioner issues a full declarative `ACL SETUSER` (complete rule set, not a
delta), so any interleaving converges to the same state. It verifies by re-reading `ACL GETUSER`
after writing and reports a mismatch rather than retrying.

## No-Gos (Out of Scope)

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
  wiring, both doctor checks, the ACL provisioner, the `valor-app` user, the `default -flushall`
  denial, the hook validator and its tests, the `CLAUDE.md` paragraph, and the feature doc.

## Update System

`/update` changes are central here, not incidental — Layer 1's whole propagation story is `/update`.

- **New step (after Step 1.5 hardlinks, before Step 3 dependency sync):** install the flush-guard
  `.pth` into every repo venv via `scripts/update/redis_flush_guard_pth.py`. Placed before `uv sync`
  so a freshly recreated venv is guarded within the same run; the installer is idempotent so ordering
  is not load-bearing beyond that.
- **New step 3.135 (immediately after Step 3.13 Redis durability):** `scripts/update/redis_acl.py`.
  Same non-fatal contract as `redis_persistence.py` — log, warn, continue. Durability, then ACL, then
  replication (3.14).
- **New config file propagated:** none. The `.pth` and shim are generated, not checked in.
- **New secret:** `REDIS_APP_PASSWORD` — add to vault `~/Desktop/Valor/.env`, a placeholder plus
  comment line in `.env.example` (required by the completeness check), and a field on
  `RedisSettings` in `config/settings.py`.
- **Migration for existing installations:** none required. Both new steps are idempotent and
  self-healing on first run. No Popoto model changes, so no `scripts/update/migrations.py` entry.
- **Worktree bootstrap:** `agent/worktree_manager.py` creates a venv per worktree; it must call the
  same installer so a new worktree is guarded before its first `python` invocation.
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

## Success Criteria

- [ ] A flush guard is armed in every Python process started from a repo venv, verified by a doctor
  check that asserts liveness in a fresh subprocess (not file presence) across all repo venvs.
- [ ] `flushdb()` on db 0 and `flushall()` anywhere raise `RuntimeError` naming
  `REDIS_PRODUCTION_FLUSH_OK=1`; setting that variable to exactly `1` permits both.
- [ ] `REDIS_PRODUCTION_FLUSH_OK` set to `""`, `"0"`, `"false"`, or `"no"` leaves the guard armed.
- [ ] `tests/unit/test_redis_flush_guard.py` passes **unmodified** with the guard installed (D6
  coexistence), and the run output is pasted into the PR body.
- [ ] `ACL LIST` on this machine shows a `valor-app` user without `flushdb`/`flushall`, and `default`
  without `flushall`; a doctor check reports both.
- [ ] Test-suite Redis behavior is unchanged: `tests/unit/` passes with no edits to `tests/conftest.py`
  or `tests/db_claim.py`.
- [ ] The PreToolUse dispatcher blocks `python -c "…flushdb()"` and `redis-cli -n 0 flushdb`, and does
  **not** block `grep -rn flushdb tests/`.
- [ ] `CLAUDE.md` § Manual Testing Hygiene names the `setdefault` foot-gun.
- [ ] No file under `tests/conftest.py`, `tests/db_claim.py`, or `docs/features/test-db-ownership.md`
  appears in this PR's diff.
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
  - Role: `scripts/update/redis_acl.py`, `config/settings.py` field, `.env.example` placeholder. Touches `scripts/update/run.py` and `tools/doctor.py` **only after** `propagation-builder` finishes.
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

## Step by Step Tasks

### 1. Guard module + unit tests
- **Task ID**: build-guard-core
- **Depends On**: none
- **Validates**: `tests/unit/test_redis_flush_guard_prod.py` (create)
- **Informed By**: spike-2 (`.pth` viable, `sitecustomize` dead), spike-3 (zero first-party flush callers), D1, D7
- **Assigned To**: guard-builder
- **Agent Type**: builder
- **Domain**: redis-popoto-data
- **Parallel**: true
- Create `tools/redis_flush_guard.py` with `install() -> bool`, `is_installed() -> bool`, and a
  private `_db_of(client) -> int` that returns **0** whenever the db cannot be determined (fail
  closed, mirroring `tests/conftest.py:110-115`).
- Patch `flushdb` and `flushall` on `redis.Redis` and `redis.asyncio.Redis`. Block every `flushall`;
  block `flushdb` when `_db_of(self) == 0`. Delegate to the original callable otherwise, preserving
  `*args`/`**kwargs` and return value.
- Disarm **only** when `os.environ.get("REDIS_PRODUCTION_FLUSH_OK") == "1"`, read at call time (not
  import time) so a script cannot pre-set it after the guard loads and expect a stale decision.
- Mark patched callables with `_prod_flush_guarded = True`; make `install()` a no-op when already
  marked. Never raise on import; return falsy if `redis` is unimportable.
- Error messages must name the attempted db, `REDIS_PRODUCTION_FLUSH_OK=1`, the two incident dates,
  and the correct way to point a client at a test db. The `flushall` message must state that it wipes
  every db including production.
- Tests drive the **unbound** patched function with a `SimpleNamespace` stub client per D7. Do not
  construct a real `redis.Redis(db=0)` anywhere in this task.
- Cover: db 0 blocked, db 1-15 delegated, `flushall` blocked at any db, async variants, missing/
  malformed `connection_kwargs` → treated as db 0, the four falsy override values leaving the guard
  armed, `"1"` disarming both, idempotent double-`install()`, and `install()` with `redis` unimportable.

### 2. `.pth` installer, `/update` + worktree wiring, doctor checks
- **Task ID**: build-propagation
- **Depends On**: build-guard-core
- **Validates**: `tests/unit/test_redis_flush_guard_pth_installer.py` (create), existing doctor tests
- **Informed By**: spike-2 (`.pth` ordering, uv survival, 17 venvs), Risk 1, Race 1
- **Assigned To**: propagation-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `scripts/update/redis_flush_guard_pth.py`: given a repo root, discover `.venv`,
  `.worktrees/*/.venv`, and `.claude/worktrees/*/.venv`; for each, locate `lib/python*/site-packages`
  and write `_redis_flush_guard_boot.py` **then** `zzz_redis_flush_guard.pth`, both via write-temp +
  `os.replace()` (Race 1). Return a structured per-venv result.
- The shim's entire body is a `try: import tools.redis_flush_guard; …install()` / `except Exception:
  pass`. The `.pth` is the single line `import _redis_flush_guard_boot`. Document why the `zzz_`
  prefix is load-bearing (must sort after `_editable_impl_valor_bridge.pth`).
- Skip-with-reason (never crash) for: not a venv, no site-packages, read-only site-packages.
  Idempotent: identical content is a no-op that reports `unchanged`.
- Wire into `scripts/update/run.py` after Step 1.5 and before Step 3, following the non-fatal
  log/warn/continue shape of Step 3.13.
- Wire into `agent/worktree_manager.py`'s venv bootstrap so a new worktree is guarded before its first
  `python` call.
- Add `tools/doctor.py::_check_redis_flush_guard`: for **each** discovered venv, spawn
  `<venv>/bin/python -c` that imports `redis` and prints
  `getattr(redis.Redis.flushdb, "_prod_flush_guarded", False)`. FAIL naming every venv that reports
  False, with the installer command as remediation. Follow `_check_worktree_interpreters` for the
  iteration pattern and `CheckResult` shape; register in `get_checks()`.
- Installer tests use `tmp_path` fake venvs only. Never write into a real venv from a test.

### 3. Redis ACL provisioner + settings + secret placeholder
- **Task ID**: build-acl
- **Depends On**: build-propagation
- **Validates**: `tests/unit/test_redis_acl.py` (create)
- **Informed By**: spike-1 (no db discrimination; aclfile immutable; `NoPermissionError`), D3, D4, Risk 6, Race 3
- **Assigned To**: acl-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `scripts/update/redis_acl.py` modeled directly on `scripts/update/redis_persistence.py`:
  same result dataclass shape, same non-fatal contract, same "skipped" action when `redis-cli` is
  absent or Redis is down.
- Behavior: (a) ensure an `aclfile` directive exists in the active redis.conf, appending it if absent
  and reporting a warning that it takes effect on the next restart; (b) `ACL SETUSER valor-app on
  ><REDIS_APP_PASSWORD> ~* &* +@all -flushdb -flushall`; (c) `ACL SETUSER default … -flushall`,
  preserving every other existing `default` rule; (d) `ACL SAVE`, downgrading to a warning if no
  aclfile is loaded yet; (e) re-read `ACL GETUSER` for both users and report a mismatch (Race 3).
- **Never restart Redis** and never issue `SHUTDOWN`, `CONFIG REWRITE`, or `brew services restart`
  (Risk 6). Runtime `ACL SETUSER` is immediate; the aclfile is for persistence only.
- Issue each `ACL SETUSER` as a complete declarative rule set, never a delta, so concurrent runs
  converge (Race 3).
- Add a comment recording spike-1's selector finding verbatim in substance, so nobody "optimizes" the
  rule into a vacuous selector.
- Wire as Step 3.135 in `scripts/update/run.py`, immediately after Step 3.13 and before Step 3.14.
- Add `REDIS_APP_PASSWORD` to `RedisSettings` in `config/settings.py`, and a commented placeholder in
  `.env.example`. **Do not write to `.env` or the vault** — report the required value instead.
- Add `tools/doctor.py::_check_redis_acl`: reports whether `valor-app` exists with flush denied and
  whether `default` still permits `flushall`, with the `/update` remediation. Register in
  `get_checks()`.
- Tests fake `redis-cli` via a stub executable or by patching the subprocess call. Never touch the
  live server; never assert against production `ACL LIST` output in a test.

### 4. PreToolUse flush validator
- **Task ID**: build-hook-validator
- **Depends On**: none
- **Validates**: `tests/unit/test_validate_no_redis_flush.py` (create), `tests/unit/test_pre_tool_use_dispatcher.py`
- **Informed By**: spike-3 (`_POPOTO_CONTEXT` gate is wrong for flush), D5
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/hooks/validators/validate_no_redis_flush.py` exposing a pure
  `find_violation(command: str) -> str | None` that never raises for well-formed input and returns
  `None` for empty/None input. **No `_POPOTO_CONTEXT` gate** — flush is unconditionally dangerous.
- Match call shapes only: `\.flushdb\s*\(`, `\.flushall\s*\(`, and `redis-cli\s+.*\bFLUSHDB\b` /
  `\bFLUSHALL\b` (case-insensitive for the CLI forms). Do not match the bare word.
- The block reason names `REDIS_PRODUCTION_FLUSH_OK=1` as the deliberate escape, points at the
  per-process test-db idiom, and cites both incident dates.
- Add `_run_no_redis_flush(command, cwd)` to `.claude/hooks/dispatch/pre_tool_use_bash.py` (lazy
  import inside the function, matching `_run_no_raw_redis_delete` at `:116-119`) and append to
  `_VALIDATORS` at `:183` with `fail_closed=False`.
- Update the `manifest.toml` comment block at `:74-89` to say 9 in-process predicates and name this
  one. **Add no new `[[hook]]` stanza.** Re-confirm the `timeout = 20` budget still holds and say so
  in the PR body.
- Tests: each blocked shape blocks; `grep -rn flushdb tests/`, `rg flushall`, and prose containing the
  words do **not** block; empty and `None` input return `None`; the reason renders through
  `dispatch()` for a real `{"tool_name": "Bash", …}` payload.

### 5. Coexistence + seam validation
- **Task ID**: validate-coexistence
- **Depends On**: build-guard-core, build-propagation
- **Assigned To**: coexistence-validator
- **Agent Type**: validator
- **Parallel**: false
- Install the guard into this worktree's venv, then run `tests/unit/test_redis_flush_guard.py`
  **unmodified**. All 7 cases must pass. Capture the output verbatim for the PR body (D6).
- Confirm both sentinels are present and distinct under pytest: `_prod_flush_guarded` on the inner
  callable, `_db0_guarded` on the outer.
- Confirm a permitted flush still reaches Redis: within a pytest process, flush **this process's own
  claimed db** via the existing fixture path. Do not construct a client on any db this process does
  not own, and do not construct a db-0 client at all.
- Run the seam anti-criterion: the PR diff must contain no `tests/conftest.py`, `tests/db_claim.py`,
  or `docs/features/test-db-ownership.md`.
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

| Check | Command | Expected |
|-------|---------|----------|
| Guard blocks db 0 (stub-driven, D7) | `.venv/bin/python -c "import redis;from types import SimpleNamespace as N;import tools.redis_flush_guard as g;g.install();c=N(connection_pool=N(connection_kwargs={'db':0}));\ntry:\n redis.Redis.flushdb(c);print('NOT_BLOCKED')\nexcept RuntimeError:print('BLOCKED')"` | output contains BLOCKED |
| Guard blocks flushall (stub-driven) | `.venv/bin/python -c "import redis;from types import SimpleNamespace as N;import tools.redis_flush_guard as g;g.install();c=N(connection_pool=N(connection_kwargs={'db':7}));\ntry:\n redis.Redis.flushall(c);print('NOT_BLOCKED')\nexcept RuntimeError:print('BLOCKED')"` | output contains BLOCKED |
| Guard live at interpreter start | `.venv/bin/python -c "import redis;print(getattr(redis.Redis.flushdb,'_prod_flush_guarded',False))"` | output contains True |
| `.pth` + shim installed | `ls .venv/lib/python*/site-packages/zzz_redis_flush_guard.pth .venv/lib/python*/site-packages/_redis_flush_guard_boot.py` | exit code 0 |
| Doctor reports guard liveness | `python -m tools.doctor --json` | output contains redis_flush_guard |
| Doctor reports ACL state | `python -m tools.doctor --json` | output contains redis_acl |
| `valor-app` denied flush | `redis-cli ACL GETUSER valor-app` | output contains -flushdb |
| `default` denied flushall | `redis-cli ACL LIST` | output contains -flushall |
| Existing guard tests pass unmodified | `scripts/pytest-clean.sh tests/unit/test_redis_flush_guard.py -q` | exit code 0 |
| New unit tests pass | `scripts/pytest-clean.sh tests/unit/test_redis_flush_guard_prod.py tests/unit/test_validate_no_redis_flush.py tests/unit/test_redis_flush_guard_pth_installer.py tests/unit/test_redis_acl.py -q` | exit code 0 |
| Dispatcher contract intact | `scripts/pytest-clean.sh tests/unit/test_pre_tool_use_dispatcher.py -q` | exit code 0 |
| Validator blocks a flush | `python -c "import sys;sys.path.insert(0,'.claude/hooks/validators');import validate_no_redis_flush as v;print('BLOCK' if v.find_violation('python -c \"r.flushdb()\"') else 'PASS')"` | output contains BLOCK |
| Validator does not block grep | `python -c "import sys;sys.path.insert(0,'.claude/hooks/validators');import validate_no_redis_flush as v;print('BLOCK' if v.find_violation('grep -rn flushdb tests/') else 'PASS')"` | output contains PASS |
| CLAUDE.md names the foot-gun | `grep -c 'setdefault' CLAUDE.md` | output > 0 |
| Feature doc indexed | `grep -c 'redis-flush-hardening' docs/features/README.md` | output > 0 |
| Anti-criterion: seam with #2628 held | `git diff --name-only origin/main...HEAD \| grep -c -e 'tests/conftest.py' -e 'tests/db_claim.py' -e 'docs/features/test-db-ownership.md'` | match count == 0 |
| Anti-criterion: no sitecustomize install | `grep -rn 'sitecustomize' scripts/ tools/ agent/ \| grep -v '\.md:' \| grep -vc 'never\|not usable\|shadow'` | match count == 0 |
| Anti-criterion: no Redis restart in provisioner | `grep -c -e 'SHUTDOWN' -e 'CONFIG REWRITE' -e 'brew services restart' scripts/update/redis_acl.py` | match count == 0 |
| Anti-criterion: no real db-0 client in tests | `grep -rn 'Redis(db=0)\|/0")' tests/unit/test_redis_flush_guard_prod.py` | exit code 1 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Scope & Value | Layer 2 mutates the live production Redis with no human sign-off. `scripts/update/redis_acl.py` is wired as an unconditional `/update` Step 3.135 that issues `ACL SETUSER valor-app …`, `ACL SETUSER default … -flushall`, appends an `aclfile` directive to `/opt/homebrew/etc/redis.conf`, and `ACL SAVE`s — and `/update` is mandatory after every merge, so merging this PR *is* the apply. The plan requires the applied state: Success Criterion 5 and the two `redis-cli ACL …` Verification rows can only pass against an already-mutated production server, and `## Prerequisites` demands `test -w /opt/homebrew/etc/redis.conf`. Applying the ACL to the live server is not authorized in this work. | pending | Ship the full provisioner report-only. Use the repo's apply-gate precedent (`MEMORY_DECAY_PRUNE_APPLY`, `data/auto-revert-enabled`): `apply_redis_acl(apply: bool = False)` returns a structured plan (`planned_commands: list[str]`) and issues **no** `ACL SETUSER`, no `ACL SAVE`, no redis.conf write unless `apply=True`. `/update` Step 3.135 calls it with `apply=False` **always** and never inherits a global `params.apply`; the real apply is gated on an operator-created `data/redis-acl-enabled` marker **and** `REDIS_ACL_APPLY=true`. Rewrite Success Criterion 5 and the two `redis-cli ACL …` Verification rows to assert dry-run output (`python -m scripts.update.redis_acl --dry-run` prints the four commands), and relocate the live-server assertions into `docs/features/redis-flush-hardening.md` under an explicit "requires human sign-off — not performed by this PR" runbook heading. Drop `test -w /opt/homebrew/etc/redis.conf` from `## Prerequisites`. |
| BLOCKER | Risk & Robustness | Layer 3 blocks the plan's own Verification commands and has no working escape. The validator matches `\.flushdb\s*\(` in any Bash command string, and three Verification rows are Bash commands literally containing `redis.Redis.flushdb(c)`, `redis.Redis.flushall(c)`, and `v.find_violation('python -c "r.flushdb()"')`, so once Layer 3 is in `_VALIDATORS` the final-validator cannot run them. Worse, D5/Task 4/Risk 4 state the block message names `REDIS_PRODUCTION_FLUSH_OK=1` as "the deliberate escape", but `find_violation(command)` sees only the command string and no env-var branch is specified — prefixing the variable does not disarm the hook, so Layer 3 has no escape at all and Risk 4's mitigation is void. | pending | Two changes. (1) In `validate_no_redis_flush.find_violation`, add `if re.search(r"\bREDIS_PRODUCTION_FLUSH_OK=1\b", command): return None` **before** the `_BLOCK_PATTERNS` loop so the escape named in the block message is the one that actually works, and quote that exact prefix form in the message. (2) Rewrite the three rows so they contain no matching call shape: invoke via `getattr(redis.Redis, "flush" + "db")(c)` (string-split defeats the regex while calling the same function), or move both guard rows into `tests/unit/test_redis_flush_guard_prod.py` and make the Verification row `scripts/pytest-clean.sh tests/unit/test_redis_flush_guard_prod.py -q`. Add a regression test asserting `find_violation('REDIS_PRODUCTION_FLUSH_OK=1 python -c "r.flushdb()"') is None`. |
| CONCERN | Risk & Robustness | Harness-created agent worktrees are never wired to the installer. Task 2 wires the `.pth` installer into `agent/worktree_manager.py`'s venv bootstrap only, but `tools/venv_health.py:9` and `tools/doctor.py:246` both document `.claude/worktrees/{agent}/` checkouts as **harness-created** (not produced by `worktree_manager`), and spike-2 counted 3 such venvs. Those are exactly the checkouts where agents write ad-hoc debug scripts, so the load-bearing layer is absent in the highest-risk location from worktree creation until the next `/update`. | pending | `provision_worktree_venv` is not on the harness path, so a second wiring point is required. Make `tools/redis_flush_guard.install()` self-heal: when running in a venv whose `site-packages` lacks `zzz_redis_flush_guard.pth`, call `scripts.update.redis_flush_guard_pth.install_into(venv_path)` for that one venv, wrapped in `try/except Exception: pass`, skipped on read-only site-packages, and reusing the write-temp + `os.replace()` path from Race 1 so a partial write is impossible. Independently, make `_check_redis_flush_guard`'s FAIL text name the per-venv installer invocation rather than just `/update`. |
| CONCERN | Risk & Robustness | The `.pth` forces `import redis` into every interpreter start on the machine. `install()` must eagerly import `redis` and `redis.asyncio` to patch the classes, and the `.pth` runs in **every** Python process from a repo venv — including every PreToolUse hook invocation (fired on every Bash tool call, under the dispatcher's shared `timeout = 20` that already covers a 15s out-of-process subprocess), every `python -c`, every CLI, and every launchd start. `## Architectural Impact` asserts "import-cheap" with no measurement, and `redis.asyncio` transitively pulls in `asyncio` and `ssl`. | pending | Patch lazily: from the `.pth` shim install a `sys.meta_path` finder (or an `importlib` post-import callback) that patches `redis.Redis` / `redis.asyncio.Redis` on first actual import of either module, so a process that never touches Redis pays only the finder insertion. Keep `install()` as the eager entry point for unit tests and the doctor liveness probe (which imports `redis` anyway, so the sentinel assertion is unaffected). Add a Verification row measuring the delta with `python -X importtime -c pass` before/after against a named env-overridable provisional budget (e.g. `_STARTUP_BUDGET_MS = 15`) and state the measured number in the PR body. |
| CONCERN | History & Consistency | The seam anti-criterion omits a file the plan itself says #2628 owns. `## Test Impact` states `tests/unit/test_redis_flush_guard.py` "is owned by #2628 Task 4; this plan must not edit it", but the anti-criterion greps only `tests/conftest.py`, `tests/db_claim.py`, and `docs/features/test-db-ownership.md`. The file most likely to be edited under pressure (relaxing an assertion when D6 coexistence does not come out clean) is the one the seam does not mechanically protect, defeating the Freshness Check's "enforced by an anti-criterion … not by discipline" claim. | pending | Extend the row to `git diff --name-only origin/main...HEAD \| grep -c -e 'tests/conftest.py' -e 'tests/db_claim.py' -e 'tests/unit/test_redis_flush_guard.py' -e 'docs/features/test-db-ownership.md'` with expected output `0`. `grep -c` exits **1** when the count is zero, so this row and the two sibling `match count == 0` anti-criteria (`sitecustomize`, ACL provisioner) must compare the printed number to `0` and must not be evaluated on exit status — otherwise the passing state reads as a failure and a validator "fixes" it by inverting the check. |
| CONCERN | History & Consistency | D6's idempotence claim is false in exactly the nesting it describes. `tests/conftest.py::_install_redis_db0_flush_guard` wraps whatever `cls.flushdb` is at conftest-import time and sets `_guarded_flushdb._db0_guarded = True` **only** — it does not carry `_prod_flush_guarded` forward. After conftest installs, `getattr(redis.Redis.flushdb, "_prod_flush_guarded", False)` is `False`, so any later `install()` re-wraps, yielding `prod(db0(prod(orig)))`; the same stale sentinel makes an in-process liveness assertion read `False` under pytest even though the guard is armed. | pending | Keep the `_prod_flush_guarded` attribute as the liveness signal for the doctor's **subprocess** probe (a clean interpreter, so it stays accurate there per Risk 1), but back `install()`/`is_installed()` with a module-level `_INSTALLED: set[type]` keyed on the patched class and check that before patching. Add a regression test that installs Layer 1, applies a conftest-shaped wrapper on top setting only `_db0_guarded`, calls `install()` again, and asserts the delegation chain did not grow (count closure depth, or assert a call counter on the original increments exactly once). |
| CONCERN | Scope & Value | The build cannot start: `REDIS_APP_PASSWORD` is a hard prerequisite the builder is forbidden to satisfy. `## Prerequisites` checks it via `scripts/check_prerequisites.py`, while Task 3 says "**Do not write to `.env` or the vault** — report the required value instead" and CLAUDE.md § Secrets makes vault writes human-only. With the live apply moved behind human sign-off the password is not needed at build time at all, yet its absence fails the prerequisite gate before any task runs. | pending | Remove the `REDIS_APP_PASSWORD` row from `## Prerequisites` and move it into `docs/features/redis-flush-hardening.md`'s "before the operator runs the apply" checklist. Keep in this PR: `RedisSettings.app_password: str = Field(default="", description="…(env: REDIS_APP_PASSWORD)")` in `config/settings.py` — defaulted empty so `Settings()` never fails on a machine without it — plus the `.env.example` placeholder **with a comment line directly above the `KEY=`** (required by the completeness check). Have `redis_acl.py` return `action="skipped", error="REDIS_APP_PASSWORD unset"` rather than `success=False`, matching `RedisPersistenceResult`'s existing `skipped` action so `/update` logs quietly instead of appending to `result.warnings`. |
| NIT | Scope & Value | "Paste the run output into the PR body" is process, not a success criterion. Two Success Criteria items and Task 4's `timeout = 20` re-confirmation require pasting/attesting in the PR body; `final-validator`, whose role is "runs every `## Verification` row", has no command that can assert them. | pending | (NIT — exempt) Keep the pytest run as the criterion; move the paste/attest instructions into `## Team Orchestration` under the relevant builder's role. |
| NIT | History & Consistency | `## Prior Art` omits the two most recent precedents for the exact change Task 4 makes. #2448 added `validate_no_destructive_git_in_shared_checkout` and #2562 added `validate_no_broad_process_kill` to `_VALIDATORS` *after* the #2435 consolidation; both are the working template for "new validator, `_VALIDATORS` entry, no `manifest.toml` stanza, manifest comment count bump", and #2562 is the closest behavioural analogue (hard block on a destructive command family with a documented sanctioned alternative). | pending | (NIT — exempt) Add #2448 and #2562 to `## Prior Art` and point Task 4's "Informed By" at #2562's validator as the shape to copy. |

---

## Open Questions

1. **Layer 2 rotation sequencing.** This plan ships the `valor-app` ACL user and the provisioner but
   leaves the `REDIS_URL` rotation as an `[EXTERNAL]` No-Go, because the vault `.env` is iCloud-synced
   to every machine at once and a machine that has not yet run `/update` would hard-fail on
   `WRONGPASS`. Is that the sequencing you want, or would you rather the rotation land in this PR and
   accept a short window where a stale machine is down loudly? (Recommendation: keep it sequenced —
   Layers 1 and 3 already cover agent-driven clients, which is what the acceptance criterion asks for.)

2. **`default` keeps `flushdb`.** Denying it would force test-credential plumbing into
   `tests/conftest.py` and `tests/db_claim.py`, which #2628 owns and is about to rewrite. So a bare
   `redis-cli -n 0 flushdb` typed at a shell remains possible at the server layer until #2628 lands
   and a follow-up flips it. Layer 3 blocks that command when an *agent* types it. Acceptable, or do
   you want the ACL tightened now and the #2628 conflict absorbed?

3. **Appetite.** I sized this Large because it spans four subsystems plus fleet propagation. If you
   want it smaller, the clean cut is to ship Layers 1, 3, and 4 now (the incident is fully prevented
   for every Python and agent-driven path) and split Layer 2 — ACL provisioner, secret, doctor check,
   `/update` step — into its own issue. Say the word and I will re-cut it that way.
