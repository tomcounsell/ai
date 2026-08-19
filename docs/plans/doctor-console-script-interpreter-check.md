---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-08-19
tracking: https://github.com/tomcounsell/ai/issues/2748
last_comment_id: 5280929435
---

# Doctor console-script check: verify the winning script's interpreter

## Problem

`python -m tools.doctor` reports every `[project.scripts]` entry point as healthy
whenever the name resolves into a repo venv bin directory. It decides that from
`shutil.which()` and directory identity alone — it never opens the file it just
blessed. A shim sitting in the right directory whose shebang points at a retired
or off-pin interpreter therefore reads as green, and the operator finds out only
when a gate exits non-zero for reasons that look like a code regression.

This is the same fault class the check was built for. #2566 and #2536 were both
wrong-interpreter faults; the merged check catches the *wrong directory* half of
that class and is blind to the *wrong interpreter* half.

**Current behavior:**

`_check_console_scripts_resolve` (`tools/doctor.py:150-317`) computes its verdict at
`:228-255`. `shutil.which(name)` at `:229` gives the winning path, and the name is
accepted when any of three conditions hold:

- `:242` — the winning file's parent directory *is* a repo venv bin dir;
- `:243` — the realpath of that parent is;
- `:244` — `_same_file` says it is a hardlink of the venv copy (the `~/.local/bin`
  links `/update` creates).

Every one of those accepts on location. `grep -n "shebang" tools/doctor.py` returns
nothing, and the docstring at `:173-177` states the choice outright: "a name is
healthy when it resolves into a repo venv bin directory". So all three accept
branches will pass a script whose `#!` line names an interpreter that was deleted,
sits two minor versions off the `.python-version` pin, or lives outside the venv
entirely and has no editable install of this repo.

The failure is silent in the worst direction. `critique-roster-check` and
`critique-resume-probe` are fail-closed gates invoked by bare name; the resume probe
in particular fails *open*, so a crashed critique restarts from scratch instead of
resuming and nothing surfaces that the resume path was skipped (#2858). Doctor is
the instrument that is supposed to make that visible in advance.

**Desired outcome:**

For every entry point that survives the existing resolution test, doctor reads the
winning script's shebang, resolves it to a concrete interpreter, and fails the check
when that interpreter is missing, off the `.python-version` pin, or outside the repo
venv. The finding names the shebang target and which of those three things is wrong,
and the fix line matches the state rather than repeating generic PATH advice.

## Freshness Check

**Baseline commit:** `327dacb82` (this plan's parent). All code reads below were taken
at `f491306c5`; main advanced to `327dacb82` while the plan was being written, and
`git diff f491306c5..327dacb82` over `tools/doctor.py`,
`tests/unit/test_doctor_console_scripts.py`, `agent/worktree_manager.py`, and
`pyproject.toml` shows a single change: `claude-agent-sdk` bumped 0.2.139 → 0.2.140.
`[project.scripts]` is untouched, so every reading below holds at the parent.
**Issue filed at:** 2026-08-13T04:29:44Z (6 days before planning)
**Disposition:** Minor drift

**File:line references re-verified:**

- `tools/doctor.py:150-317` — `_check_console_scripts_resolve` — still holds, exact
  line range unchanged.
- `tools/doctor.py:228-255` — verdict computation from `shutil.which` + directory
  identity — still holds.
- `tools/doctor.py:242 / :243 / :244` — the three accept branches (parent is venv bin
  / realpath of parent is / `_same_file` hardlink) — all three still present and
  unchanged.
- `tools/doctor.py:253` — `misresolved.append(f"{name} -> {found}")`, the
  winning-path naming that #2665 shipped — still holds.
- `tools/doctor.py:269-277` — the three-state `path_note` split — still holds.
- `tools/doctor.py:108-135` — `_repo_venv_bin_dirs` — still holds.
- `tools/doctor.py:138-147` — `_same_file` — still holds.
- `tools/doctor.py:442-539` — `_check_worktree_interpreters`, the existing pin
  comparison this plan reuses — still holds.
- `agent/worktree_manager.py:991-1015` — `venv_python_version` — still holds.
- `agent/worktree_manager.py:1076-1096` — `repo_interpreter_pin` — still holds.
- `grep -n "shebang" tools/doctor.py` — zero hits. The gap is unfixed on main.

**Cited sibling issues/PRs re-checked:**

- **#2665** — merged at `2560a191c`. Shipped the winning-PATH-entry naming, the
  three-state `path_note`, and state-specific fix lines. Not to be redone.
- **#2749** — still OPEN. Caller side (SDLC gates distinguishing exit 127 from a real
  gate verdict). Out of scope by design.
- **#2780** — still OPEN. Remediation half: 63 stale `~/Library/Python/3.12/bin`
  entries shadowing venv executables, and the PATH-posture decision. Out of scope.
- **#2566** — CLOSED. The originating wrong-interpreter fault.
- **#2536** — CLOSED. Popoto version-floor guard; the other wrong-interpreter
  precedent named in the issue.
- **#2858** — CLOSED 2026-08-18 as NOT_PLANNED, no closing PR, no commit references
  it. It reported `critique-*` resolving to `~/Library/Python/3.12/bin` shims whose
  shebang is `/Library/Frameworks/Python.framework/Versions/3.12/bin/python3`, and
  explicitly asked for the check this issue plans. Closing it changed no code, so it
  removes nothing from this plan's premise — it strengthens it.

**Commits on main since issue was filed (touching referenced files):**

- `8bb12c001` "Harden production Redis against accidental flush (four layers) (#2680)"
  — irrelevant to the console-script check. Added `_check_redis_flush_guard` elsewhere
  in the file; `git diff 2560a191c..8bb12c001` shows no change to
  `_check_console_scripts_resolve`.
- `2560a191c` "Doctor: flag console scripts that resolve outside the repo venv (#2665)"
  — this is the prior work the issue was filed against, already accounted for.
- No commits since to `tests/unit/test_doctor_console_scripts.py` or
  `agent/worktree_manager.py`.

**Active plans in `docs/plans/` overlapping this area:** none. The most recent plans
(`move-bridge-utc-to-utils`, `issue-lock-renewer-pid-authoritative`,
`update-warning-channel-repair`, `docs-auditor-review-gate`,
`durability-room-job-agentrun`) touch bridge time handling, lock renewal, the update
warning channel, the docs auditor, and durability respectively. None touches
`tools/doctor.py`.

**Notes:** The drift is in the *thread*, not the code. One comment on the issue
asserts that a resolution-only check "passes" for `critique-resume-probe` /
`critique-roster-check` winning out of `~/Library/Python/3.12/bin`. Against merged
code that is false: the parent is not a repo venv bin dir and the shims are not
hardlinks of the venv copies, so `tools/doctor.py:248-253` already flags them and
`:281-285` already emits the PATH remedy. Tom's later comment corrects this and
re-anchors the residual gap onto the two accept branches that a shebang read would
actually close. This plan builds on the corrected framing, and the acceptance
criteria below name those two branches rather than the already-caught machine
evidence. Reproduction of the *residual* fault is by construction (a shim inside a
venv bin with a stale shebang), because the observed machine evidence is a different,
already-handled shape.

## Prior Art

- **PR #2665** — "Doctor: flag console scripts that resolve outside the repo venv",
  merged at `2560a191c`. Created `_check_console_scripts_resolve`,
  `_repo_venv_bin_dirs`, `_same_file`, and `tests/unit/test_doctor_console_scripts.py`
  (18 tests). Succeeded at what it scoped: three-state resolution attribution with
  state-specific remedies. Explicitly measured location, not interpreter — the
  docstring says so. This plan extends it; it does not revisit it.
- **Issue #2566** — CLOSED. `critique-*` shims crashing with
  `ModuleNotFoundError: No module named 'tools'` on another machine. The originating
  report; #2665 was its fix.
- **Issue #2536** — CLOSED. Popoto index rebuild destroyed by a below-floor
  interpreter, guarded by `docs/features/popoto-version-floor-guard.md`. Named in
  #2748 as the second wrong-interpreter precedent. Establishes the house pattern:
  runtime fails open on uncertainty, doctor fails loud.
- **Issue #2858** — CLOSED NOT_PLANNED, no code landed. Reported the same shape again
  from the #2845 critique lane, and its own "fix direction" section proposed exactly
  this check. Evidence the fault recurs and that closing the report did not close the
  gap.
- **Issue #2617 / #2572** — the `.python-version` pin work. Produced
  `repo_interpreter_pin`, `venv_python_version`, `scripts/check-interpreter-pin.sh`,
  and `_check_worktree_interpreters`. This plan consumes those; it adds no new pin
  machinery.
- **Issues #2749, #2780** — both OPEN, both deliberately adjacent. See No-Gos.

## Research

**Queries used:**

- `setuptools console script long shebang 127 byte limit "exec" /bin/sh wrapper`
- `uv console script shebang absolute path venv relocatable`

**Key findings:**

- **The kernel caps shebang length, and installers work around it with a two-line
  polyglot.** `BINPRM_BUF_SIZE` gives 127 usable bytes on Linux and 256 on macOS.
  When the absolute interpreter path exceeds that, pip/distlib emit a script whose
  first line is `#!/bin/sh` and whose second line carries the real interpreter inside
  an `'''exec' <python> "$0" "$@"` construct that both `sh` and Python parse
  harmlessly. Source: [pypa/setuptools#494](https://github.com/pypa/setuptools/issues/494).
  **Informs the plan:** a shebang reader that looks only at line 1 would classify
  every such script as "interpreter `/bin/sh`, outside the venv" — a false accusation
  on a healthy machine. The extractor must read line 2 when line 1 is a shell.
- **`uv venv --relocatable` emits that same two-line shape with a *relative*
  interpreter reference.** The flag persists a `relocatable` key in `pyvenv.cfg` and
  instructs the wheel installer to build entrypoints using the `exec` trick plus
  `dirname $0` on POSIX. Sources:
  [astral-sh/uv#5515](https://github.com/astral-sh/uv/pull/5515),
  [astral-sh/uv#13350](https://github.com/astral-sh/uv/issues/13350).
  **Informs the plan:** the relocatable form has no absolute path to compare at all.
  It must be resolved against the script's own directory before classification, or it
  will read as missing.
- **uv's default is an absolute shebang into the venv**, which is why the Docker
  volume-mount and directory-move cases break at all
  ([astral-sh/uv#13350](https://github.com/astral-sh/uv/issues/13350)). Confirmed on
  this machine: all 26 declared scripts carry
  `#!/Users/tomcounsell/src/ai/.venv/bin/python3`. **Informs the plan:** the common
  path is the simple one, and the polyglot handling is a correctness guard for
  long-path worktrees and relocatable venvs rather than the primary case.

Both findings are saved to memory (`valor` project) for reuse.

## Spike Results

### spike-1: What shebang forms actually exist on disk for this repo's entry points?

- **Assumption**: "Every `[project.scripts]` entry in `.venv/bin` carries a plain
  absolute shebang pointing into the venv."
- **Method**: code-read + filesystem measurement
- **Finding**: True here, and uniform. All 26 declared scripts have exactly
  `#!/Users/tomcounsell/src/ai/.venv/bin/python3`. Across the whole of `.venv/bin`
  there are only two shebang forms (57 × `.../bin/python3`, 40 × `.../bin/python`)
  and 14 files with no shebang at all (compiled binaries). No `/bin/sh` polyglot and
  no `env` form present.
- **Confidence**: high
- **Impact on plan**: the primary extraction path is a one-line read. The polyglot and
  `env` forms are correctness guards (see Research), not the common case — so they
  belong in the extractor with tests, but they do not shape the check's structure.
  The 14 shebang-less binaries confirm the extractor must return "unverified" rather
  than raising or failing on a file with no `#!`.

### spike-2: Can the pin comparison be done without spawning a subprocess?

- **Assumption**: "Resolving a shebang target to a `MAJOR.MINOR` requires executing
  it."
- **Method**: prototype (read-only, in-process)
- **Finding**: False for the case that matters. For
  `/Users/tomcounsell/src/ai/.venv/bin/python3`, the parent is a repo venv bin dir and
  `venv_python_version(parent.parent)` returns `3.14`, matching
  `repo_interpreter_pin()` = `3.14`. The realpath resolves to
  `~/.local/share/uv/python/cpython-3.14.6-macos-aarch64-none/bin/python3.14`, so a
  version is recoverable from the path too. A target *outside* every repo venv is a
  failure regardless of its version, so it never needs one.
- **Confidence**: high
- **Impact on plan**: no subprocess. The check stays a pure filesystem read, matching
  `_check_worktree_interpreters`'s precedent, and 26 entry points cost 26 small file
  reads rather than 26 process spawns. Recorded as an anti-criterion.

### spike-3: Is a retired interpreter distinguishable from an off-pin one?

- **Assumption**: "A venv python whose uv-managed base download was garbage-collected
  is detectable without executing it."
- **Method**: prototype
- **Finding**: Yes. `Path.exists()` follows symlinks and returns `False` for a broken
  one (`is_symlink()` stays `True`, and `os.path.realpath` still yields the dangling
  target's path, which is useful for the message). Meanwhile the venv's `pyvenv.cfg`
  still reports the version it was *built* against, so a pin comparison alone would
  call a retired interpreter healthy.
- **Confidence**: high
- **Impact on plan**: existence must be tested **before** the pin comparison, and
  "missing" is its own reported state with its own fix line. Ordering is load-bearing,
  so it gets its own test.

### spike-4: Does the existing test fixture survive the new check?

- **Assumption**: "The new check is additive, so
  `tests/unit/test_doctor_console_scripts.py` needs no changes."
- **Method**: code-read of the fixture
- **Finding**: False, and this is the plan's main hazard. `_fake_checkout`
  (`tests/unit/test_doctor_console_scripts.py:31-39`) builds a `.venv/bin` with no
  `pyvenv.cfg`, no `python` binary, no `.python-version`, and writes every shim with
  the body `#!/bin/sh\nexit 0\n`. Under a naive implementation those shims would
  classify as "interpreter `/bin/sh`, outside the venv" and **all seven currently
  passing assertions would flip to failures**. Confirmed by prototype:
  `repo_interpreter_pin(fixture_root)` returns `None` and
  `venv_python_version(fixture_root/".venv")` returns `None`.
- **Confidence**: high
- **Impact on plan**: the fixture must become a realistic venv (a `pyvenv.cfg` with
  `version_info`, a real `bin/python3`, a `.python-version`, and shims whose shebang
  points into that venv) as a *precondition* of the build, not a cleanup afterwards.
  Critically, "pin is unresolvable → skip the comparison" must NOT be used as the way
  to keep the old tests green: that would leave the new guard unreached by the whole
  existing suite, which is the "a green test reaches no code at all" trap. Fail-open on
  an unknown pin is still correct behavior, but it is scoped to the off-pin comparison
  only and gets its own dedicated test.

## Data Flow

1. **Entry point**: an operator or the SDLC pipeline runs `python -m tools.doctor`;
   `get_checks()` (`tools/doctor.py:1821-1828`) dispatches
   `_check_console_scripts_resolve` **before** `_check_system_tools`, because the
   latter's `scripts.update.verify` import prepends `~/Library/Python/3.12/bin` to
   `os.environ["PATH"]` and would corrupt the measurement.
2. **Declared names**: `pyproject.toml` `[project.scripts]` is parsed with `tomllib`
   (`:187-206`) into 26 names.
3. **Resolution (unchanged)**: for each name, `shutil.which` (`:229`) plus the
   three-branch venv-membership test (`:238-247`) sorts it into resolved,
   `misresolved`, or `not_installed`.
4. **Interpreter read (new)**: for each name that *resolved*, the winning file's
   leading bytes are read and the shebang is extracted to a concrete interpreter path
   (handling the plain, `env`, `/bin/sh` polyglot, and relocatable `dirname $0` forms).
5. **Classification (new)**: the target is sorted into `ok`, `missing`, `off-pin`, or
   `outside`, using `_repo_venv_bin_dirs()` for venv membership, `venv_python_version`
   for the version, and `repo_interpreter_pin(PROJECT_DIR)` for the reference.
6. **Grouping (new)**: findings are keyed by `(reason, target)` so one bad interpreter
   shared by 26 scripts reports as one line with a count, not 26 lines.
7. **Output**: a single `CheckResult`. On the failure path `message` and `fix` carry the
   existing resolution clause and PATH/`uv sync` advice unchanged, with an interpreter
   clause and a reason-specific remedy appended. On the pass path `message` gains a
   trailing interpreter-verified count. The doctor CLI renders it in the Environment
   category.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #2665 (`2560a191c`) | Added `_check_console_scripts_resolve`: three-state resolution attribution, winning-path naming, state-specific fix lines | Did not fail. Scoped deliberately to *location* — the docstring at `tools/doctor.py:173-177` states the choice explicitly. The interpreter question was never in its scope, so the gap is unaddressed rather than mis-addressed |
| Issue #2858 (closed NOT_PLANNED) | Reported the fault a second time from the #2845 critique lane; proposed both shim removal and this check | Closed with no code, no PR, no referencing commit. The report was consumed as information and the durable half was never built |

**Root cause pattern:** every fix so far has treated a wrong-interpreter fault as a
*PATH* fault, because PATH is what the symptom points at first. Removing a stale shim
or reordering PATH converts one failure shape into another without ever asking what
interpreter the winning file actually binds to. Doctor inherited that framing. The
durable fix is to make interpreter identity a first-class thing the check measures,
so the answer does not depend on which directory happened to win.

## Architectural Impact

- **New dependencies**: none. `repo_interpreter_pin` and `venv_python_version` are
  already importable from `agent/worktree_manager.py` and already imported by
  `_check_worktree_interpreters` in the same module.
- **Interface changes**: none public. `_check_console_scripts_resolve` keeps its
  signature and its `CheckResult` return type. New helpers are module-private.
- **Coupling**: adds a read-only dependency from `tools/doctor.py` onto the
  `.python-version` pin contract, which the same file already depends on at
  `:465-484`. No new coupling direction.
- **Data ownership**: unchanged. Doctor stays detect-only; it writes nothing and
  deletes nothing.
- **Reversibility**: high. The change is one additive block inside one function plus
  two private helpers. Reverting is a single-commit revert with no migration and no
  state to unwind.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**

- PM check-ins: 0 (scope is fully specified by the issue's narrowed body and Tom's
  re-anchoring comment)
- Review rounds: 1

The work is one function extension, two private helpers, a fixture upgrade, and about
a dozen tests, all inside two files. The bottleneck is getting the false-positive
surface right, which is what the test matrix below is for.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Repo venv on the pin | `scripts/check-interpreter-pin.sh` | The check compares against `.python-version`; a drifted venv would make manual verification lie |
| Console scripts currently healthy | `.venv/bin/python -c "from tools.doctor import _check_console_scripts_resolve as c; assert c().passed"` | Establishes the green baseline the change must preserve |
| `[project.scripts]` populated | `.venv/bin/python -c "import tomllib,pathlib; assert tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']['scripts']"` | The check is a no-op without declared entry points |

## Solution

### Key Elements

- **Shebang extractor** — given a path, returns the concrete interpreter it will
  actually execute under, or `None` when the file declares none. Understands the four
  forms that exist in the wild: plain absolute, `env`-mediated, the `/bin/sh`
  long-path polyglot, and uv's relocatable `dirname $0` variant.
- **Interpreter classifier** — sorts a resolved interpreter into one of four states
  (`ok`, `missing`, `off-pin`, `outside`) using the venv-bin-dir set the check already
  computes and the `.python-version` pin the repo already publishes.
- **Grouped finding reporter** — collapses findings sharing a `(reason, target)` into
  one line naming the target, the reason, the count, and a few example script names.
- **Reason-specific remediation** — one fix sentence per reason, added alongside the
  existing PATH and `uv sync` advice rather than replacing it. The hardlink case gets
  its own sentence because rebuilding a venv does not repair a hardlink.
- **Realistic test fixture** — `_fake_checkout` upgraded to build a venv that is
  actually a venv, so the new guard is reachable by the existing suite instead of
  being skipped by an unresolvable pin.

### Flow

**`python -m tools.doctor`** → Environment section runs → **console script check** →
name resolves into a repo venv bin dir → **shebang read** → interpreter classified →
`ok` for all → **PASS**, message names the venv and the Python version verified

Failure branch:

**`python -m tools.doctor`** → **console script check** → 26 names resolve into
`.venv/bin` → **shebang read** → all 26 point at `/Library/Frameworks/.../3.12/python3`
→ classified `outside` → **FAIL**, one grouped line naming that target, the count, and
three example names → **fix** names the rebuild and, if hardlinks are involved, the
re-link

### Technical Approach

- **Read the interpreter only for names that already passed resolution.** Names in
  `misresolved` / `not_installed` are already reported with their own remedies;
  layering a second complaint on them adds noise and would perturb the existing
  message assertions. This keeps the two concerns composable and leaves the
  three-state `path_note` logic at `tools/doctor.py:269-277` byte-identical.

- **Extraction reads bounded bytes in binary mode.** Open the file, read at most the
  first two lines with a per-line cap, and decode leniently. Binary mode matters:
  `.venv/bin` legitimately contains files that are not UTF-8 (14 on this machine), and
  a `UnicodeDecodeError` in a health check is a bug, not a finding. Sequence:

  1. No leading `#!` → return `None` (unverified, **not** a failure — see below).
  2. First line's program basename is a shell (`sh`, `bash`, `dash`) → parse line 2 for
     the distlib/uv `'''exec'` construct and take the quoted interpreter. If that
     reference is relative or contains `dirname`/`$0`, resolve it against the script's
     own parent directory.
  3. First line's program basename is `env` → take the next token and resolve it with
     `shutil.which`. This form's interpreter genuinely depends on invocation-time PATH;
     resolving it under the doctor's own PATH is the honest reading, and an
     unresolvable token becomes `missing` rather than silently passing.
  4. Otherwise → the first token after `#!`, with any trailing interpreter arguments
     discarded.

- **Classification order is existence, then membership, then pin** — spike-3 showed a
  retired interpreter still reports a healthy `version_info` in `pyvenv.cfg`, so a
  pin-first order would call it green:

  1. `Path(target).exists()` is `False` (symlinks followed) → **missing**. Report the
     dangling realpath, which is what tells the operator *which* interpreter went away.
  2. Target's parent, or the realpath of that parent, is in `_repo_venv_bin_dirs()` →
     read `venv_python_version(parent.parent)`. Differs from the pin → **off-pin**,
     naming both versions. Equal, or pin unresolvable → **ok**.
  3. Neither → **outside**. No version is needed: an interpreter outside every repo
     venv has no editable install of this repo regardless of its version, which is the
     `ModuleNotFoundError: No module named 'tools'` shape from #2566 and #2858. Name
     the version anyway when it is cheaply parseable from the path, since it is useful
     context.

- **Fail open only where the evidence is genuinely absent, and scope it narrowly.**
  An unresolvable `repo_interpreter_pin` disables the **off-pin comparison alone**;
  `missing` and `outside` need no pin and keep firing. A file with no shebang is
  unverified, not failed. Both degradations must be *visible*: the pass message states
  how many scripts were actually interpreter-verified, so "verified" is never claimed
  for a set the check did not read. This mirrors the house rule from #2536 — runtime
  fails open on uncertainty, doctor fails loud — while refusing to let silence read as
  health.

- **Group findings by `(reason, target)`.** The issue thread asked for the offending
  location to be reported once rather than N times. Re-aimed at the target rather than
  the containing directory, because the residual cases all live *inside* accepted venv
  bin dirs. On this machine all 26 scripts share one target, so grouping is the
  difference between one actionable line and 26 identical ones. Cap the example names
  shown and signal the omission with a `(+N more)` suffix, matching the existing
  truncation convention at `:260-263` and `:288-293`.

- **Compose the message and fix additively, and scope the byte-identical guarantee to
  the failure path.** The two paths get different contracts, because only one of them
  can afford to stay frozen:

  - *Failure path* (`tools/doctor.py:300-311`): the existing `X/N ... do not resolve`
    clause, the three-state `path_note`, and the PATH / `uv sync` fixes are emitted byte
    for byte as they are today. The interpreter clause is appended with `; ` and the
    reason-specific remedy is appended to the `fixes` list. Both a resolution failure and
    an interpreter failure can be true at once and the output must name both. When *only*
    interpreter findings exist, the check still fails, with a message that does not borrow
    the `X/N ... do not resolve` phrasing (they did resolve — that would be a lie).
  - *Pass path* (`tools/doctor.py:313-318`): the message **changes**, and it has to. It
    gains a trailing interpreter-verified count, so a run that verified nothing cannot
    read as a clean bill of health. Today's measured pass message is
    `26 console scripts resolve into /Users/tomcounsell/src/ai/.venv/bin`; the new shape
    appends a clause, e.g. `..., 26 interpreter-verified`. This is safe against the suite
    because the three `passed is True` tests assert substrings, never equality:
    `test_passes_when_venv_bin_leads_path` asserts `"3 console scripts" in result.message`
    and `str(root / ".venv" / "bin") in result.message`;
    `test_main_checkout_venv_accepted_from_a_worktree` additionally asserts
    `str(worktree / ".venv" / "bin") not in result.message`, so the appended clause must
    be a count and must name no venv path of its own.

- **Remediation text, per reason.** `missing` and `off-pin` both point at
  `rm -rf .venv && uv sync --all-extras` in the affected checkout, which is the same
  remedy `_check_worktree_interpreters` already prescribes at `:520-523` — consistency
  matters more than novelty. `outside` points at the same rebuild, because a shebang
  pointing out of the venv means the scripts were generated by a foreign installer.
  Additionally, when a finding's script was accepted via the `_same_file` hardlink
  branch, the fix must also name `/update`: rebuilding a venv writes new inodes and the
  old hardlink in `~/.local/bin` keeps its stale content, so the rebuild alone would
  leave the operator exactly where they started.

- **No subprocess, no writes.** The check spawns nothing (spike-2) and deletes nothing.
  Both are recorded as anti-criteria in the Verification table.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] The extractor's file read is the only new I/O and it must swallow `OSError` /
      `UnicodeDecodeError` — a permission-denied or binary file is "unverified", never a
      crash. Assert the observable behavior: the check still returns a `CheckResult`,
      does not raise, and the unverified count in the pass message reflects the skip.
- [ ] `_repo_venv_bin_dirs` already carries a broad `except Exception` at
      `tools/doctor.py:122`. It is pre-existing and untouched; no new blanket handler is
      introduced by this work.
- [ ] No `except Exception: pass` is added. Every new handler either records an
      unverified count or produces a finding.

### Empty/Invalid Input Handling

- [ ] Zero-byte script file → extractor returns `None`, classified unverified, no crash.
- [ ] Shebang line present but empty after `#!` (`"#!\n"`) → `None`, unverified.
- [ ] `/bin/sh` first line with **no** second line, or a second line that is not the
      `'''exec'` construct → `None`, unverified. It must not fall back to reporting
      `/bin/sh` as the interpreter.
- [ ] Shebang with trailing arguments (`#!/path/python -E -s`) → the interpreter path
      alone is extracted; the flags are discarded.
- [ ] `repo_interpreter_pin` returns `None` → off-pin comparison skipped; `missing` and
      `outside` still fire. Dedicated test, per spike-4.
- [ ] `[project.scripts]` empty or `pyproject.toml` unreadable → unchanged behavior,
      guarded by the existing tests at `:124-137`.

### Error State Rendering

- [ ] Every failing state asserts on the rendered `message` **and** `fix`, not just
      `passed is False`. A finding whose text omits the shebang target is the exact
      defect this issue exists to fix.
- [ ] Each of the three failure reasons asserts its own distinct fix sentence, so a
      single generic remedy cannot satisfy all three.
- [ ] The hardlink case asserts `/update` appears in the fix — the rebuild-only remedy
      is provably insufficient there.
- [ ] Mixed resolution + interpreter failure asserts both clauses in `message` and both
      remedies in `fix`.

## Test Impact

- [ ] `tests/unit/test_doctor_console_scripts.py::_fake_checkout` — UPDATE: build a
      realistic venv. Write `.venv/pyvenv.cfg` with a `version_info` line, create a real
      `.venv/bin/python3`, write a repo-root `.python-version` matching it, and give each
      shim a shebang pointing at that `bin/python3`. Without this, spike-4 confirms the
      new check flips seven currently-passing assertions to failures.
- [ ] `tests/unit/test_doctor_console_scripts.py::TestConsoleScriptsResolve` (9 tests) —
      UPDATE: no assertion changes intended, but every one routes through `_fake_checkout`
      and must be re-verified green after the fixture upgrade. `test_passes_when_venv_bin_leads_path`,
      `test_hardlinked_copy_outside_the_venv_is_accepted`, and
      `test_main_checkout_venv_accepted_from_a_worktree` are the three that assert
      `passed is True` and are therefore the ones the new check can break.
- [ ] `tests/unit/test_doctor_console_scripts.py::TestDeclaredButNotInstalled` (4 tests) —
      UPDATE: re-verify only. `test_a_shadowed_name_still_reads_as_shadowed` asserts
      `"uv sync" not in result.fix`, so it fails the moment a spurious interpreter finding
      appends a `uv sync` remedy. It is the sharpest canary for a fixture regression and
      must be run explicitly.
- [ ] `tests/unit/test_doctor_console_scripts.py::TestShimmedAndNeverInstalled` (4 tests) —
      UPDATE: re-verify only. Same fixture dependency.
- [ ] `tests/unit/test_doctor_console_scripts.py::TestRegisteredInDoctor` (1 test) —
      no change. Asserts ordering against `_check_system_tools`, which this work
      preserves.
- [ ] `tests/unit/test_doctor_console_scripts.py::_stale_shim_dir` — no change. Its shims
      fail resolution and never reach the interpreter read.
- [ ] `tests/unit/test_doctor_console_scripts.py::TestWinningScriptInterpreter` — CREATE:
      the new matrix (see Step by Step Tasks, task 3).
- [ ] `tests/unit/test_interpreter_pin_guard.py` — no change expected. It exercises
      `scripts/check-interpreter-pin.sh` and the pin helpers, which this work consumes
      read-only. Run it to confirm no incidental coupling.
- [ ] No xfail markers exist for this bug —
      `grep -rn 'pytest.mark.xfail\|pytest.xfail(' tests/` surfaces nothing related to
      doctor console scripts, so there is nothing to convert.

## Rabbit Holes

- **Fixing the machine instead of the check.** `~/Library/Python/3.12/bin` holds 63
  shadowing entries on at least one host. Deleting them is #2780 and it is a different
  kind of work (a destructive host mutation). This plan ships detection and stops.
- **Executing the shebang target to ask it its version.** Authoritative and tempting,
  and it turns a filesystem check into up to 26 process spawns. spike-2 showed
  `pyvenv.cfg` answers the only case that needs a version. Recorded as an
  anti-criterion so it cannot creep back in.
- **A general "is this script runnable" prober.** Import checks, `--help` smoke runs, and
  exit-code probing all belong to the caller-side question in #2749. Doctor's job here
  is interpreter identity.
- **Generalizing the interpreter read to every file in `.venv/bin`.** 97 shebang-bearing
  files sit there. Only the 26 declared entry points are invoked by bare name from
  skills and hooks, and only those are what the check is about. Widening the scan
  multiplies output volume without adding a single actionable finding.
- **Rewriting shebangs in place.** Doctor is detect-only; the whole file writes nothing.
  A "repair" mode is a separate design conversation with its own blast radius.
- **Parsing arbitrary shell in the polyglot second line.** Match the specific
  distlib/uv `'''exec'` construct and give up cleanly on anything else. A general shell
  parser is a project, and "unverified" is a safe answer.
- **Merging with `_check_worktree_interpreters`.** They share helpers and answer
  different questions (which venvs drifted vs. what a console script binds to).
  Collapsing them would couple two independently-useful findings.

## Risks

### Risk 1: False accusation on a healthy machine

**Impact:** The single worst outcome. Doctor is a trust instrument; a check that cries
wolf on a correct setup gets ignored, taking the genuine findings with it. The
concrete vectors are the `/bin/sh` polyglot (long worktree paths), uv's relocatable
form, `env`-mediated shebangs, and shebang-less binaries.
**Mitigation:** each vector has a named passing test (task 3, cases 6-9). The
Verification table carries a live control row asserting
`_check_console_scripts_resolve().passed` is still `True` on this machine, where the
baseline is green today and all 26 scripts are interpreter-verifiable. Unverifiable
inputs are unverified, never failed.

### Risk 2: The fixture upgrade masks the new guard instead of exercising it

**Impact:** The subtle one. If the build takes the easy route and lets an unresolvable
pin skip the interpreter check, all 18 existing tests stay green while the new code is
never reached by any of them — a green suite proving nothing.
**Mitigation:** fail-open is scoped to the off-pin comparison alone; `missing` and
`outside` fire with no pin. The fixture is upgraded to a real venv so the existing
tests traverse the new code on the healthy path. Task 4 mandates a per-guard mutation
check: break each guard individually, confirm a *specific* named test fails, restore.

### Risk 3: Perturbing #2665's merged message and fix strings

**Impact:** 18 existing tests assert exact substrings (`"2/3"`, `"not on PATH"`,
`"shadowed"`, `"uv sync" not in fix`). Restructuring the message would break them and
invite "fixing" the assertions, quietly undoing shipped attribution work.
**Mitigation:** the interpreter clause is strictly *appended*, on both paths. The
failure strings stay byte-identical (see the composition bullet in Technical Approach);
the pass string gains a trailing count, which every `passed is True` test survives
because all three assert substrings rather than equality. Task 2 requires re-running the
full existing file before any new test is written, so a regression is attributed to the
right change.

### Risk 4: The hardlink remedy is incomplete and reads as complete

**Impact:** `/update` hardlinks entry points into `~/.local/bin`. Because a hardlink
shares an inode, `rm -rf .venv && uv sync` produces fresh files in the venv and leaves
the `~/.local/bin` copy holding the old content. An operator who follows a
rebuild-only fix re-runs doctor and sees the same finding, with no idea why.
**Mitigation:** findings whose script was accepted via the `_same_file` branch carry an
additional `/update` sentence. Asserted explicitly in task 3 case 5.

### Risk 5: A pin file that disagrees with an on-disk venv produces a confusing double report

**Impact:** If `.python-version` says 3.14 and the venv is 3.12, both
`_check_worktree_interpreters` and this check fire, and an operator may read two
findings as two problems.
**Mitigation:** the interpreter finding names the shebang target and both versions
explicitly, and prescribes the same `rm -rf .venv && uv sync --all-extras` remedy the
worktree check already gives. One action clears both. Verified by reading both
messages together during task 5.

## Race Conditions

**Race 1: doctor reads a shebang while `uv sync` is rewriting the script**

**Location:** the new extractor, called from `_check_console_scripts_resolve`
(`tools/doctor.py:150-317`)
**Trigger:** an operator or `/update` runs `uv sync` in the same checkout while doctor
is mid-scan; the entry point is unlinked and recreated between `shutil.which` and the
open.
**Data prerequisite:** none. The check derives nothing from prior state and stores
nothing.
**State prerequisite:** none.
**Mitigation:** benign by construction, and no locking is warranted. A vanished file
raises `OSError`, which the extractor already turns into "unverified" rather than a
crash or a finding. The worst outcome is a transient unverified count in one report,
which the next run corrects. Doctor is a point-in-time instrument with no persisted
verdict, so there is nothing for a stale read to corrupt.

No other race conditions identified: the check is synchronous, single-threaded,
read-only, and shares no mutable state with any other check.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2780] Removing the stale `~/Library/Python/3.12/bin` shims and
  deciding this fleet's PATH posture. That is the remediation half and it mutates host
  state outside the checkout; this issue is detection only. Doctor must remain
  detect-only, which is asserted as an anti-criterion below.
- [SEPARATE-SLUG #2749] Making SDLC gate callers (`critique-roster-check`,
  `critique-resume-probe`, `sdlc-push-guard`) distinguish exit 127 from a real non-zero
  gate verdict. Named as deliberately separate in the issue body.
- [EXTERNAL] Verifying the fix against the `valorengels` host, which carries the
  63-shadowed-executable state reported in the thread. Only a human with access to that
  machine can run doctor there. The two cases this plan closes are reproduced
  synthetically in the test suite instead, which is why the acceptance criteria are
  written against constructed fixtures rather than that host's output.

## Update System

No update system changes required. The check is internal to `tools/doctor.py` and
`python -m tools.doctor` is already invoked by the existing update flow; it gains a new
finding type with no new dependency, config file, or migration. Nothing needs to be
propagated to other machines beyond the normal `git pull` that `/update` already does.

Worth noting in the other direction: this check makes an *existing* `/update`
behavior legible. `/update` hardlinks entry points into `~/.local/bin`, and Risk 4
describes why a venv rebuild leaves those hardlinks stale. The new fix line tells the
operator to re-run `/update` in exactly that case, so the two systems agree on the
remedy without either changing.

## Agent Integration

No agent integration required. `python -m tools.doctor` is already an established
module entry point the agent reaches through its Bash tool, and `tools.doctor` needs no
`[project.scripts]` entry (adding one would be self-referential given what this check
measures). The bridge does not import doctor and gains no reason to. The change is a
new finding inside an existing check that the agent already knows how to run and read.

## Documentation

### Feature Documentation

- [ ] Update `docs/features/local-doctor.md` — document the console-script interpreter
      verification: the four shebang forms understood, the four classification states
      (`ok` / `missing` / `off-pin` / `outside`), the fail-open scoping (unknown pin
      disables only the off-pin comparison; no shebang is unverified, not failed), and
      the hardlink remedy that needs `/update` rather than a venv rebuild.
- [ ] Update the Local Doctor row in `docs/features/README.md` to mention interpreter
      verification alongside the existing resolution check, so the index reflects what
      the check now measures.

### Inline Documentation

- [ ] Update `_check_console_scripts_resolve`'s docstring at `tools/doctor.py:173-177`.
      It currently declares "the check measures resolution rather than shim hygiene: a
      name is healthy when it resolves into a repo venv bin directory" — that sentence
      becomes false with this change and must state the two-part contract instead.
      Per the no-legacy-code rule, describe only the new status quo.
- [ ] Docstring the extractor with the four shebang forms and cite the sources
      (pypa/setuptools#494, astral-sh/uv#5515) so the polyglot handling does not read as
      unmotivated defensiveness to a future reader.
- [ ] Docstring the classifier with the existence-before-pin ordering and *why*
      (spike-3: a retired interpreter still reports a healthy `version_info`).

## Success Criteria

- [ ] A shim inside a repo venv bin dir whose shebang names a nonexistent interpreter
      fails the check, and the message names that interpreter path.
- [ ] A shim inside a repo venv bin dir whose shebang names an interpreter belonging to
      a venv off the `.python-version` pin fails the check, and the message names both
      the target and the two versions.
- [ ] A shim inside a repo venv bin dir whose shebang names an interpreter outside every
      repo venv fails the check, and the message says so.
- [ ] A `~/.local/bin` hardlink accepted via `_same_file` whose shebang is stale is
      flagged, and its fix names `/update` in addition to the venv rebuild.
- [ ] Each of the three failure reasons emits a distinct fix sentence.
- [ ] Findings sharing one `(reason, target)` report as a single line with a count and
      capped example names, not one line per script.
- [ ] The `/bin/sh` long-path polyglot, uv's relocatable `dirname $0` form, an
      `env`-mediated shebang resolving into the venv, and a shebang-less binary all pass
      without a finding.
- [ ] An unresolvable `.python-version` suppresses only the off-pin comparison; a
      missing interpreter still fails.
- [ ] The pass message discloses how many scripts were interpreter-verified, so an
      all-unverified run does not read as verified.
- [ ] On the **failure** path, the existing resolution clause, `path_note`, and PATH /
      `uv sync` fixes are byte-identical to today's; the interpreter clause and remedy
      are appended.
- [ ] On the **pass** path, the message keeps today's
      `N console scripts resolve into <venv bin>` prefix verbatim and appends an
      interpreter-verified count clause that names no venv path of its own.
- [ ] All 18 pre-existing tests in `tests/unit/test_doctor_console_scripts.py` pass
      with their assertions unchanged.
- [ ] Each new guard is individually mutation-checked: breaking it fails a specific
      named test.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (doctor-check)**
  - Name: `doctor-interpreter-builder`
  - Role: Implement the extractor, classifier, grouping, and remediation inside
    `tools/doctor.py`; upgrade the test fixture; write the new test matrix
  - Agent Type: builder
  - Resume: true

- **Validator (doctor-check)**
  - Name: `doctor-interpreter-validator`
  - Role: Verify every success criterion, run the mutation checks, confirm the
    pre-existing 18 tests are green with unmodified assertions, and confirm the live
    control still passes
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Upgrade the test fixture to a realistic venv

- **Task ID**: build-fixture
- **Depends On**: none
- **Validates**: `tests/unit/test_doctor_console_scripts.py` (all 18 existing tests)
- **Informed By**: spike-4 (the current fixture has no `pyvenv.cfg`, no `.python-version`,
  and `#!/bin/sh` shims, so a naive implementation flips 7 passing assertions)
- **Assigned To**: `doctor-interpreter-builder`
- **Agent Type**: builder
- **Parallel**: false
- In `_fake_checkout`, write `.venv/pyvenv.cfg` containing a `version_info` line and
  create a real `.venv/bin/python3` file.
- Write a repo-root `.python-version` in the fixture whose value matches that
  `version_info`.
- Give each generated shim a shebang pointing at the fixture's own
  `.venv/bin/python3` instead of `#!/bin/sh`.
- Parameterize the fixture so a test can request an off-pin `version_info`, a broken
  `bin/python3` symlink, or a custom shebang body — the new matrix needs all three.
- Leave `_stale_shim_dir` alone; its shims fail resolution and never reach the read.
- Gate on a self-measured count, not a remembered literal: run
  `scripts/pytest-clean.sh tests/unit/test_doctor_console_scripts.py -q` and confirm it
  reports `18 passed` **before** any production change, so a later failure is
  attributable. If the collected count is not 18, reconcile the plan against the file
  before proceeding rather than assuming the plan is right.

### 2. Implement the interpreter read inside the console-script check

- **Task ID**: build-check
- **Depends On**: build-fixture
- **Validates**: `tests/unit/test_doctor_console_scripts.py`
- **Informed By**: spike-1 (four shebang forms; 14 shebang-less binaries in
  `.venv/bin`), spike-2 (no subprocess needed — `venv_python_version` answers it),
  spike-3 (existence must be tested before the pin)
- **Assigned To**: `doctor-interpreter-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add a private shebang extractor handling the plain, `env`, `/bin/sh` polyglot, and
  relocatable `dirname $0` forms. Binary-mode bounded read; `OSError` and decode errors
  yield "unverified".
- Add a private classifier returning `ok` / `missing` / `off-pin` / `outside`, ordered
  existence → venv membership → pin, reusing `_repo_venv_bin_dirs()`,
  `venv_python_version`, and `repo_interpreter_pin`.
- Call both only for names that passed the existing resolution test at
  `tools/doctor.py:238-247`.
- Group findings by `(reason, target)`; cap example names and append `(+N more)`,
  matching the convention at `:260-263`.
- Append the interpreter clause to `message` and a reason-specific sentence to `fixes`.
  When only interpreter findings exist, emit a standalone message that does not reuse
  the `X/N ... do not resolve` phrasing.
- Add the `/update` sentence for findings whose script was accepted via `_same_file`.
- Extend the pass message with the interpreter-verified count.
- Update the `:173-177` docstring to state the new two-part contract; docstring both
  helpers per the Documentation section.
- Add no subprocess call and no filesystem write.

### 3. Write the interpreter test matrix

- **Task ID**: build-tests
- **Depends On**: build-check
- **Validates**: `tests/unit/test_doctor_console_scripts.py::TestWinningScriptInterpreter` (create)
- **Informed By**: spike-1, spike-3, spike-4; Research (polyglot and relocatable forms)
- **Assigned To**: `doctor-interpreter-builder`
- **Agent Type**: builder
- **Parallel**: false
- Case 1 — control: venv-internal shebang matching the pin → `passed is True`.
- Case 2 — off-pin: fixture venv `version_info` differs from `.python-version` → fails;
  message names the target and both versions.
- Case 3 — missing: `bin/python3` is a broken symlink → fails as missing; message names
  the dangling target. Ordering guard for spike-3.
- Case 4 — outside: shebang points at `/usr/bin/python3` → fails as outside.
- Case 5 — hardlink: `~/.local/bin`-style hardlink whose content shebang is stale →
  flagged, and `fix` contains `/update`.
- Case 6 — polyglot: `#!/bin/sh` line 1 plus an `'''exec'` line 2 naming the venv python
  → passes.
- Case 7 — relocatable: the `dirname $0` variant → passes, resolved against the script's
  own directory.
- Case 8 — env form: `#!/usr/bin/env python3` with the fixture venv leading PATH →
  passes.
- Case 9 — no shebang: a shebang-less file → no finding; the pass message's verified
  count excludes it.
- Case 10 — no pin: `.python-version` absent → off-pin comparison suppressed, but a
  broken-symlink target still fails. The fail-open scoping guard.
- Case 11 — grouping: several scripts sharing one bad target → the target appears once
  with a count, and the finding line count is below the script count.
- Case 12 — mixed: one misresolved name plus one bad-interpreter name → both clauses in
  `message`, both remedies in `fix`.
- Case 13 — distinct remedies: the three reasons produce three different fix sentences.
- Case 14 — degenerate inputs: zero-byte file, bare `#!`, `/bin/sh` with no usable line
  2, shebang with trailing flags → no crash, correct unverified/extracted result.

### 4. Mutation-check every guard

- **Task ID**: validate-mutations
- **Depends On**: build-tests
- **Assigned To**: `doctor-interpreter-validator`
- **Agent Type**: validator
- **Parallel**: false
- For each guard independently — existence test, venv-membership test, pin comparison,
  polyglot line-2 parse, relocatable resolution, `env` resolution, no-shebang skip,
  grouping, `/update` hardlink sentence, per-reason fix distinctness — break it, run the
  suite, and record **which named test** fails. Restore before the next mutation.
- A mutation that leaves the suite green is a missing test, not an acceptable result:
  report it and route back to task 3.
- Re-measure after any change to task 3's tests; a guard verified in an earlier round
  does not stay verified.
- Confirm `scripts/pytest-clean.sh tests/unit/test_doctor_console_scripts.py -q` reports
  the 18 pre-existing tests passing plus the new `TestWinningScriptInterpreter` cases,
  with the pre-existing assertions unmodified from `main`
  (`git diff main -- tests/unit/test_doctor_console_scripts.py` must show only fixture
  changes and additions, no edits to existing `assert` lines).

### 5. Documentation

- **Task ID**: document-feature
- **Depends On**: validate-mutations
- **Assigned To**: `doctor-interpreter-builder`
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/local-doctor.md` per the Documentation section.
- Update the Local Doctor row in `docs/features/README.md`.
- While there, read the new interpreter finding and
  `_check_worktree_interpreters`'s finding side by side and confirm they prescribe one
  consistent remedy (Risk 5).

### 6. Final validation

- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: `doctor-interpreter-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run every row of the Verification table.
- Walk the Success Criteria list and name the evidence for each.
- Confirm the live control (`_check_console_scripts_resolve().passed`) is still `True`
  on this machine.
- Confirm doctor still writes nothing and spawns nothing in this check.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Console-script tests pass | `scripts/pytest-clean.sh tests/unit/test_doctor_console_scripts.py -q` | exit code 0 |
| Pin-guard tests unaffected | `scripts/pytest-clean.sh tests/unit/test_interpreter_pin_guard.py -q` | exit code 0 |
| Interpreter read exists | `grep -c shebang tools/doctor.py` | output > 0 |
| New test matrix exists | `grep -c "class TestWinningScriptInterpreter" tests/unit/test_doctor_console_scripts.py` | output > 0 |
| Healthy machine still passes (live control) | `.venv/bin/python -c "from tools.doctor import _check_console_scripts_resolve as c; print(c().passed)"` | output contains True |
| Pass message discloses verification | `.venv/bin/python -c "from tools.doctor import _check_console_scripts_resolve as c; print(c().message)"` | output contains interpreter |
| Existing assertions unmodified | `.venv/bin/python -c "import subprocess;d=subprocess.run(['git','diff','main','--','tests/unit/test_doctor_console_scripts.py'],capture_output=True,text=True).stdout;print(sum(1 for l in d.splitlines() if l.startswith('-') and 'assert ' in l))"` | match count == 0 |
| Anti-criterion: no subprocess in this check | `.venv/bin/python -c "import re,pathlib;s=pathlib.Path('tools/doctor.py').read_text().splitlines();a=next(i for i,l in enumerate(s) if l.startswith('def _check_console_scripts_resolve'));b=next(i for i,l in enumerate(s[a+1:],a+1) if l.startswith('def '));print(len(re.findall('subprocess','\n'.join(s[a:b]))))"` | match count == 0 |
| Anti-criterion: doctor deletes nothing (#2780 stays out) | `grep -c unlink tools/doctor.py` | match count == 0 |
| Anti-criterion: doctor removes no trees (#2780 stays out) | `grep -c rmtree tools/doctor.py` | match count == 0 |
| Anti-criterion: check inspects no process result (#2749 stays out) | `.venv/bin/python -c "import re,pathlib;s=pathlib.Path('tools/doctor.py').read_text().splitlines();a=next(i for i,l in enumerate(s) if l.startswith('def _check_console_scripts_resolve'));b=next(i for i,l in enumerate(s[a+1:],a+1) if l.startswith('def '));print(len(re.findall('returncode','\n'.join(s[a:b]))))"` | match count == 0 |
| Docs updated | `grep -c interpreter docs/features/local-doctor.md` | output > 0 |
| Lint clean | `python -m ruff check tools/doctor.py tests/unit/test_doctor_console_scripts.py` | exit code 0 |
| Format clean | `python -m ruff format --check tools/doctor.py tests/unit/test_doctor_console_scripts.py` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | History & Consistency | Pass-message requirements are mutually unsatisfiable: "message and fix are byte-identical to today's when there are no interpreter findings" vs. "the pass message discloses how many scripts were interpreter-verified" (task 2) vs. the Verification row requiring `interpreter` in `c().message`. Measured now: the pass message is `26 console scripts resolve into /Users/tomcounsell/src/ai/.venv/bin` with zero interpreter findings, so the first forces that exact string and the other two forbid it. Task 6 deadlocks. | pending | Scope the byte-identical guarantee to the FAILURE strings only (`tools/doctor.py:300-311`) and state plainly that the PASS message gains a trailing interpreter clause. Safe against the suite: the three `passed is True` tests assert substrings, not equality (`tests/unit/test_doctor_console_scripts.py:57-62` asserts `"3 console scripts" in result.message`), so appending `", 3 interpreter-verified"` breaks none of them. |
| CONCERN | Risk & Robustness | spike-2 records that the target "realpath resolves to ~/.local/share/uv/python/cpython-3.14.6-macos-aarch64-none/bin/python3.14", which invites resolving the interpreter target before the membership test. `.venv/bin/python3` IS a symlink here, so `Path(os.path.realpath(target)).parent` lands outside every repo venv bin dir and classifies all 26 healthy scripts as `outside` — Risk 1's worst case arriving on the primary path. | pending | Mirror `tools/doctor.py:242-243` one level up, realpathing BOTH sides: `parent = Path(target).parent` then `any(parent == b or os.path.realpath(parent) == os.path.realpath(b) for b in venv_bins)`. Never compute `Path(os.path.realpath(target)).parent`. Version lookup stays `venv_python_version(parent.parent)` against the UNRESOLVED venv, because `pyvenv.cfg` sits beside `bin/`, not beside the base interpreter. Record "the interpreter target is never realpath-ed before classification" as an anti-criterion. |
| CONCERN | History & Consistency | The plan says "All 20 pre-existing tests" in five places (Success Criteria, Prior Art, Risk 3, task 1 "confirm 20/20 green", task 4) but the file holds 18. `pytest --collect-only -q` reports "18 tests collected", and the plan's own Test Impact enumeration sums to 9 + 4 + 4 + 1 = 18. Task 1 gates the build, before any production change, on a count that cannot be reached. | pending | Replace every "20" with "18" and make the gate self-measuring rather than a literal: `scripts/pytest-clean.sh tests/unit/test_doctor_console_scripts.py -q` must report `18 passed` before task 2 begins, and `18 passed` plus the new `TestWinningScriptInterpreter` cases at task 4. Correct per-class counts are already right in Test Impact. |
| CONCERN | Risk & Robustness | Risk 4's mechanism does not hold. `rm -rf .venv && uv sync` destroys the shared inode, so `_same_file` (`tools/doctor.py:244`) is then False and the stale `~/.local/bin` copy is no longer accepted at all — it falls into the resolution branch, whose remedy at `:281-285` is "Put the repo venv first on PATH", useless for a stale hardlink. The `/update` sentence is attached to the one branch the post-rebuild state can never reach. | pending | Key the `/update` sentence off a rebuild-surviving predicate instead of `_same_file`: emit it when `found_path.parent not in venv_bins` AND `any((b / name).exists() for b in venv_bins)` — a same-named file exists in a repo venv bin dir but is not the file that won. True both before the rebuild (hardlink accepted) and after it (hardlink misresolved). Task 3 case 5 should assert both states with `/update` present in `fix` for each. |
| CONCERN | Scope & Value | spike-1 measured "No `/bin/sh` polyglot and no `env` form present", yet the plan specifies a distlib `'''exec'` parser, a uv relocatable `dirname $0` resolver, and an `env` resolver whose semantics Open Question 1 leaves unresolved — three parsers plus test cases 6-8 for forms that occur nowhere in this fleet, on an `appetite: Small` plan whose stated bottleneck is the false-positive surface. | pending | Extract only the plain-absolute form; classify every other shape as `unverified`, a state the plan already defines as neither pass nor fail. Guard after reading line 1: `if prog is None or not prog.startswith("/") or Path(prog).name in {"sh", "bash", "dash", "env"}: return None`. The interpreter-verified count in the pass message keeps this honest — a relocatable venv reports "0 of 26 verified" rather than a silent green. Removes Open Question 1 and cases 6-8; the parsers can be added later against a real sample. |
| CONCERN | History & Consistency | Update System asserts "`python -m tools.doctor` is already invoked by the existing update flow". It is not: `grep -rn -i doctor scripts/update/ .claude/skills-global/update/` returns only an unrelated comment at `scripts/update/redis_flush_guard_pth.py:184`. The section's conclusion is right but rests on a premise a reviewer falsifies in one command, and it hides the real deployment answer. | pending | Accurate replacement: `/update` sets `core.hooksPath=.githooks` (`scripts/update/git.py`), and `.githooks/pre-push` runs only `tools.push_ancestry_guard` and `scripts/check_issue_disposition.py`, never doctor. The only doctor-running hook is the opt-in `.git/hooks/pre-push` written by `--install-hook`, which `core.hooksPath` overrides on every updated checkout. So the check propagates by `git pull` and is exercised only on a manual `python -m tools.doctor` run. |
| NIT | Risk & Robustness | Risk 1 gives the false-positive blast radius as "gets ignored, taking the genuine findings with it". `_check_console_scripts_resolve` is in `get_checks(quick=True)`, and `--quick` backs the pre-push hook `--install-hook` writes (`tools/doctor.py:1980-2003`, `set -e` plus an explicit `exit 1`), so a false positive refuses every push where that hook is live. Inert on this fleet because `core.hooksPath=.githooks`. | pending | Name the `--quick` / pre-push reachability in Risk 1 so the false-positive appetite is set against the real worst case. |
| NIT | Scope & Value | `docs/features/local-doctor.md` contains zero occurrences of "console", "venv", "resolve", or "entry" — #2665's check was never documented there, and the `docs/features/README.md` Local Doctor row is a generic one-liner. "Document the console-script interpreter verification" therefore adds the second half of a contract whose first half the reader has never met. | pending | Reword the task to "document the console-script check and its two-part contract (resolution + interpreter)" so the doc gains the whole check. |
| NIT | Scope & Value | `grep -c` exits 1 on a zero count, so four Verification rows are exit-code-ambiguous. Confirmed on main: `grep -c unlink tools/doctor.py` and `grep -c rmtree tools/doctor.py` print `0` and exit 1 in their PASSING state, while `grep -c shebang tools/doctor.py` and `grep -c interpreter docs/features/local-doctor.md` print `0` and exit 1 in their FAILING state. A runner keying on exit status cannot tell them apart. | pending | Make the rows exit-code-honest: `! grep -q unlink tools/doctor.py` (exit 0 when absent) and `grep -q shebang tools/doctor.py` (exit 0 when present), so exit status and the Expected column agree. |

---

## Open Questions

1. **Should an `env`-mediated shebang that resolves outside the venv fail, or be
   reported as unverifiable?** The plan currently resolves `#!/usr/bin/env python3`
   through the doctor's own PATH and classifies the result normally, so it can fail as
   `outside`. The argument against: that form's real interpreter depends on the PATH at
   *invocation* time, which doctor cannot know, so a failure is a guess about a
   different process's environment. The argument for: doctor's PATH is the best
   available proxy and silence here would reintroduce the blind spot this issue is
   about. Not present in this repo's venv today (spike-1), so the choice is about
   future-proofing rather than current behavior.

2. **Should the pass message name the verified Python version, or only the count?**
   Naming it (`"26 console scripts resolve into <venv> on Python 3.14"`) makes a
   correct setup self-documenting and would have made the #2858 diagnosis instant. It
   also adds a version string to a line that some tooling may match on. The plan
   currently specifies the count; adding the version is a one-word change if wanted.

3. **Is a shebang-less file inside the venv worth its own finding rather than a silent
   unverified?** `[project.scripts]` always generates a Python shim, so a binary
   winning one of those names is genuinely anomalous. The plan treats it as unverified
   to avoid false positives on exotic-but-valid setups, which means one real anomaly
   would be reported only as a count. Erring the other way risks the false-accusation
   failure mode from Risk 1.
