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

**Launchd caveat, load-bearing for every slice that touches a launchd-managed
package** (that is s3-s7 *and* s8, which scopes `reflections/` — do not read
the range as excluding it). Per `docs/features/config-timeout-catalog.md:192-204`
step 4: launchd-managed processes run with `VALOR_LAUNCHD=1` and therefore
**skip the `.env` read entirely**, so a key that exists only in `.env` is
invisible to them. A promotion that stops at "added a settings field + a
`.env.example` line" is incomplete and will silently run on defaults in
production.

**But the remedy is per-service, and for most services it is already automatic.**
There is no single "env-injection path" to edit. The four launchd-managed
processes use three different mechanisms:

| Process | Install path | Injection |
|---|---|---|
| `worker/__main__.py` | `install_worker()` in `scripts/update/service.py` | Automatic — `_inject_env_into_plist()` merges *every* `dotenv_values()` key into the plist. No per-key edit. |
| `bridge/email_bridge.py` | `scripts/install_email_bridge.sh` | Automatic — same generic merge. No per-key edit. |
| `reflections/__main__.py` | `com.valor.reflection-worker.plist` | Automatic — the plist runs `/bin/bash -c 'source .env; exec ... -m reflections'`. No per-key edit, and nothing in `service.py` is involved. |
| `monitoring/bridge_watchdog.py` | `install_bridge_watchdog()` in **`scripts/valor-service.sh`** | **None.** Its `EnvironmentVariables` dict carries only `PATH`, `HOME`, and `VALOR_LAUNCHD`. |

So the caveat binds hard on exactly one service. A slice promoting a key that
`monitoring/bridge_watchdog.py` reads must extend `install_bridge_watchdog` in
`scripts/valor-service.sh` — not `scripts/update/service.py`, which has no
injection point for it. For the other three, the correct action is to confirm
the key reaches `.env` and then do nothing further.

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
| **s7** | `monitoring/` + `worker/` (`session_watchdog.py` 5, `worker_watchdog.py` 4, `bridge_watchdog.py` 2, `worker/__main__.py` remaining 8) | s3 | Launchd caveat applies hardest here, and `monitoring/bridge_watchdog.py` is **the single highest-risk promotion in the migration**: it is the one launchd service with no `.env` injection whatsoever, so a promotion that stops at `.env` leaves it silently on defaults. It already imports `config.settings` at module scope (line 52) yet reads `CRASH_STORM_THRESHOLD` and `WEDGE_DOMINANCE_FRACTION` from `os.environ` at lines 144/150 — it fails Axis A's pre-config test outright, so PROMOTE is the verdict. |
| **s8** | Tail: `scripts/` 10, `reflections/` 5, `models/` 3, `ui/` 3, `config/` 1, `utils/` 1 | s2 | Independent of s5/s6. `scripts/` are single-shot entry points with different lifecycle constraints — expect a high DELETE rate. |
| **s9** | Comment sweep + `.env.example` reconciliation | s1-s8 | Drives `git grep -n -iE "provisional\|GRAIN OF SALT"` to zero in production code and closes the "every surviving override is declared or documented" criterion. |

s5, s6, and s8 are **mutually independent** once s2 has set the recipe and can
run as parallel lanes. Everything else is a chain.

## Success Criteria

- [ ] `python scripts/scan_module_scope_env.py` reports 3 modules / 2 call
      sites, all allowlisted (the two counted `VALOR_LAUNCHD` gates; the
      `config/settings.py` class-body gate is marked but uncounted).
      **This proves the *syntactic* class is drained, and nothing more — see
      Limitation 1.** Do not restate it as "the defect class is eliminated."
- [ ] `git grep -n -iE "provisional|GRAIN OF SALT"` in production code returns
      no hits — every site reclassified promote / keep-with-justification /
      delete. **Note the `-i`:** see Limitation 2.
- [ ] Every surviving env override is either declared in `config/settings.py`
      or documented in `.env.example` with a clear owner.
- [ ] Every promoted key read by a launchd-managed service reaches that
      service by its own mechanism (see the four-row table under the launchd
      caveat) — in practice: present in `.env` for worker / email-bridge /
      reflection-worker, and explicitly added to `install_bridge_watchdog` in
      `scripts/valor-service.sh` for `monitoring/bridge_watchdog.py`.

The two criteria above are the only ones with no script behind them. **s9 owns
making them checkable** — the same slice that settles the `.env.example`
counting rule should land a check that walks surviving `# env-scope-guard: allow`
markers and promoted `Settings` fields and cross-references `.env.example` and
the watchdog plist. An eyeball-only criterion will not survive nine slices.
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

- **Plist env injection.** Launchd processes run with `VALOR_LAUNCHD=1` and
  skip the `.env` read, so a key that lives only in `.env` is invisible to
  them. This is `docs/features/config-timeout-catalog.md:192-204` step 4 and
  the most likely way a slice ships a silently-broken promotion. **The remedy
  is per-service — see the four-row mechanism table under the launchd caveat
  above.** Three of the four services inject `.env` generically and need no
  per-key edit; only `monitoring/bridge_watchdog.py` has no injection at all,
  and its install path is `install_bridge_watchdog` in
  `scripts/valor-service.sh`, not `scripts/update/service.py`.
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
- [x] s0: `docs/features/module-scope-env-guard.md` — the instrument's own page,
      covering the census script, the diff-scoped guard, the allowlist marker,
      and the syntactic-census limitation. Indexed in `docs/features/README.md`.
      This supersedes the original "no new feature doc" line: the guard fires on
      every `git commit` on the machine, so its behaviour needed a discoverable
      page from the moment it landed, not at the end of the migration.
- [ ] s9: fold the settled status quo into
      `docs/features/module-scope-env-guard.md` — the Axis A/Axis B triage rule
      and the three permanent exemptions, once the migration is drained. Do not
      create a second page; extend the s0 one.
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

## Critique Results

Critique ran after s0 had already been built and approved (PR #2940), so it is
retrospective for s0 and prospective for s1-s9 — which is where the remaining
risk lives. **No finding blocks s0**, whose deliverable is the instrument only.
All five findings are about plan text governing later slices; all are addressed
in this revision.

| Severity | Finding | Addressed By |
|---|---|---|
| CONCERN | The launchd caveat misdescribed the mechanism as one "env-injection path in `scripts/update/service.py`". In fact worker, email-bridge and reflection-worker all inject `.env` generically and need no per-key edit, while `monitoring/bridge_watchdog.py` — the one service with no injection at all — is installed from `scripts/valor-service.sh`, where no such path exists. Following the plan literally meant hunting for an injection point that isn't there. | Replaced with a four-row per-service mechanism table; the Update System bullet now points at it instead of restating the wrong remedy. |
| CONCERN | `monitoring/bridge_watchdog.py` was never named anywhere in the plan despite being a real in-scope defect: module-scope `CRASH_STORM_THRESHOLD` / `WEDGE_DOMINANCE_FRACTION` at lines 144/150, *after* `from config.settings import settings` at line 52, so it fails Axis A's pre-config test outright. It is also the one launchd service with no `.env` injection, making it the likeliest place to ship a silently-broken promotion. | Added to s7's scope with an explicit PROMOTE verdict and a highest-risk flag. |
| CONCERN | Success Criteria 3 and 4 had no automated enforcement, unlike 1, 2 and 5 — eyeball-only checkboxes across a nine-slice migration. | s9 now explicitly owns making both checkable, alongside the `.env.example` counting rule it already settles. |
| NIT | The caveat's "load-bearing for slices 3-7" range excluded s8, which scopes `reflections/` — also launchd-managed. | Rebound to "every slice that touches a launchd-managed package", naming s8. |
| NIT | Success Criterion 1 ("3 modules / 2 call sites") is the practical restatement of "72 → 0" and did not cross-reference Limitation 1, so a skimmer could present it as proof the defect class was eliminated. | Cross-reference added inline to the criterion. |

Claims checked and found accurate, recorded so they are not re-litigated: the
s1→s2 `SessionRunnerSettings` collision is real (`config/settings.py:827`
defines it; `agent/session_runner/runner.py` has 12 module-scope reads under a
flat `SESSION_RUNNER_*` spelling that collides with the class's nested
`SESSION_RUNNER__*` prefix); every `config-timeout-catalog.md` line citation
resolves; and no module-scope `VALOR_LAUNCHD` read exists outside the three
allowlisted sites. The s5/s6/s8 mutual-independence claim is unrefuted on
file-scope inspection but was not proven by an exhaustive import-graph check.

Refs #2866
