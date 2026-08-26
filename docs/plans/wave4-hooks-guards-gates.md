---
status: Planning
type: bug
appetite: Large
owner: Valor Engels
created: 2026-08-26
tracking: https://github.com/tomcounsell/ai/issues/2736
last_comment_id: none
covers: [2779, 2736, 3021, 2715]
revision_applied: true
revision_applied_at: 2026-08-26T13:44:00Z
---

# Wave 4 — Hooks, Guards, and Gate Authoring Discipline

## Problem

This repo's PreToolUse hooks are its safety layer. They intercept Bash commands and refuse the
dangerous ones. Four of them are broken in the same structural way: **they match text, not
structure.** A pattern is searched against the raw command string, so the guard cannot tell the
difference between a command that *does* a thing and a sentence that *mentions* it.

Four defects, all reproduced live against `origin/main` @ `fcb597cdc` on 2026-08-26:

| # | Command (tokens split so this document does not trip the live hook) | Actual | Correct |
|---|---|---|---|
| 1 | `gh issue create --body "do not run redis-cli -n 0 FLUSH·DB ever"` | BLOCKS | allow |
| 2 | `echo "REDIS_PRODUCTION_FLUSH_OK=1 is the escape hatch" && redis-cli -n 0 FLUSH·ALL` | **ALLOWS** | block |
| 3 | `git commit -m "revert the pkill -f pytest change"` | BLOCKS | allow |
| 4 | `echo reap-xdist.sh && pkill -f pytest` | **ALLOWS** | block |

Rows 1 and 3 are the false positives the issues were filed for. **Rows 2 and 4 are fail-open
guard bypasses, and neither is filed anywhere.** They are the same defect pointed the other way:
an escape or sanction token is matched by presence anywhere in the line rather than at the
position that governs the dangerous command, so it short-circuits the whole predicate. A guard
that can be talked out of firing by unrelated text in the same line is bypassable by any agent
that narrates what it is doing.

Row 2 is also a **divergence from the documented spec**, not merely an unconsidered case:
`docs/features/redis-flush-hardening.md:85` states "On Layer 2 it works as a command prefix:
`REDIS_PRODUCTION_FLUSH_OK=1 python -c "..."`". The implementation matches it anywhere.

A fifth, separate defect shares the theme at a different substrate.
`validate_no_inline_timeout.py` picks its file list from the diff and then throws the diff away,
rescanning each **whole staged blob**. **55 tracked non-test files** currently carry at least one
flagged literal, so all 55 are frozen: no unrelated edit can be committed to them. The guard has
frozen itself most severely — it cannot be edited without tripping on its own pattern strings.

A sixth defect is about a gate that was never authored at all. `/do-build` mandates the Task tool
and forbids building directly, but nothing verifies the dispatched session has that tool. On
#2701 that produced **four** silent no-op BUILD dispatches, each blamed on "builder agents
produced no output," before a generic oscillation backstop stopped the loop.

**Current behavior:** guards fire on prose, fail to fire on real violations, freeze 55 files
against unrelated edits, and mandate a capability nobody checks for.

**Desired outcome:** a guard's decision is a function of **command structure** — which command is
being run, and what role each piece of text plays in it — not of token presence in a string. A
commit-time guard judges what a commit *changes*, not what its files already contain. And a skill
that requires a capability says so, and fails immediately and specifically when it is absent.

The behavioral consequence is the real stake. An agent or human hitting a block that is
*visibly wrong* — the flagged lines are demonstrably untouched, the flagged command is
demonstrably prose — learns to reach for `git commit --no-verify`. That disables **every** commit
hook, not just the wrong one. A guard that cries wolf trains people to bypass the whole safety
layer, which is how a false positive becomes a security problem.

## Freshness Check

**Baseline commit:** `fcb597cdc` (`origin/main`, fetched 2026-08-26)
**Issues filed at:** #2715 2026-08-10T08:52:19Z · #2736 2026-08-13T03:00:15Z ·
#2779 2026-08-13T09:19:15Z · #3021 2026-08-26T06:07:45Z
**Disposition:** **Minor drift** — all six defects reproduce live; two numeric corrections; one
acceptance criterion in #3021 points at the wrong doc row.

**File:line references re-verified — all still hold:**

- `.claude/hooks/validators/validate_no_redis_flush.py:44-49` — `_BLOCK_PATTERNS` with the
  `redis-cli\s+.*\bFLUSH…\b` shape. Holds.
- `.claude/hooks/validators/validate_no_redis_flush.py:40, :85-86` — `_ESCAPE` matched anywhere
  and short-circuiting before any block pattern. Holds; this is defect row 2.
- `.claude/hooks/validators/validate_no_raw_redis_delete.py:75-79` — `_EXECUTABLE_CONTEXT`.
  Holds.
- `.claude/hooks/validators/validate_no_inline_timeout.py:125-149, :150-167, :169-182, :211-213`
  — whole-blob rescan. Holds; verified by introspection that `find_violations` takes exactly
  `(content, filename)` and the module has no `_staged_added_lines_map`.
- `.claude/hooks/validators/validate_no_broad_process_kill.py:41` — `_SANCTIONED` matched
  anywhere. Holds; this is defect row 4.
- `.claude/skills-global/do-build/SKILL.md:10, :124, :130, :152` — the Task-tool mandate with no
  fallback. Holds.
- `.claude/skills-global/do-sdlc/SKILL.md:372` — every stage subagent pinned to
  `general-purpose`. Holds. **This is #2715's root cause and it was not named in the issue.**

**Corrections to issue claims:**

- **#2779 blast radius is 55, not 65.** Measured by running the validator's own `find_violations`
  over `git ls-files '*.py'` with its own `is_test_file` exclusion. Conclusion unchanged.
- **#3021's last acceptance criterion points at the wrong row.** It says
  "`docs/features/redis-flush-hardening.md`'s **Layer 3** row describes the new matching rule."
  Layer 3 is *documentation* (:70-74); the validator is **Layer 2** (:59-66). The Layer 2 row is
  the one to update. Carried into this plan's Documentation section as Layer 2.

**Cited sibling issues/PRs re-checked:**

- **#3004** — closed, merged as `c1fc86014` ("Delete the Redis ACL layer"). As #3021 predicted,
  this removed observed-failure case 1 (deny-modifier under `ACL SETUSER`) and left case 2
  (documentation/prose) fully live. Verified: the prose false positive still reproduces.
- **#2686** (fixing #2638/#2641) — merged `cc58d564e` 2026-08-13. This is the change that
  *deliberately* left #2736's residual out of scope. Nothing has moved since.
- **#2680** — merged `8bb12c001` 2026-08-13, introduced the flush validator in its current form.
- **#2701** — the incident behind #2715. Still the only recorded occurrence.
- **#2022** — the tool-availability mismatch guard; still parent-observes-child and still keyed
  on Bash absence, not Task absence.
- **#2658** — open. The wave capstone, planned separately at
  `docs/plans/gates-that-cannot-fire.md`. Lands **after** this work by owner instruction.

**Commits on main since the issues were filed (touching referenced files):**

- `ad56700c8` (2026-08-19, "Resolve hook validator targets from the hook payload", #2746) —
  **irrelevant**; verified it does not modify any of the four validators in scope.
- No other commit has touched `validate_no_inline_timeout.py`, `validate_no_raw_redis_delete.py`,
  `validate_no_redis_flush.py`, or `validate_no_broad_process_kill.py` since filing.

**Active plans in `docs/plans/` overlapping this area:** none. `docs/plans/gates-that-cannot-fire.md`
(#2658) is adjacent by design — it is this wave's capstone and consumes these fixes as its first
regression cases — but it changes no file this plan changes.

## Prior Art

- **PR #2686** — "Scope the raw-Redis validator to this repo, and fix its two stale entries
  (#2638, #2641)". Added the two gates (`_guard_applies`, `_EXECUTABLE_CONTEXT`) that *narrowed*
  the prose false positive without closing it. Its own round-1 comment reported the residual and
  deliberately deferred it, correctly: "expanding a hook that gates every Bash call with a
  heredoc-body parser is a design change, not a patch-round fix." That deferral is this plan.
- **PR #2680** — "Harden production Redis against accidental flush (four layers)". Introduced
  `validate_no_redis_flush.py` and the shared `REDIS_PRODUCTION_FLUSH_OK` escape. Documented the
  escape as a *command prefix*; implemented it as an anywhere-match. The gap between those two is
  defect row 2.
- **PR #2746** — "Resolve hook validator targets from the hook payload". Precedent for a
  cross-cutting change to validator input handling; establishes that touching several validators
  in one PR is an accepted shape here.
- **Issue #3004** — "Delete the Redis ACL layer". Explicitly scoped #3021 out and filed it. Its
  own body had to be authored with the `Write` tool and passed via `--body-file` to get past this
  very hook — the defect obstructing the documentation of the defect.

### Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|---|---|---|
| PR #2686 | Added `_EXECUTABLE_CONTEXT`: block only if the command contains an interpreter-shaped token | Asks "does an interpreter appear *anywhere*", never "is that interpreter the thing *consuming this text*". A `gh --body` carrying prose about an interpreter satisfies it. Narrowed the false positive; did not close it. |
| PR #2680 | Anchored flush patterns to `redis-cli\s+.*` | `.*` spans the rest of the line, crossing `&&`, quotes, and heredoc bodies. Anchoring to a token is not anchoring to a position. |
| PR #2680 | Gave both layers one escape hatch | Implemented the Layer 2 escape as an anywhere-match while documenting it as a command prefix, converting the escape into a bypass. |
| #2022 remedies | Detect tool-availability mismatch by inspecting a child's final message | Parent-observes-child and keyed on **Bash** absence. Cannot see a **Task** absence, and cannot fire before the wasted dispatch. |

**Root cause pattern:** every prior fix added a *better regex over the same substrate*. Each
narrowed the false-positive surface without changing what the guard is looking at. The substrate
is the problem: a flat string has no notion of command position, argument role, or "added line."
No further pattern refinement closes this class, which is why this plan replaces the substrate
once and reconciles every consumer against it rather than patching validators individually.

## Research

Purely internal work: stdlib `shlex`, this repo's own hooks, and prior art that already exists in
the tree. No external libraries, APIs, or ecosystem patterns are involved, and the two candidate
third-party parsers were ruled out on dependency grounds before search (see Rabbit Holes).

No relevant external findings — proceeding with codebase context and training data.

## Spike Results

The four spikes below were executed as code-reads and live predicate invocations against
`fcb597cdc` during Phase 1, before the plan was written. All four resolved; none remain open.

### spike-1: Does a shared command-structure helper already exist?
- **Assumption**: "We need to write Bash command-position parsing from scratch."
- **Method**: code-read
- **Finding**: **False.** Two independent implementations already exist.
  `.claude/hooks/hook_utils/destructive_git_shapes.py` does simple-command splitting
  (`_split_simple_commands` :38), argv0 identification with env-assignment skipping
  (`_git_tokens` :47), and `effective_dir` :288 — and its docstring :7-11 already states the goal
  verbatim: "the single place that logic lives so neither validator can drift." Separately,
  `validate_merge_guard.py:461-644` (`_extract_executed_commands`, `_skip_heredoc` :322,
  `_find_dollar_paren_close` :360, `_find_backtick_close` :443) is the only code in the repo that
  isolates heredoc bodies, quoted literals, and `$()`/backtick substitution — trapped inside one
  validator with no shared home. `validate_no_uv_sync_in_worktree.py:64-124` is a third,
  near-verbatim copy of the splitting logic.
- **Confidence**: high
- **Impact on plan**: **revised by round-2 critique.** The donors are references, not building
  blocks. `_extract_executed_commands` answers "which byte ranges are executable text" and drops
  quoted regions entirely, so it cannot supply simple-command boundaries; composing it with
  `_split_simple_commands` reproduces the very fail-open it was meant to close (Decision 2a). The
  seam is therefore a **purpose-built single-pass scanner informed by both donors**, not a lift.
  Risk and size are higher than this spike first concluded — Task 3 is the wave's real work — and
  preserving `validate_merge_guard`'s existing tokenizer contract remains a hard requirement.

### spike-2: Can the fix simply strip quoted string literals?
- **Assumption**: "Quoted text is inert and can be removed before matching."
- **Method**: code-read + live invocation
- **Finding**: **False, and dangerously so.** The house idiom for a *real* violation is
  `.venv/bin/python -c "<raw redis call>"` — the dangerous payload lives **inside** quotes.
  Stripping quoted literals would gut `validate_no_raw_redis_delete` entirely, converting a false
  positive into a total fail-open. The discriminator is not quoting; it is **which command
  consumes the text**. An interpreter's `-c` payload and a heredoc piped into an interpreter are
  code. A heredoc redirected to a file, a `--body`/`--body-file` argument, and an `echo` argument
  are data.
- **Confidence**: high
- **Impact on plan**: the helper's core abstraction is **argument-role classification**, not
  quote-stripping. Recorded in Rabbit Holes so a builder does not rediscover it by shipping it.

### spike-3: Do sibling validators share the whole-blob rescan defect (#2779)?
- **Assumption**: "The `_staged_python_files` + `_staged_content` shape is widespread; the fix
  needs a broad sweep."
- **Method**: code-read
- **Finding**: **False.** Exactly two validators use the shape, and one is **already fixed**.
  `validate_no_module_scope_env.py` has `_staged_added_lines_map()` :207-235 built on
  `git diff --cached -U0 -M`, rename-aware and single-pass, threaded into its `find_violations`
  via a `changed_lines` parameter (:137, :155, :288), with tests at
  `test_validate_no_module_scope_env.py::test_reports_only_changed_lines` :152, empty-set :162,
  `None` :167, and `::test_added_lines_map_is_empty_on_git_failure` :407.
- **Confidence**: high
- **Impact on plan**: #2779 collapses to lift-and-consume. It also **settles the issue's own open
  design question** — "pass `(line_number, text)` pairs" vs. "intersect violation line numbers
  with the added-line set" — in favor of the second, because the working reference implementation
  already does that. Do not re-litigate it.

### spike-4: What actually caused #2715's four no-op dispatches?
- **Assumption**: "`/do-build` needs a runtime self-check for the Task tool."
- **Method**: code-read
- **Finding**: **Partially false, and the real root cause is upstream and unnamed in the issue.**
  `.claude/skills-global/do-sdlc/SKILL.md:372` instructs the supervisor to spawn **every** stage
  subagent as `general-purpose`, BUILD included. `general-purpose` has **no definition in this
  repo** — no `.claude/agents/general-purpose.md`, and absent from
  `agent/agent_definitions.py:119-158` — so its toolset is harness-determined and unverifiable
  from repo config. Meanwhile `do-docs` already solved the identical sibling problem with a
  one-line agent-type pin at `.claude/skills-global/do-docs/SKILL.md:30`. Separately,
  `allowed-tools:` frontmatter is the wrong instrument: per
  `.claude/skills-global/new-skill/SKILL.md:68` it **restricts** which tools a skill may use — a
  permission ceiling, not a requirement floor. Declaring `allowed-tools: Task` would raise no
  error when Task is absent.
- **Confidence**: high
- **Impact on plan**: the primary fix is a **one-line agent-type pin** following the `do-docs`
  precedent, not a new capability-declaration mechanism. A runtime self-check remains as a
  cheap fail-fast backstop. No new frontmatter field is introduced.

## Data Flow

**Seam A — a Bash command reaching a guard decision (today, and after):**

1. **Entry point**: the agent emits a Bash tool call. Claude Code fires the PreToolUse hook
   registered at `.claude/hooks/manifest.toml:102-109` (`matcher = "Bash"`, `timeout = 20`).
2. **Dispatcher**: `.claude/hooks/dispatch/pre_tool_use_bash.py` reads stdin JSON once
   (`read_stdin` :73), extracts `command` and `cwd` (:87-97), and puts both validators'
   directories on `sys.path` (:55-62).
3. **Predicates**: `dispatch` (:215-264) calls ~10 pure predicates in-process, **first-block-wins**.
   Each is fail-open except `validate_merge_guard`, which is fail-closed.
4. **Today**: each predicate runs `re.search(pattern, command)` against the **flat string**. All
   structure — command boundaries, argument roles, heredoc bodies, quoting — is invisible.
5. **After**: each predicate first calls the new `hook_utils/bash_structure.py` to obtain a
   structured view of the command, then matches **only within regions that command structure says
   are executable**, and evaluates escape/sanction tokens **only at the position that governs the
   dangerous command**.
6. **Output**: a block reason string, or `None`. Unchanged contract; every predicate keeps its
   current signature.

**Seam B — a `git commit` reaching the timeout guard:**

1. **Entry point**: agent emits `git commit ...`; same dispatcher.
2. `validate_no_inline_timeout.find_violation_for_command` triggers on `"git commit" in command`.
3. **Today**: `_staged_python_files()` asks git *which files changed*, then `_staged_content()`
   reads each **entire staged blob** via `git show :{path}` and `find_violations` walks all of it.
   The diff is used to pick files and then discarded.
4. **After**: the same file list, plus an added-lines map from `git diff --cached -U0 -M`.
   `find_violations` still parses the whole blob (preserving any context-sensitivity), and its
   results are **intersected** with the added-line set before being reported.
5. **Output**: block reason naming `file:line` of an **added** line only. The direct-invocation
   CLI path (`_run_cli` :246) bypasses steps 3-4 entirely and retains whole-file semantics.

## Architectural Impact

- **New dependencies**: none. `shlex` is stdlib and is already the house tool
  (`destructive_git_shapes.py:29,49,310`, `validate_no_uv_sync_in_worktree.py:60,83,114`). No
  third-party parser is added — see Rabbit Holes.
- **Interface changes**: one new module, `.claude/hooks/hook_utils/bash_structure.py`. Every
  existing predicate keeps its current public signature (`find_violation(command, ...)`), so the
  dispatcher and all existing tests are unaffected at the call boundary.
  `validate_no_inline_timeout.find_violations` gains an optional `changed_lines` parameter,
  defaulting to `None` = whole-file — matching the reference implementation's shape exactly, so
  the CLI path needs no change.
- **Coupling**: **decreases.** Three duplicated implementations of command-splitting collapse to
  one. The `destructive_git_shapes.py` docstring already claims to be that single place; this
  makes the claim true.
- **Data ownership**: unchanged. No new state, no persistence, no Redis, no Popoto models — so
  the Popoto schema-migration requirement in `docs/sdlc/do-plan.md:77-85` does not apply.
- **Reversibility**: high. Each validator's adoption of the helper is an independent commit and
  independently revertible. The helper itself is additive until a consumer imports it.
- **Packaging**: free. `.claude/hooks/` is not a package; importability comes from `sys.path`
  mutation the dispatcher already performs (:55-62). A new `hook_utils/bash_structure.py` drops
  in with zero packaging work.
- **Hot path**: this code runs on **every Bash call the agent makes**. Parse cost is a real
  constraint, not a theoretical one. The manifest's comment block (:74-101) records the
  per-predicate timeout-budget reasoning for the 20s ceiling and is the documented place to
  re-confirm it. A parse-cost measurement is a required verification row.

## Appetite

**Size:** Large

**Team:** Solo dev (orchestrating builder + validator + documentarian subagents), PM, code reviewer

**Interactions:**
- PM check-ins: 2-3 (scope alignment at the seam boundary; the PR-splitting decision; the #2715
  fallback-vs-fail-fast ruling)
- Review rounds: 2+ (this is the safety layer; a fail-open regression here is worse than the bugs
  being fixed)

Large is driven by **blast radius and review burden, not line count.** The helper itself is a
consolidation of code that already exists. But every change lands in the layer that stops
destructive commands, four validators must be reconciled against one seam without regressing
their existing contracts, and one of them (`validate_merge_guard`) is the repo's only fail-closed
guard. The expensive part is proving each guard still fires.

## Prerequisites

| Requirement | Check Command | Purpose |
|---|---|---|
| Repo venv on the pinned interpreter | `python -c "import platform,pathlib,sys; pin=pathlib.Path('.python-version').read_text().strip(); sys.exit(0 if platform.python_version().startswith(pin) else 1)"` | `scripts/pytest-clean.sh` aborts on an off-pin venv. Checks the pin specifically; `tools.doctor` is broader and fails on unrelated Redis index drift. |
| `gh` authenticated | `gh auth status` | Issue/PR operations for the four tracked issues |
| Hook dispatcher importable | `python -c "import sys; sys.path.insert(0, '.claude/hooks'); import hook_utils"` | Confirms the `sys.path` idiom the helper relies on |

Run via `python scripts/check_prerequisites.py docs/plans/wave4-hooks-guards-gates.md`.

## Solution

### Key Elements

- **`hook_utils/bash_structure.py`** (new): the single source of truth for Bash command
  structure. Splits a command into simple commands, identifies argv0 (skipping leading environment
  assignments), classifies each argument's role, and isolates heredoc bodies, quoted literals, and
  command substitutions — reporting for each text region whether it is **executable** (an
  interpreter's `-c` payload, a heredoc piped into an interpreter, a command word) or **inert**
  (a heredoc redirected to a file, a `--body`/`--body-file` argument, an `echo` argument).
  Fails closed on any parse ambiguity, uniformly for every consumer.
- **`hook_utils/staged_diff.py`** (new): the single source of truth for commit-time diff scoping.
  Provides the staged file list and an added-lines map from `git diff --cached -U0 -M`. Lifted
  from `validate_no_module_scope_env.py`, rename-aware.
- **Four reconciled validators**: `validate_no_redis_flush`, `validate_no_raw_redis_delete`,
  `validate_no_broad_process_kill` consume `bash_structure`; `validate_no_inline_timeout` consumes
  `staged_diff`. `validate_merge_guard` donates its tokenizer and consumes the result.
- **A `/do-build` capability pin and fail-fast**: BUILD dispatches name an agent type that carries
  the Task tool, following the existing `do-docs` precedent, plus a first-instruction self-check
  that exits with a specific diagnostic when the tool is absent.

### Flow

**A dangerous command**
`agent emits redis-cli -n 0 <flush>` → dispatcher → `bash_structure` reports argv0 `redis-cli`,
flush name is a bare positional argument → **BLOCK**

**Prose about a dangerous command**
`agent emits gh issue create --body "...<flush>..."` → dispatcher → `bash_structure` reports argv0
`gh`, the token sits inside an inert `--body` argument → **ALLOW**

**A real escape**
`agent emits REDIS_PRODUCTION_FLUSH_OK=1 redis-cli -n 3 <flush>` → `bash_structure` reports the
token as an **environment assignment prefixing this simple command** → escape honored → **ALLOW**

**A fake escape (today's bypass)**
`agent emits echo "REDIS_PRODUCTION_FLUSH_OK=1 ..." && redis-cli -n 0 <flush>` → the token is an
inert `echo` argument in a *different* simple command → escape **not** honored → **BLOCK**

**An unrelated edit to a file with a pre-existing literal**
`agent emits git commit` → `staged_diff` reports added lines → the pre-existing literal is not
among them → **ALLOW**

### Technical Approach

**Decision 1 — one helper, adopted incrementally.** Build `bash_structure.py` first with its own
exhaustive test suite, then adopt it one validator per commit. Each adoption commit must leave
that validator's existing test file fully green. This keeps every step revertible and makes a
regression attributable to one validator rather than to "the refactor."

**Decision 2 — argument-role classification is the abstraction.** Per spike-2, the discriminator
is which command consumes the text, never whether the text is quoted. The helper's public surface
should let a caller ask "is offset N inside an executable region?" rather than handing back a
pre-stripped string, because pre-stripping loses the offsets that produce accurate `file:line`
and block messages.

**Decision 2a — the two donors are strictly ordered, and this ordering is load-bearing.**
`destructive_git_shapes._split_simple_commands` (:38-44) is a bare `_CONTROL_SPLIT_RE.split()`
(:32) with **zero quote-awareness**. Applied to a raw command it severs a dangerous argv0 from its
dangerous argument whenever an *earlier quoted argument* contains a control operator. Reproduced
against the live tree:

```
redis-cli -n 0 --pattern "a;b" <flush>
  today (flat regex):            BLOCK
  naive per-fragment position check:  ALLOW   <-- a NEW fail-open
```

The splitter yields `redis-cli -n 0 --pattern "a` and `b" <flush>`; no fragment carries both the
`redis-cli` argv0 and the flush token, so a per-fragment check passes a real flush. **That is the
exact regression class this wave exists to close**, manufactured by the fix.

**The donors cannot be composed as black boxes — this was round 2's blocker.** The previous
revision proposed running `_extract_executed_commands` first for "safe spans" and splitting only
within each. That does not work, and it fails this plan's own regression row.
`_extract_executed_commands` does not emit one span per simple command: it calls `flush()` on
entering a quote and resets `cmd_start` only after the closing quote, so a quoted argument **drops
out and leaves two disjoint spans** even when no control operator separates them. Reproduced
against the live tree:

```
redis-cli -n 0 --pattern "a;b" <flush>
  spans:     (0,25) 'redis-cli -n 0 --pattern '   (31,38) '<flush>'
  fragments: ['redis-cli -n 0 --pattern', '<flush>']
  => argv0 and the flush token land in different fragments: ALLOW  <-- still fail-open
```

The quoted region becomes a *gap*, when correctness requires it to be an *opaque token inside one
simple command*. The scanner answers "which byte ranges are executable text," not "where are the
simple-command boundaries." Those are different questions, and only the second one supports an
argv0-to-argument relationship check.

**Therefore: write a single-pass scanner in `bash_structure.py` that tracks quote, heredoc, and
substitution state _while_ building simple-command boundaries.** Reuse the donors' *logic* —
merge_guard's quote/heredoc/substitution state machine, and `destructive_git_shapes`'s
env-assignment-skipping argv0 identification — but call neither as a black box.

The invariant, stated in the module docstring and named in a verification row: **a quoted or
heredoc region is a token within its simple command, never a boundary between simple commands;
only an unquoted control operator starts a new simple command.**

This is the plan's single largest correction, and it revises spike-1's conclusion: the seam is a
purpose-built scanner *informed by* two donors, not a composition *of* them. Budget accordingly —
Task 3 is the wave's real work. Both boundary classes (quoted-argument and control-operator) are
named regression tests written **before** any consumer is wired.

**Decision 3 — one ambiguity posture: fail closed, everywhere.** The two donors disagree today —
`destructive_git_shapes._git_tokens` returns `None` on a `shlex` ValueError (fails **open**,
:50-51); `validate_merge_guard` fails **closed** (:505, :663-673) with tests pinning it
(`::test_tokenizer_failure_fails_closed` :109, `::test_tokenizer_empty_span_fails_closed` :120).
An earlier draft of this plan made the posture a parameter. It is not: **every consumer in scope
resolves to fail-closed**, so a parameter would encode a distinction no call site uses and would
offer a future author a fail-open switch on the safety layer for no benefit. The helper fails
closed unconditionally — an unparseable command is treated as executable (so the guard still
fires) and does *not* receive the escape. Both point the same direction: when in doubt, block.
That is the correct default for a destructive-command guard and it is what closes rows 2 and 4.
If a consumer ever genuinely needs fail-open, that is a deliberate change with its own review, not
a pre-installed lever.

**Decision 4 — escape and sanction tokens are position-anchored, and this is the fix for rows 2
and 4.** `REDIS_PRODUCTION_FLUSH_OK=1` is honored only as an environment assignment prefixing the
same simple command that carries the flush. `validate_no_broad_process_kill._SANCTIONED` is
honored only when the sanctioned script is the argv0 of the simple command being judged. This
aligns the implementation with `docs/features/redis-flush-hardening.md:85`, which already
specifies "command prefix."

**Decision 5 — #2779 follows the existing reference implementation exactly.** Lift
`_staged_added_lines_map` and its `_git` helper out of `validate_no_module_scope_env.py` into
`hook_utils/staged_diff.py`, have both callers consume it, and delete the copy. Keep the
whole-file parse in `find_violations` and intersect its results with the added-line set. Fix the
`--diff-filter=ACM` rename gap (`R` omitted) **in the shared helper**, closing it for both callers
at once — `validate_no_module_scope_env.py:187-196` documents that gap as a bug in its own
comments.

**Decision 6 — the CLI/hook split is a hard contract.** `_run_cli` (:246) is the lint/audit mode
and must retain whole-file semantics; only the commit-hook path scopes to added lines. This is an
acceptance criterion in #2779 and gets its own verification row.

**Decision 7 — moved lines count as added.** If a commit relocates an existing literal, the moved
line appears as `+` in the diff and will block. This is deliberate: the alternative requires
content-matching across hunks, and a relocated bare timeout literal is a reasonable thing to ask
an author to look at. #2779 asks for this to be decided rather than discovered; it is decided.

**Decision 8 — #2715 is fixed by an agent-type pin naming `builder`, not a new mechanism.** Per
spike-4, pin `/do-build`'s stage dispatch to a **Task-capable agent type, concretely `builder`**
(`.claude/agents/builder.md:11` declares `tools: ['*']`; `agent/agent_definitions.py:131-139`
gives it `tools=None`, inheriting all parent tools), and correct
`.claude/skills-global/do-sdlc/SKILL.md:372` so BUILD is not spawned as `general-purpose`.

**Correction to an earlier draft:** this plan previously cited `do-docs/SKILL.md:30` as the
precedent. It is only a *partial* one and must not be copied verbatim. That pin reads "spawn them
ONLY on a Bash-capable agent type — `documentarian` **or** `general-purpose`" — it is about
**Bash** capability and it explicitly sanctions `general-purpose`, the very type spike-4 shows is
undefined in this repo and unverifiable from config. Copying its shape would re-authorize the
cause of #2715 and would contradict this plan's own verification row. Borrow the *form* (a
one-line agent-type pin in the skill body, citing its issue) and **not** the type list.

**Decision 8a — the pinned type is a new `build-orchestrator`, decided here rather than at build
time.** Round 2 correctly refused the earlier `builder` answer. Two capability axes must both be
satisfied, and no existing agent type satisfies both:

| Axis | Requirement | `general-purpose` | `builder` |
|---|---|---|---|
| Task-tool presence | must have `Task`/`TaskCreate` | undefined in this repo, unverifiable | yes (`builder.md:11`, `tools: ['*']`) |
| Orchestration permission | must be allowed to deploy subagents | n/a | **no** — `builder.md:28`: "Do NOT spawn other agents or coordinate work. You are a worker, not a manager." |

Surveyed every definition in `.claude/agents/`: all Task-capable types either forbid orchestration
(`builder`) or are role-scoped to something else. `dev` is the only all-tools type without an
orchestration prohibition, but it is the session-level SDLC owner, not a stage subagent, and
pinning a stage to it would confuse the two roles.

So: **create `.claude/agents/build-orchestrator.md`** — `tools: ['*']`, explicitly permitted to
deploy builder/validator subagents, and explicitly forbidden from editing files directly, mirroring
`/do-build`'s own contract at `SKILL.md:10` and `:151`. This is one new file, it makes the skill's
requirement and the agent's contract agree for the first time, and it avoids the alternative of
silently overriding a documented agent contract. It carries its own verification row on the
orchestration-permission axis, mirroring the existing row on the Task-tool axis.

`builder.md:28` is left **unchanged** — it is correct for builders, and weakening it to make an
orchestrator fit would erode the separation `/do-build` depends on.

Do **not** introduce a "required tools" frontmatter field: `allowed-tools:` is a ceiling, not a
floor, and a new field would need a checker, a schema, and adoption across ~28 skills — a
disproportionate mechanism for a problem a one-line pin solves.

**Decision 9 — #2715 gets fail-fast, not a sequential fallback.** Ruled in favor of fail-fast.
The issue records that prompt-level permission to self-execute was already tried on #2701 and the
child halted anyway, correctly, because `SKILL.md:10` outranks a prompt. A fallback would
therefore have to live in the skill body — which means weakening the orchestrator/builder
separation that keeps the orchestrator's CWD out of worktrees (`SKILL.md:159`) in order to
tolerate a misconfiguration the pin already prevents. Fail-fast with a specific, machine-readable
diagnostic is strictly better: it makes the dispatch bug visible on attempt one instead of
absorbing it. *(Flagged in Open Questions for PM ratification, since the issue poses it as open.)*

**Decision 10 — every guard touched gets a two-pole proof.** #2658 lands immediately after this
work and will consume these four fixes as its first regression cases. Each guard change must be
demonstrated **red** against a genuine violation and **green** against the allowed case, with both
directions present in the Verification table. The four defects reproduced in the Problem section
are ready-made red-state proofs and are used verbatim as rows.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Audit every `except` block in the four touched validators and in the new helper. The
      dispatcher is fail-open by default (`dispatch` :215-264), so a helper that raises silently
      converts a guard into a no-op — the exact failure class this wave exists to fix.
- [ ] Each `except` path in `bash_structure.py` must have a test asserting observable behavior:
      which posture was applied (fail-open vs fail-closed) and that the caller's decision matches.
- [ ] Preserve `validate_merge_guard`'s fail-closed-on-tokenizer-exception contract; its existing
      tests (`::test_tokenizer_failure_fails_closed` :109, `::test_tokenizer_empty_span_fails_closed`
      :120) must stay green **unmodified**. If they need modification, that is a signal the move
      changed semantics.
- [ ] Assert the dispatcher still fails open for the three Redis/kill validators and fails closed
      for `validate_merge_guard` — `tests/unit/test_pre_tool_use_dispatcher.py` already covers
      this ordering and must stay green.

### Empty/Invalid Input Handling
- [ ] `bash_structure` must handle: empty string, `None`, whitespace-only, unterminated quote,
      unterminated heredoc, nested `$()`, backticks inside quotes, and a command that is only
      environment assignments. Each gets an explicit test with the posture-appropriate outcome.
- [ ] `staged_diff` must return an empty map (not raise, not `None`-crash) when `git` fails, is
      absent, or the index is empty — `test_validate_no_module_scope_env.py::test_added_lines_map_is_empty_on_git_failure`
      :407 is the existing precedent to mirror.
- [ ] Confirm every predicate still returns `None` (allow) for empty/`None` command input, which
      is today's documented behavior for all of them.

### Error State Rendering
- [ ] Block messages must keep their current `file:line` and suggested-fix formats. #2779's
      acceptance criteria require the same message shape. #3021's related criterion is that the
      `REDIS_PRODUCTION_FLUSH_OK=1` escape **still short-circuits first** — verify ordering, and
      confirm the escape the block message quotes is the escape that actually works now that it is
      position-anchored. (An earlier draft of this plan attributed a broader "escape hint"
      criterion to #3021 that the issue does not contain.)
- [ ] Test that a block message for an added timeout literal names the **added** line, not a
      pre-existing one in the same file.
- [ ] `/do-build`'s new fail-fast diagnostic must name the missing capability explicitly and be
      machine-readable, so the router can distinguish it from a genuine build failure. Test the
      diagnostic string, not just the exit path.

## Test Impact

- [ ] `tests/unit/test_validate_no_redis_flush.py` — UPDATE: add allow-cases for `gh --body`,
      heredoc bodies, and a token past a `&&`; add the **fail-open regression** (escape mentioned
      in an `echo`, real flush after `&&`, must block). Existing 10 allow-cases stay green.
- [ ] `tests/unit/test_validate_no_raw_redis_delete.py` — UPDATE: keep
      `::test_prose_without_an_interpreter_is_allowed` :150 green, and add the harder cases it
      does not cover — a heredoc-to-doc-file and a `gh --body` that **do** contain an interpreter
      token. Note the existing heredoc case (:132-146) passes today only because its body carries
      no interpreter token; after the fix it must pass for the *right* reason, so add an
      interpreter token to a sibling case.
- [ ] `tests/unit/test_validate_no_broad_process_kill.py` — UPDATE: add the prose false-positive
      case and the `_SANCTIONED` fail-open regression.
- [ ] `tests/unit/test_validate_no_inline_timeout.py` — UPDATE: add added-lines-only tests
      mirroring `test_validate_no_module_scope_env.py::test_reports_only_changed_lines` :152; add
      an explicit test that `_run_cli` retains whole-file semantics.
- [ ] `tests/unit/test_validate_no_module_scope_env.py` — UPDATE: repoint its `_staged_added_lines_map`
      tests at the shared helper after the copy is deleted. Behavior must not change.
- [ ] `tests/unit/test_validate_merge_guard.py` — UPDATE (imports only, ideally zero): its Part 1
      tokenizer suite must stay green. Any assertion change here is a regression signal, not a
      test update.
- [ ] `tests/unit/test_pre_tool_use_dispatcher.py` — UPDATE if registration changes.
      `::test_redis_flush_validator_is_registered_after_broad_process_kill` :381 and
      `::test_manifest_declares_single_dispatcher_entry_for_bash` :333 constrain the refactor;
      prefer keeping registration order identical so neither needs touching.
- [ ] `tests/unit/test_validate_no_uv_sync_in_worktree.py` — UPDATE only if its duplicated
      splitting logic (:64-124) is deduped onto the helper. **Optional**; see No-Gos.
- [ ] `tests/unit/test_sdlc_fork_no_background.py` — UPDATE: add a static assertion that BUILD's
      dispatch template names a Task-capable agent type. It currently asserts
      `run_in_background: false` only and is the natural host.
- [ ] `tests/unit/hooks/` + `tests/unit/test_hook_manifest.py` — VERIFY: no change expected;
      re-run to confirm the manifest timeout budget still holds after the parse-cost measurement.
- [ ] NEW: `tests/unit/test_bash_structure.py` — the helper's own exhaustive suite, including
      every case in Empty/Invalid Input Handling above.
- [ ] NEW: `tests/unit/test_staged_diff.py` — the diff-scoping helper's suite.

No xfail/xpass markers exist anywhere in `tests/` related to these validators (searched
`pytest.mark.xfail` and runtime `pytest.xfail(`), so there are no expected-failure markers to
convert.

## Rabbit Holes

- **Writing a real Bash parser.** The goal is to distinguish executable positions from inert data
  for a bounded set of shapes, not to be `bash`. `shlex` plus the merge_guard scanner already
  covers everything the four validators need. Resist generalizing to process substitution,
  arithmetic expansion, or arrays.
- **Adding `bashlex` or `tree-sitter`.** Ruled out before search: this runs on the hot path of
  every Bash call under `.claude/hooks/hook_python`, a pinned interpreter shim that is not
  necessarily the project venv. A new runtime dependency there is a machine-provisioning problem
  disguised as a parsing improvement. `shlex` is stdlib and already the house tool.
- **Stripping quoted string literals.** Per spike-2 this converts a false positive into a total
  fail-open, because the house idiom for a real violation puts the payload inside quotes. Named
  here because it is the obvious first idea and it is actively dangerous.
- **Converting all ten Bash predicates to the helper.** Only four are in scope. The remainder
  (`validate_design_system_sync`, `validate_issue_recon`, the two destructive-git validators,
  `validate_no_uv_sync_in_worktree`) either already anchor on position or have a different defect.
  Name the consumer set and stop.
- **Rewriting `validate_commit_message`.** Its `has_co_author_trailer` (:78) scans the entire raw
  command **deliberately**, with the reasoning recorded at :100-101, because a co-author trailer
  smuggled anywhere into a commit invocation is a merge blocker. Its `extract_commit_message`
  (:52-76) *is* a weak 4-regex quote scraper with no heredoc support despite a docstring claiming
  otherwise — a genuine defect, but a separate one about message extraction, not about position
  matching. Out of scope; see No-Gos.
- **Annotating the 55 frozen files with `# timeout-guard: allow`.** Explicitly rejected in #2779.
  It edits 55 files to work around a scan bug and permanently marks unreviewed literals as
  reviewed.
- **Fixing G4.** The oscillation guard lives in `agent/sdlc_router.py`, which Wave 2 owns. #2715
  is fixed by never producing the oscillation, not by changing how it is counted.

## Risks

### Risk 1: A fail-open regression in the safety layer
**Impact:** The worst outcome available here. Every one of these guards exists because something
destructive already happened — production Redis was wiped twice (2026-06-03 unrecoverable,
2026-08-07 25,825 keys). A refactor that makes a guard *stop firing* is worse than the false
positives it fixes, and would not necessarily be noticed.
**Mitigation:** Decision 3 sets fail-closed unconditionally, with no posture lever to get this
wrong later. Decision 2a orders the two donors so the quote-blind splitter can never see a raw
command — critique found this exact regression in the first draft, where a real flush carrying a
quoted `;` would have been let through; it is now a named regression test and two verification
rows. Decision 10 requires a two-pole proof per guard. Every validator's block
direction gets an explicit verification row, and the adoption is one commit per validator so a
bisect names the culprit. The two fail-open bypasses found during recon become permanent
regression tests — this wave leaves the layer measurably tighter than it found it, not merely
less noisy.

### Risk 2: `validate_merge_guard`'s tokenizer changes behavior when moved
**Impact:** It is the repo's only fail-closed Bash validator and it gates merges. A subtle
semantic change during the lift could either block legitimate merges or, worse, stop blocking
illegitimate ones.
**Mitigation:** Move the code verbatim first with zero behavioral edits, prove the existing Part 1
tokenizer suite green **without modifying a single assertion**, and only then generalize. Any
required assertion change is treated as a stop-and-report signal, not a test update.

### Risk 3: Parse cost on the hot path
**Impact:** The helper runs on every Bash call the agent makes, inside a 20s dispatcher budget
shared by ~10 predicates. A slow parse degrades every agent action.
**Mitigation:** Parse once per dispatch and share the result across predicates rather than each
predicate re-parsing. A parse-cost measurement is a required verification row, and the manifest
comment block (:74-101) is updated to re-confirm the budget.

### Risk 4: `.claude/skills-global/do-sdlc/SKILL.md` is a hot shared file
**Impact:** #2715's root cause is at its line 372, and other lanes touch this file. A broad edit
invites a merge conflict or a cross-lane collision.
**Mitigation:** Keep the edit surgical — the agent-type pin and nothing else. Do not reflow,
reorder, or restructure surrounding sections.

### Risk 5: Skills-global hardlink breakage
**Impact:** `.claude/skills-global/` is hardlinked to `~/.claude/skills/`. A Write/Edit
replace-and-rename breaks the hardlink, silently leaving the live skill on pre-edit text.
**Mitigation:** A PostToolUse relink hook auto-repairs this. Named here so a builder does not
misdiagnose it as a lost edit. Verify with an inode check after editing any `skills-global` file.

### Risk 6: The PR is large enough to review badly
**Impact:** Four issues, one new module, four validators, and a skill change is a lot of diff for
the layer where review matters most. A rubber-stamped review here defeats the purpose.
**Mitigation:** Strict commit hygiene — helper first with its own tests, then one commit per
validator adoption, then the skill pin — so the PR reads as a sequence of independently
reviewable steps. Splitting into multiple PRs is raised in Open Questions for the PM.

## Race Conditions

No race conditions identified. Every component in scope is synchronous and single-threaded: the
hook dispatcher reads stdin once and calls pure predicates in-process (`pre_tool_use_bash.py:73,
:215-264`), and the new helpers are pure functions over a command string plus, for
`staged_diff.py`, a `git` subprocess read.

One adjacent hazard is worth naming as a non-race so it is not mistaken for one: `staged_diff.py`
shells out to `git diff --cached`, which reads the index. If the index changed between the hook
firing and the read, the guard would judge a different commit than the one being made. This is
not reachable here — the PreToolUse hook runs *before* the `git commit` it is inspecting, and
nothing else mutates the index in that window, since the agent is blocked awaiting the hook's
decision. It becomes reachable only if a future change moves this logic to a post-commit surface.
Recorded so that change is made deliberately.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #3030] **Nested agents escaping supervision, and supervisor reports that
  contradict the branch.** #2715 names three defects; this plan fixes (1) dispatch/capability
  mismatch and (2) fail-fast diagnosis. Defect (3) — a nested build agent wrote six commits its
  supervisor reported as zero — is a supervision-boundary and report-integrity problem in a
  different subsystem, and a correctly-tooled build can still hit it. Filed as #3030, carrying
  #2715's acceptance criteria 4 and 5. Those two criteria should be struck from #2715 when #3030
  is picked up so neither issue claims to own them.
- [SEPARATE-SLUG #2658] **The general form: guards nobody proved could fail.** The two-pole-proof
  requirement for verification rows, guards, and skill self-checks is this wave's capstone, planned
  at `docs/plans/gates-that-cannot-fire.md` and landing **after** this work by owner instruction,
  so these four fixes become its first real regression cases rather than hypotheticals.
- [SEPARATE-SLUG #2658] **`validate_commit_message.extract_commit_message`'s weak quote scraping.**
  Its 4-regex scraper (:52-76) has no heredoc support despite a docstring claiming
  `-m "$(cat <<'EOF'...)"` support — a real defect, but about message *extraction*, not command
  position, and its `has_co_author_trailer` whole-command scan (:78) is deliberate per its own
  :100-101 reasoning. It belongs to the capstone's guard audit, not to this seam.

**Deliberately in scope despite not appearing in the wave list:**
`validate_no_broad_process_kill.py`. It is not named in `docs/bug-backlog-waves.md`'s Wave 4
table, but recon reproduced both a prose false positive and a `_SANCTIONED` fail-open bypass in
it, from the identical root cause. Fixing the seam and leaving a known-bypassable consumer
unreconciled would be indefensible, so it is scoped in with that evidence attached.

## Update System

One update-system consequence, handled by existing machinery. Otherwise repo-internal: hook validators, a new
`hook_utils` module, and skill bodies. No new dependencies, config files, secrets, or migration
steps.

Three propagation notes, all handled by existing machinery and needing no new work:

- `.claude/skills-global/do-build/SKILL.md` and `.claude/skills-global/do-sdlc/SKILL.md` are
  hardlinked to `~/.claude/skills/` by `/update` (`scripts/update/hardlinks.py`). Edits propagate
  on the next `/update` with no registration step, since no directory is added or renamed.
- **`.claude/agents/build-orchestrator.md` (new, Decision 8a) is hardlinked to `~/.claude/agents/`
  by `/update`** (`scripts/update/hardlinks.py:439`, `_sync_commands` over `.claude/agents`).
  Dropping the file in is the entire registration step — no manifest entry, no code change. Two
  consequences to honor: it is a **general-purpose subagent shared to every machine**, per that
  file's :434-438 comment, so it must not carry anything repo-specific; and because it is a *new*
  file rather than a rename, no `RENAMED_REMOVALS` entry is needed. A machine that has not yet run
  `/update` will not have the type, so the `/do-build` fail-fast diagnostic (Task 7) must name the
  missing agent type clearly rather than failing obscurely.
- Hook registration is generated from `.claude/hooks/manifest.toml`. No new hook is registered —
  `bash_structure.py` and `staged_diff.py` are library modules under `hook_utils/`, not hooks —
  so the `hooks` blocks in `.claude/settings.json` and `~/.claude/settings.json` are untouched.
  If the parse-cost measurement shows the 20s budget needs revisiting, that is a manifest edit
  regenerated through the normal path, never a hand-edit.

## Agent Integration

No agent integration required. This work changes the hook layer that already sits in front of the
agent's existing Bash tool, plus two skill bodies the agent already invokes. No new CLI entry
point in `pyproject.toml [project.scripts]`, no new MCP surface, and no bridge import.

The one agent-visible behavior change is intended and is the point of the wave: the agent will be
able to author documentation, issues, and runbooks that discuss destructive commands using Bash,
without reaching for `--body-file` as a workaround. `/do-build`'s fail-fast diagnostic is
consumed by the SDLC router, which already reads stage outcomes; it introduces no new surface.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/bash-command-position-matching.md` describing the shared seam: what
      "executable position" means, the executable-vs-inert region rules, the ambiguity posture and
      why it is fail-closed, and the consumer list. This is the doc a future guard author reads
      before writing a Bash-matching rule.
- [ ] Add `bash-command-position-matching` to the `docs/features/README.md` index table (note:
      `.claude/hooks/validators/validate_features_readme_sort.py` enforces ordering).
- [ ] Update `docs/features/redis-flush-hardening.md` **Layer 2** row (:37-41 table, prose
      :59-66) to describe the new matching rule. **Note:** #3021's acceptance criterion says
      "Layer 3"; that is an off-by-one — Layer 3 is documentation (:70-74), the validator is
      Layer 2. Also correct the escape description at :85 if the implementation's new behavior
      needs restating; the doc's "command prefix" wording is already correct and the code is what
      changes to match it.
- [ ] Update `docs/features/raw-redis-guard.md` to describe gate 2's replacement by argument-role
      classification, superseding the `_EXECUTABLE_CONTEXT` description.
- [ ] Update `docs/features/hook-manifest.md` if the timeout budget commentary changes.

### Inline Documentation
- [ ] `bash_structure.py` module docstring: the executable-vs-inert rule table; the
      fail-closed-unconditionally policy and why it applies identically to every consumer; the
      boundary invariant (a quoted or heredoc region is a token within its simple command, never a
      boundary between them) stating that it always precedes any operator split and is never
      applied to a raw command; and an explicit note that quote-stripping is wrong and why
      (spike-2), so the trap is documented where the next author will be standing.
- [ ] `staged_diff.py` module docstring: the CLI-whole-file vs hook-added-lines split, and the
      rename-gap fix.
- [ ] Update `destructive_git_shapes.py`'s docstring (:7-11), whose "single place that logic
      lives" claim becomes true only after this work.
- [ ] Record the two fail-open bypasses (rows 2 and 4) in the relevant validators' docstrings as
      the reason position-anchoring is load-bearing, so a future simplification does not undo it.

### CLAUDE.md
- [ ] Update the § Manual Testing Hygiene raw-Redis paragraph, which currently describes gate 2 in
      terms of "the Bash call's cwd" and the executable-context heuristic. Keep the edit tight —
      `## Work Completion Criteria` is regex-parsed into worker system prompts and asserted
      byte-for-byte by fixtures; do not disturb neighboring headings.

## Success Criteria

- [ ] All four defects from the Problem table behave correctly: rows 1 and 3 allow, rows 2 and 4
      block. Each is a permanent regression test.
- [ ] A commit touching a file with a pre-existing bare timeout literal, without adding one, is
      allowed; a commit that adds one is still blocked and names the **added** line.
- [ ] `# timeout-guard: allow` still suppresses an added line.
- [ ] `validate_no_inline_timeout`'s CLI path retains whole-file semantics.
- [ ] The three `# timeout-guard: allow` annotations added under duress to `agent/sdk_client.py`
      (~:1254/1291/1298, commit `b30cc732e`) are reviewed and dispositioned deliberately — kept as
      genuine local one-offs or promoted to `settings.timeouts`, with the reasoning recorded.
- [ ] Exactly one implementation of Bash command-position parsing exists in
      `.claude/hooks/`; the duplicated copies are deleted, not left alongside.
- [ ] **No new fail-open is introduced by the seam.** Specifically: a real flush whose earlier
      quoted argument contains a control operator (`redis-cli -n 0 --pattern "a;b" <flush>`) still
      blocks, and `echo "<cmd>" | sh` stays in scope. Both are named regression tests.
- [ ] **A quoted argument is a token inside its simple command, not a boundary between two.**
      `redis-cli -n 0 --pattern "a;b" <flush>` still blocks — the round-2 blocker, and the case a
      donor-composition approach silently fails. Both boundary classes (quoted-argument and
      control-operator) are named regression tests.
- [ ] No configurable fail-open ambiguity lever exists on the helper.
- [ ] `.claude/agents/build-orchestrator.md` exists, is Task-capable, and carries no
      "do not spawn / not a manager" prohibition; `builder.md:28` is unchanged.
- [ ] Non-BUILD SDLC stages keep their existing dispatch agent type (no collateral change from the
      #2715 fix).
- [ ] `validate_merge_guard`'s Part 1 tokenizer suite passes with **zero assertion changes**.
- [ ] A `/do-build` dispatch to a session lacking the Task tool fails on the first attempt with a
      diagnostic naming the missing capability.
- [ ] BUILD dispatches name an agent type that carries the Task tool.
- [ ] Parse cost measured and the manifest's 20s budget re-confirmed.
- [ ] Every guard changed carries a two-pole proof (red and green) in the Verification table,
      ready for #2658 to consume.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER
builds directly — they deploy team members and coordinate.

**Sequencing is owner-mandated and non-negotiable:** #2779 first, then #2736 + #3021 together on
the shared seam, then #2715. #2658 is a separate lane that lands after this one.

Note the two ironies to steer by rather than trip over: this plan's own build is executed by
`/do-build`, whose capability gap is task 8; and several tasks below edit the very hooks that
gate the commits making those edits. Builders should expect to need the `Write`-then-`--body-file`
idiom for issue/PR text until task 4 lands.

### Team Members

- **Builder (staged-diff seam)**
  - Name: `staged-diff-builder`
  - Role: `hook_utils/staged_diff.py` and the `validate_no_inline_timeout` fix (#2779)
  - Agent Type: builder
  - Resume: true

- **Builder (bash-structure seam)**
  - Name: `bash-structure-builder`
  - Role: `hook_utils/bash_structure.py` — the shared position-matching helper and its test suite
  - Agent Type: builder
  - Domain: security/untrusted-input
  - Resume: true

- **Builder (validator reconciliation)**
  - Name: `validator-reconciler`
  - Role: adopt the helper in the three Redis/kill validators (#2736, #3021, + the scoped-in kill guard)
  - Agent Type: builder
  - Domain: security/untrusted-input
  - Resume: true

- **Builder (build-dispatch capability)**
  - Name: `dispatch-capability-builder`
  - Role: the `/do-build` agent-type pin and fail-fast diagnostic (#2715)
  - Agent Type: builder
  - Resume: true

- **Test engineer (two-pole proofs)**
  - Name: `two-pole-tester`
  - Role: red-then-green proofs for every guard changed; the four reproduced defects as regression tests
  - Agent Type: test-engineer
  - Resume: true

- **Validator (safety layer)**
  - Name: `guard-validator`
  - Role: verify no guard fails open; verify merge_guard's contract is byte-for-byte preserved
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `wave4-documentarian`
  - Role: feature docs, the Layer 2 correction, CLAUDE.md, inline docstrings
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Extract the staged-diff helper
- **Task ID**: build-staged-diff
- **Depends On**: none
- **Validates**: tests/unit/test_staged_diff.py (create), tests/unit/test_validate_no_module_scope_env.py
- **Informed By**: spike-3 (a rename-aware reference implementation already exists)
- **Assigned To**: staged-diff-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/hooks/hook_utils/staged_diff.py` by lifting `_git` and `_staged_added_lines_map`
  from `validate_no_module_scope_env.py:207-235` **verbatim** in the first commit.
- Fix the `--diff-filter=ACM` rename gap (add `R`) in the shared helper; the gap is documented as
  a bug at `validate_no_module_scope_env.py:187-196`.
- Repoint `validate_no_module_scope_env.py` at the helper and delete its local copy. Its existing
  tests must stay green with no assertion changes.
- Write `tests/unit/test_staged_diff.py` covering git-failure, empty-index, and rename cases.

### 2. Scope the timeout guard to added lines (#2779)
- **Task ID**: build-2779
- **Depends On**: build-staged-diff
- **Validates**: tests/unit/test_validate_no_inline_timeout.py
- **Informed By**: spike-3 (settles the issue's open design question in favor of line intersection)
- **Assigned To**: staged-diff-builder
- **Agent Type**: builder
- **Parallel**: false
- Give `find_violations` an optional `changed_lines` parameter defaulting to `None` = whole file,
  mirroring `validate_no_module_scope_env.py`'s shape exactly.
- Intersect violation line numbers with the added-line set on the commit-hook path only. Keep the
  whole-blob parse; do not feed hunks in.
- Leave `_run_cli` (:246) on whole-file semantics — Decision 6, and an acceptance criterion.
- Add tests: unrelated edit to a file with a pre-existing literal → allowed; added literal →
  blocked naming the added line; `# timeout-guard: allow` still suppresses an added line; CLI
  retains whole-file behavior.
- Review and disposition the three `# timeout-guard: allow` annotations in `agent/sdk_client.py`
  (~:1254/1291/1298 from `b30cc732e`) — keep or promote to `settings.timeouts`, deliberately, and
  record the reasoning in the commit message.

### 3. Build the shared position-matching helper
- **Task ID**: build-bash-structure
- **Depends On**: none
- **Validates**: tests/unit/test_bash_structure.py (create), tests/unit/test_validate_merge_guard.py
- **Informed By**: spike-1 (two donor implementations exist), spike-2 (quote-stripping is wrong)
- **Assigned To**: bash-structure-builder
- **Agent Type**: builder
- **Domain**: security/untrusted-input
- **Parallel**: true
- Create `.claude/hooks/hook_utils/bash_structure.py`. Move `validate_merge_guard.py:461-644`'s
  tokenizer (`_extract_executed_commands`, `_skip_heredoc`, `_find_dollar_paren_close`,
  `_find_backtick_close`) **verbatim** in the first commit; prove `test_validate_merge_guard.py`
  Part 1 green with zero assertion changes before generalizing.
- Fold in `destructive_git_shapes.py`'s `_split_simple_commands` (:38) and argv0 identification
  with env-assignment skipping (`_git_tokens` :47).
- **Write a single-pass scanner per Decision 2a.** Do not compose the two donors as black boxes —
  round 2 proved that still fails open. Track quote/heredoc/substitution state *while* building
  simple-command boundaries, so a quoted argument is an opaque token **inside** its simple command
  rather than a gap between two. Reuse merge_guard's state machine and `destructive_git_shapes`'s
  env-assignment-skipping argv0 logic as references, not as calls.
- Add named regression tests for **both** boundary classes, written before any consumer is wired
  and assembled by string concatenation so the test file does not trip the live hook:
  `redis-cli -n 0 --pattern "a;b" <flush>` (quoted-argument boundary) and
  `echo "hi" && redis-cli -n 0 <flush>` (control-operator boundary). Both must BLOCK.
- Add argument-role classification: for each text region, report **executable** (command word,
  interpreter `-c` payload, heredoc piped to an interpreter) or **inert** (heredoc redirected to a
  file, `--body`/`--body-file` argument). Expose offsets, not a pre-stripped string, so callers
  keep accurate positions for block messages.
- **`echo`/`printf` arguments are inert only when not piped into an interpreter.** Apply the same
  piped-vs-redirected rule used for heredocs: `echo "<cmd>"` is data, but `echo "<cmd>" | sh` and
  `printf '%s' "<cmd>" | bash` execute their argument and must stay in scope. An earlier draft
  treated `echo` arguments as unconditionally inert, which would have been a fail-open.
- Fail closed unconditionally on any parse ambiguity (Decision 3). Do **not** add a
  configurable ambiguity parameter; document in the docstring why ambiguity always resolves to
  fail-closed and why that is uniform across consumers.
- Do **not** strip quoted literals (spike-2). Document why in the docstring.
- Write `tests/unit/test_bash_structure.py` covering every case in Empty/Invalid Input Handling.

### 4. Reconcile the three Bash guards against the seam (#2736, #3021, + kill guard)
- **Task ID**: build-reconcile-validators
- **Depends On**: build-bash-structure
- **Validates**: tests/unit/test_validate_no_raw_redis_delete.py, tests/unit/test_validate_no_redis_flush.py, tests/unit/test_validate_no_broad_process_kill.py, tests/unit/test_pre_tool_use_dispatcher.py
- **Informed By**: spike-2 (argument-role classification, not quote-stripping)
- **Assigned To**: validator-reconciler
- **Agent Type**: builder
- **Domain**: security/untrusted-input
- **Parallel**: false
- **One commit per validator**, so a bisect names the culprit.
- `validate_no_redis_flush`: match a flush only as a bare positional argument of a `redis-cli`
  argv0, plus the `.flushdb(`/`.flushall(` call shapes inside executable regions. Anchor `_ESCAPE`
  to an environment assignment prefixing the *same simple command* — this closes defect row 2 and
  aligns the code with `docs/features/redis-flush-hardening.md:85`.
- `validate_no_raw_redis_delete`: replace `_EXECUTABLE_CONTEXT`'s anywhere-match with the helper's
  executable-region query. Keep gate 1 (`_guard_applies`) and `_POPOTO_CONTEXT` unchanged.
- `validate_no_broad_process_kill`: anchor `_SANCTIONED` to the argv0 of the simple command being
  judged — closes defect row 4 — and scope block patterns to executable regions.
- Preserve every predicate's public signature and the dispatcher's registration order so
  `test_pre_tool_use_dispatcher.py` needs no changes.

### 5. Two-pole proofs for every guard changed
- **Task ID**: build-two-pole-proofs
- **Depends On**: build-2779, build-reconcile-validators
- **Validates**: all four validator test files
- **Informed By**: Decision 10; #2658 consumes these as its first regression cases
- **Assigned To**: two-pole-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- For each of the four guards, demonstrate **red** against a genuine violation and **green**
  against the allowed case; paste the red-state output into the PR body.
- Turn the four reproduced defects from the Problem table into permanent regression tests,
  assembling command fragments so the test files do not trip the live hooks (house idiom, see
  `test_validate_no_raw_redis_delete.py` docstring :7-10).
- Measure parse cost on the dispatcher hot path and re-confirm the manifest's 20s budget;
  update the comment block at `.claude/hooks/manifest.toml:74-101` with the measurement.

### 6. Validate the safety layer did not weaken
- **Task ID**: validate-guards
- **Depends On**: build-two-pole-proofs
- **Assigned To**: guard-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify no guard fails open: for each, confirm the block direction still fires on a genuine
  violation, including under unparseable/ambiguous input (fail-closed posture).
- Verify `validate_merge_guard`'s Part 1 suite passes with zero assertion changes; treat any
  required change as a stop-and-report.
- Confirm exactly one implementation of command-position parsing remains under `.claude/hooks/`.
- Confirm the dispatcher's fail-open/fail-closed split is unchanged.

### 7. Pin BUILD to a Task-capable agent type and fail fast (#2715)
- **Task ID**: build-2715
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_fork_no_background.py
- **Informed By**: spike-4 (root cause is do-sdlc SKILL.md:372; do-docs:30 is the precedent)
- **Assigned To**: dispatch-capability-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/agents/build-orchestrator.md` per Decision 8a: `tools: ['*']`, explicitly
  permitted to deploy builder/validator subagents, explicitly forbidden from editing files
  directly. Do **not** modify `builder.md:28`.
- Add an agent-type pin to `.claude/skills-global/do-build/SKILL.md` naming `build-orchestrator`.
  Borrow the *form* of `do-docs/SKILL.md:30` (a one-line pin citing its issue) but **not** its type
  list — that line sanctions `general-purpose`, which is the cause of #2715 (Decision 8).
- **`do-sdlc/SKILL.md:372` is the shared dispatch template for all seven stages**, not a BUILD-only
  line — it sits under "### 5c. Spawn the stage subagent" (`:363`). Diverting BUILD alone therefore
  requires **stage-conditional logic**, analogous to the existing Stage→Model table: add a
  Stage→AgentType mapping whose BUILD row is `build-orchestrator` and whose other six rows preserve
  today's behavior. "Surgical" means *narrowly scoped*, not *one line* — do not reflow or reorder
  surrounding sections (Risk 4), but do expect a small structural addition.
- Leave `do-sdlc/SKILL.md:451` (the #2022 re-dispatch fallback) alone unless BUILD can reach it; if
  it can, route BUILD's fallback to `build-orchestrator` too. Check this explicitly — the
  verification rows do not cover `:451`.
- Two verification rows guard this: one asserting BUILD no longer resolves to `general-purpose`,
  and one **positive assertion** that non-BUILD stages still do — so stripping `general-purpose`
  repo-wide (which would silently change six other stages) fails the gate.
- Add a first-instruction self-check to `/do-build`: if the Task tool is absent, exit immediately
  with a specific, machine-readable diagnostic naming the missing capability. Fail-fast, no
  sequential fallback (Decision 9).
- Correct `WORKFLOW.md:137`'s zero-commit abort message so it no longer attributes a tooling
  failure to "builder agents produced no output."
- Add a static assertion to `tests/unit/test_sdlc_fork_no_background.py` that BUILD's dispatch
  template names a Task-capable agent type.
- Do **not** introduce a required-tools frontmatter field (Decision 8). Do **not** touch
  `agent/sdlc_router.py` (Wave 2 owns it).
- After editing any `skills-global` file, verify the hardlink survived (Risk 5).

### 8. Documentation
- **Task ID**: document-wave4
- **Depends On**: validate-guards, build-2715
- **Assigned To**: wave4-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/bash-command-position-matching.md` and add it to the
  `docs/features/README.md` index in sorted position.
- Update `docs/features/redis-flush-hardening.md` **Layer 2** (not Layer 3 — see Documentation).
- Update `docs/features/raw-redis-guard.md` and, if the budget commentary changed,
  `docs/features/hook-manifest.md`.
- Update the CLAUDE.md § Manual Testing Hygiene raw-Redis paragraph; keep the edit tight and do
  not disturb neighboring headings.
- Write the module docstrings specified in the Documentation section, including the
  quote-stripping trap and the two fail-open bypasses.

### 9. Final validation
- **Task ID**: validate-all
- **Depends On**: build-staged-diff, build-2779, build-bash-structure, build-reconcile-validators, build-two-pole-proofs, validate-guards, build-2715, document-wave4
- **Assigned To**: guard-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification row.
- Confirm all Success Criteria.
- Confirm the two-pole proofs are recorded in the PR body for #2658 to consume.
- The `agent/sdk_client.py` annotation disposition (task 2) is **reported, not gated**: #2779 asks
  for a deliberate decision, and either outcome — kept as one-offs or promoted to
  `settings.timeouts` — closes it. Surface the choice and its reasoning; do not block on it.
- Generate the final report.

## Verification

Where a command would otherwise contain a token that trips a live hook, the row builds that token
by string concatenation (`'FLUSH'+'DB'`) so the check is executable while the plan document itself
stays committable. This is the same idiom the existing validator tests use
(`test_validate_no_raw_redis_delete.py` docstring :7-10). The `·` separator used in the Problem
table above is prose-only and never appears in an executable row.

| Check | Command | Expected |
|-------|---------|----------|
| Targeted tests pass | `scripts/pytest-clean.sh tests/unit/test_bash_structure.py tests/unit/test_staged_diff.py tests/unit/test_validate_no_redis_flush.py tests/unit/test_validate_no_raw_redis_delete.py tests/unit/test_validate_no_broad_process_kill.py tests/unit/test_validate_no_inline_timeout.py tests/unit/test_validate_no_module_scope_env.py tests/unit/test_validate_merge_guard.py tests/unit/test_pre_tool_use_dispatcher.py tests/unit/test_sdlc_fork_no_background.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .claude/hooks/` | exit code 0 |
| Format clean | `python -m ruff format --check .claude/hooks/` | exit code 0 |
| Helper module exists | `test -f .claude/hooks/hook_utils/bash_structure.py` | exit code 0 |
| Staged-diff helper exists | `test -f .claude/hooks/hook_utils/staged_diff.py` | exit code 0 |
| RED PROOF row 1 — prose no longer blocks | `python -c "import sys;sys.path.insert(0,'.claude/hooks/validators');import validate_no_redis_flush as f;w='FLUSH'+'DB';print('ALLOW' if f.find_violation('gh issue create --body \"never run redis-cli -n 0 '+w+'\"') is None else 'BLOCK')"` | output contains ALLOW |
| RED PROOF row 2 — escape-mention bypass closed | `python -c "import sys;sys.path.insert(0,'.claude/hooks/validators');import validate_no_redis_flush as f;w='FLUSH'+'ALL';e='REDIS_PRODUCTION'+'_FLUSH_OK=1';print('BLOCK' if f.find_violation('echo \"'+e+' is the escape\" && redis-cli -n 0 '+w) else 'ALLOW')"` | output contains BLOCK |
| GREEN — real flush still blocked | `python -c "import sys;sys.path.insert(0,'.claude/hooks/validators');import validate_no_redis_flush as f;w='FLUSH'+'ALL';print('BLOCK' if f.find_violation('redis-cli -n 0 '+w) else 'ALLOW')"` | output contains BLOCK |
| GREEN — real escape still honored | `python -c "import sys;sys.path.insert(0,'.claude/hooks/validators');import validate_no_redis_flush as f;w='FLUSH'+'DB';e='REDIS_PRODUCTION'+'_FLUSH_OK=1';print('ALLOW' if f.find_violation(e+' redis-cli -n 3 '+w) is None else 'BLOCK')"` | output contains ALLOW |
| RED PROOF row 3 — kill-guard prose no longer blocks | `python -c "import sys;sys.path.insert(0,'.claude/hooks/validators');import validate_no_broad_process_kill as k;print('ALLOW' if k.find_violation('git commit -m \"revert the pkill -f pytest change\"') is None else 'BLOCK')"` | output contains ALLOW |
| RED PROOF row 4 — sanctioned-mention bypass closed | `python -c "import sys;sys.path.insert(0,'.claude/hooks/validators');import validate_no_broad_process_kill as k;print('BLOCK' if k.find_violation('echo reap-xdist.sh && pkill -f pytest') else 'ALLOW')"` | output contains BLOCK |
| GREEN — broad kill still blocked | `python -c "import sys;sys.path.insert(0,'.claude/hooks/validators');import validate_no_broad_process_kill as k;print('BLOCK' if k.find_violation('pkill -f pytest') else 'ALLOW')"` | output contains BLOCK |
| RED PROOF — raw-Redis prose with interpreter no longer blocks | `python -c "import sys,os;sys.path.insert(0,'.claude/hooks/validators');import validate_no_raw_redis_delete as m;p='.venv/bin/py'+'thon';c='r.de'+'lete(';print('ALLOW' if m.find_violation('gh pr comment 1 --body \"never run '+p+' -c '+c+'AgentSession)\"',os.getcwd()) is None else 'BLOCK')"` | output contains ALLOW |
| GREEN — raw-Redis call still blocked | `python -c "import sys,os;sys.path.insert(0,'.claude/hooks/validators');import validate_no_raw_redis_delete as m;p='.venv/bin/py'+'thon';c='r.de'+'lete(';print('BLOCK' if m.find_violation(p+' -c \"'+c+'\\'AgentSession:1\\')\"',os.getcwd()) else 'ALLOW')"` | output contains BLOCK |
| #2779 — no file frozen by pre-existing literals | `python -c "import sys,subprocess;sys.path.insert(0,'.claude/hooks/validators');import validate_no_inline_timeout as t;fs=[f for f in subprocess.run(['git','ls-files','*.py'],capture_output=True,text=True).stdout.split() if not t.is_test_file(f)];print(sum(1 for f in fs if t.find_violations(open(f).read(),f,changed_lines=set())))"` | output contains 0 |
| #2779 — CLI retains whole-file semantics | `python -c "import sys;sys.path.insert(0,'.claude/hooks/validators');import validate_no_inline_timeout as t;import inspect;p=inspect.signature(t.find_violations).parameters;print('OK' if p['changed_lines'].default is None else 'BAD')"` | output contains OK |
| Exactly one command-splitter remains | `grep -rln "_split_simple_commands" .claude/hooks --include=*.py \| grep -v __pycache__ \| wc -l` | output contains 1 |
| merge_guard tokenizer contract intact | `git diff origin/main -- tests/unit/test_validate_merge_guard.py \| grep -c "^-.*assert"` | match count == 0 |
| Anti-criterion — router untouched (Wave 2 owns it) | `git diff --name-only origin/main \| grep -c "agent/sdlc_router.py"` | match count == 0 |
| Anti-criterion — no required-tools frontmatter invented | `grep -rc "required-tools" .claude/skills-global/ 2>/dev/null \| grep -v ":0" \| wc -l` | output contains 0 |
| Anti-criterion — the 55 files were not mass-annotated | `git diff --name-only origin/main \| xargs -I{} sh -c 'git diff origin/main -- {} \| grep -c "^+.*timeout-guard: allow"' \| paste -sd+ \| bc` | output contains 0 |
| BLOCKER regression — quoted control operator still blocks | `python -c "import sys;sys.path.insert(0,'.claude/hooks/validators');import validate_no_redis_flush as f;w='FLUSH'+'DB';print('BLOCK' if f.find_violation('redis-cli -n 0 --pattern \"a;b\" '+w) else 'ALLOW')"` | output contains BLOCK |
| BLOCKER regression — echo piped to a shell stays in scope | `python -c "import sys;sys.path.insert(0,'.claude/hooks/validators');import validate_no_redis_flush as f;w='FLUSH'+'ALL';print('BLOCK' if f.find_violation('echo \"redis-cli -n 0 '+w+'\" \| sh') else 'ALLOW')"` | output contains BLOCK |
| Boundary invariant is documented, not just imported | `grep -ci "never a boundary\|always precedes\|never.*raw command" .claude/hooks/hook_utils/bash_structure.py` | output > 0 |
| No fail-open ambiguity lever was installed | `grep -rc "posture" .claude/hooks/hook_utils/bash_structure.py` | match count == 0 |
| BLOCKER r2 regression — quoted arg is a token, not a boundary | `python -c "import sys;sys.path.insert(0,'.claude/hooks');from hook_utils import bash_structure as b;w='FLUSH'+'DB';cmds=b.simple_commands('redis-cli -n 0 --pattern \"a;b\" '+w);print('OK' if any(c.argv0=='redis-cli' and w in ' '.join(c.args).upper() for c in cmds) else 'SPLIT')"` | output contains OK |
| #2715 — BUILD names a Task-capable agent type | `grep -c "general-purpose" .claude/skills-global/do-build/SKILL.md` | match count == 0 |
| #2715 root cause — do-sdlc BUILD dispatch no longer resolves to general-purpose | `python -c "t=open('.claude/skills-global/do-sdlc/SKILL.md').read();m=[l for l in t.splitlines() if 'general-purpose' in l and 'Agent tool' in l];print('FIXED' if not m else 'STILL PINNED')"` | output contains FIXED |
| #2715 — non-BUILD stages keep their existing dispatch type (no collateral change) | `python -c "t=open('.claude/skills-global/do-sdlc/SKILL.md').read();print('OK' if 'general-purpose' in t else 'OVERREACHED')"` | output contains OK |
| #2715 — pinned agent type is permitted to orchestrate | `python -c "import glob,re;f=[p for p in glob.glob('.claude/agents/*.md') if 'build' in p and 'orchestrat' in open(p).read().lower()];print('OK' if f and not any(re.search(r'do NOT spawn\|not a manager',open(p).read(),re.I) for p in f) else 'MISSING')"` | output contains OK |
| Feature doc exists and is indexed | `test -f docs/features/bash-command-position-matching.md && grep -c "bash-command-position-matching" docs/features/README.md` | output > 0 |
| features README still sorted | `python .claude/hooks/validators/validate_features_readme_sort.py` | exit code 0 |
| No co-author trailers | `git log origin/main..HEAD --format=%B \| grep -ci "co-authored-by"` | match count == 0 |

### Red-state proof (recorded at plan time, 2026-08-26 @ `fcb597cdc`)

Per Decision 10, the guard rows above were executed against the **unfixed** tree before this plan
was committed, to prove they are capable of firing. This is the non-check table referred to in the
`## Verification` spec and is skipped by the runner.

| Row | Expected after fix | Actual today (unfixed) | Verdict |
|---|---|---|---|
| RED row 1 — flush prose no longer blocks | ALLOW | BLOCK | fails today, as required |
| RED row 2 — escape-mention bypass closed | BLOCK | ALLOW | fails today, as required |
| RED row 3 — kill-guard prose no longer blocks | ALLOW | BLOCK | fails today, as required |
| RED row 4 — sanctioned-mention bypass closed | BLOCK | ALLOW | fails today, as required |
| RED — raw-Redis prose no longer blocks | ALLOW | BLOCK | fails today, as required |
| GREEN — real flush still blocked | BLOCK | BLOCK | passes today, baseline held |
| GREEN — real escape still honored | ALLOW | ALLOW | passes today, baseline held |
| GREEN — broad kill still blocked | BLOCK | BLOCK | passes today, baseline held |
| GREEN — raw-Redis call still blocked | BLOCK | BLOCK | passes today, baseline held |

**5/5 red rows fail today and 4/4 green rows pass today.** Neither pole is vacuous: the red rows
will flip only if the fix actually lands, and the green rows will flip only if the fix breaks a
guard. Reproduce with the rows in the table above.

One incidental finding worth recording, because it is the wave's thesis in miniature: these rows
could not be executed directly from a Bash call. The kill-guard blocks its own test command, since
the command text contains the literal it matches on. The proof had to be run from a script file
written with the `Write` tool — the same `--body-file` workaround #3004 needed, and the same one
#2736 documents. The defect obstructs its own verification.

## Critique Results

**Round 3 (3 critics, FULL depth): 2 blockers, 1 concern, 1 nit.** Round 2's blocker and its 3 concerns + 1 nit were re-verified by the critics who raised them and are genuinely closed; they are not re-listed. Round 1's 8 findings remain closed. Both round-3 blockers were independently reproduced against the live tree by the critique driver before being accepted.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness (driver-reproduced) | **Command-wrapper and subshell prefixes defeat the new argv0-exact-match rule, creating a NEW fail-open that today's flat regex does not have.** Task 4 (:986-989) pins `validate_no_redis_flush` to matching a flush "only as a bare positional argument of a `redis-cli` argv0", and Decision 2a's argv0 identification (borrowed from `destructive_git_shapes._git_tokens` :47-61) skips only *environment assignments*. Reproduced live: today `redis-cli -n 0 FLUSHALL`, `sudo redis-cli ...`, `(redis-cli ... )`, `nohup redis-cli ... &`, and `env redis-cli ...` **all BLOCK**; under the stated rule `shlex.split` yields argv0 = `sudo` / `(redis-cli` / `nohup` / `env` respectively, so all four wrapper forms would **ALLOW**. That is a regression relative to current behavior in the exact direction Risk 1 calls "the worst outcome available". The plan mentions `sudo`, `nohup`, and subshell grouping **zero times** across all 1205 lines. | pending | Argv0 resolution must peel a *wrapper allowlist* before the exact-match compare, not just env assignments: strip a leading `sudo` (and its flags), `env` (and its `VAR=val` args), `nohup`, `command`, `exec`, `time`, `xargs`, and strip leading/trailing `(`/`)` from subshell grouping -- then compare. Equivalently, state the test as "the flush token is a positional argument of a simple command whose *effective* argv0 is `redis-cli`". Add the five reproduced wrapper forms as named regression rows in the Verification table (all must BLOCK) and extend the "No new fail-open is introduced by the seam" criterion (:819-825), which currently names only two cases. The same peel applies to `validate_no_broad_process_kill`'s argv0-anchored `_SANCTIONED` check (Task 4, :992): a wrapper-prefixed broad kill must still block. |
| BLOCKER | History & Consistency (driver-reproduced, elevated from CONCERN) | **The "exactly one implementation" Success Criterion and its Verification row are unsatisfiable inside the plan's own declared scope.** Success Criteria :817-818 requires "Exactly one implementation of Bash command-position parsing exists in `.claude/hooks/`; the duplicated copies are deleted", Architectural Impact :289-291 claims "Three duplicated implementations of command-splitting collapse to one", and Verification row :1119 gates on the `_split_simple_commands` file count being 1. But Rabbit Holes :624-627 explicitly puts `validate_no_uv_sync_in_worktree` and the two destructive-git validators **out of scope**, and Test Impact :596-597 marks that dedup "**Optional**". No task step repoints or deletes `destructive_git_shapes.py`'s own `_split_simple_commands`. Measured live: that grep returns **4** files today (`destructive_git_shapes.py`, `validate_no_uv_sync_in_worktree.py`, `validate_no_destructive_git_in_worktree.py`, `validate_no_destructive_git_in_shared_checkout.py`). A correct in-scope build therefore leaves the count at 4 and **fails the gate**; the only ways to pass are to expand scope into three more safety-layer validators (contradicting the No-Gos) or to quietly drop the row. | pending | Pick one and make all four sites agree. Recommended: keep the No-Gos as written and retarget the criterion to the four in-scope consumers -- replace row :1119 with a row asserting the symbol actually being moved has exactly one definition site (`grep -rln "def _extract_executed_commands" .claude/hooks --include=*.py` == 1), since the 3 other `_split_simple_commands` occurrences are out-of-scope by design. If the full dedup IS wanted, promote it from "Optional" to a numbered task with its own commit, move `test_validate_no_uv_sync_in_worktree.py` and the two destructive-git test files into Test Impact as required rather than optional, and widen the Appetite and roster accordingly. Either way, delete or correct the "three duplicated implementations collapse to one" claim at :289-291. |
| CONCERN | Risk & Robustness (driver-confirmed) | **The parse-cost measurement is declared "a required verification row" four times but no such row exists.** :302 ("A parse-cost measurement is a required verification row"), :669 (Risk 3 mitigation), :835 (Success Criterion "Parse cost measured and the manifest's 20s budget re-confirmed"), and :1010 (Task 5 bullet) all assert the obligation; the Verification table (:1101-1136) contains **no** row that measures or gates on timing -- confirmed by grepping the table for `perf_counter`, `parse cost`, `measure`, `budget`, and `20s` (zero hits). Task 9 (`validate-all`, "Run every Verification row") therefore has nothing to run for it, so the build can go fully green with the hot-path obligation silently unmet. This is the same "stated mandatory, not gated" shape round 2 fixed for the invariant row. | pending | Add an executable Verification row with a concrete ceiling, e.g. time 1000 `bash_structure` parses of a representative command with `time.perf_counter()` and print `OK` below a named threshold, `SLOW <ms>` above it. Derive the threshold as per-call budget times ~10 predicates against the manifest's 20s dispatcher ceiling, name it as a provisional env-overridable constant, and have Task 5 write the *measured* number into the `.claude/hooks/manifest.toml:74-101` comment block rather than leaving "re-confirmed" unquantified. |
| NIT | History & Consistency (driver-confirmed) | Task 7 (:1055) says to correct "`WORKFLOW.md:137`". There is no `WORKFLOW.md` at the repo root; the only one in the tree is `.claude/skills-global/do-build/WORKFLOW.md`, and the "builder agents produced no output" string is at **line 139**, not 137. A builder following the citation literally looks in the wrong place. | pending | n/a (NIT) |

---

## Open Questions

1. **One PR or two? (recommendation changed after critique.)** The plan is one document by wave
   convention, but critique confirmed **#2715 is fully independent** — `Depends On: none`, zero
   file overlap with tasks 1-6 (it touches only `skills-global/` and a test; the rest touches only
   `.claude/hooks/`). Bundling it works against this plan's own Risk 6. **Recommended: two PRs** —
   one for the hook/guard work (tasks 1-6, 8-9) and one for #2715 (task 7), landing in either
   order. The guard PR keeps strict commit hygiene (helper first, then one commit per validator
   adoption) so it still reads as a sequence. Confirm, or override back to one PR.

2. **Ratify Decision 9 (fail-fast, no sequential fallback for `/do-build`).** #2715 poses this as
   an explicit open question. This plan rules for fail-fast, because a fallback would have to live
   in the skill body and would weaken the orchestrator/builder separation in order to tolerate a
   misconfiguration the agent-type pin already prevents. Confirm.

3. **Confirm the scope addition of `validate_no_broad_process_kill.py`.** It is not in
   `docs/bug-backlog-waves.md`'s Wave 4 table, but it carries the identical defect in both
   directions, including a reproduced fail-open bypass. Fixing the seam and leaving a
   known-bypassable consumer unreconciled seems indefensible, but it is a scope expansion the
   owner did not authorize. Confirm or defer it to #2658.

4. **Should the two fail-open bypasses (rows 2 and 4) be filed as their own issue?** They are
   unfiled security defects discovered during recon, currently documented only in this plan and in
   #3021's recon summary. Filing them would create a citable record independent of this plan's
   fate; not filing them keeps the issue tracker leaner since they are fixed here. Recommend
   filing if there is any chance this lane is deferred.
