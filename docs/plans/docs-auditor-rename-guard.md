---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-08-11
tracking: https://github.com/tomcounsell/ai/issues/2711
last_comment_id: 5253708017
revision_applied: true
revision_applied_at: 2026-08-11T13:37:09Z
---

# Docs-Auditor Rename Guard

**This plan combines two issues into one PR**, because they are the fixer and the damage:

| Issue | Role in this plan |
|---|---|
| **#2711** | The auto-fixer invents references to modules that do not exist, and self-commits them. Fix the fixer. |
| **#2713** | A stale doc reference to a deleted module. The damage class the fixer is supposed to handle correctly. |

**Revised bundling rationale (critique finding 4).** The original justification claimed that
splitting would ship a doc correction the unfixed auditor could re-corrupt. That is false and has
been withdrawn: `bridge/session_router.py` contains no `STALE_TERMS` key, and a pure deletion with
no rename record never reaches the rename detectors either — it goes to the advisory issue-filer,
a path neither code change in this plan touches. The two issues cannot interact.

The bundle stands on a weaker but real basis: both are docs-auditor defects, both land in `docs/`
plus one module, and the docs corrected for #2713 join the corpus that Task 5 sweeps with the
auditor's own detectors — which is the verification method borrowed from PR #2528. If the build
turns up any reason these want separate review, split them; nothing in the design couples them.

## Problem

The docs auditor's auto-fix substrate rewrites code references inside documentation and
**commits the result itself**, with no review gate. On 2026-08-10 it rewrote five correct
references into references to a module that has never existed, reported `status: "ok"`, and
committed them as `d7bf3ad99`:

| Before | After | Reality |
|---|---|---|
| `agent/session_logs.py` | `agent/agent_sessions.py` | never existed |
| `bridge/session_logs.py` | `bridge/agent_sessions.py` | never existed |

Only a manual `ls` caught it. The `/do-docs` context file tells the cascade that `ok` means
"treat the substrate's output as done", and explicitly says "do not re-commit the substrate's
changes — it commits them itself". Both instructions assume correctness.

**Current behavior:** a four-entry substring dictionary silently corrupts any documentation
that mentions a path containing one of its keys, and self-commits the corruption.

**Desired outcome:** the auditor cannot introduce a reference to a path that does not exist on
disk. When it wants to, it reports instead of writing.

## Freshness Check

**Baseline commit:** `e48607be3`
**Issues filed at:** #2711 — 2026-08-10T05:54:17Z; #2713 — 2026-08-10T08:04:08Z
**Disposition:** **Major drift** — on #2711's *stated mechanism*, not on its contract.

The issue's Problem and Desired Outcome are correct and still reproduce. Its **Suspected
mechanism** and **Suggested fix** sections are both wrong, and building to them would have
shipped a no-op. This was found during recon, corrected on the issue
([comment 5253708017](https://github.com/tomcounsell/ai/issues/2711#issuecomment-5253708017)),
and this plan builds on the corrected premise. Per repo convention the deviation is named
rather than silently absorbed.

**File:line references re-verified:**

- `reflections/docs_auditor.py:342` `_git_log_follow_renames` — still holds.
- `reflections/docs_auditor.py:387,417,434` — the three rename detectors — still hold, and
  **all three already check old-path absence** (`:407`, `:426`, `:455`). The issue assumed
  they did not.
- `reflections/docs_auditor.py:45` `STALE_TERMS` — **the actual culprit**, not named in the
  issue at all.
- `reflections/docs_auditor.py:467` `_detect_stale_term_fixes` — bare `in` substring test,
  no filesystem access of any kind.
- `reflections/docs_auditor.py:513-516` `_apply_fixes_to_file` — unanchored global
  `str.replace` over the whole document.
- `docs/features/nonharness-llm-wrapper.md:75` — #2713's cited line — still holds.

**Corrected root cause.** `STALE_TERMS` contains `"session_log": "agent_session"`. Detection is
`if old_term not in content`; application is `new_text.replace(old, new)`. Neither is anchored,
so `session_log` matches inside `session_logs` and rewrites the path. Reproduced
deterministically (the issue claimed it could not be):

```python
from reflections.docs_auditor import _detect_stale_term_fixes
s = '| `save_session_snapshot()` | `agent/session_logs.py` | (none) |'
[(old, new)] = _detect_stale_term_fixes(s)      # ('session_log', 'agent_session')
s.replace(old, new)
# '| `save_session_snapshot()` | `agent/agent_sessions.py` | (none) |'
```

The issue's suggested fix (existence checks on the **rename** class) would not have prevented
this: the rename detectors were never involved, `agent/session_logs.py` exists, and
`_detect_renamed_symbol_fixes` correctly skipped it.

**Blast radius is wider than either issue states.** Both scopes were verified by grep sweep,
not taken from the issue text:

- #2711's invented rename is **live on `main` in two more docs** from earlier cascades:
  `docs/features/worker-service.md:189`, `docs/guides/valor-name-references.md:178`.
- #2713's stale `bridge/session_router.py` reference is in **three** live feature docs, not
  one: `nonharness-llm-wrapper.md:75`, `durability-model.md:30`, `message-drafter.md:168`.

**Cited sibling issues/PRs re-checked:**

- PR #2681 / issue #2517 — merged; the cascade that surfaced the bug. `agent/session_logs.py`
  is alive and is the home of `save_session_snapshot()` (`:74`).
- #2058 — closed 2026-07-14; source of the markdown-only apply guard at `:1013`.

**Commits on main since the issues were filed (touching referenced files):** none.
`git log --since=2026-08-08 -- reflections/docs_auditor.py tests/unit/test_docs_auditor_substrate.py`
is empty.

**Active plans in `docs/plans/` overlapping this area:** none.

## Prior Art

- **PR #2528** (merged 2026-08-04) — "Docs: clear all 22 open docs-auditor findings". The direct
  precedent for #2713's fix class: 15 deleted-target issues plus 7 orphan plans, all hand-verified
  before editing, four of which needed a rewritten *claim* rather than a repointed path. It fixed
  the damage but **did not touch the auditor**, which is why #2711/#2713 exist. Its verification
  method — run the auditor's own detectors over the touched docs and require zero findings — is
  adopted here.
- **#2058** (closed 2026-07-14) — added the markdown-only write guard (`:1013`) after the auditor
  wrote into non-`.md` files. Same failure shape: an unbounded auto-fix, constrained after the
  fact. This plan adds the correctness bound that #2058 added the file-type bound for.
- **#2512, #2515, #2513, #2511, #2514, #2509, #2507, #2506, #2505** (all closed 2026-08-04) —
  the deleted-target issue family #2713 belongs to; all resolved by repointing, none by changing
  the detector.

No prior attempt has modified `STALE_TERMS` or `_apply_fixes_to_file`'s matching semantics.

## Research

Purely internal — no external libraries, APIs, or ecosystem patterns are involved. The fix is
confined to this repo's own auditor module and its documentation corpus.

No relevant external findings — proceeding with codebase context.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #2058 (`:1013` markdown-only guard) | Stopped the auditor writing into non-`.md` files | Bounded *which files* the auditor may write, never *what it may write into them*. A wrong edit to a `.md` file is still fully authorized. |
| PR #2528 | Hand-corrected 22 findings | Treated the auditor's output as the unit of work. Left the generator untouched, so the same class of corruption regenerates on the next cascade. |
| `1b1b7a0af` | Reverted `d7bf3ad99` | A revert of one instance. The dictionary entry that produced it is still live and still fires. |

**Root cause pattern:** every prior fix constrained or cleaned up the auditor's *output*. None
established an invariant the auditor's output must satisfy. This plan adds that invariant.

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:** `_apply_fixes_to_file` gains a rejection path and a richer return
  (applied count plus rejected fixes). It is a module-private helper with one call site
  (`audit()` `:1013-1017`), so the change is contained.
- **Coupling:** slightly increases coupling between the apply step and the filesystem — which is
  the point. The apply step currently touches the filesystem only to read and write the doc; it
  must also be able to ask "does this path exist?".
- **Data ownership:** unchanged.
- **Reversibility:** high. The guard is additive and independently revertable from the
  `STALE_TERMS` anchoring.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (the mechanism correction above is the one thing worth a look)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. The fix is a pure-Python change to a
module already in the repo, plus documentation edits.

## Solution

### Key Elements

- **Word-anchored stale terms** — a `STALE_TERMS` entry matches a whole word, never a fragment
  of a longer identifier or path segment. Precision at the source.
- **The existence invariant** — a fix-class-agnostic post-condition on every write: the auditor
  may not introduce a repo-path reference that is absent from the working tree. Defense in
  depth; catches classes the source-level fix does not anticipate.
- **Report instead of write** — a fix that violates the invariant is dropped from the write and
  surfaced (logged, and counted in the result) rather than silently discarded.
- **Residue sweep** — the corrupted references already on `main` are corrected by grep sweep,
  not from an enumerated list.

### Flow

Cascade runs `audit(...)` → detectors propose `(old, new)` fixes → **apply step simulates the
result and checks every newly-introduced path reference against disk** → violating fixes are
rejected and reported; the rest are written → self-commit covers only the surviving fixes.

### Technical Approach

**1. Anchor stale-term matching (`_detect_stale_term_fixes`, `:467`).**
Match on word boundaries so `session_log` does not match inside `session_logs`, and
`SessionLog` does not match inside `SessionLogs`. Because both `\b`-delimited forms fail
against the plural, this alone kills the reported corruption. Detection and application must
share one matching semantics — the present split between them is half the bug.

**Fix-list typing (critique blocker 2).** `audit()` (`:998-1005`) concatenates the output of all
four detectors into a single `list[tuple[str, str]]`, consumed by one loop in
`_apply_fixes_to_file` (`:499-516`) that also carries the `new == ""` line-delete sentinel used by
`_detect_readme_broken_entries` (`:463`). Emitting a `re.Pattern` into that list would break both
`old in new_text` and `.replace()`, and would either crash or force a second matching path — the
exact defect being fixed.

So the anchored replacements travel on a **separate, homogeneous channel**:
`_apply_fixes_to_file(path, repo_root, fixes, regex_fixes=None)`, where
`regex_fixes: list[tuple[re.Pattern[str], str]]` is applied in its own loop via `pattern.subn()`.
The existing `fixes` parameter keeps its `list[tuple[str, str]]` type and its sentinel semantics
**unchanged**, so the other three detectors and the line-delete path are untouched.
`_detect_stale_term_fixes` changes its return type to the regex channel; `audit()` threads it
through as a distinct argument rather than extending the concatenated list.

**2. The existence invariant (`_apply_fixes_to_file`, `:486`).**
Before writing, diff the path-shaped references (`(?:[\w.-]+/)+[\w.-]+\.(?:py|md)`) present in
the candidate text against those in the original. Any reference **newly introduced** by the fix
set that does not exist under `repo_root` invalidates the fix that introduced it. Attribute per
fix — reject that fix, keep the rest — rather than abandoning the whole file.

This is the generalized form of what #2711 asked for, and it independently covers three latent
defects the issue did not name:

- `renames[0][1]` (`:412`, `:430`, `:460`) is the new-side of the newest rename *commit*; for a
  rename-then-delete chain that target does not exist.
- `_detect_renamed_link_fixes` emits a **repo-root-relative** replacement for a **doc-relative**
  link (`:413`), so even a correct rename produces a broken link in any doc outside the repo root.
- Any future `STALE_TERMS` entry whose value happens to form a plausible-but-absent path.

**3. Surface the rejection behaviorally, not in prose (critique concern 3).** A cascade that
branches only on `status` would still read `ok` on an all-rejected run and proceed. So the
withheld set is a first-class, **named** part of the result contract:

| Result key | Type | Meaning |
|---|---|---|
| `fixes_withheld` | `int` | count of fixes rejected by the existence invariant |
| `withheld` | `list[dict]` | one entry per rejection: `{"doc": ..., "old": ..., "new": ..., "reason": "target-absent"}` |

Both keys are added to `_ok_result` (`:92-105`). `status` stays `"ok"` — a withheld fix is not an
error — which is precisely why the count must be separately checkable. Task 4 wires
`.claude/skill-context/do-docs.md` to branch on `fixes_withheld > 0`, and the key name is pinned
here so Task 2 and Task 4 cannot drift apart. Rejections are additionally logged at warning level
naming the offending path.

**4. Sweep the damage.** Correct the two live `agent_sessions.py` references and the
`session_router.py` references, found by grep rather than from the issue text. Two scoping rules,
both load-bearing:

- **Excluded: `docs/plans/`, `docs/plans/completed/`, `docs/research/`.** Several of those
  documents are the records that *specify* the deletion; rewriting them would falsify the history.
  This is also why the Verification greps are scoped to `docs/features/` and `docs/guides/` — a
  repo-wide grep can never reach zero, because this plan document itself names the bad paths
  (critique blocker 1).
- **Excluded: `site/`.** It carries ~17 stale references, but `site/assets/graph.js` is a
  *generated* code-graph artifact and hand-patching it would be clobbered on regeneration. Filed
  as **#2727** (critique concern 5).

**One of the three `session_router.py` hits is a claim, not a path** (critique concern 5).
`docs/features/message-drafter.md:168` reads "give `session_router.py` and other routing readers a
coarse topic hint" — prose about who consumes the value. Mechanical substitution would be wrong;
the sentence is rewritten to name the successor authority (`bridge/job_router.py`). This is
exactly the failure mode PR #2528 hit on four of its 22 findings, where the doc's *claim* had gone
stale rather than just its path.

## Failure Path Test Strategy

### Exception Handling Coverage
`_apply_fixes_to_file` has two `except Exception` blocks (`:494` read failure, `:522` write
failure); both already log a warning and return `0`. Behavior is unchanged by this work and is
covered by an existing test asserting the warning path. The new rejection path is **not** an
exception path — it is a normal-control-flow outcome that must be observable in the return value,
and is asserted as such.

### Empty/Invalid Input Handling
- Empty document, whitespace-only document, and empty fix list: `_apply_fixes_to_file` returns
  `0` and writes nothing (existing `not fixes` guard at `:489`). Tested.
- A fix set where *every* fix is rejected must write nothing and must not create an empty commit.
  Tested explicitly — this is the failure mode that would have produced `d7bf3ad99` as an empty
  or partial commit.
- A `STALE_TERMS` key that appears zero times: no fix emitted. Tested.

### Error State Rendering
The auditor's user-visible surface is its `audit()` result dict and its log lines. The rejection
path asserts on both: a warning naming the rejected path, and a non-zero rejection count in the
result. A rejected fix must never be silently absorbed into `fixes_applied`.

## Test Impact

- [ ] `tests/unit/test_docs_auditor_substrate.py::TestStaleTermDictionary` — UPDATE: existing
      cases assert unanchored substring behavior; re-assert against word-anchored semantics.
- [ ] `tests/unit/test_docs_auditor_substrate.py` — ADD `TestStaleTermWordBoundary`: the
      `agent/session_logs.py` regression as a hard assertion (currently reproduces in 3 lines,
      untested).
- [ ] `tests/unit/test_docs_auditor_substrate.py` — ADD `TestExistenceInvariant`: a fix
      introducing an absent path is rejected, reported, and not written; sibling valid fixes in
      the same file still apply.
- [ ] `tests/unit/test_docs_auditor_substrate.py::TestGitLogFollowCap` — UNCHANGED: tests the
      query cap only, unaffected.

The three rename detectors have **zero** direct coverage today; the existence-invariant tests
give them their first behavioral test through the shared apply path.

## Rabbit Holes

- **Rewriting the rename detectors to walk the full rename chain.** Tempting, and `renames[0][1]`
  really is imprecise, but the existence invariant makes a wrong hop *fail safe* rather than
  corrupt. Correct hop selection is a precision improvement, not a correctness one, and it needs
  its own multi-hop fixture corpus. Out of scope.
- **Fixing the doc-relative vs repo-root link path bug (`:413`) properly.** Same reasoning: the
  invariant demotes it from "writes a broken link" to "declines to write". Filing separately
  beats bundling a path-resolution rewrite into a guard PR.
- **Auditing every `STALE_TERMS` entry for other collisions.** The anchoring fix makes the whole
  dictionary safe by construction; enumerating hypothetical collisions is the checklist trap.
- **Removing the auditor's self-commit.** Arguably the deepest fix — a self-committing auto-fixer
  has no review gate at all — but that is a workflow decision about the `/do-docs` contract, not
  a bug fix, and it would change how every cascade behaves.
- **Sweeping `docs/plans/` and `docs/research/`.** Actively wrong. Those are historical records.

## Risks

### Risk 1: Word-anchoring silently disables a stale-term rename that was legitimately firing on a compound identifier
**Impact:** A genuinely stale term stops being auto-corrected, and docs drift without signal.
**Mitigation:** All four `STALE_TERMS` entries are model/field names (`SessionLog`, `RedisJob`,
`session_log`, `redis_job`) whose correct usages are standalone words. Grep the docs corpus for
each key before and after anchoring and diff the match sets; any match lost to anchoring is
inspected by hand and recorded in the PR body.

### Risk 2: The existence check misfires on legitimately non-repo paths
**Impact:** Valid fixes are rejected; the auditor becomes less useful.
**Mitigation:** The invariant applies only to references **newly introduced** by a fix, and only
to `dir/file.{py,md}`-shaped tokens resolved under `repo_root`. Paths already present in the
document are never re-validated, so the guard cannot reject a fix over pre-existing prose.

### Risk 3: The corrupted references are load-bearing somewhere beyond docs
**Impact:** Correcting them breaks a consumer.
**Mitigation:** Verified — both residue sites are prose table cells in human-facing docs
(`worker-service.md`, `valor-name-references.md`). Neither path is imported, globbed, or parsed.

## Race Conditions

No race conditions identified. `_detect_stale_term_fixes` and `_apply_fixes_to_file` are
synchronous, single-threaded, operate on a single file at a time, and share no mutable state
beyond the module-level `_RENAME_QUERY_COUNT` counter, which this work does not touch. The
auditor's cross-process concurrency is already handled upstream by the Redis run lock
(`REDIS_RUNNING_KEY`, `LOCK_TTL_SECONDS`), which this change does not modify.

## No-Gos (Out of Scope)

Note that #2713 is **not** deferred — it is bundled *into* this plan and closed by this PR.

- [SEPARATE-SLUG #2725] Rename-target selection: `renames[0][1]` takes the newest rename *commit*
  rather than the final hop, and `_detect_renamed_link_fixes:413` resolves a doc-relative link
  against the repo root. Both found during this plan's recon. Deferred because the existence
  invariant added here demotes each from "writes a wrong reference" to "declines to write", making
  them precision defects rather than correctness ones — and correct hop selection needs its own
  multi-hop fixture corpus. The Verification table carries an anti-criterion asserting no
  rename-chain rewrite lands in this diff.
- [SEPARATE-SLUG #2726] The auditor is its own committer (`_commit_current_branch:1072`, and
  `git add -A` at `:1246` on the rotation path, which can auto-merge). This is the structural
  reason a generator bug reaches `main`, but choosing between stage-and-report, an explicit path
  list, and a review requirement is a decision about the `/do-docs` contract that needs an owner
  ruling — not a bug with one correct fix.

## Update System

No update system changes required — this is a behavior fix inside an existing module that
`/update` already propagates by pulling `main`. No new dependencies, config files, or migration
steps. `reflections/docs_auditor.py` is imported by the reflections runner, which picks up the
change on the next service restart that `/update` already performs.

## Agent Integration

No agent integration required. `reflections.docs_auditor` is already reachable through its
existing surfaces — the `python -m reflections.docs_auditor` CLI entry (`:1953`) and the direct
import used by the `/do-docs` cascade. This plan changes the behavior of existing functions and
adds no new callable surface, so no `pyproject.toml [project.scripts]` entry and no bridge import
are needed.

## Documentation

- [ ] Update `docs/features/docs-auditor.md` to document the existence invariant and the
      word-anchored stale-term semantics, including that rejected fixes are reported rather than
      applied.
- [ ] Correct `docs/features/worker-service.md:189` — `agent/agent_sessions.py` →
      `agent/session_logs.py`.
- [ ] Correct `docs/guides/valor-name-references.md:178` — `bridge/agent_sessions.py` →
      `bridge/session_logs.py`.
- [ ] Repoint the two `bridge/session_router.py` **path** references (#2713):
      `docs/features/nonharness-llm-wrapper.md:75`, `docs/features/durability-model.md:30`.
- [ ] Rewrite the `bridge/session_router.py` **claim** at `docs/features/message-drafter.md:168` —
      prose about who consumes the routing hint, so it names the successor authority
      (`bridge/job_router.py`), not a substituted filename.
- [ ] Update `.claude/skill-context/do-docs.md` so the cascade no longer treats `status: "ok"`
      as "output is correct": it must branch on `fixes_withheld > 0` and re-check before trusting
      the substrate's self-committed output.

## Success Criteria

- [x] `_detect_stale_term_fixes` does not emit a fix for `agent/session_logs.py` or
      `bridge/session_logs.py` (the #2711 regression, as a hard assertion).
- [x] `_apply_fixes_to_file` rejects and reports any fix introducing a path absent from the
      working tree, while still applying valid sibling fixes in the same file.
- [x] `audit()` reports a `fixes_withheld` count and a `withheld` list; an all-rejected run
      writes nothing and still surfaces the withheld count.
- [x] Zero live references to `agent/agent_sessions.py` or `bridge/agent_sessions.py` in
      `docs/features/` and `docs/guides/` (the plan doc and `docs/plans/` legitimately name them).
- [x] Zero references to `bridge/session_router.py` in `docs/features/`, with `docs/plans/`,
      `docs/research/`, and `site/` untouched.
- [x] The auditor's own detectors report zero findings over every doc this PR touches (the
      PR #2528 verification method).
- [x] Tests pass (`/do-test`) — `scripts/pytest-clean.sh`, scoped to
      `tests/unit/test_docs_auditor_substrate.py`.
- [x] Documentation updated (`/do-docs`).
- [x] Both #2711 and #2713 closed from the PR.

## Team Orchestration

### Team Members

Trimmed to one builder plus one validator for a Small plan (critique nit): the code change and the
doc sweep are ~40 lines and ~6 edits respectively, and a second agent costs more handoff context
than it saves. The doc edits are folded into the builder's scope.

- **Builder (auditor-guard)**
  - Name: `auditor-guard-builder`
  - Role: Both code changes in `reflections/docs_auditor.py`, their tests, the doc sweep, and the
    `docs-auditor.md` / `do-docs.md` updates
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: `auditor-validator`
  - Role: Verifies every Success Criterion and every Verification row
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Anchor stale-term matching

- **Task ID**: build-anchor
- **Depends On**: none
- **Validates**: `tests/unit/test_docs_auditor_substrate.py`
- **Assigned To**: auditor-guard-builder
- **Agent Type**: builder
- **Parallel**: false
- Change `_detect_stale_term_fixes` (`:467`) to match `STALE_TERMS` keys on word boundaries.
- Return the anchored replacements on the **new `regex_fixes` channel**
  (`list[tuple[re.Pattern[str], str]]`), applied in `_apply_fixes_to_file` by its own
  `pattern.subn()` loop. Do **not** put a `re.Pattern` into the existing `fixes` list: it is
  shared with three other detectors and carries the `new == ""` line-delete sentinel (`:463`,
  `:502-511`), and `old in new_text` / `.replace()` both break on a pattern object. `fixes` keeps
  its `list[tuple[str, str]]` type unchanged.
- Thread `regex_fixes` through `audit()` (`:998-1017`) as a distinct argument rather than
  extending the concatenated list.
- Preserve the existing `migration_context` escape hatch unchanged.
- Add `TestStaleTermWordBoundary` with the `agent/session_logs.py` and `bridge/session_logs.py`
  regressions as hard assertions.
- Before/after grep of each `STALE_TERMS` key over `docs/`, diffing the match sets; record any
  match lost to anchoring in the PR body (Risk 1).

### 2. Existence invariant on apply

- **Task ID**: build-invariant
- **Depends On**: build-anchor
- **Validates**: `tests/unit/test_docs_auditor_substrate.py`
- **Assigned To**: auditor-guard-builder
- **Agent Type**: builder
- **Parallel**: false
- In `_apply_fixes_to_file` (`:486`), reject any fix that newly introduces a
  `dir/file.{py,md}`-shaped reference absent under `repo_root`.
- Attribute rejection per fix; valid sibling fixes in the same file still apply.
- Never re-validate references already present in the document.
- Log rejections at warning level naming the offending path, and add the pinned keys
  `fixes_withheld` (int) and `withheld` (list of `{"doc","old","new","reason"}`) to `_ok_result`
  (`:92-105`). Use exactly these names — Task 4 branches on them.
- Add `TestExistenceInvariant` covering: rejection, reporting, sibling-fix survival, and the
  all-fixes-rejected case writing nothing.

### 3. Sweep the residue and the stale refs

- **Task ID**: build-docs-sweep
- **Depends On**: none
- **Validates**: the three residue/stale-ref Verification rows (grep-based, no test file)
- **Assigned To**: auditor-guard-builder
- **Agent Type**: builder
- **Parallel**: true
- Grep sweep for `agent/agent_sessions.py` and `bridge/agent_sessions.py` across `docs/features/`
  and `docs/guides/`; correct every hit to `agent/session_logs.py` / `bridge/session_logs.py`.
- Grep sweep for `session_router.py` across `docs/features/` only. Two hits are paths and get
  repointed; `message-drafter.md:168` is a prose **claim** and gets its sentence rewritten to name
  `bridge/job_router.py` — do not substitute the filename.
- Leave `docs/plans/`, `docs/plans/completed/`, `docs/research/`, and `site/` untouched. The first
  three are historical records of the deletion; `site/` is generated and tracked as #2727.

### 4. Documentation

- **Task ID**: document-feature
- **Depends On**: build-invariant, build-docs-sweep
- **Validates**: `docs/features/docs-auditor.md` and `.claude/skill-context/do-docs.md` exist and
  mention `fixes_withheld`
- **Assigned To**: auditor-guard-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `docs/features/docs-auditor.md` with the invariant and the anchored semantics.
- Update `.claude/skill-context/do-docs.md` so `status: "ok"` no longer implies "output verified":
  branch on `fixes_withheld > 0`.

### 5. Final validation

- **Task ID**: validate-all
- **Depends On**: build-anchor, build-invariant, build-docs-sweep, document-feature
- **Validates**: every row of the `## Verification` table
- **Assigned To**: auditor-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification row and every Success Criterion.
- Run the auditor's own detectors over each touched doc and require zero findings.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Scoped tests pass | `scripts/pytest-clean.sh tests/unit/test_docs_auditor_substrate.py -q` | exit code 0 |
| Lint clean | `python -m ruff check reflections/ tests/unit/test_docs_auditor_substrate.py` | exit code 0 |
| Format clean | `python -m ruff format --check reflections/ tests/unit/test_docs_auditor_substrate.py` | exit code 0 |
| #2711 regression dead | `python -c "from reflections.docs_auditor import _detect_stale_term_fixes as d; print(len(d('\`agent/session_logs.py\`')))"` | output contains 0 |
| No invented-module residue | `grep -rn "agent/agent_sessions.py\|bridge/agent_sessions.py" docs/features/ docs/guides/ \| wc -l` | output contains 0 |
| #2713 refs gone from features | `grep -rn "session_router.py" docs/features/ \| wc -l` | output contains 0 |
| Historical records preserved | `grep -rln "session_router.py" docs/plans/ docs/research/ \| wc -l` | output > 0 |
| Withheld count is reported | `python -c "import inspect,reflections.docs_auditor as m; print('fixes_withheld' in inspect.getsource(m._ok_result))"` | output contains True |
| Anti-criterion: site/ untouched | `git diff main --name-only -- site/ \| wc -l` | output contains 0 |
| Anti-criterion: no rename-chain rewrite | `git diff main --unified=0 -- reflections/docs_auditor.py \| grep -c "renames\[-1\]\|for old, new in renames"` | match count == 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | History & Consistency + structural | The "No invented-module residue" Verification row and the matching Success Criterion grep `agent/agent_sessions.py`/`bridge/agent_sessions.py` across all of `docs/` and require 0, but this plan file (`docs/plans/docs-auditor-rename-guard.md`, 8 hits at lines 34, 35, 84, 347, 349, 363, 439, 473) is itself in `docs/` and the plan's own No-Gos forbid sweeping `docs/plans/`. The check can never pass while the plan exists on the branch. | pending | Scope the residue grep the way the `session_router.py` row already is scoped: `grep -rn "agent/agent_sessions.py\\|bridge/agent_sessions.py" docs/ --exclude-dir=plans --exclude-dir=research \| wc -l` → 0, and reword the Success Criterion to "zero references outside `docs/plans/` and `docs/research/`". Note `--exclude-dir=plans` also excludes `docs/plans/completed/`. |
| BLOCKER | Risk & Robustness (Skeptic) | Technical Approach item 1 tells `_detect_stale_term_fixes` to emit "a regex-anchored replacement rather than a bare `(old, new)` string pair", but `audit()` (`reflections/docs_auditor.py:999-1005`) concatenates all four detectors into one `fixes: list[tuple[str, str]]` consumed by a single loop in `_apply_fixes_to_file` (`:503-517`). The other three detectors still emit plain string tuples, including the `new == ""` line-delete sentinel. The plan's Interface changes section never names this element-type change, so the builder either hits a `TypeError` or forks a second matching path — re-creating the detection/application semantics split that is half the present bug. | pending | Define one fix element type all four detectors emit (e.g. a `FixSpec` dataclass with `kind: Literal["literal","regex","line-delete"]`, `pattern`, `replacement`) and dispatch on `kind` inside `_apply_fixes_to_file`. `old in new_text` and `new_text.replace(old, new)` (`:513-516`) both break on a `re.Pattern`; the `new == ""` branch (`:504-512`) must keep its exact-line-equality semantics, not become a regex. Name the element-type change under Architectural Impact → Interface changes. |
| CONCERN | Risk & Robustness (Operator) | Technical Approach item 3 surfaces rejected fixes only as a warning log plus a new field in the `audit()` result, and the Documentation task only asks that `.claude/skill-context/do-docs.md` "mention" withheld fixes. The cascade's parse block branches only on `status`, so an all-rejected run still reports `status: "ok"` and the cascade proceeds unchanged — the same "ok gave no signal to re-check" failure #2711 named, moved one level up the stack. | pending | Make the do-docs context change behavioral, not prose: add a third branch alongside the existing `status: "ok"` / `"error"` / `"disabled"` checks — if the result's rejected-fix list is non-empty, the cascade must echo the rejected paths into its own transcript output before Step 3. Pick the result key name in Task 2 and use the same literal in Task 4 so writer and reader cannot drift. |
| CONCERN | Scope & Value (User) | The stated justification for bundling #2713 ("Splitting them would ship a doc correction that the unfixed auditor could re-corrupt on its next cascade") is unsupported for #2713's damage class: deleted-target references are handled by the advisory issue-filer (`reflections/docs_auditor.py:1017-1026`, "Editorial, not auto-fixable"), a path neither code change in this plan touches. The bundle may still be right, but the reason given is not the real one. | pending | `bridge/session_router.py` is a pure deletion, not a rename (`bridge/job_router.py:4` documents it as the successor, `bridge/telegram_bridge.py:1289` calls it "retired"), so `_git_log_follow_renames` returns `[]` and the rename detectors never fire on it. Replace the coupling claim with the accurate one: the docs sweep is the regression corpus for the invariant (Success Criterion "the auditor's own detectors report zero findings over every doc this PR touches"), not a re-corruption risk. |
| CONCERN | structural (cross-reference) | The #2713 sweep is scoped to `docs/features/` only, but a live user-facing reference to the retired module survives outside it: `site/runtime.html:107` carries `file:bridge/session_router.py` in a `data-files` chip on the published site. The auditor's markdown-only write guard means that surface can never self-heal. Separately, `docs/features/message-drafter.md:168` is a prose *claim* about who consumes the routing hint, not a path reference — the PR #2528 precedent this plan adopts found 4 of 15 such cases needed a rewritten claim rather than a repointed path. | pending | Extend Task 3's sweep to `site/` and reword the Success Criterion to name both surfaces; the correct target is `bridge/job_router.py` (`bridge/job_router.py:4`: "``bridge/session_router.py`` semantic session router. Structure is modeled..."). For `message-drafter.md:168`, rewrite the sentence to name `job_router.py` as the routing reader rather than mechanically substituting the path inside a claim about behavior. |
| NIT | structural | Tasks 3 (`build-docs-sweep`), 4 (`document-feature`), and 5 (`validate-all`) carry no `Validates:` field, so three of five tasks have no per-task validation command. Task numbering, `Depends On` references, and file paths all check out otherwise. | pending | n/a |
| NIT | Scope & Value (Simplifier) | A `Small` / solo-dev plan allocates two builder agents plus a validator for a one-module change and a handful of doc corrections; Task 4 blocking on both builders is the only real coupling point. | pending | n/a |
</content>
