---
status: planned
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-16
tracking: https://github.com/tomcounsell/ai/issues/2066
last_comment_id:
---

# Merge-Gate Baseline: Refresh Resilience (post-#2064)

## Problem

The merge gate (`/do-merge` → `scripts/baseline_gate.py`) classifies each PR-branch test
failure as **pre-existing** (also fails on `main` — non-blocking) or a **regression** (new to
the PR — blocking) by diffing against `data/main_test_baseline.json`, a machine-local, gitignored
snapshot of `main`'s failures. When that snapshot is stale, the gate emits false "new regression"
flags and forces an expensive per-gate live-`main` re-classification pass.

Issue #2066 reported the baseline was **253 commits / 5 days stale** at PR #2057's gate, that an
in-worktree `refresh_test_baseline.py --runs 3` attempt was **killed at the 10-minute bash
timeout**, and that #1965's "refresh cadence" had not kept the baseline fresh.

## Freshness Check (re-verified against current `main`, this is the required first step)

**Baseline commit at plan time:** `bc1a311b4` (`git rev-parse --short HEAD`).

**What #2064 (PR #2107, already on `main`) resolved:** #2064 made the full-suite advisory lock
**machine-global** — a `/tmp` path keyed to the repo's git common dir, shared across every
worktree — and pointed `refresh_test_baseline.py` at that same lock via
`suite_lock.default_lock_dir()`. Confirmed live in `scripts/refresh_test_baseline.py`:
`SUITE_LOCK_DIR = suite_lock.default_lock_dir()`, acquired per-run with a 1800s wait. So the
**concurrency / cross-worktree contention** axis of "refresh attempts die to contention" is
**already fixed on `main`** — a refresh now serializes against other lanes instead of
oversubscribing CPU. No further lock work is warranted.

**What #1965 actually shipped:** NOT an automatic refresh. It shipped a **warn-only weekly
detector** (`reflections/housekeeping/test_baseline_refresh_check.py`, registered in vault
`reflections.yaml` as `test-baseline-refresh` — `every: 7d`, `priority: low`, `enabled: true`).
Its own docstring states "the operator runs `python scripts/refresh_test_baseline.py` manually
once this fires." A **scheduled full-suite auto-regen was explicitly rejected** in #1933
(Redis-collision / memory-thrash hazard). So there is, by design, no automatic refresh — only a
quiet weekly warning plus a manual operator step.

**What is still genuinely broken (distinct from #2064):**
1. **The baseline is stale right now.** At plan time `data/main_test_baseline.json` is commit
   `37d4cc74` (2026-07-08), **337 commits behind HEAD** — worse than the 253 at filing. #2064 did
   nothing to refresh it. It needs an actual refresh.
2. **The manual refresh cannot complete via a foreground bash tool.** `config/personas/engineer.md`
   line 187 documents the refresh as "~30 min wall time on a quiesced machine," but the harness Bash
   tool caps at 10 minutes (`600000` ms max). A foreground `python scripts/refresh_test_baseline.py`
   is therefore **always killed before it finishes** — the exact "killed at the 10-minute bash
   timeout" failure in #2066. The script's own internal caps (30-min lock wait, 2h global timeout)
   never get a chance to help because the *harness* kills the parent. There is no detached /
   background-safe way to launch it.
3. **The staleness alert is not actionable enough to prevent silent drift.** The weekly low-priority
   warning let the baseline drift to 337 commits, and when an operator did try to act, the refresh
   died (see #2). The alert text points at a command that cannot succeed in the harness.

**Disposition:** #2064 substantially resolved the *contention* root cause. The remaining work is
(a) operational — actually refresh the baseline now — and (b) a small, decision-consistent
hardening so the refresh can be launched in a timeout-safe way and the alert points operators at
that safe path. No auto-regen (barred by #1933); no lock changes (done by #2064).

## Prior Art

- **PR #2107 (#2064)** — machine-global suite lock + refresh pointed at it. The lock/contention fix
  this plan builds on; not re-touched.
- **#1965 / `merge-gate-baseline-stale-refresh.md`** — shipped the warn-only weekly staleness
  detector and hardened `refresh_test_baseline.py` (nameless-`<testcase>` resilience, loud <2-run
  failure). This plan reuses that detector, only making its guidance actionable.
- **#1933** — rejected scheduled full-suite auto-regen (Redis-collision / memory-thrash). This plan
  honors that no-go: no scheduled pytest.

## Research

Purely internal (an operator-run refresh script, a read-only reflection, a machine-local gitignored
artifact, and persona/doc guidance). No external findings.

## Data Flow

1. `/do-merge` → `scripts/baseline_gate.py` reads `data/main_test_baseline.json`;
   `format_staleness_warning` warns (advisory) when stale.
2. Weekly reflection `test_baseline_refresh_check.run()` evaluates the same shared
   `staleness()` definition and records a warning finding when stale.
3. Both warnings tell the operator to run `refresh_test_baseline.py` — which cannot complete in a
   foreground harness bash call.
4. This plan inserts a **detached launch wrapper** so the refresh runs to completion out-of-band,
   and repoints the two warnings + persona/doc guidance at it.

## Architectural Impact

- **New file:** `scripts/refresh_baseline_detached.sh` — a thin `nohup` wrapper that launches
  `refresh_test_baseline.py` detached, writes a timestamped log + pidfile under `logs/`, prints the
  PID + log path + a poll hint, and returns immediately (well under the 10-min cap). Forwards all
  extra args to the python script. Two resilience properties (from critique blockers):
  - **Exit-code preservation:** the child's exit code is captured across detachment and appended to
    the log as an `EXIT=<code> at <ts>` line
    (`nohup bash -c 'python scripts/refresh_test_baseline.py "$@"; echo "EXIT=$? ..." >> "$LOG"' _ "$@" &`).
    The printed poll hint instructs grepping for `EXIT=`: `EXIT=0` = fresh baseline written;
    `EXIT=1` = failed (stale baseline unchanged) OR degraded (`<2` usable runs, `degraded=true`
    stamped) — the operator must inspect the log/artifact to distinguish. This closes the
    "silent failed/degraded 30-min run" gap.
  - **Concurrency guard:** before launching, if the pidfile exists and `kill -0 <pid>` succeeds, the
    wrapper prints "refresh already running (PID …)" and `exit 0` without spawning a second run — so
    two launches can't clobber each other's pidfile or have a degraded second run overwrite a clean
    first result.
- **Changed:** the staleness warning strings in `reflections/housekeeping/test_baseline_refresh_check.py`
  and `scripts/baseline_gate.py::format_staleness_warning` reference the detached wrapper as the exact
  remediation command.
- **Changed:** `config/personas/engineer.md` "Stale-Baseline Bypass" section points at the detached
  wrapper (the foreground command it currently names cannot finish in-harness).
- **New dependencies:** none. **Interface changes:** none to the gate verdict schema. **Data
  ownership:** unchanged — `data/main_test_baseline.json` stays gitignored / machine-local.
- **Reversibility:** high — one additive shell script plus string/doc edits.

## Appetite

**Size:** Small. One shell wrapper, three text/doc edits, one integration test, one operational
refresh run.

## No-Gos

- No committing of `data/main_test_baseline.json` — it is gitignored and machine-local; the refresh
  is an operational data change on this machine, not part of the PR.
- No scheduled full-suite auto-regen — barred by #1933 (Redis-collision / memory-thrash).
- No changes to the suite lock — #2064 already made it machine-global and pointed the refresh at it.
- No changes to the gate verdict schema or classification logic.
- No push of code to `main` — code lands on `session/{slug}` via PR; this plan doc commits on `main`.

## Update System

- The new `scripts/refresh_baseline_detached.sh` propagates to every machine via git (it lives in the
  repo). No `scripts/update/run.py`, `scripts/remote-update.sh`, or `scripts/update/hardlinks.py`
  change required — it is not a synced skill and has no install step.
- Optional, per-machine (NOT required by this PR): the vault `reflections.yaml` `test-baseline-refresh`
  entry could be tightened from `every: 7d` to a shorter cadence; note this in the doc as an operator
  knob. No repo change — the cadence lives in the gitignored, iCloud-synced vault file.

## Agent Integration

- Agents/operators launch the refresh through the Bash tool. Today they are told to run
  `python scripts/refresh_test_baseline.py` (foreground → killed at 10 min). After this change they
  run `scripts/refresh_baseline_detached.sh` (returns immediately with a PID + log path to poll), so
  the agent-facing surface is the persona/doc guidance being repointed — no new
  `pyproject.toml [project.scripts]` entry and no bridge import. The wrapper is a shell script invoked
  by path, consistent with `scripts/pytest-clean.sh` / `scripts/reap-xdist.sh`.
- Integration test verifies the wrapper actually launches detached and produces a pidfile + log.

## Documentation

- [ ] Update `docs/features/merge-gate-baseline.md`: document `scripts/refresh_baseline_detached.sh`
      as the timeout-safe refresh launch path, why the foreground command cannot complete in-harness
      (10-min bash cap vs ~30-min run), and that #2064 already resolved the contention axis.
- [ ] Update `config/personas/engineer.md` "Stale-Baseline Bypass" to name the detached wrapper.

## Failure Path Test Strategy

- **Wrapper launch failure** (python script missing / bad args): the wrapper still returns 0 after
  launching `nohup`; the *log* captures the python error and the `EXIT=` line. The integration test
  forwards a fast, harmless target and asserts the log records a completed refresh invocation plus an
  `EXIT=` line, proving the detached child actually ran (not just that the launcher returned).
- **Baseline-clobber safety (critique BLOCKER):** the integration test MUST pass
  `--output "$(mktemp).json" --runs 2` and a tiny passing target so the child (a) writes to a temp
  path, never the real gitignored `data/main_test_baseline.json`, and (b) has ≥2 usable runs so it
  is not `degraded`. The test snapshots the real `data/main_test_baseline.json` mtime/content before
  launch and asserts it is unchanged after — the file exists on the operator's machine, so the test
  asserts "untouched," not "absent."
- **Detector text regression:** a unit test asserts the staleness finding text names the detached
  wrapper, so a future edit that drops the actionable command fails loudly.

## Test Impact
- [ ] `tests/unit/test_refresh_test_baseline.py` — UPDATE: add/adjust a case asserting
      `format_staleness_warning` / the reflection finding text references
      `refresh_baseline_detached.sh` (if any test asserts the exact current warning string,
      update that assertion).
- [ ] reflection detector test (wherever `test_baseline_refresh_check` is covered) — UPDATE:
      assert the new actionable command in the warning finding.
- [ ] NEW `tests/integration/test_refresh_baseline_detached.py` — ADD: launch the wrapper against a
      trivial fast target, assert it returns promptly with a pidfile + log path, then poll for
      completion and assert the log shows the refresh ran. (No existing test covers detached launch.)

## Step by Step Tasks

1. Add `scripts/refresh_baseline_detached.sh`: `nohup python scripts/refresh_test_baseline.py "$@"`
   into a timestamped `logs/baseline_refresh_<ts>.log`, write a pidfile, print PID + log path + poll
   hint, `exit 0` immediately. Make it executable.
2. Repoint the warning text in `reflections/housekeeping/test_baseline_refresh_check.py` and
   `scripts/baseline_gate.py::format_staleness_warning` at the detached wrapper.
3. Update `config/personas/engineer.md` "Stale-Baseline Bypass" and
   `docs/features/merge-gate-baseline.md`.
4. Add `tests/integration/test_refresh_baseline_detached.py`; update the existing staleness-text
   assertions.
5. `ruff format` + `ruff check` the touched python; run the narrow test set
   (`tests/integration/test_refresh_baseline_detached.py` + the updated unit/reflection tests) via
   `scripts/pytest-clean.sh`.
6. Open the PR on `session/{slug}` with `Closes #2066`.
7. (Operational, executed as part of this pipeline run — not a loose footnote) Refresh
   `data/main_test_baseline.json` on `main` via the detached wrapper (already launched at plan time),
   reap xdist workers after, and verify the resulting artifact shows a fresh `generated_at`, `commit`
   at/near HEAD, and `runs` ≥ 2 (not `degraded`). #2066 stays open until this verified refresh lands
   on the owning machine — the Dev performs and attests to it in this run.

## Success Criteria

- `scripts/refresh_baseline_detached.sh` launched with a trivial target returns in well under 10
  minutes and produces a pidfile + log.
- The integration test proves the detached child actually ran the refresh.
- Both staleness warnings (gate + reflection) name the detached wrapper.
- `data/main_test_baseline.json` regenerated locally with a fresh `generated_at`, `commit` near HEAD,
  and `runs` ≥ 2 (staleness cleared).
- The freshness re-verification above answers "did #2064 resolve #2066?" with evidence.

## Open Questions

None blocking. The refresh host is this machine (where the pipeline runs), gated on the machine-global
suite lock; the detached wrapper is launched here as the operational remediation.

## Rabbit Holes

- Do NOT build a scheduled auto-regen — barred by #1933.
- Do NOT re-architect the suite lock — #2064 owns it.
- Do NOT add a new gate severity/exit code — keep the warning advisory; only its remediation text
  changes.

## Accepted Risk (knowingly deferred)

The staleness detector stays **warn-only and human-triggered** (auto-regen is barred by #1933). This
plan makes the remediation *actionable and completable*, but does not add an
escalate-after-N-unacted-warnings trigger — that is state-bearing (needs a persisted consecutive-warning
counter) and exceeds the Small appetite. If a future drift again reaches hundreds of commits, that is
the *deferred trigger gap*, NOT a regression of this fix. Filing a follow-up for auto-escalation is a
reasonable next step but is explicitly out of scope here.
