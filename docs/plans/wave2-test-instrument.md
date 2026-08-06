---
status: Ready
revision_applied: true
revision_applied_at: 2026-08-06T08:51:46Z
type: bug
appetite: Medium
owner: Wave 2 build lane (host `Valor the Pirate`)
created: 2026-08-06
tracking: https://github.com/tomcounsell/ai/issues/2552
last_comment_id: 5202173519
---

# Wave 2: Repair the Test Suite's Measuring Instrument

## Problem

The test suite is not currently a trustworthy instrument. Four independent defects each break the relationship between "the suite is green" and "the code is correct" — in both directions. Two produce false reds, two produce false greens.

**Current behavior:**

- Running pytest from the main checkout on this host produces 17 failures that have nothing to do with the code under test. A gitignored operator kill-switch file, `data/catchup-disabled`, is read by `bridge/catchup.py` through a path anchored to the source tree rather than the working directory. Anyone reading the diff sees nothing explaining the red, and the same nodes pass from a worktree. (#2552)
- `tests/unit/test_pipeline_integrity.py:176` asserts a field, `expectations`, that `df6097fe6` deleted. The test fails for a reason unrelated to any live contract, and nothing detects that the field list it guards has since drifted 50 fields away from the model. (#2553)
- Two reflections cutover tests assert on YAML *comments* in `config/reflections.yaml`. They pass today only because nothing has rewritten that file since the comments were added. The next run of `tools/reflection_machine_filter.py` destroys them via `safe_load`/`safe_dump` and both tests go red. This is a latent red masquerading as a stable green. (#2556)
- The hardlinks staleness probes test skill liveness with `.is_dir()`. A directory whose `SKILL.md` was deleted but which still holds a `__pycache__` or a stray reference file reads as a live skill. The probe cannot detect the staleness class it exists to detect, and one such husk exists right now. (#2557, #2523)

**Desired outcome:**

A run from the main checkout produces the same result as a run from a worktree. Every guard in scope either fires on the condition it names or fails the build. No test asserts on a contract that no longer exists, and no test asserts on a substrate that a routine background job destroys. Every guard added here ships with a negative control proving it can fail.

## Freshness Check

**Baseline commit:** `a85b7cc7c39a74eff225f8d695e01274f81b80e9`
**Issues filed at:** 2026-08-06T07:40:00Z (all four, same audit batch)
**Disposition:** Minor drift

**File:line references re-verified:**

- `bridge/catchup.py:40` — issue claims `CATCHUP_DISABLED_FLAG` is anchored to the module's parent directory — **still holds**, verbatim.
- `tests/unit/test_pipeline_integrity.py:176` — issue claims a stale `expectations` assertion — **still holds**; the assertion is at :176 and the failure surfaces at :184.
- `tools/reflection_machine_filter.py:110` / `:146` — issue claims `safe_load` / `safe_dump` — **still holds**, both verbatim.
- `tests/unit/test_update_hardlinks.py:412-413` — issue claims the `.is_dir()` probe — **drifted by one line**; the predicates are at :413-414. Claim holds. A **third** `.is_dir()` probe the issue does not mention exists at :25 and is the more consequential one (see Technical Approach).

**Cited sibling issues/PRs re-checked:**

- #2473 — open. The production `data/catchup-disabled` flag's fate is tracked there and stays out of scope here.
- #2518 / #2538 — the durability fence work; merged. Its invariant is what makes #2553's stated fix unsafe (see below).
- #2523 — open. Same husk directory as #2557; this plan closes it.
- #2532, #2429, #2488, #2430 — superseded by the restructured issues per the audit.

**Commits on main since the issues were filed (touching referenced files):** none. The most recent commit touching any referenced file is `877720530` (Durability M1 fence), which predates the issues.

**Active plans in `docs/plans/` overlapping this area:** `durability-m1-fence-canary` touches the fence fields that #2553 reasons about, but does not touch `_AGENT_SESSION_FIELDS` or any file in this plan's diff. Coordination signal only, not a blocker.

**Notes:** The recon on each issue was rewritten from direct measurement rather than accepted as filed; see each issue's `## Recon Summary`. Two of the four issues had their fix shape materially corrected as a result (#2553 and #2557).

## Prior Art

- **#2532, #2429, #2488, #2430** — the predecessor cluster issues. They bundled these four root causes together as symptom groups ("Cluster A/B", "Group A"), which is why no single one of them was actionable. The Wave 2 audit restructured them into the four single-root-cause issues this plan implements. Relevance: the restructure is the reason this plan can carry four `Closes` trailers without being a grab-bag — each issue now has exactly one cause and one fix.
- **`44026cb96` / `ad4e95c8d`** — renamed `do-skills-audit` → `audit-skills` and pruned stale skills-global skills, adding `("skills", "do-skills-audit")` to `RENAMED_REMOVALS`. Relevance: the rename completed in `~/.claude/skills/` and in the removal list but left the source-tree directory behind. That leftover is the husk #2557 and #2523 both describe. The prior fix was not wrong, it was incomplete in a direction no test could observe — which is precisely the probe defect #2557 identifies.
- **`df6097fe6` (#2494 Tasks 6-8, via #2516)** — deleted the `expectations` field and added the fenced-execution-record fields. Relevance: it is the direct cause of #2553's stale assertion, and its fence invariant is what makes the naive version of #2553's fix unsafe.

**Why previous fixes were incomplete:** the `do-skills-audit` rename is the instructive case. It updated every artifact that a test could see (`RENAMED_REMOVALS`, the user-level hardlink, the live successor directory) and missed the one artifact no test could see, because the only probe that could have caught it — `.is_dir()` — was itself blind to the failure mode. A fix is only as complete as the instrument that measures it, which is the thesis of this whole plan.

## Research

No relevant external findings — proceeding with codebase context. The one external-facing question (whether PyYAML can round-trip comments) is settled by the library's documented behavior: `safe_load`/`safe_dump` discard comments by construction, and preserving them requires a different library (`ruamel.yaml`). That option is evaluated and rejected in the Technical Approach for cost reasons, not feasibility reasons.

## Step 0 Baseline (measured before any change)

Recorded verbatim. Main checkout, serial `-n0`, `scripts/pytest-clean.sh`, seven focused files: `tests/unit/test_agent_catchup.py`, `test_reconciler.py`, `test_catchup_claim.py`, `test_dedup.py`, `test_pipeline_integrity.py`, `test_reflections_yaml_migration.py`, `test_update_hardlinks.py`.

| Arm | Condition | Result |
|---|---|---|
| 1 | Production `data/catchup-disabled` present (today's reality) | **18 failed / 120 passed** |
| 2 | Identical selection, `CATCHUP_DISABLED_FLAG` monkeypatched to a temp path via a throwaway plugin; production flag untouched on disk | **1 failed / 137 passed** |

The 17-node delta is entirely flag-caused, distributed as 2 failures in `test_agent_catchup`, 13 in `test_reconciler`, 2 in `test_catchup_claim`. The single arm-2 survivor is `test_pipeline_integrity.py::TestEnqueueContinuationFallback::test_extract_agent_session_fields_includes_metadata` — the #2553 stale assertion, confirmed flag-independent.

`test_reflections_yaml_migration.py` and `test_update_hardlinks.py` are **fully green in both arms**. That is the expected and damning result: #2556 and #2557 are false greens, and a green result from those files carries no information today.

This table is the acceptance baseline. The post-fix target is 0 failed with the fixture active, and — critically — a reproduction of arm 1's 17 failures when the fixture is disabled (the negative control).

## Appetite

**Size:** Medium

**Team:** Solo dev (this lane), PM (queue and merge-slot ownership), code reviewer

**Interactions:**
- PM check-ins: 2-3 — one scope ruling already requested on #2553's split, one for the merge slot, one for stage reporting
- Review rounds: 1

Four small, independent diffs in one PR. The cost is not implementation, it is getting the classification decisions right and proving each guard can fail.

## Prerequisites

| Requirement | Check Command | Purpose |
|---|---|---|
| Redis reachable (focused suite touches ORM) | `.venv/bin/python -c "import redis, os; redis.Redis().ping()"` | Session-model tests in the focused selection |
| Production kill-switch flag present | `test -f data/catchup-disabled` | Required for the #2552 negative control to be meaningful on this host |

## Solution

### Key Elements

- **Catchup kill-switch isolation (#2552)**: an autouse fixture in `tests/conftest.py` repoints `bridge.catchup.CATCHUP_DISABLED_FLAG` at a per-test temporary path, so no test can observe operator control state. The production flag is never touched.
- **Field-contract drift guard (#2553)**: delete the stale `expectations` assertion; add a guard that catches both drift directions — phantom names in the list, and new model fields absent from it — while honestly recording the existing 50-field gap as a frozen constant pointing at #2563.
- **Durable-signal cutover assertions (#2556)**: replace the two comment-substring assertions with assertions on parsed YAML structure, which survives the `safe_load`/`safe_dump` round-trip.
- **Skill-liveness probe correction (#2557, #2523)**: probe for `SKILL.md` rather than directory existence at all three probe sites, sweep the husk, and add a guard that fails while any husk exists in either skill root.

### Flow

Not a user-facing feature. The operative flow is the diagnostic one:

Developer runs focused suite from main checkout → result reflects only the code under test → a red means a real defect and a green means real coverage → developer can act on the signal without first re-deriving whether the instrument is lying.

### Technical Approach

**#2552 — kill-switch isolation.**

All three production readers (`bridge/catchup.py:73`, `bridge/reconciler.py:182-186`, `bridge/agent_catchup.py:792-795`) import `CATCHUP_DISABLED_FLAG` from `bridge.catchup` rather than re-deriving the path. A single autouse fixture patching `bridge.catchup.CATCHUP_DISABLED_FLAG` therefore covers every reader, and no production code change is required. The fixture is autouse and lives in `tests/conftest.py` so it covers the integration catchup files that were outside the baseline selection.

Deliberately *not* changing `bridge/catchup.py` to resolve the flag relative to the working directory. That would be a production behavior change to fix a test problem, and the source-tree anchor is correct for the production case — the bridge runs under launchd with an unpredictable cwd, and a cwd-relative flag would silently stop working there. The defect is that tests read production control state, and the fix belongs in the tests.

**#2553 — corrected fix shape; see the scope ruling in Open Questions.**

The issue instructs adding six named fields to `_AGENT_SESSION_FIELDS`. Implementing that literally would introduce a bug. Five of the six (`exec_pid`, `pid_create_time`, `exec_cwd`, `exec_harness`, `spawn_history`) are fenced-execution-record fields from #2518; copying them into a *newly created* session forges a fence describing a process that never ran.

Recon further established that the helper serves two incompatible contracts — `agent/session_health.py:4586` and `tools/agent_session_scheduler.py:886` delete-and-recreate the *same* session (where dropping any field is data loss and the fence must be preserved), while `agent/agent_session_queue.py:784` and `agent/session_executor.py:586` create a *new* session (where the fence must be reset). No single classification of the drifted fields is correct for both. That conflict is filed as **#2563** and is out of scope here.

In scope for this lane:
1. Delete the `"expectations"` entry from the `critical_fields` list at `tests/unit/test_pipeline_integrity.py:176`.
2. Add a guard asserting every name in `_AGENT_SESSION_FIELDS` exists on the model. This is the check that would have caught `expectations` the day `df6097fe6` removed it, and it is the direct fix for the "no drift detection" half of the issue.
3. Add a guard asserting the set of model fields absent from `_AGENT_SESSION_FIELDS` equals a frozen `KNOWN_GAP` constant, with a comment pointing at #2563. Green today; fails loudly the moment a new field is added; does not pretend the gap is closed. When #2563 ships, `KNOWN_GAP` empties and this guard becomes a plain completeness assertion.

**The guard's left-hand set excludes auto-generated key fields, and this exclusion is load-bearing.** `AgentSession._meta.fields` has 89 entries, `_AGENT_SESSION_FIELDS` has 38, and the raw difference is **51**. One of those 51 is `id`, an `AutoKeyField` that Popoto generates and that must never appear in a `create(**fields)` payload. Excluding auto-key fields gives the correct figure of **50**. Derive the exclusion programmatically — `{n for n, f in AgentSession._meta.fields.items() if not isinstance(f, AutoKeyField)}` — rather than subtracting the literal `{"id"}`, so that a second `AutoKeyField` added later cannot silently reopen the same off-by-one. State the rule in the `KNOWN_GAP` source comment as well as here.

Generate `KNOWN_GAP`'s 50 names at build time by running the exclusion-filtered diff and pasting the sorted result. Do not transcribe them from this plan — a plan is not a measurement.

**#2556 — durable signal.**

The issue offers two paths and recommends the cheaper one; taking the recommendation. The contract these tests actually protect is *absence of the reflection entry from the registry*, and the assertion immediately preceding the comment check already verifies exactly that against parsed YAML (`assert "sentry-issue-triage" not in names`). The comment assertion adds no contract coverage the structural one lacks — it only adds a dependency on a substrate that a background job destroys. Both comment-substring assertions are deleted and the structural assertions strengthened to name what they are protecting.

Rejected: making `reflection_machine_filter` comment-preserving via `ruamel.yaml`. That means a new runtime dependency and rewriting a derived-artifact writer's I/O to serve a test's convenience, which inverts the cost relationship. The comment in `config/reflections.yaml` remains useful as human documentation; it simply stops being a test fixture.

**How #2556 is proven, corrected after critique.** The original plan proposed round-tripping a temp copy of `config/reflections.yaml` and re-running the two cutover tests against it. That is not executable: both tests re-derive the config path from `__file__` in their bodies (`:220-221`, `:286-287`) *and* in their module-level `skipif` predicates (`:180`, `:238`), which evaluate at collection time — before any per-test `monkeypatch` could redirect them. Building that redirect hook would mean adding a production-facing override point to protect a one-shot manual check.

Instead, the proof becomes a **new permanent regression test** in the same file, operating entirely on `tmp_path` and never touching the real machine-local config:

1. Write a synthetic `reflections.yaml` into `tmp_path` carrying a pointer comment, the structural state the cutover tests assert on, and — critically — at least one reflection whose `project_key` maps to a machine other than the running one. That last part is required because `filter_reflections_for_machine` only writes when `disabled_names` is non-empty (`tools/reflection_machine_filter.py:145-146`); without it the round-trip silently no-ops and a passing control would prove nothing.
2. Run the filter over that file.
3. Assert the pointer comment is **gone** — this is the destruction, demonstrated rather than asserted from documentation.
4. Assert the structural signal **survives** the same round-trip.

Steps 3 and 4 together are self-proving: step 3 is the negative control for the assertion style being removed, and step 4 is the positive control for the style replacing it, both against the same round-tripped artifact. No backup/restore of `config/reflections.yaml` is needed, and the evidence becomes permanent rather than a one-shot verification.

**#2557 / #2523 — probe correction and husk sweep.**

Three `.is_dir()` probes, not the two the issue cites:

- `tests/unit/test_update_hardlinks.py:25` — `any((_REPO_ROOT / root / name).is_dir() for root in _SKILL_ROOTS)`. This is the consequential one. `test_renamed_removals_covers_deleted_skills` excuses a deleted skill from needing a `RENAMED_REMOVALS` entry when it "currently exists on disk in any skill root". A husk makes that read `True`, so a genuinely deleted skill that left a husk silently escapes the completeness check. This is a false **pass**.
- `tests/unit/test_update_hardlinks.py:413-414` — the two probes the issue cites. These can only produce a false failure, not a false pass, so they are the lower-severity half.

All three become `(dir / "SKILL.md").is_file()`.

The husk sweep needs care on two points. First, `.claude/skills-global/do-skills-audit/` is **entirely untracked** — `git ls-files` returns nothing for it, `references/metadata.json` is untracked and the `.pyc` files are gitignored. Deleting it locally therefore produces no diff and fixes exactly one machine. The durable artifact must be a guard test that fails while any husk exists in either skill root; the local sweep is only what makes this host green. Second, `.claude/skills/_shared/` holds git-tracked `test-quality.md` and no `SKILL.md`. It is an intentional shared-resource directory. The husk guard skips it via an explicit named allowlist — `HUSK_GUARD_ALLOWLIST = frozenset({"_shared"})`, checked as `name not in HUSK_GUARD_ALLOWLIST` — **not** via a `name.startswith("_")` prefix rule. An allowlist of one is more honest than a convention inferred from a single case, and it fails loudly when a second non-skill directory appears rather than silently absorbing it. Task 11's check that `_shared` is the only such directory is what keeps the allowlist honest.

## Failure Path Test Strategy

### Exception Handling Coverage

No exception handlers are introduced or modified by this work. The touched production file set is empty except for the husk deletion; all other changes are in test files and test fixtures. `bridge/catchup.py`'s `catchup_disabled()` has no exception handler — it is a bare `Path.exists()`.

### Empty/Invalid Input Handling

- The `KNOWN_GAP` guard must behave correctly when the gap is empty (the post-#2563 state). Covered by asserting set equality rather than a length or a subset relation, so an empty `KNOWN_GAP` is a valid and meaningful state.
- The husk guard must behave correctly when a skill root contains no directories at all, and when a directory contains a `SKILL.md` that is empty. An empty `SKILL.md` counts as present — file existence is the contract, content validation belongs to the skills audit, not here.
- The catchup fixture must work for tests that never import `bridge.catchup`. Patching a module attribute requires the import; the fixture imports it directly rather than relying on the test having done so.

### Error State Rendering

No user-visible output. The "rendering" surface here is the pytest failure message, and each new guard's negative control verifies the message names the offending field or directory, so a future failure is actionable without re-deriving the cause.

## Test Impact

- [ ] `tests/unit/test_pipeline_integrity.py::TestEnqueueContinuationFallback::test_extract_agent_session_fields_includes_metadata` — UPDATE: drop `"expectations"` from `critical_fields`; the remaining six names are all still live on the model and stay.
- [ ] `tests/unit/test_reflections_yaml_migration.py::TestSentryTriageCutover::test_sentry_issue_triage_absent_from_repo_registry` — UPDATE: delete the comment-substring assertion, keep and strengthen the structural one.
- [ ] `tests/unit/test_reflections_yaml_migration.py::TestPrReviewAuditCutover` — UPDATE: same change, structurally identical assertion.
- [ ] `tests/unit/test_reflections_yaml_migration.py` — ADD: new `tmp_path` round-trip regression test proving the comment substrate is destroyed by the filter and the structural signal survives it. Carries its own negative control (Success Criteria 6, 7, 7a). Never reads the real `config/reflections.yaml`.
- [ ] `tests/unit/test_update_hardlinks.py` `_skill_exists` helper at :25 — UPDATE: `.is_dir()` → `SKILL.md` probe. Affects `test_renamed_removals_covers_deleted_skills`.
- [ ] `tests/unit/test_update_hardlinks.py::test_renamed_removals_entries_are_not_stale` — UPDATE: both `.is_dir()` probes at :413-414 → `SKILL.md` probe.
- [ ] `tests/conftest.py` — UPDATE: add the autouse catchup-flag fixture. Blast radius is the whole suite by construction; the risk is a test that *wants* the flag set, and none exists (verified: no test references `CATCHUP_DISABLED_FLAG` or `catchup_disabled`).
- [ ] The 17 baseline-red nodes across `test_agent_catchup.py`, `test_reconciler.py`, `test_catchup_claim.py` — no source change; they go green via the fixture. Their greening is the acceptance evidence for #2552.

## Rabbit Holes

- **Fixing `_AGENT_SESSION_FIELDS` properly in this lane.** It is a production semantics change inside the #2518 fence blast radius, it requires resolving a two-contract conflict, and it does not belong in a PR titled "repair the measuring instrument". Filed as #2563.
- **Making the reflections filter comment-preserving.** A new runtime dependency and an I/O rewrite to protect a test assertion. Rejected above.
- **Chasing the "19 nodes" figure from #2552.** Measurement shows 17 at unit level; the remaining 2 are presumably in the integration catchup files that were outside the baseline selection. The fixture is autouse and covers them regardless. The count is not load-bearing and reconciling it exactly is not worth a full-suite run — especially with the machine's full-suite slot occupied.
- **Auditing every gitignored file that might be read as control state.** #2552's fix shape suggests it. The three catchup readers are the confirmed instance and all route through one symbol. A general audit is a separate investigation, not a prerequisite to this fix.
- **Deleting the production `data/catchup-disabled` flag.** Operator-gated state, tracked in #2473. Out of scope and explicitly forbidden for this lane.

## Risks

### Risk 1: The autouse fixture masks a real catchup regression

**Impact:** If a future change breaks catchup in a way that resembles "disabled", the fixture guarantees the flag is absent and could make the failure harder to attribute — or, worse, a test that should exercise the disabled path silently exercises the enabled one.
**Mitigation:** The fixture points the flag at a `tmp_path` rather than deleting or stubbing the check, so `catchup_disabled()` still executes its real `Path.exists()` logic against a real filesystem path. Any test that wants to exercise the disabled path can `touch` the redirected path and get true disabled behavior. The negative control documents this by proving the fixture's only effect is relocating the flag.

### Risk 2: The `KNOWN_GAP` constant rots into a permanent excuse

**Impact:** A frozen list of 50 known-broken fields is exactly the kind of artifact that stops being read and starts being appended to, converting a guard into a suppression list.
**Mitigation:** The guard asserts set *equality*, not containment, so a new field cannot be silently absorbed — adding one fails the test and forces an explicit decision. The constant carries a comment naming #2563 as its termination condition. This is the honest option; the alternatives are a red test in main or a guard that lies.

### Risk 3: The husk sweep produces no reviewable diff

**Impact:** The `do-skills-audit` husk is untracked, so its deletion is invisible in the PR. A reviewer cannot verify it happened, and other machines are unaffected.
**Mitigation:** The durable artifact is the husk guard test, which is fully reviewable and which fails on any machine still carrying a husk. The local deletion is stated in the PR body as a host-local operation with its verification command, not implied by the diff.

### Risk 4: `_`-prefixed directory convention is undocumented

**Impact:** The husk guard skips `_`-prefixed directories based on `_shared` being the only current instance. If that convention is not real, the guard has an arbitrary hole.
**Mitigation:** Verify `_shared` is the only `_`-prefixed directory in either root at build time, and encode the skip as an explicit named allowlist rather than a prefix rule if it is the only one. An explicit allowlist of one is more honest than a convention inferred from a single case, and it fails loudly when a second such directory appears.

## Race Conditions

No race conditions identified. Every change is to test code, test fixtures, or a local filesystem deletion; all operations are synchronous and single-threaded. The one concurrency-adjacent consideration is that this lane shares a host with other build lanes and a running diagnostic full-suite job, which is a resource-contention concern handled by running focused serial selections only — not a correctness race in the code under change.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2563] Resolving the `_extract_agent_session_fields` two-contract conflict and closing the 50-field gap. Production semantics change in the #2518 fence blast radius; filed and scoped separately.
- [SEPARATE-SLUG #2473] Deciding the fate of the production `data/catchup-disabled` flag. Operator-gated control state.
- [SEPARATE-SLUG #2558] Out of lane scope per PM routing; needs a bisect and full-suite verification this host cannot currently provide.
- [SEPARATE-SLUG #2469] Out of lane scope per PM routing; needs a bisect and full-suite verification.
- [SEPARATE-SLUG #2554] Out of lane scope per PM routing; blocked on a human contract ruling.
- [ORDERED] Full-suite verification of the fixture's blast radius. The host's full-suite slot is occupied by a diagnostic run, and the PM owns the queue order. Focused serial verification only until that slot frees.

**Anti-criteria** (inverse Success Criteria rows asserting these absences):
- `_AGENT_SESSION_FIELDS` must be **unchanged** by this PR — the guard is added, the list is not edited.
- `data/catchup-disabled` must still exist on disk after the PR, unmodified.
- `bridge/catchup.py`, `bridge/reconciler.py`, `bridge/agent_catchup.py` must be **unchanged** — #2552 is fixed entirely in test code.

## Update System

No update system changes required. The husk sweep is the one item that plausibly belongs in `/update` — `scripts/update/hardlinks.py` already sweeps `RENAMED_REMOVALS` targets under `~/.claude/`, but never touches repo-side source directories, which is why this husk survived. Extending the sweep to the source tree is a real improvement and a real behavior change to a deployment script; it is deliberately not bundled into a test-repair PR. The husk **guard** added here fails on any machine carrying a husk, which surfaces the problem everywhere without changing deployment behavior — the correct sequencing.

## Agent Integration

No agent integration required. Every change is in test code, test fixtures, or a local filesystem cleanup. No new CLI entry point, no `pyproject.toml [project.scripts]` change, no bridge import, no MCP surface.

## Documentation

- [ ] Update `tests/README.md` — add the catchup kill-switch isolation fixture to the fixture/blind-spot notes, recording that operator flag files are neutralized suite-wide and why (a test reading a gitignored production flag is a contamination class, not a one-off).
- [ ] Update `docs/features/skill-context-convention.md` or the nearest skills-infrastructure doc — record that skill liveness is defined by `SKILL.md` presence, not directory existence, and that `_`-prefixed directories under the skill roots are shared resources rather than skills.
- [ ] No new feature doc. This plan repairs existing behavior rather than adding capability; a `docs/features/` entry would describe a test fixture, which belongs in `tests/README.md`.

## Success Criteria

Machine-checkable acceptance rows. Rows marked **negative control** exist because an assertion that never fires is indistinguishable from one that cannot fire.

| # | Criterion | Command / Check | Expected |
|---|---|---|---|
| 1 | The 17 flag-caused failures are green | Focused serial run of the 7 baseline files | 0 failed |
| 2 | **Negative control, #2552** — the fixture is what does the work | Same run with the fixture disabled | 17 failures reappear, same node names as the Step 0 arm-1 list |
| 3 | Stale assertion gone | `grep -c '"expectations"' tests/unit/test_pipeline_integrity.py` | 0 |
| 4 | Phantom-name guard fires | **Negative control**: temporarily append a bogus name to `_AGENT_SESSION_FIELDS` in-memory | Guard fails naming the bogus field |
| 5 | Drift guard fires on a new field | **Negative control**: temporarily remove one name from `KNOWN_GAP` | Guard fails naming the unclassified field |
| 6 | **Negative control, #2556** — the comment substrate really is destroyed | New `tmp_path` regression test: filter a synthetic registry seeded with an other-machine `project_key`, then assert the pointer comment is absent | Comment gone; assertion in the deleted style would fail here |
| 7 | The replacement signal survives the same round-trip | Same test, same round-tripped artifact, structural assertion | Structural assertion passes |
| 7a | The round-trip actually wrote | Same test asserts `disabled_names` is non-empty before checking the comment | Non-empty; guards against a silent no-op control |
| 8 | Husk guard fires | **Negative control**: create a temp husk dir in a skill root | Guard fails naming the husk |
| 9 | Husk swept | `test -e .claude/skills-global/do-skills-audit` | absent |
| 10 | `_shared` not swept | `test -f .claude/skills/_shared/test-quality.md` | present |
| 11 | Probe corrected at all three sites | All three former `.is_dir()` liveness sites route through a single `_skill_is_live(path)` helper whose body is a `SKILL.md` file check | 3/3 route through it; no liveness decision reads directory existence |
| 12 | **Anti-criterion** — production catchup code untouched | `git diff --name-only main -- bridge/` | empty |
| 13 | **Anti-criterion** — field list not edited | `git diff main -- agent/agent_session_queue.py` | empty |
| 14 | **Anti-criterion** — operator flag intact | `test -f data/catchup-disabled` | present, 0 bytes |
| 15 | Lint clean | `python -m ruff check` and `python -m ruff format --check` | clean |

## Step by Step Tasks

1. Create worktree `.worktrees/wave2-test-instrument` on branch `session/wave2-test-instrument`.
2. **#2552**: add the autouse `CATCHUP_DISABLED_FLAG` redirect fixture to `tests/conftest.py`. Verify no existing test wants the flag set.
3. **#2552**: run the 7 baseline files focused/serial; confirm the 17 nodes go green (Success Criteria 1).
4. **#2552**: run the negative control with the fixture disabled; confirm all 17 reappear by name (Success Criteria 2). Capture output as evidence.
5. **#2553**: delete `"expectations"` from `critical_fields` at `tests/unit/test_pipeline_integrity.py:176`.
6. **#2553**: add the phantom-name guard and the `KNOWN_GAP` equality guard. Exclude auto-key fields programmatically via `isinstance(f, AutoKeyField)`, not by subtracting `{"id"}`. Generate the 50 names by running the exclusion-filtered diff at build time and pasting the sorted result; state the exclusion rule in the source comment alongside the #2563 pointer.
7. **#2553**: run both negative controls (Success Criteria 4, 5); capture output.
8. **#2556**: delete both comment-substring assertions; strengthen the structural assertions.
9. **#2556**: add the new `tmp_path` round-trip regression test (comment destroyed + structural signal survives + write actually happened). Run it; confirm Success Criteria 6, 7, 7a. Do not touch the real `config/reflections.yaml`.
10. **#2557**: correct all three `.is_dir()` probes to `SKILL.md` presence, including the uncited one at :25.
11. **#2557**: add the husk guard with an explicit allowlist for `_shared`; verify `_shared` is the only such directory.
12. **#2557**: run the husk-guard negative control (Success Criteria 8), then sweep the husk (Success Criteria 9, 10).
13. Run the full focused selection serially; confirm 0 failed.
14. Run `ruff check` and `ruff format`.
15. Confirm all three anti-criteria (Success Criteria 12, 13, 14).
16. Commit, push, open one PR with `Closes #2552`, `Closes #2553`, `Closes #2556`, `Closes #2557`, `Closes #2523`.
17. Documentation tasks; then `/do-pr-review`; then request the merge slot from the PM.

## Open Questions

1. **#2553 scope split — PM ruling requested and pending.** This plan assumes the split: the stale assertion and the drift guard land here, and the two-contract resolution lands under #2563. If the PM prefers the whole thing in this lane, tasks 5-7 expand substantially and the PR stops being a pure test-instrument repair. Recommendation is to keep the split.
2. **Does `#2523` close cleanly on the husk sweep?** The reflection re-runs on its own schedule and the streak counter resets when rule 19 passes. Carrying `Closes #2523` is correct, but if the reflection has other husk findings not visible in this repo state, it may re-file. Acceptable; the guard makes any recurrence loud rather than silent.

## Critique Results

**Critique pass 2026-08-06, against plan baseline `942d802d4`.** Depth: FULL
(force-FULL: the plan touches the doctrine paths `.claude/skills/` and
`.claude/skills-global/`). Critics: Risk & Robustness, Scope & Value, History &
Consistency, plus driver structural checks and independent source verification.
Roster gate: 3/3 complete, 3/3 grounded. Critiqued on the split assumption for
Open Question 1 (#2553's production semantics fix deferred to #2563), per the
critique request.

Driver verification notes, recorded because they confirm or correct plan claims:

- **The husk inventory is exactly as the plan states.** A direct sweep of both
  skill roots for directories lacking `SKILL.md` returns exactly two:
  `.claude/skills-global/do-skills-audit/` (fully untracked — `git ls-files`
  returns nothing) and `.claude/skills/_shared/` (git-tracks
  `.claude/skills/_shared/test-quality.md`). `_shared` is the only
  underscore-prefixed directory in either root; `.claude/skills-global/` has
  none. The plan's Risk 4 premise holds.
- **The `.is_dir()` probe count is exactly three and Success Criterion 11 is
  sound as written.** `grep -c 'is_dir()' tests/unit/test_update_hardlinks.py`
  returns 3 today, and all three occurrences (`:25`, `:413`, `:414`) are the
  liveness sites the plan names, so the criterion's expected `0` is a valid
  whole-file check rather than an unverifiable "at the three sites" qualifier.
- **`expectations` is stale only in the test, not in the production list.**
  `set(_AGENT_SESSION_FIELDS) - set(model fields)` is empty today: there are
  zero phantom names. `"expectations"` appears only in the test's
  `critical_fields` list at `tests/unit/test_pipeline_integrity.py:176`. The
  plan's Task 5 is correct; the phantom-name guard of Task 6 is green on
  arrival, which makes its negative control (Success Criterion 4) the only
  evidence it can fail.
- **`config/reflections.yaml` is a regular 17881-byte file on this host, not a
  symlink.** Any in-place round-trip verification for #2556 mutates a real,
  machine-local, gitignored artifact and needs a backup/restore.
- **Five other test modules already assert membership in
  `_AGENT_SESSION_FIELDS`** (`test_health_check_recovery_finalization.py:961`,
  `test_agent_session_hierarchy.py:412`, `test_agent_session.py:585`,
  `test_nudge_loop.py:241`, `test_session_completion_zombie.py:18`). None
  conflict with the new guards, but the new guard is the sixth such assertion
  site and should be sited so it is discoverable alongside them.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| BLOCKER | Risk & Robustness, Scope & Value, History & Consistency (3/3) | Success Criteria rows 6-7 and Task 9 require running the two reflections cutover tests "against a temp copy" of `config/reflections.yaml`, but both tests hard-code `Path(__file__).resolve().parent.parent.parent / "config" / "reflections.yaml"` in their bodies AND in the module-level `skipif` predicates, with no parameter, fixture, or env var to redirect them. The negative control's mechanism does not exist, and adding it is not listed in Test Impact. | resolved | `_sentry_triage_cutover_pending()` (`tests/unit/test_reflections_yaml_migration.py:180`) and `_pr_review_audit_cutover_pending()` (`:238`) re-derive the path independently and run at IMPORT/COLLECTION time, before any in-test `monkeypatch.setattr` can take effect — a per-test monkeypatch is structurally too late to move the `skipif` gate. Fix is either (a) a module-level override point (env var read inside both predicates plus both bodies at `:220-221` / `:286-287`), or (b) rewrite rows 6-7 to describe the mechanism actually used: back up the real `config/reflections.yaml`, round-trip it in place, run the tests, restore. Additionally `filter_reflections_for_machine` only writes when `disabled_names` is non-empty (`tools/reflection_machine_filter.py:145-146`), so the round-trip silently no-ops unless the input contains at least one reflection whose `project_key` maps to a machine other than the running one — seed that condition explicitly or the control proves nothing. |
| BLOCKER | Scope & Value, History & Consistency, Risk & Robustness (3/3; 2 BLOCKER + 1 CONCERN, elevated on cross-validation) | The #2553 `KNOWN_GAP` guard is specified as freezing "the 50 names measured today," but the raw diff `set(model fields) - set(_AGENT_SESSION_FIELDS)` measured today is **51**, and the extra name is `id`, an `AutoKeyField`. The plan never states an AutoKeyField exclusion rule, so a literal implementation is red on the commit that adds it — contradicting the plan's own "Green today" claim and Success Criterion 5's implicit passing baseline. | resolved | `AgentSession._meta.fields` has 89 entries and includes `id` (`AutoKeyField`); `_AGENT_SESSION_FIELDS` has 38. The guard's left-hand set must drop AutoKeyFields before comparison — derive it programmatically (`{n for n, f in AgentSession._meta.fields.items() if not isinstance(f, AutoKeyField)}`) rather than hardcoding `- {"id"}`, so a future second AutoKeyField does not silently reopen the same off-by-one. State the exclusion rule in the Technical Approach and in the `KNOWN_GAP` source comment, and confirm `KNOWN_GAP` is the 50 non-`id` names. Generate `KNOWN_GAP`'s 50 names by running the exclusion-filtered diff at build time and pasting the sorted result, rather than transcribing a count from this plan. |
| CONCERN | History & Consistency | Internal contradiction on the husk guard's design: the Technical Approach says it "must skip `_`-prefixed directories" (a prefix rule), while Risk 4 explicitly rejects a prefix rule in favor of "an explicit named allowlist," and Task 11 follows Risk 4. A builder reading only the Technical Approach implements the rejected variant. | resolved | Rewrite the Technical Approach sentence to match Risk 4 and Task 11. The guard checks `name not in HUSK_GUARD_ALLOWLIST` where `HUSK_GUARD_ALLOWLIST = frozenset({"_shared"})`, not `name.startswith("_")`. Task 11's "verify `_shared` is the only such directory" is the check that keeps the allowlist honest, and it passes today (verified above). |
| NIT | driver (structural) | The plan carried no `## Critique Results` section before this pass; the repo's SDLC verdict substrate reads that section as the record of what the critics said. | resolved | Section added by this critique pass. No builder action required. |
