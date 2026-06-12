---
status: Ready
type: chore
appetite: Small
owner: Valor
created: 2026-06-12
tracking: https://github.com/tomcounsell/ai/issues/1620
last_comment_id: IC_kwDOEYGa088AAAABFuB7ZA
revision_applied: true
---

# valor CLI Follow-ups (Post-Cutover)

## Problem

The `valor` CLI wrapper (`tools/valor_cli.py`, documented in `docs/features/valor-cli-wrapper.md`) shipped with the granite PTY production cutover (#1572). Its feature doc lists nine rough edges; #1619/#1624 fixed the three production-blocking ones. Four lower-priority paper cuts remain, deliberately deferred until the cutover merged (it has, via PR #1612):

1. **`--help` after a positional prompt shows the wrong help text.** `valor "fix the bug" --help` prints `agent-session` sub-help, not top-level help.
2. **The feature doc frames "PTY path implicit" as a shortcoming** when it is actually a correct CLI/worker boundary.
3. **The #1288 worktree-bound commit guard has no sanctioned path** for a human operator working deliberately in the main checkout.
4. **The shortcut-rewrite allowlist (`KNOWN_SUBCOMMANDS`) is a hand-maintained literal** that duplicates the subparser declarations.

**Current behavior:** documented in the feature doc's "Where It Falls Short" section (current numbering: §2 allowlist, §4 help, §5 PTY-implicit, §6 #1288 guard).

**Desired outcome:** help is reachable and shows the right text from any argv shape; the feature doc states the CLI/worker boundary accurately; the #1288 guard has a decided, documented answer for the human-operator-in-main-checkout case; the allowlist is derived from the subparser registry, not maintained by hand.

## Freshness Check

**Baseline commit:** `8f2bffde6fcbbdcf201eb8771baafaa7a6089618`
**Issue filed at:** 2026-06-11T02:24:24Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `tools/valor_cli.py:44` — `KNOWN_SUBCOMMANDS` literal set — **still holds.** Consulted in `main()` (line 254) before argparse runs.
- `tools/valor_cli.py:248-264` `main()` shortcut rewrite — **still holds.** Line 254: prepends `agent-session` when `argv[0]` is not a flag and not in `KNOWN_SUBCOMMANDS`.
- `.githooks/pre-commit` Phase 0.5 (lines 24-58) — #1288 guard — **still holds exactly as described.**
- `docs/features/valor-cli-wrapper.md` "Where It Falls Short" — **drifted (renumbered).** Issue cites "items 2, 4, 5, 6"; the doc now numbers allowlist §2, help §4, PTY-implicit §5, #1288 guard §6 (§1/§3 marked "fixed" from #1619). Plan targets current numbering.
- **Revised claim — help (item 1):** `valor "prompt" --help` does NOT nearly create a session. Verified: `valor_cli.main(['fix the bug','--help'])` raises `SystemExit(0)` (argparse `-h` on the `agent-session` subparser fires) before reaching `_run`. **No session is created.** The real gap is narrower: wrong help text (sub vs top-level), not an accidental create.
- **Revised claim — item 4 drift test:** `tests/unit/test_valor_cli.py:43` (`test_known_subcommands_matches_parser`) already asserts `KNOWN_SUBCOMMANDS == _parser_subcommands()` on main. The upstream-change-notice comment claimed no wrapper drift test existed — that is outdated. The derived-allowlist fix converts this test, it does not need to author a brand-new one.
- **Revised claim — item 2 force-legacy hatch:** `agent/session_executor.py:1514` states "All session types route to the granite PTY container." Post-cutover there is **no legacy substrate** to force. The escape hatch has nothing to switch to. Item 2 collapses to a pure doc-reframe.

**Cited sibling issues/PRs re-checked:**
- #1572 (granite PTY cutover) — **closed 2026-06-11**, merged via PR #1612. Blocker cleared.
- #1619 (CLI hardening) — **closed 2026-06-11**, merged via PR #1624. Prerequisite met.
- #887 / #1288 (worktree isolation lineage) — context only; guard unchanged.

**Commits on main since issue was filed (touching referenced files):** None touched `tools/valor_cli.py` or `.githooks/pre-commit` after the issue was filed. (The wrapper landed via #1612 just before; nothing has modified it since.)

**Active plans in `docs/plans/` overlapping this area:** `valor-cli-hardening.md` — the **predecessor** (#1619), already shipped. No active overlap.

**Notes:** The two "Revised" findings reshape scope: item 1's acceptance criterion "creates no session" is already satisfied; item 2 ships zero code. This makes the plan smaller than the issue implies.

## Prior Art

- **#1619 / PR #1624** — "valor CLI hardening": fixed alias-shadow, worker pre-flight false-negative, and added the wrapper's first test file (`tests/unit/test_valor_cli.py`, including the allowlist drift test). **Succeeded.** This plan extends that test file.
- **#1572 / PR #1612** — "Granite PTY Production Cutover": removed the legacy execution path; all sessions now route through the granite PTY container. **Succeeded.** Directly informs item 2 (no legacy path exists to force).
- **#1570 / PR #1570** — granite operator PoC (PTY-driven interactive Claude Code). Context for the substrate the wrapper enqueues onto.

No prior attempts at items 1-4 specifically — they were carved out of the #1619 scope and deferred. This is first-attempt work.

## Data Flow

The change is isolated to argv pre-processing and a git hook — no multi-component data flow. Skipped.

## Architectural Impact

- **New dependencies**: None.
- **Interface changes**: None to the public CLI contract. `valor "prompt" --help` changes which help text prints (top-level vs sub), a strict improvement. The derived allowlist changes the *source* of `KNOWN_SUBCOMMANDS` (computed at import from the registry) but not its observable membership.
- **Coupling**: Item 4 *reduces* coupling — the allowlist stops duplicating the subparser declarations and derives from them, removing the parallel-maintenance failure mode.
- **Data ownership**: Unchanged.
- **Reversibility**: Fully reversible. Each item is an independent, small diff.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0-1 (the #1288 guard policy is now decided — option (a))
- Review rounds: 1

The coding is trivial (two small wrapper edits, one hook edit, one doc rewrite). All policy decisions are resolved.

## Prerequisites

No prerequisites — this work has no external dependencies. All four items touch repo-local files (`tools/valor_cli.py`, `.githooks/pre-commit`, `docs/features/valor-cli-wrapper.md`, `tests/unit/test_valor_cli.py`).

## Solution

### Key Elements

- **Help short-circuit (item 1)**: In `main()`, before the positional shortcut rewrite, detect a standalone `-h`/`--help` token anywhere in `argv` and print top-level help, exiting cleanly. The shortcut rewrite currently only excludes a help flag when it is `argv[0]`; this extends the exclusion to a help flag appearing after a positional prompt.
- **Doc reframe (item 2)**: Rewrite feature-doc §5 from "shortcoming" to a "Design Boundary" subsection stating the CLI enqueues, the worker chooses the substrate. Document that post-cutover there is no legacy substrate, so a force-legacy knob is N/A (not "future") — there is nothing to switch to.
- **#1288 operator path (item 3) — DECIDED: option (a), allow-when-no-worktree.** The guard self-detects: on a `session/{slug}` branch committed from the main checkout, if no `.worktrees/{slug}/` directory exists, the main checkout *is* the only workspace for that slug — there is nothing to contaminate, so the commit is allowed (with an informational stderr note). If the worktree *does* exist, the commit is still blocked exactly as before (the operator must commit from inside it). This is zero-friction (no env var to remember), low-abuse (an agent session cannot bypass an *existing* worktree), and adds no durable-bypass surface. Option (b) (`VALOR_GUARD_OVERRIDE` env escape) and option (c) (no code, document the dance) are rejected — see the Decisions section for the rationale.
- **Derived allowlist (item 4)**: Replace the literal `KNOWN_SUBCOMMANDS` set with a value derived from the registered subparsers (introspect the `_SubParsersAction.choices` keys the test already reads via `_parser_subcommands()`). Convert `test_known_subcommands_matches_parser` from a parity assertion into a derivation check (the derived set must equal the registry, and the rewrite must still fire correctly).

### Flow

`valor "fix the bug" --help` → `main()` detects help token before rewrite → prints top-level help → exit 0 (no session, no sub-help)

`git commit` on `session/{slug}` from main checkout → Phase 0.5 guard → if `.worktrees/{slug}/` does NOT exist → informational stderr note + allow → else block with the existing message (operator must commit from inside the worktree)

### Technical Approach

- **Item 1**: Add a help-detection guard at the top of `main()` (after `argv` defaulting, before the rewrite), running on the **pre-rewrite** argv. The guard fires iff **all** of: `argv` is non-empty AND `argv[0]` does not start with `-` AND `argv[0] not in KNOWN_SUBCOMMANDS` AND a standalone `-h`/`--help` token appears anywhere in argv. This fires for `valor "prompt" --help` (argv[0] is a prompt) but NOT for `valor list --help` (argv[0]=="list", which must reach its own sub-help). When it fires, build the parser and `parser.print_help(); raise SystemExit(0)` — **not** `return 0`. The existing `test_help_flag_exits_zero` requires `main(["--help"])` to raise `SystemExit(0)`; argparse's own `-h` handling raises it, so the new short-circuit must match that contract to keep `SystemExit` semantics uniform across all help paths. Keep it minimal — a single first-token special-case is the documented tradeoff in feature-doc §4. Decision baked in: show **top-level** help for `valor "prompt" --help` (the user clearly wants help, not to create the prompt).
- **Item 2**: Pure prose edit in `docs/features/valor-cli-wrapper.md`. No code.
- **Item 3 (option a)**: Edit `.githooks/pre-commit` Phase 0.5. After computing `EXPECTED_SUFFIX=".worktrees/${SLUG}"` and detecting that `TOPLEVEL` does not end with it, add a check: if the worktree directory does not exist on disk (`[ ! -d "$(git rev-parse --show-toplevel)/${EXPECTED_SUFFIX}" ]`), print an informational note to stderr (the main checkout is the only workspace for this slug) and allow the commit; otherwise block as before. No env var, no bypass log — the allow path only triggers when there is provably nothing to contaminate. Update the Phase 0.5 comment block to describe this self-detecting allowance.
- **Item 4**: Compute `KNOWN_SUBCOMMANDS` from the parser at import time. The parser is built in `_build_parser()`; extract the subparser choice names into a module-level derivation (a small helper that builds the parser once and reads `action.choices.keys()` for the `_SubParsersAction`). The existing test's `_parser_subcommands()` helper already demonstrates the introspection.

## Failure Path Test Strategy

### Exception Handling Coverage
- No `except Exception: pass` blocks in `tools/valor_cli.py` (verified — the module has no try/except). The git hook is bash with `set -e`. No silent-swallow handlers in scope.

### Empty/Invalid Input Handling
- [ ] `valor` with no args already prints help and returns 1 (`main()` lines 260-262) — add/confirm a test.
- [ ] `valor --help` (help as `argv[0]`) must continue to work unchanged — regression test.
- [ ] `valor "prompt" --help` and `valor "prompt" -h` — new tests asserting top-level help prints and the call raises `SystemExit(0)` (matching the existing `valor --help` contract) before any `valor_session.cmd_create` call (mock/spy `cmd_create` is NOT invoked). Also assert the printed help TEXT is top-level (contains the subcommand list / `agent-session` metavar), not the `agent-session` sub-help.
- [ ] `valor list --help` — regression: must reach the `list` sub-help (the first-token guard must NOT fire), and still raise `SystemExit(0)` via argparse.
- [ ] Empty prompt with help flag (`valor "" --help`) — assert help, no create.

### Error State Rendering
- [ ] Item 3 (option a): when a `.worktrees/{slug}/` worktree exists, the guard's block message must still render to stderr (regression). When no worktree exists, the informational allow-note must render and the commit must proceed. Both are user-visible.

## Test Impact

- [ ] `tests/unit/test_valor_cli.py::test_known_subcommands_matches_parser` — UPDATE: convert from "literal == registry" parity assertion to "derived value == registry" derivation check. After item 4, the literal no longer exists; the test verifies the derivation produces the right set.
- [ ] `tests/unit/test_valor_cli.py` (help cases) — UPDATE/ADD: add cases for `valor "prompt" --help` and `valor "prompt" -h` asserting top-level help short-circuits with no create. Existing `valor --help` / `valor agent-session --help` cases stay as regression coverage.
- [ ] `.githooks/pre-commit` — no existing unit test (bash hook). Item 3 (option a) is verified by the policy-neutral `#1288` grep and the `worktrees` grep in the Verification table, plus a documented manual check (commit on a `session/{slug}` branch from main with and without the worktree present). If a shell-level test is added it is net-new, not a modification.

No other test files reference the wrapper or the guard (verified via prior-art grep).

## Rabbit Holes

- **Re-architecting the #1288 guard.** The guard's worktree-isolation intent for *agent* sessions is sound and out of scope. Item 3 adds one operator escape, not a redesign of worktree enforcement.
- **Building a force-legacy execution path.** There is no legacy substrate post-cutover (#1572). Do NOT add a knob that switches to a path that no longer exists. Item 2 is doc-only.
- **Over-generalizing help handling.** Resist building a full argv-preprocessing framework. A single help-token special-case is the documented, accepted tradeoff (feature-doc §4).
- **Touching `valor_session.py` internals.** All four items live in the wrapper, the hook, and the docs. The underlying `valor-session` CLI is unchanged.

## Risks

### Risk 1: Help short-circuit changes behavior for an edge argv shape
**Impact:** A user who genuinely wanted a prompt containing the literal token `--help` (e.g. `valor "document the --help flag"`) would get help instead of a session.
**Mitigation:** This is an acceptable, vanishingly-rare collision and matches argparse's own greedy-help convention. Document it in the feature doc. The short-circuit only fires on a *standalone* `-h`/`--help` token, not substrings.

### Risk 2: Option (a) allows a commit that a later-created worktree could collide with
**Impact:** With option (a), a `session/{slug}` commit lands in the main checkout because no `.worktrees/{slug}/` exists yet. If a worktree for that slug is created later, the two histories could diverge.
**Mitigation:** This is a narrow, low-probability window and strictly better than the rejected env-var escape (which #887/#1288 warn against — it would let an agent session set a bypass var). Option (a) never bypasses an *existing* worktree, so the contamination #887/#1288 prevent (an agent session committing into a workspace that already has an isolated worktree) remains blocked. The informational stderr note makes the allowance visible. If the late-worktree-collision ever bites in practice, tighten later — but the Small appetite does not justify pre-building for it now.

## Race Conditions

No race conditions identified — all operations are synchronous and single-threaded (argv parsing at process start, a git hook running once per commit). No shared mutable state, no async, no cross-process data flow.

## No-Gos (Out of Scope)

- [ORDERED] Shipping a worker-side force-legacy-substrate env var — blocked by the fact that the legacy substrate was removed in #1572; there is nothing to switch to until/unless a second substrate is reintroduced, which is a separate initiative. Documented as N/A in item 2 rather than built.
- Feature-doc shortcomings §7 (slug requirement) and §8 (per-session model ignored by granite) — these are correctly-attributed *underlying CLI* and *substrate* behaviors, not wrapper bugs; out of scope for this wrapper-ergonomics pass.

## Update System

No update system changes required — this feature is purely internal. `tools/valor_cli.py`, `.githooks/pre-commit`, and the feature doc are all repo-local and propagate via the normal `git pull` in `/update`. No new dependencies, config files, or migration steps. (The `/update` verify step's `check_valor_alias_shadow` from #1619 is unaffected.)

## Agent Integration

No agent integration required — `valor` is already a registered CLI entry point (`pyproject.toml [project.scripts]`, invoked via the agent's Bash tool). These changes refine its existing behavior; they add no new tool surface, no MCP server, and no bridge import. The agent already reaches `valor` and `valor-session` through Bash.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/valor-cli-wrapper.md` §4 — mark the help gap resolved; document the top-level-help-on-positional behavior and the standalone-token caveat.
- [ ] Rewrite `docs/features/valor-cli-wrapper.md` §5 — reframe "PTY path implicit" as a **Design Boundary** subsection: CLI enqueues, worker selects substrate; force-legacy is N/A post-cutover.
- [ ] Update `docs/features/valor-cli-wrapper.md` §6 — document the decided #1288 operator path: option (a), the guard allows a `session/{slug}` commit from the main checkout when no `.worktrees/{slug}/` exists, and still blocks when it does.
- [ ] Update `docs/features/valor-cli-wrapper.md` §2 — note the allowlist is now derived, not hand-maintained; the test verifies the derivation.
- [ ] No `docs/features/README.md` index change needed — the feature doc already exists and is indexed.

### External Documentation Site
- Not applicable — this repo has no Sphinx/MkDocs site for these internal tools.

### Inline Documentation
- [ ] Update the `KNOWN_SUBCOMMANDS` docstring comment (`tools/valor_cli.py:38-43`) to describe the derivation instead of the literal.
- [ ] Add a comment on the help short-circuit explaining the standalone-token rule.
- [ ] Update the `.githooks/pre-commit` Phase 0.5 comment block to describe the option-(a) allow-when-no-worktree path (cite #1288 + #1620).

## Success Criteria

- [ ] **Behavioral delta:** `valor "anything" --help` prints **top-level** help TEXT (output contains the subcommand list / `agent-session` metavar), not the `agent-session` sub-help — this is the genuine fix
- [ ] `valor "anything" --help` raises `SystemExit(0)` (matches the existing `valor --help` contract)
- [ ] **Regression guard:** `valor "anything" --help` creates no session (spy confirms `cmd_create` not called) — already holds today, locked in to prevent future regression
- [ ] `valor "anything" -h` behaves identically
- [ ] `valor --help` and `valor agent-session --help` still work (regression)
- [ ] `valor list --help` reaches `list` sub-help (first-token guard does not over-fire)
- [ ] Feature doc §5 reframes the PTY-path item as a design boundary; force-legacy documented as N/A (no legacy substrate post-#1572)
- [ ] #1288 guard implements option (a): a `session/{slug}` commit from the main checkout is allowed iff no `.worktrees/{slug}/` exists (with an informational stderr note); an existing worktree still blocks. Encoded in `.githooks/pre-commit`, documented in the feature doc.
- [ ] `KNOWN_SUBCOMMANDS` is derived from the registered subparsers; the hand-maintained literal is removed
- [ ] `tests/unit/test_valor_cli.py::test_known_subcommands_matches_parser` converted to verify the derivation
- [ ] Feature doc shortcomings §2, §4, §5, §6 updated to reflect resolved/reframed status
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly.

### Team Members

- **Builder (cli)**
  - Name: cli-builder
  - Role: Implement help short-circuit (item 1) and derived allowlist (item 4) in `tools/valor_cli.py`, plus the #1288 guard override in `.githooks/pre-commit` (item 3) per the decided policy.
  - Agent Type: builder
  - Resume: true

- **Builder (docs)**
  - Name: docs-builder
  - Role: Rewrite feature-doc §2/§4/§5/§6 (items 2-4 framing) in `docs/features/valor-cli-wrapper.md`.
  - Agent Type: documentarian
  - Resume: true

- **Validator (cli)**
  - Name: cli-validator
  - Role: Verify help short-circuit, derived allowlist, guard override, and all tests pass.
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Standard tiers (see template). This plan uses `builder`, `documentarian`, and `validator` only.

## Step by Step Tasks

### 1. Help short-circuit + derived allowlist

- **Task ID**: build-cli-core
- **Depends On**: none
- **Validates**: tests/unit/test_valor_cli.py
- **Assigned To**: cli-builder
- **Agent Type**: builder
- **Parallel**: true
- In `tools/valor_cli.py` `main()`, add a first-token help guard on the **pre-rewrite** argv: fire iff `argv` non-empty AND `argv[0]` does not start with `-` AND `argv[0] not in KNOWN_SUBCOMMANDS` AND a standalone `-h`/`--help` token is present. When it fires: `parser = _build_parser(); parser.print_help(); raise SystemExit(0)` — NOT `return 0` (must match the existing `test_help_flag_exits_zero` SystemExit(0) contract).
- Replace the literal `KNOWN_SUBCOMMANDS` set with a derivation from the registered subparsers. Apply `@functools.lru_cache(maxsize=None)` to `_build_parser` so the import-time derivation and runtime calls share one parser build (NIT mitigation — no double build, no import-order side effects). Read the `_SubParsersAction.choices` keys for the derivation. Update the module docstring/comment accordingly.
- Add help-case tests (`valor "prompt" --help`, `valor "prompt" -h`, `valor "" --help`) asserting `SystemExit(0)`, top-level help TEXT (printed output contains the subcommand list / `agent-session` metavar, not the sub-help), and no `cmd_create` call (spy/mock). Add a `valor list --help` regression asserting the guard does NOT fire and `list` sub-help is reached.

### 2. #1288 guard operator path

- **Task ID**: build-guard
- **Depends On**: none (policy decided: option (a))
- **Validates**: manual/shell verification (see Verification table)
- **Assigned To**: cli-builder
- **Agent Type**: builder
- **Parallel**: true
- Encode option (a) in `.githooks/pre-commit` Phase 0.5: in the `TOPLEVEL` mismatch branch, before blocking, check whether `${repo_root}/.worktrees/${SLUG}` exists on disk. If it does NOT, print an informational stderr note (main checkout is the only workspace for this slug) and allow the commit (skip the `exit 1`). If it does, block as before.
- Update the Phase 0.5 comment block to describe the self-detecting allow-when-no-worktree path (option a) and reference #1288 + #1620.

### 3. Convert allowlist drift test

- **Task ID**: build-test-convert
- **Depends On**: build-cli-core
- **Validates**: tests/unit/test_valor_cli.py::test_known_subcommands_matches_parser
- **Assigned To**: cli-builder
- **Agent Type**: builder
- **Parallel**: false
- Convert `test_known_subcommands_matches_parser` from a literal-parity assertion to a derivation check (derived set equals the registry; rewrite still fires for unknown first tokens).

### 4. Feature doc reframe

- **Task ID**: build-docs
- **Depends On**: build-cli-core, build-guard
- **Assigned To**: docs-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/valor-cli-wrapper.md` §2 (allowlist now derived), §4 (help resolved + caveat), §5 (rewrite to Design Boundary; force-legacy N/A), §6 (decided #1288 operator path).

### 5. Final validation

- **Task ID**: validate-all
- **Depends On**: build-cli-core, build-guard, build-test-convert, build-docs
- **Assigned To**: cli-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_valor_cli.py -q` and confirm pass.
- Confirm `valor "x" --help` prints top-level help and creates no session.
- Confirm the guard override behaves per the decided policy (set/unset env var).
- Verify all Success Criteria met including doc updates.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Wrapper tests pass | `pytest tests/unit/test_valor_cli.py -q` | exit code 0 |
| Help short-circuits, no session | `.venv/bin/python -c "from tools import valor_cli; import sys; rc=None;\nimport types\nfrom unittest import mock\nwith mock.patch('tools.valor_session.cmd_create') as m:\n  try: valor_cli.main(['x','--help'])\n  except SystemExit: pass\n  assert not m.called"` | exit code 0 |
| Help text is top-level, not sub-help | `pytest tests/unit/test_valor_cli.py -q -k help` | exit code 0 (asserts printed help contains the subcommand list, raises SystemExit(0)) |
| `list --help` still reaches sub-help | `pytest tests/unit/test_valor_cli.py -q -k list_help` | exit code 0 |
| Allowlist derived (no literal set) | `grep -n 'KNOWN_SUBCOMMANDS = {' tools/valor_cli.py` | exit code 1 |
| #1288 policy documented in hook | `grep -n '#1288' .githooks/pre-commit` | output contains the chosen-policy comment block (policy-neutral — passes for any decided option) |
| #1288 option-(a) allowance encoded | `grep -n 'worktrees' .githooks/pre-commit` | hook references the worktree-existence check (option a) |
| Lint clean | `python -m ruff check tools/valor_cli.py tests/unit/test_valor_cli.py` | exit code 0 |
| Format clean | `python -m ruff format --check tools/valor_cli.py tests/unit/test_valor_cli.py` | exit code 0 |

(All rows reflect the decided policies: item-1 top-level help, item-3 option (a). The `#1288` grep row is policy-neutral by design — it asserts the chosen policy is *documented* in the hook comment block, which holds regardless of which option had been chosen.)

## Critique Results

_War room run 2026-06-12. Verdict: **NEEDS REVISION** (2 blockers). Critics: Skeptic, Operator, Archaeologist, Adversary, Simplifier, User, Consistency Auditor._

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Skeptic, Adversary | Item-1 impl text says "print top-level help then **return 0**", but existing `test_help_flag_exits_zero` requires `main(["--help"])` to **raise SystemExit(0)**. A builder following the plan verbatim breaks the regression test. | Revision: change item-1 wording (lines 105, 234) to `parser.print_help(); raise SystemExit(0)`, not `return 0`. | Guard must preserve argparse's `SystemExit(0)` contract. After `parser = _build_parser()`: `parser.print_help(); raise SystemExit(0)`. Line 118's "SystemExit/return" hedge must be resolved to SystemExit. |
| BLOCKER | Consistency Auditor | Verification table row greps for `VALOR_GUARD_OVERRIDE` and the "Allowlist derived" row asserts the literal is gone — both hard-code leading-candidate option (b). But Open Question 1 is unresolved; under option (a) or (c) those rows produce false build failures. Success Criterion ("decided answer, even if 'no change'") is satisfiable by option (c) with zero VALOR_GUARD_OVERRIDE anywhere. | Revision: gate the override verification row on "option b chosen", OR replace with a policy-neutral check (pre-commit contains a comment block documenting the chosen #1288 policy). The "update rows during finalization" note is insufficient. | Replace the grep row with: `grep -n '#1288' .githooks/pre-commit` expects the chosen policy documented in the comment block — passes under all three options. Resolve Open Question 1 before build. |
| CONCERN | Adversary, Skeptic | Item-1 guard rule "no recognized subcommand precedes it" is under-specified: `valor list --help` must show list sub-help, not top-level. Flat `any(t in KNOWN_SUBCOMMANDS for t in argv)` is wrong; must test the FIRST token only. | Revision: specify guard as first-token test. | Guard fires iff: `argv` non-empty AND `argv[0]` does not start with `-` AND `argv[0] not in KNOWN_SUBCOMMANDS` AND (`-h`/`--help` in argv). This fires for `valor "prompt" --help`, not for `valor list --help` (argv[0]=="list"). Run guard on pre-rewrite argv. |
| CONCERN | Operator, Archaeologist | Item-3 override audit line goes only to stderr; in non-interactive contexts (worker/PTY/CI) it vanishes — no durable forensic trace of a bypass. Risk 2's "convention + #887" defense recreates the bypass surface #1288 was added to close. | Revision (if option b ships): make the override write a durable audit record, not just stderr; OR pick option (a) which needs no env var to remember. | If option (b): append branch+UTC-timestamp+PID/PPID to a gitignored `.guard-override.log` at repo root, unconditionally when the var is set. PPID distinguishes human shell from agent spawn. If option (a)/(c): concern is moot. |
| CONCERN | User, Operator | Item-3 env-var trades blocked-commit friction for remember-the-var-every-commit friction. Option (a) allow-when-no-worktree is zero-friction (guard self-detects). Plan designates (b) "leading candidate" without justifying why higher-friction wins. | Operator decision (Open Question 1). | Option (a) check: `git worktree list` — if no `.worktrees/{slug}` exists, the main checkout IS the workspace; allow with no operator action. Recommend surfacing (a) as the ergonomic default in the revision. |
| CONCERN | Simplifier | Option (c) (document worktree dance, zero code/risk) may be the right call for item 3; the env override is the "force-legacy path" the plan's own Rabbit Holes warn against, under a different name. | Operator decision (Open Question 1). | If real recurring pain is unconfirmed, ship (c) now; (b) is a one-liner to add later if pain materializes. Aligns with Small appetite. |
| CONCERN | User, Consistency Auditor | Item-1 success criterion "spy confirms cmd_create not called" verifies a property that ALREADY holds (Freshness Check: no session created today). It reads as a fix criterion but is really a regression guard; the genuine delta is help TEXT (sub vs top-level), which no criterion asserts. | Revision: reframe the spy criterion as a regression guard; add a criterion asserting the help TEXT delta (top-level vs sub-help output). | The real behavioral change is `print_help()` output content. Assert the printed text contains top-level usage (e.g. the subcommand list / `metavar="agent-session"` line), not just that cmd_create is uncalled. |
| NIT | Skeptic, Adversary | Item-4 import-time `_build_parser()` for the derivation adds a second parser build and a latent import-order/side-effect risk; also freezes the rewrite blacklist at import. | Optional: `@functools.lru_cache` on `_build_parser`, or derive lazily in `main()`. | `@functools.lru_cache(maxsize=None)` on `_build_parser` makes import-time + call-time derivation share one build at zero extra cost. |
| NIT | Simplifier | Item-4 derives a 9-line literal that already has a loud drift test; net complexity is neutral-to-negative. | Optional: confirm the derivation genuinely removes a maintenance burden vs. just relocating it. The drift test already catches desync pre-merge. | If kept, the win is "no parallel literal to forget"; if the lru_cache derivation is clean it's worth it, otherwise the literal+test is already minimal. |

---

## Decisions (resolved during revision)

The three open questions raised in the draft are now decided. They are recorded here as durable rationale for build and review.

1. **#1288 operator path policy (item 3) — DECIDED: option (a), allow-when-no-worktree.**
   Rejected (b) (`VALOR_GUARD_OVERRIDE` env escape): it is the "force-legacy path under a different name" the plan's own Rabbit Holes warn against — an agent session could set the var, recreating exactly the bypass surface #1288 closed, and its stderr-only audit line vanishes in non-interactive contexts (worker/PTY/CI) with no durable forensic trace.
   Rejected (c) (no code, document the dance): leaves the operator pain that prompted this issue unsolved.
   Chose (a): zero operator friction (the guard self-detects — no var to remember), low abuse surface (an existing worktree is never bypassed, so the agent-contamination case stays blocked), and no durable-bypass surface to audit. The only residual risk (a commit that a *later*-created worktree could collide with) is narrow, visible via the stderr note, and acceptable at this Small appetite.

2. **Help text choice (item 1) — DECIDED: top-level help.** `valor "prompt" --help` shows top-level help; the user clearly wants help, not to create the prompt. The genuine fix is the help *text* (top-level vs the `agent-session` sub-help that shows today); the no-session property already holds and is kept as a regression guard.

3. **Item 2 force-legacy knob — DECIDED: documented as N/A.** No legacy substrate exists post-#1572; reintroducing a second substrate would be its own initiative. We are not reserving the idea as "future" — item 2 is a pure doc reframe with zero code.
