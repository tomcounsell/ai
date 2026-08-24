---
status: Ready
type: chore
appetite: Large
owner: valor
created: 2026-08-24
tracking: https://github.com/tomcounsell/ai/issues/2866
---

# Module-scope env reads: drain 190 import-time reads and ratify the knobs they hide

## Problem

An AST pass over git-tracked `*.py` at `22cb19025` finds **72 non-test modules
performing 190 environment reads at module top level** — calls to
`os.environ.get` / `os.getenv` / `os.environ.setdefault` / `os.environ.pop`
that execute the moment the module is first imported. (Including test modules:
79 / 202. By function: `os.environ.get` 155, `os.getenv` 35, `setdefault` 0,
`pop` 0 — the migration is entirely `get`/`getenv`, though the guard covers all
four so the unused shapes cannot appear.)

Two defects live on the same 190 lines, and they are independent:

**Axis A — when the read executes.** A value frozen into a module global at
import time is a property of *when the process started*, not of *who is
configuring it*. It cannot be re-configured without a process restart, it
cannot vary per instance, and it makes the module untestable without
`importlib.reload`. The fix is the #1968 precedent: a `config/settings.py`
field read lazily at call time.

**Axis B — whether the knob is settled.** Issue #2881 (closed, folded into
#2866) counted comment markers reading `provisional` / `GRAIN OF SALT` —
knobs nobody has ever ratified. **51 of the 72 module-scope-env modules (71%)
also carry a provisional marker**, including every named worst offender. Two
lanes would have made two passes over the same lines and risked conflicting
verdicts on the same `os.environ.get(...)`.

A knob can be wrong on either axis independently: a ratified key can still be
read at import time, and a lazily-read key can still be an unratified one-off.
Every slice below must return a verdict on both.

Key-level shape of the 190: **173 distinct keys**, only **12** read at module
scope in more than one file, only 9 with no default, and roughly **144-146 of
173 absent from `.env.example`** entirely. That last number is the harder half
of the merged acceptance criteria — see "Open measurement" below.

Worst single-file offenders:

| Calls | File |
|-------|------|
| 12 | `agent/session_health.py` |
| 12 | `agent/session_runner/runner.py` |
| 11 | `tools/video_watch/constants.py` |
| 9 | `worker/__main__.py` |
| 6 | `agent/constants.py` |
| 6 | `bridge/telegram_bridge.py` |
| 5 | `agent/agent_session_queue.py` |
| 5 | `bridge/email_bridge.py` |
| 5 | `monitoring/session_watchdog.py` |

## Triage criterion

### Axis A — may this read stay at import time?

A module-scope env read may stay where it is **only if all three hold**:

1. **Pre-config.** It runs before `config.settings` is importable, or it
   determines whether/how config loads at all. A read that could have asked
   `settings` fails this test.
2. **Launcher-owned.** It is set by launchd / systemd / a shell wrapper, not by
   a human tuning `.env`. If a human would ever edit it to change behavior, it
   fails this test.
3. **Cannot vary per-instance by construction.** Two components in the same
   process could never legitimately want different values.

Applied to all 190 sites, this exempts **exactly three**, all the same
`VALOR_LAUNCHD` dotenv-bootstrap gate:

| Site | Why exempt |
|------|-----------|
| `worker/__main__.py` | Decides whether `load_dotenv()` runs at all; launchd sets it; process-wide by construction. |
| `bridge/telegram_bridge.py` | Same gate, same reasoning. |
| `config/settings.py` (`model_config.env_file`) | Class-body equivalent — it is literally the branch that decides whether pydantic-settings reads `.env`. Not counted in the 190 (the census does not descend into class bodies), but marked so a later refactor cannot lose the verdict. |

Everything else is a defect. Marked in code with `# env-scope-guard: allow`
plus a one-line written justification.

### Axis B — is the knob settled?

Extends the existing #1968 promote-vs-name-locally rule at
`docs/features/config-timeout-catalog.md:142-152` from timing literals to env
keys. For each of the 173 keys:

- **PROMOTE** to a `config/settings.py` field if it is read in **≥2 modules**
  (12 keys qualify today), **plausibly machine-tuned**, a **session-lifecycle
  TTL**, or a **credential**. Promoted keys get a `.env.example` declaration
  with a comment block, per the Secrets convention.
- **KEEP with justification** if it is genuinely per-deployment but read at a
  single site. It still moves to call time (Axis A is independent); it just
  stays a raw-env read rather than becoming a settings field. The
  `provisional` / `GRAIN OF SALT` comment is replaced by a sentence naming the
  owner and the reason.
- **DELETE the knob** if the override is set **nowhere** — no `.env.example`
  entry, no plist injection, no test — *and* the value is a logic-coupled
  one-off. Inline the constant and drop the env read entirely.

Given that ~144 of 173 keys are absent from `.env.example`, **DELETE is
expected to be the majority verdict.** A slice that promotes 20 keys into
`settings` has probably mis-triaged; the goal is eliminating undiscoverable
knobs, not maximizing the size of `Settings`.

**Launchd caveat, load-bearing for slices 3-7.** Per
`docs/features/config-timeout-catalog.md:192-204` step 4: if a
launchd-managed service reads a key at runtime, the key must be added to the
plist env-injection path in `scripts/update/`. Launchd-managed processes run
with `VALOR_LAUNCHD=1` and therefore **skip the `.env` read entirely** — a key
that exists only in `.env` is invisible to them. `worker/`, `bridge/`, and
`monitoring/` are all launchd-managed. A promotion in those packages that
stops at "added a settings field + a `.env.example` line" is incomplete and
will silently run on defaults in production.

## Slices

Ten slices. Each is independently mergeable and each must land a census
delta (`python scripts/scan_module_scope_env.py`) in its PR body.

| # | Scope | Depends on | Notes |
|---|-------|-----------|-------|
| **s0** | Regression guard + reproducible census + 3 bootstrap allowlists | — | This PR. Changes no env read; establishes the instrument. |
| **s1** | #2874 (`SessionRunnerSettings` / `stale_granite_env_keys` deletion) | s0 | Sequenced first because it *touches* `SessionRunnerSettings`, which s2 rewrites — not, as an earlier comment claimed, because it removes a counted offender (it does not; see Limitation 1). |
| **s2** | `agent/session_runner/` (runner.py 12, harness/claude.py 4) | s1 | **Highest signal, sets the recipe.** `SessionRunnerSettings` already exists at `config/settings.py:827`, so this is populate-an-existing-class, not invent-a-class. Also resolves a live flat-vs-nested naming inconsistency. |
| **s3** | `agent/` health + constants (`session_health.py` 12, `constants.py` 6, `agent_session_queue.py` 5, `tool_budget.py` 3, `redis_offload.py` 4) | s2 | The `TOOL_TIMEOUT_*` promotion is already pre-sanctioned by `config-timeout-catalog.md:123-125`. Launchd caveat applies. |
| **s4** | Telegram credentials + bootstrap (`TELEGRAM_API_ID`/`API_HASH` ×4 modules, `TELEGRAM_PHONE`, `TELEGRAM_PASSWORD`, `TELEGRAM_SESSION_NAME`) | s2 | Credentials — automatic PROMOTE under Axis B. The highest-fan-out keys in the whole census. |
| **s5** | `bridge/` remainder (`email_bridge.py`, `redundancy_filter.py`, `message_drafter.py`, `telegram_relay.py`, `injection_inspection.py`) | s2 | Independent of s6/s8. Launchd caveat applies. |
| **s6** | `tools/` (14 modules / 30 calls, incl. `video_watch/constants.py` 11) | s2 | Mostly DELETE-the-knob: single-site, undeclared, logic-coupled. Independent of s5/s8. |
| **s7** | `monitoring/` + `worker/` (`session_watchdog.py` 5, `worker_watchdog.py` 4, `worker/__main__.py` remaining 8) | s3 | Launchd caveat applies hardest here. |
| **s8** | Tail: `scripts/` 10, `reflections/` 5, `models/` 3, `ui/` 3, `config/` 1, `utils/` 1 | s2 | Independent of s5/s6. `scripts/` are single-shot entry points with different lifecycle constraints — expect a high DELETE rate. |
| **s9** | Comment sweep + `.env.example` reconciliation | s1-s8 | Drives `git grep -n -iE "provisional\|GRAIN OF SALT"` to zero in production code and closes the "every surviving override is declared or documented" criterion. |

s5, s6, and s8 are **mutually independent** once s2 has set the recipe and can
run as parallel lanes. Everything else is a chain.

## Success Criteria

- [ ] `python scripts/scan_module_scope_env.py` reports 3 modules / 2 call
      sites, all allowlisted (the two counted `VALOR_LAUNCHD` gates; the
      `config/settings.py` class-body gate is marked but uncounted).
- [ ] `git grep -n -iE "provisional|GRAIN OF SALT"` in production code returns
      no hits — every site reclassified promote / keep-with-justification /
      delete. **Note the `-i`:** see Limitation 2.
- [ ] Every surviving env override is either declared in `config/settings.py`
      or documented in `.env.example` with a clear owner.
- [ ] Every promoted key read by a launchd-managed service is injected in
      `scripts/update/`.
- [ ] `.claude/hooks/validators/validate_no_module_scope_env.py` blocks new
      module-scope reads at commit time (landed in s0).

## Methodology limitations

These are stated up front so no later slice presents a number it did not earn.

**1. The census is syntactic and blind to indirect import-time reads.** It
counts *calls written at module scope*. It cannot see an import-time env read
made through a function call. `config/settings.py:1184` calls
`stale_granite_env_keys()` at module scope; that function reads `os.environ`
internally, so the read genuinely happens at import — and the scan never sees
it. It is a real instance of the exact defect this plan describes.

Consequence: **"72 → 0" must not be presented as proof the class is
eliminated.** It proves the *syntactic* class is drained. An earlier comment on
#2866 claimed #2874 "removes one offender from this issue's count for free" by
deleting `stale_granite_env_keys()`; that claim was retracted by its own
author, because line 1184 was never in the 72 to begin with. s1 is sequenced
first for the `SessionRunnerSettings` collision, full stop.

**2. The folded #2881 acceptance criterion needs `-i`.** Case-sensitive
`git grep -E "provisional|GRAIN OF SALT"` finds 54 files. Case-**in**sensitive
finds **82 files / 255 lines**, because the dominant comment spelling is
capitalized at line start (`# Provisional/tunable — override with ...`). As
originally written, the criterion would pass with 28 `Provisional` files still
standing. Every slice and the final check must use `-i`.

## Open measurement

The "absent from `.env.example`" figure is 144 in the #2866 recon comment and
146 under a naive `KEY=` parse of `.env.example`. The gap is commented-out and
grouped declarations. **s9 must settle the counting rule** (and encode it in
whatever check enforces the third acceptance criterion) rather than quoting
either number as settled.

## Update System

`scripts/update/` is a first-class consumer of this plan, not an afterthought.

- **Plist env injection.** Every key promoted in s3-s7 that a launchd-managed
  service reads must be added to the plist env-injection path in
  `scripts/update/service.py` (`install_worker` and siblings). Launchd
  processes run with `VALOR_LAUNCHD=1` and skip the `.env` read; a key that
  lives only in `.env` is invisible to them. This is
  `docs/features/config-timeout-catalog.md:192-204` step 4, and it is the most
  likely way a slice ships a silently-broken promotion.
- **`.env.example` completeness.** Promoted keys need a `.env.example`
  declaration with a comment block above the `KEY=` line, per the Secrets
  convention. `check_env_completeness` (`scripts/update/verify.py`) treats
  every declaration as required unless marked `# @optional`, and
  `tests/unit/test_env_declaration_readers.py` requires every declaration to
  have a reader. A DELETE verdict on a declared key must remove the
  declaration in the same commit or the reader test fails.
- **`scripts/update/service.py` itself has 3 module-scope reads** and is
  migrated in s8; sequence that after s7 so the injection path is stable while
  s3-s7 are adding to it.
- No `/update` step ordering changes are required by this plan. Services are
  restarted by the existing flow; no slice introduces a new service.

## Agent Integration

- **Regression guard (s0).** Registered by appending a predicate tuple to
  `.claude/hooks/dispatch/pre_tool_use_bash.py`'s validator list — the
  in-process PreToolUse/Bash dispatcher #2435 consolidated the validators
  into. **No `manifest.toml` `[[hook]]` entry is added**, and the generated
  `hooks` block in `settings.json` is never hand-edited. The manifest's
  `timeout = 20` budget is re-confirmed in a comment (the #2645 precedent) but
  not changed.
- **Guard behavior in agent sessions.** The guard is scoped to lines the staged
  commit adds or rewrites, not to whole files. This is load-bearing: slices
  1-9 must edit exactly the 72 modules that contain the 188 unmigrated sites,
  and a whole-file guard would block every one of its own migration commits.
  The census script, not the guard, tracks the backlog.
- **Census as an SDLC instrument.** Each slice's PR body carries a
  `python scripts/scan_module_scope_env.py` delta, so the reviewing agent can
  verify the claimed progress rather than trusting the description.
- No skill, prompt, or persona file changes. No MCP tool changes.

## Test Impact

No existing tests are affected by s0 — it adds a guard and a census script and
changes no runtime behavior. Later slices touch running code and carry real
test impact:

- [ ] `tests/unit/test_validate_no_module_scope_env.py` — NEW in s0. Covers the
      AST module-scope vs. `def`/`class`-body distinction, the allowlist
      marker, the changed-line scoping, both entry points, and a monotonic
      ratchet on the repo census.
- [ ] `tests/unit/test_env_declaration_readers.py` — UPDATE in s9 (and
      opportunistically in any slice that DELETEs a declared key): the
      declaration-has-a-reader invariant breaks the moment a reader is deleted
      without its `.env.example` line.
- [ ] Any test that sets an env var and then imports a module to observe the
      effect — UPDATE per slice. This shape only works *because* the read is at
      import time; once the read is lazy, the test must set the value and call
      the function instead. s2 and s3 will surface most of these
      (`agent/session_runner/`, `agent/session_health.py`).
- [ ] `tests/unit/` timeout/settings fixtures asserting `TimeoutSettings`
      shape — UPDATE in s3 when `TOOL_TIMEOUT_*` is promoted.

Each slice audits its own package's tests before implementation and records the
disposition in its PR body; this plan does not pre-enumerate them, because the
set depends on the promote/keep/delete verdicts that slice reaches.

## Documentation

### Feature Documentation
- [ ] s0: no new feature doc. The guard and census are documented in their own
      module docstrings and referenced from this plan; a standalone
      `docs/features/` page would duplicate the plan while the migration is in
      flight.
- [ ] s9: create `docs/features/module-scope-env-reads.md` describing the
      settled status quo — the Axis A/Axis B triage rule, the guard, the census
      script, the allowlist marker, and the three permanent exemptions. Add it
      to the `docs/features/README.md` index table.
- [ ] s3-s7: update `docs/features/config-timeout-catalog.md` as knobs are
      promoted — its field catalog is the canonical list, and its
      promote-vs-name-locally section is what Axis B extends.

### External Documentation Site
Not applicable — this repo has no Sphinx/MkDocs/Read-the-Docs site. `site/` is
a public explainer, unaffected by internal config plumbing.

### Inline Documentation
- [ ] Every `# env-scope-guard: allow` marker carries a one-line written
      justification naming which of the three Axis A tests it clears.
- [ ] Every KEEP-with-justification verdict replaces its
      `provisional` / `GRAIN OF SALT` comment with a sentence naming the owner
      and the reason (this is what drives the s9 grep to zero).
- [ ] Promoted `config/settings.py` fields get a description naming their call
      sites, matching the existing `TimeoutSettings` commenting style.

### CLAUDE.md
- [ ] s9: add a one-line note under "Configuration Files" pointing at the new
      feature doc, once the rule is settled. Not before — CLAUDE.md describes
      the status quo, and mid-migration the status quo is "in flight".

## No-Gos (Out of Scope)

- **No value re-tuning.** Moving a read from import time to call time must
  preserve the effective default. Changing a default is a separate change with
  its own justification, exactly as #1968 scoped it.
- **No parallel-run migration.** A slice cuts its modules over completely; it
  does not leave the old module-global alongside the new settings field.
- **No `settings` bloat.** DELETE is the expected majority verdict. Promote
  only what clears the Axis B bar.
- **No new `manifest.toml` hook entries.** #2435 consolidated PreToolUse Bash
  validators into `.claude/hooks/dispatch/pre_tool_use_bash.py`; guards
  register by appending to its predicate list.

## References

- Issue #2866 (this plan's tracking issue); #2881 (closed, folded in).
- #1968 — the precedent: settings object → lazy read → regression guard.
- `docs/features/config-timeout-catalog.md` — promote-vs-name-locally rule
  (:142-152) and the "adding a new knob" launchd step (:192-204).
- `.claude/hooks/validators/validate_no_inline_timeout.py` — the guard shape
  s0 is modelled on.
- #2435 — the PreToolUse dispatcher that replaced per-validator registration.
