---
status: docs_complete
type: bug
appetite: Small
owner: Valor Engels
created: 2026-06-15
tracking: https://github.com/tomcounsell/ai/issues/1690
last_comment_id:
revision_applied: true
---

# /do-plan-critique: Artifact-Based Roster Barrier for War-Room Critics

## Problem

`/do-plan-critique` spawns its seven war-room critics (Skeptic, Operator, Archaeologist, Adversary, Simplifier, User, Consistency Auditor) as **background tasks** via `run_in_background: true`, then relies on **prose instructions** ("you MUST wait and collect", "poll/await all six background agents") to make the driving subagent block until every critic finishes before synthesizing the verdict.

Prose is not an enforceable barrier. The driving subagent is itself an LLM that can — and did — return early, recording the verdict before late-arriving critics finished. Their findings are then silently dropped.

**Current behavior:**
- The skill spawns critics with `run_in_background: true` (`SKILL.md:154`, `CRITICS.md:7`).
- Step 3.5 ("Wait and Collect", added v1.3.0 / #1654) tells the subagent in prose to block on all background critics before aggregating (`SKILL.md:171-179`).
- Despite that prose barrier, in the #1681 / PR #1689 run a late-arriving **Adversary** critic measured (on a live transcript) that the shipped `mtime`-advancement freshness guard was defeated ~34% of tool-using turns — a measured **BLOCKER** — but the finding landed *after* the verdict was already recorded as "READY TO BUILD, 0 blockers." The bug shipped into a green PR and had to be patched post-build (`c478c13d`).
- The supervisor also reported the critique stage **stalled 3× before** in the same run: the driving subagent returned before any critic completed, so no verdict was recorded at all and the loop re-dispatched.

**Desired outcome:**
A critique run **cannot** record a verdict until the **full expected critic roster has each written a result artifact to a known file path**. Synthesis asserts every expected roster file is present — a **membership check against the named roster**, not a driver-controlled count — before recording any verdict. The barrier is **independently verifiable from the filesystem**: it does not depend on the LLM driver choosing to await, and it can be exercised directly in a regression test by creating or omitting result files and asserting the gate's behavior. If the roster is still incomplete after a **bounded number of re-dispatch attempts** (named cap), the stage emits a STOP-grade `MAJOR REWORK (CRITIQUE INCOMPLETE)` verdict that the SDLC router (G1) treats as blocked — never a silent green, never a forever-`in_progress` stall.

### Why artifact-based, not "foreground batch + trust the driver" (revision rationale)

The prior revision of this plan proposed a **single-message foreground Agent batch** as a "structural" await-all barrier. The war-room critique (NEEDS REVISION, 5 blockers) correctly rejected that direction: the foreground-batch await-all is *asserted* harness behavior, **verified and tested nowhere**, so the plan recursively repeated the very error class it diagnoses — relying on the driver/harness behaving rather than enforcing the invariant from a verifiable artifact. A foreground batch is still an LLM emitting tool calls; nothing in a markdown skill *proves* the harness blocks, and a future harness change could silently weaken it (the prior plan even conceded this in its own Risk 2). Worse, a bare `len(collected) == len(dispatched)` count is trivially satisfied by **under-dispatch** (dispatch 1, collect 1, passes).

The fix that does **not** depend on driver compliance: each critic **writes its findings to a known result file**, and synthesis is a **filesystem membership check against the named roster** (every expected critic's file must exist, with a structured "completed" sentinel inside). The files are real, inspectable, and assertable by a test that creates/omits them and checks the gate behaves. This is the direction the critique recommended and is what this revision adopts. It resolves the dropped-findings bug (B1), the under-dispatch loophole (B2), the empty-vs-incomplete conflation (B3), and the undefined fail-loud contract (B4) together.

## Freshness Check

**Baseline commit:** `0d000e59cf39304b0861e93240d5623aad6f43f3`
**Issue filed at:** 2026-06-15T04:00:18Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `.claude/skills-global/do-plan-critique/SKILL.md:154` — "Use `run_in_background: true` for all six" — **still holds** (and the count is stale: seven critics now run, see CRITICS.md §"Critic Selection").
- `.claude/skills-global/do-plan-critique/SKILL.md:171-179` — Step 3.5 "Wait and Collect" prose barrier — **still holds**. This is the v1.3.0 fix that the issue's run post-dates: the barrier was already present and still failed.
- `.claude/skills-global/do-plan-critique/CRITICS.md:7` — "spawned as a parallel Agent tool call with `run_in_background: true`" — **still holds**.
- `.claude/skills-global/do-plan-critique/SKILL.md:253-294` — Step 5.5 (verdict record + completion marker) and Step 5.6 (plan_revising lock) — **still holds**, unchanged by this work.

**Cited sibling issues/PRs re-checked:**
- #1681 — closed 2026-06-15T06:58:22Z; PR #1689 merged 2026-06-15T06:58:21Z. This is the run where the BLOCKER was dropped. Both landed *after* the v1.3.0 prose barrier (commit `6e943ea9`, 2026-06-13 02:05), confirming the prose barrier does not hold.
- #1654 — closed 2026-06-12T19:05:44Z. Added Step 3.5 prose "Wait and Collect" and the mandatory Step 5.5 finalize block. **This is the fix that proved insufficient** — it removed the "stall → no verdict" path *in prose* but left the spawn pattern fire-and-forget, so the LLM driver could still return early.

**Commits on main since issue was filed (touching referenced files):** None. The critique skill files are unchanged since `0d04f4ac` (2026-06-13).

**Active plans in `docs/plans/` overlapping this area:**
- `docs/plans/sdlc-plan-critique-revision.md` — concerns the revision *routing* after critique, not the critic-await barrier. No overlap with this fix.
- `docs/plans/pm-skips-critique-and-review.md` — concerns the router skipping critique entirely, not the in-skill barrier. No overlap.

**Notes:** The issue says "six" critics; the skill actually runs **seven** (Consistency Auditor was added). The fix must cover all dispatched critics, not a hardcoded six. The issue's "v1.3.0 already has a wait-and-collect step" is the *reason* this is not a duplicate: the prose barrier exists and demonstrably failed in a post-v1.3.0 run. The fix replaces the spawn mechanism, not the prose.

## Prior Art

- **#1654** ("war-room never finalizes: critics complete but verdict stays in_progress"): Added the Step 3.5 "Wait and Collect" prose barrier and the mandatory Step 5.5 finalize block (commit `6e943ea9`, v1.3.0, 2026-06-13). Outcome: removed the stall *in prose* but the BLOCKER-drop recurred in the #1681 run the next day. Directly relevant — this plan supersedes the spawn-mechanism half of that fix.
- **#1671 / #1672 / PR #1673** (commit `0d04f4ac`): Hardened Step 5.5 session resolution (`--issue-number` beats env-var on writes). Adjacent to verdict recording but orthogonal to the await barrier. Must not regress: the fix keeps Step 5.5 intact.
- No prior issue attempted to replace the `run_in_background` + prose-await spawn pattern with a **verifiable artifact-based roster barrier**. This is the first such attempt. (An earlier revision of *this* plan tried a foreground-batch "await-all" but was rejected at critique for being unverifiable prose; see the revision rationale under Problem.)

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #1654 (v1.3.0) | Added Step 3.5 prose: "you MUST wait and collect … block on every background critic before aggregating." | The barrier is **advisory text aimed at an LLM**, not a harness-enforced gate. `run_in_background: true` makes the Agent results retrievable later but does not *require* the driver to retrieve them before continuing. A driver that returns early (or that the supervisor resumes early) skips Steps 4/5/5.5 entirely or synthesizes on a partial set. Prose cannot enforce a synchronization invariant that the tool semantics leave optional. |

**Root cause pattern:** The synchronization point relies on the LLM *choosing* to block (or, in the prior revision, on the harness *being asserted to* block), when the invariant should be enforced by a **verifiable artifact** the synthesis step can check independently of any driver behavior. Any barrier that lives only in prose — "await", "poll", "issue in one message" — is unverifiable from outside the run and can be silently weakened by an early-returning driver or a future harness change. The fix is to make completion **observable on the filesystem**: each critic writes a result file; synthesis asserts the full named roster of result files exists before recording any verdict. A test can then create and omit those files to prove the gate holds — the barrier is checkable, not merely asserted.

## Architectural Impact

- **New dependencies:** None. The fix is skill-prose (`.claude/skills-global/do-plan-critique/`) plus a small helper the skill calls to perform the roster membership check, plus one regression test.
- **New artifact surface:** Each critic writes a result file under a per-run directory `${CRITIQUE_RUN_DIR}` (default `.critique-runs/{issue|slug}-{timestamp}/`). Files are run-scoped, git-ignored, and cleaned up after the verdict is recorded. No persistent state, no Redis, no Popoto model.
- **Interface changes:** None to any Python API surface the pipeline consumes. The change is to *how* the critique skill instructs the Agent tool to spawn critics (each writes a result file) and adds one internal helper `tools/critique_roster_check.py` (CLI: `critique-roster-check`) that the skill invokes to perform the membership check and emit the gate decision.
- **Coupling:** Decreases coupling on driver/supervisor cooperation; introduces a small, explicit coupling to the result-file convention (roster names + sentinel format), which is the point — it is the verifiable contract.
- **Data ownership:** Unchanged. Step 5.5 (verdict record) and Step 5.6 (plan_revising lock) remain the sole owners of verdict/marker state. The roster-check helper only *reads* result files and *returns* a gate decision; it never records a verdict itself.
- **Reversibility:** Reversible — revert the skill edits, the helper, and the test; the `.critique-runs/` dir is ephemeral.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (the "fail loudly" contract is now resolved in this plan — see Resolved Questions; no open sign-off remains)
- Review rounds: 3 (first war-room critique: NEEDS REVISION, 5 blockers — all resolved; second re-critique: NEEDS REVISION, 2 new blockers — terminal-sentinel/atomic-write and prose-invariant enforcement, both resolved in this revision; then a final re-critique, then standard PR review)

## Prerequisites

No prerequisites — this work modifies skill markdown, adds one small Python helper plus its CLI entry, and one pytest file; no external services or secrets are required.

## Resolved Questions (incorporated into the plan body)

The first critique flagged that the plan's two Open Questions left the fail-loud contract undecided while the No-Gos claimed "nothing deferred" — a self-contradiction (B5). Both questions are now **resolved and folded into the design** below; the Open Questions section is removed (see B5 resolution).

**RQ1 — "Fail loudly" surface (was Open Question #1, B4):** When the full critic roster cannot be reached after the bounded re-dispatch cap, the skill **records a `MAJOR REWORK (CRITIQUE INCOMPLETE)` verdict via the normal Step 5.5 path**, then sets the plan_revising lock (Step 5.6) as for any revision-grade verdict. It does **not** exit non-zero with no verdict. Rationale, grounded in the live router contract (`.claude/skills-global/sdlc/SKILL.md:149`, guard **G1**): G1 routes any verdict containing `MAJOR REWORK` back to `/do-plan` — a concrete, router-consumable STOP that surfaces the incompleteness to a human via the revision pass. Recording a verdict (rather than exiting silent) guarantees the stage never lingers at `in_progress`, eliminating the forever-stall risk the critique called out as "worse than a silent green." The verdict string is `MAJOR REWORK (CRITIQUE INCOMPLETE: roster N/M — missing: {critic names})` so the substring `MAJOR REWORK` matches G1 verbatim and the human sees exactly which critics never reported.

**RQ2 — Roster reconciliation granularity (was Open Question #2, B2):** The barrier reconciles against the **actually-dispatched named roster**, computed once at dispatch time from CRITICS.md's selection rules (seven critics; six when Archaeologist + User are skipped for a Small purely-internal plan). The roster is **frozen and written to a manifest file `${CRITIQUE_RUN_DIR}/_roster.json` before any critic is dispatched**, so the membership check is against a fixed, recorded set — not a count the driver can shrink. This is what closes the under-dispatch loophole (B2): you cannot satisfy the gate by dispatching fewer critics, because the gate checks each *named* roster member's result file exists, against the pre-recorded manifest.

## Solution

### Key Elements

- **Per-critic result artifacts (the verifiable barrier, B1)**: Every critic is instructed to **write its findings to a known file** `${CRITIQUE_RUN_DIR}/{critic_name}.result.md`. The critic writes its findings body FIRST and, only as its final action, appends a **two-line terminal completion fence**: the unique delimiter line `<<<CRITIQUE-RESULT-COMPLETE>>>` immediately followed by `STATUS: COMPLETED` as the final line (see terminal-fence rationale below). The write is **atomic**: the critic writes the full file to `${CRITIQUE_RUN_DIR}/{critic_name}.result.md.tmp` and then renames it to the canonical `{critic_name}.result.md` — and `.tmp` and the canonical file live in the same run-dir (same filesystem), so the rename is atomic and a partial/truncated file is never observed at the canonical path. Synthesis does not read any critic's output from the conversation; it reads the result files. Completion is therefore observable on the filesystem, independent of whether the driver awaited.
- **Frozen roster manifest (closes under-dispatch, B2)**: Before dispatching, the skill computes the roster from CRITICS.md selection rules and writes `${CRITIQUE_RUN_DIR}/_roster.json` (list of expected critic names + the count). The gate is a **membership check**: for every name in the manifest, the corresponding `{name}.result.md` must exist and carry the terminal completion fence. A bare equality between two driver-controlled counts is never used.
- **Terminal completion fence — structurally unforgeable (closes the truncated-write hole AND the token-collision hole + distinguishes "no findings" from "did not complete", NEW-B1 + NEW-B1b + B3)**: The completion marker is a **two-line terminal fence**, not a bare substring. The **penultimate non-empty line** must be the unique delimiter `<<<CRITIQUE-RESULT-COMPLETE>>>` and the **last non-empty line** must be exactly `STATUS: COMPLETED`. The helper verifies BOTH lines in terminal position. The delimiter token `<<<CRITIQUE-RESULT-COMPLETE>>>` is chosen specifically because no critic would emit it in normal findings prose — unlike the bare string `STATUS: COMPLETED`, which critics in *this* skill routinely quote when reviewing text (this very plan contains that bare line many times). Requiring the fence makes the marker impossible to forge by ordinary critic output:
  - **Penultimate non-empty line is `<<<CRITIQUE-RESULT-COMPLETE>>>` AND last non-empty line is `STATUS: COMPLETED`** — the critic finished writing its entire body and then stamped the terminal fence as its deliberate final action; the body above it holds 0-3 findings or the literal `No findings.` (a legitimate completed-empty result).
  - **Absence of the file, OR a file missing either fence line in terminal position** — means "did not complete." This is the crux of the NEW-B1/NEW-B1b fix: a critic that crashes or truncates mid-write, OR whose findings body merely happens to *end on* the bare string `STATUS: COMPLETED` (a quoted token) without the deliberate preceding delimiter, has NOT emitted the full two-line fence — so its file fails the gate (loud STOP/re-dispatch). An incomplete write can never pass as complete, and the bare token can never be forged by quoted prose. A completed-empty critic is NOT conflated with a missing/truncated critic — the terminal fence disambiguates them explicitly.
  - **Why a two-line fence, not a bare last-line substring:** (1) *Truncation* — a first-line sentinel is written before the body, so a critic that writes line 1 then truncates would pass the gate with empty/garbage findings; making the marker terminal means presence-of-fence ⇔ body-fully-written. (2) *Token collision* — even a terminal bare `STATUS: COMPLETED` is forgeable: critics here routinely quote that exact string, so a critic whose body legitimately ends on it and then truncates *before* its deliberate append produces a renamed file whose last line IS the bare sentinel, passing a single-line gate with a partial body. Requiring the unique delimiter `<<<CRITIQUE-RESULT-COMPLETE>>>` as the penultimate line — a token no critic emits in prose — closes that collision: the fence is present only when a critic deliberately stamped it. The atomic `.tmp`→rename further guarantees the canonical path is never observed mid-write (and closes the re-dispatch double-write race: a re-dispatched critic's partial overwrite is never visible at the canonical name).
- **Bounded re-dispatch with a named cap (B3)**: `MAX_CRITIC_REDISPATCH = 2`. If, after collecting, any roster member's result file is missing or lacks the terminal completion fence, the skill re-dispatches **only the missing critics** and re-checks, up to `MAX_CRITIC_REDISPATCH` rounds. The total attempt budget is pinned explicitly: **1 initial dispatch + up to 2 re-dispatches = 3 attempts maximum** per critic. The cap is explicit and named — there is no unbounded retry loop.
- **STOP-grade verdict on incomplete roster (B4)**: If the roster is still incomplete after `MAX_CRITIC_REDISPATCH` rounds, the skill records `MAJOR REWORK (CRITIQUE INCOMPLETE: roster N/M — missing: {names})` through Step 5.5 and sets the plan_revising lock (Step 5.6). The router's G1 treats it as a STOP and routes to `/do-plan`. The stage always produces a verdict; it never returns empty and never stalls at `in_progress`.
- **Roster-check helper (makes the gate verifiable, B1 + test criterion)**: A small `tools/critique_roster_check.py` (CLI `critique-roster-check --run-dir ...`) reads `_roster.json` and the result files and prints a JSON gate decision: `{"complete": bool, "missing": [...], "present": [...], "roster_count": M, "completed_count": N}`. A member is "completed" iff its `{name}.result.md` file exists AND its **last two non-empty lines** are, in order, `<<<CRITIQUE-RESULT-COMPLETE>>>` (penultimate) then `STATUS: COMPLETED` (last) (NEW-B1/NEW-B1b: terminal two-line fence, never a first-line or bare-substring match). `completed_count` is simply the count of roster members whose files pass this terminal-fence check — it proves *how many named roster members reported a deliberately-stamped completion fence*, nothing more (it does not prove the findings bodies are well-formed, only that each was fully written and stamped). The skill calls the helper instead of eyeballing files. Because it is plain Python over real files, the regression test exercises it directly — create a run dir, write some result files, omit others, write one whose body merely ends on the bare `STATUS: COMPLETED` line WITHOUT the preceding delimiter and assert it is NOT counted complete, assert the gate decision — which is exactly the independently-verifiable check the critique demanded (B-criterion 4).
- **Honest scope of "independent of the driver" (NEW-B2)**: The *helper* (`critique-roster-check`) is independently verifiable from the filesystem and directly testable — that is real and load-bearing. But the *invocation* of the helper before recording the verdict, and the instruction in Step 4 to aggregate from EVERY roster file, remain **driver steps in skill prose**. This plan does NOT claim the driver is forced by the harness to call the gate; it claims the gate itself is verifiable AND that the driver's obligation to call it (and to read every roster file) is enforced by the same mechanism the plan already applies to the spawn pattern: **grep-checkable prose invariants asserted by the regression test**. This is the honest mitigation — invariant tests over SKILL.md, not a harness guarantee — and it is the strongest enforcement a prose skill admits.

### Flow

Critique invoked → read plan + context (Steps 1-1.5) → structural checks (Step 2) → **compute + freeze roster manifest `_roster.json` (Step 3a)** → **dispatch each roster critic with an instruction to atomically write `{name}.result.md.tmp`→rename to `{name}.result.md`, ending with the two-line terminal fence `<<<CRITIQUE-RESULT-COMPLETE>>>` then `STATUS: COMPLETED` as the final lines (Step 3)** → **roster membership check via `critique-roster-check` (Step 3.5, runs BEFORE Step 4): if incomplete, re-dispatch only missing critics (no `run_in_background`) up to MAX_CRITIC_REDISPATCH rounds (1 initial + up to 2 re-dispatches = 3 attempts max); if still incomplete, record `MAJOR REWORK (CRITIQUE INCOMPLETE)` and STOP** → aggregate + dedup by iterating EVERY roster member in `_roster.json` (Step 4) → report (Step 5) → record verdict + marker gated on gate `complete: true` (Step 5.5) → set plan_revising lock if needed (Step 5.6) → clean up `${CRITIQUE_RUN_DIR}` **only when `complete: true`; PRESERVE the dir on the incomplete / CRITIQUE INCOMPLETE path for forensics**.

### Technical Approach

- **Add the run-dir + roster manifest convention** in `.claude/skills-global/do-plan-critique/SKILL.md` as a new **Step 3a** (immediately before Step 3): compute the roster from CRITICS.md selection rules, create `${CRITIQUE_RUN_DIR}` (default `.critique-runs/{issue-or-slug}-{timestamp}/`). The `{timestamp}` is **high-resolution** (`date +%s%N`, nanoseconds) and the dir is created with **`mkdir` WITHOUT `-p`** so that a collision fails loudly (non-zero exit) rather than silently reusing an existing dir's stale result files. Then write `_roster.json` with the frozen list of expected critic names and count.
- **Rewrite Step 3 (line ~154)**: each critic is dispatched with an explicit instruction to **write its findings to `${CRITIQUE_RUN_DIR}/{critic_name}.result.md`**, writing the findings body (0-3 findings or `No findings.`) FIRST and, as its final action, appending the **two-line terminal fence**: `<<<CRITIQUE-RESULT-COMPLETE>>>` then `STATUS: COMPLETED` as the last two lines. The write must be **atomic**: write the full content to `{critic_name}.result.md.tmp`, then rename to `{critic_name}.result.md`. Because `.tmp` and the canonical file are both inside `${CRITIQUE_RUN_DIR}` (same filesystem), the rename is atomic — the canonical path is never observed truncated, and a re-dispatched critic's overwrite can never expose a partial file. Remove `run_in_background: true` from the spawn instruction; whether the critics run foreground or background no longer matters to correctness, because the gate is the result-file membership check, not driver-await. (Foreground single-message dispatch is still recommended for latency, but it is no longer load-bearing for correctness — explicitly noted so no future reader re-introduces a prose-await dependency.)
- **Enforce the aggregation invariant in Step 4 (NEW-B2)**: Step 4 must instruct the driver to read **every** roster member's result file — naming all frozen roster members or iterating `_roster.json`'s manifest — NOT "the result files that are present." Reading only the present files would let a partial set through if the gate were ever bypassed; aggregation iterating the manifest means a missing file is a visible gap, not silently skipped. The grep-invariant test asserts Step 4 references the manifest / iterates roster members rather than "the present files."
- **Rewrite Step 3.5 (lines ~171-179) as the roster membership barrier**: call `critique-roster-check --run-dir ${CRITIQUE_RUN_DIR}`. If `complete: false`, re-dispatch only the `missing` critics (write their result files), up to `MAX_CRITIC_REDISPATCH = 2` rounds. If still incomplete, jump to Step 5.5 with verdict `MAJOR REWORK (CRITIQUE INCOMPLETE: roster N/M — missing: {names})`. Never aggregate a partial set; never record an empty verdict.
- **Add the helper** `tools/critique_roster_check.py` + CLI entry `critique-roster-check = "tools.critique_roster_check:main"` in `pyproject.toml [project.scripts]`. Pure stdlib (os/json/argparse); reads `_roster.json` + result files; a member is "completed" iff its file exists AND its **last two non-empty lines** (after stripping trailing whitespace) are exactly `<<<CRITIQUE-RESULT-COMPLETE>>>` (penultimate) then `STATUS: COMPLETED` (last) — a first-line sentinel, or a bare last-line `STATUS: COMPLETED` without the preceding delimiter, are explicitly NOT honored (NEW-B1/NEW-B1b). It ignores any stray `.tmp` files (only `{name}.result.md` is canonical). Prints the JSON gate decision; exits 0 when complete, 1 when incomplete (so the skill can branch on exit code or parse JSON).
- **Update `CRITICS.md` "How to Spawn" (line 7)**: replace "parallel Agent tool call with `run_in_background: true`" with the result-file convention — each critic must end by writing its findings to `${CRITIQUE_RUN_DIR}/{critic_name}.result.md.tmp` then renaming to `{critic_name}.result.md`, ending the file with the two-line terminal fence `<<<CRITIQUE-RESULT-COMPLETE>>>` then `STATUS: COMPLETED` (appended after the findings body, as the critic's deliberate final action). Add the atomic-write + terminal-fence instruction to the prompt template, AND an explicit rule: **emit the fence token `<<<CRITIQUE-RESULT-COMPLETE>>>` ONLY as the terminal completion marker — never quote it (or the bare `STATUS: COMPLETED` line) inside findings text**, so the marker can never be forged by ordinary critic prose.
- **Add `.critique-runs/` to `.gitignore`** so run artifacts never get committed.
- **Keep Steps 5/5.5/5.6 untouched in shape** — verdict record, completion-marker co-location, and plan_revising lock semantics are correct; the only change is that the incomplete-roster path now feeds a concrete `MAJOR REWORK (CRITIQUE INCOMPLETE)` verdict string into the existing Step 5.5 recorder.
- **Gate the run-dir cleanup on `complete: true`** — the post-verdict cleanup of `${CRITIQUE_RUN_DIR}` runs **only on the complete (verdict-recorded-normally) path**. On the incomplete / `CRITIQUE INCOMPLETE` path the run-dir is **preserved** so the partial/missing result files survive as forensic evidence of which critics never reported; deleting them on the failure path would destroy exactly the diagnostic the STOP exists to surface.
- **Update the repo addendum** `docs/sdlc/do-plan-critique.md` §"Wait-and-Collect" to describe the artifact-based roster barrier.
- **Bump the version history** in `SKILL.md` (v1.4.0) referencing #1690.
- **Add a regression test** `tests/unit/test_do_plan_critique_barrier.py` that (a) asserts the skill prose encodes the artifact barrier AND the ordering/aggregation invariants (NEW-B2 grep assertions, see below), and (b) exercises the `critique_roster_check` helper directly against synthetic run dirs (complete-terminal-fence / missing-file / fence-on-line-1-but-truncated-after ⇒ NOT complete / **bare `STATUS: COMPLETED` last line WITHOUT the preceding delimiter ⇒ NOT complete** (NEW-B1b token-collision guard) / present-but-no-fence / under-dispatched-roster / atomic-rename-only-canonical-observed), proving the gate behaves.

**Why artifact-based and not foreground-batch:** A markdown skill cannot *prove* the harness blocks on a single-message Agent batch, and a count check is defeated by under-dispatch. A result-file-per-critic + frozen-roster-manifest membership check is verifiable from outside the run — by a human, by the helper, and by a test — and is robust to the driver returning early or the harness changing. This is the direction the war-room critique recommended.

## Failure Path Test Strategy

### Exception Handling Coverage
- The new `tools/critique_roster_check.py` helper has real failure paths the test must cover: (a) `_roster.json` missing or unparseable → exit non-zero with `complete: false` and a clear error, never a crash that the skill could mistake for "complete"; (b) a result file present but whose **last two non-empty lines** are not the `<<<CRITIQUE-RESULT-COMPLETE>>>` / `STATUS: COMPLETED` fence → counted as *not completed*; (b2, NEW-B1) a result file with the fence on line 1 but a truncated/empty body after → counted as *not completed* (the terminal-fence check, not first-line, closes the truncated-write hole); (b3, NEW-B1b) a result file whose body merely **ends on the bare `STATUS: COMPLETED` line WITHOUT the preceding `<<<CRITIQUE-RESULT-COMPLETE>>>` delimiter** (token collision — a quoted sentinel in findings prose) → counted as *not completed* (the two-line fence closes the forgeable-substring hole); (c) a result file for a name *not* in the manifest → ignored (cannot inflate completion); (d) a stray `{name}.result.md.tmp` left by an atomic-write-in-progress → ignored (only the canonical `{name}.result.md` is read). Each is a unit test case against a synthetic run dir.

### Empty/Invalid Input Handling
- **"No findings." is a valid completed result, not an error (B3):** a result file whose body is the literal `No findings.` followed by the terminal two-line fence (`<<<CRITIQUE-RESULT-COMPLETE>>>` then `STATUS: COMPLETED`) counts as completed — the helper distinguishes it from a missing/incomplete file purely on the terminal fence, and the test asserts this disambiguation explicitly.
- **Empty roster manifest** (should never happen, but) → helper returns `complete: false` with `roster_count: 0` and the skill treats it as a STOP-grade incompleteness, never a vacuous "0 of 0, proceed."
- **Under-dispatch (B2):** the test writes a `_roster.json` of seven names but only five result files, and asserts `complete: false, missing: [two names]` — proving the gate cannot be satisfied by dispatching fewer critics than the frozen roster.

### Error State Rendering
- The user-visible failure path is "the critic roster could not be completed after the cap." The skill records `MAJOR REWORK (CRITIQUE INCOMPLETE: roster N/M — missing: {names})` through Step 5.5 (router-consumable via G1) and sets the plan_revising lock — an explicit, loud, router-actionable STOP, never a silently-recorded verdict and never a forever-`in_progress` stall. The test asserts the skill prose mandates this verdict string and the bounded `MAX_CRITIC_REDISPATCH` cap.

## Test Impact

- [ ] `tests/unit/test_do_plan_critique_barrier.py` — CREATE. Two halves:
  - **Prose invariants** (assert the skill encodes the artifact barrier; grep over `SKILL.md`/`CRITICS.md`, modeled on the existing spawn-pattern grep assertions): (a) no `run_in_background: true` in the critic-spawn instructions of `SKILL.md` Step 3 or `CRITICS.md` "How to Spawn"; (b) Step 3a freezes a roster manifest before dispatch; (c) Step 3/CRITICS.md instruct each critic to write `{name}.result.md` ending with the two-line terminal fence (`<<<CRITIQUE-RESULT-COMPLETE>>>` then `STATUS: COMPLETED` as the last lines) and to use the atomic `.tmp`→rename write, AND include the CRITICS.md rule never to quote the fence token / bare sentinel in findings text; (d) Step 3.5 calls `critique-roster-check`, re-dispatches only missing critics, names a bounded `MAX_CRITIC_REDISPATCH` cap (1 initial + up to 2 re-dispatches = 3 attempts max), carries **NO `run_in_background: true` in the re-dispatch block**, and on still-incomplete records `MAJOR REWORK (CRITIQUE INCOMPLETE...)`; (e) Steps 5.5 and 5.6 still present (no regression of verdict-record/marker/lock); (e2) run-dir cleanup is gated on `complete: true` (preserved on the incomplete path).
  - **Ordering & aggregation invariants (NEW-B2, grep over `SKILL.md` — the same enforcement class already applied to the spawn pattern):**
    - (f) **Step 3.5 (barrier/roster-check) precedes Step 4 (aggregation)** — assert the byte/line offset of the `critique-roster-check` invocation / Step 3.5 header is *before* the Step 4 header in `SKILL.md`.
    - (g) **Step 5.5 verdict-record is gated** — assert the prose between Step 3.5 and Step 5.5 states the verdict is recorded only when the gate reports `complete: true` OR emits `CRITIQUE INCOMPLETE` (grep for both the `complete`-gate phrasing and the `CRITIQUE INCOMPLETE` fallback in the gating block).
    - (h) **Step 4 reads EVERY roster file** — assert Step 4 prose references reading every roster member / iterating `_roster.json` (the manifest), and does NOT instruct "aggregate from the present files" (negative grep on a "present files only" phrasing).
    - (i) **Re-dispatch block carries no background flag** — assert no `run_in_background: true` appears anywhere in the Step 3.5 re-dispatch block.
  - **Helper behavior** (exercise `tools.critique_roster_check` directly, the independently-verifiable check, B-criterion 4): complete roster (terminal two-line fence) ⇒ `complete: true`; missing file ⇒ `complete: false` + named in `missing`; **fence on line 1 only with truncated/empty body after ⇒ NOT completed** (NEW-B1 regression guard); **bare `STATUS: COMPLETED` last line WITHOUT the preceding `<<<CRITIQUE-RESULT-COMPLETE>>>` delimiter ⇒ NOT completed** (NEW-B1b token-collision guard); present-but-no-fence ⇒ not completed; `No findings.` body + terminal two-line fence ⇒ completed; under-dispatched roster (manifest 7, files 5) ⇒ `complete: false`; missing/unparseable `_roster.json` ⇒ non-zero exit, no false "complete."
- No existing test reads `do-plan-critique/SKILL.md` — verified by grep over `tests/`. The SDLC router tests (`test_sdlc_router*.py`) consume the *recorded verdict*; this fix only adds a new verdict *value* (`MAJOR REWORK (CRITIQUE INCOMPLETE...)`) that G1 already matches via the `MAJOR REWORK` substring, so router parity tests remain valid — no UPDATE/DELETE/REPLACE needed there.

## Rabbit Holes

- **Do NOT try to convert the war-room into a Python orchestration module** with real `asyncio.gather`. The critique skill is an LLM-driven prose skill; rebuilding it as deterministic Python is a separate, much larger project (and would lose the per-critic LLM lens). The fix adds only a thin filesystem-check helper, not an orchestration engine.
- **Do NOT lean on the spawn mode (foreground vs. background) for correctness.** The artifact membership check is the barrier. Foreground dispatch is a latency preference only; never re-introduce a "the harness awaits, so we're safe" assumption — that was the rejected prior direction.
- **Do NOT add a polling/timeout loop in prose** (e.g., "poll TaskOutput every N seconds"). The result-file membership check replaces all polling; re-dispatch is bounded by `MAX_CRITIC_REDISPATCH`, not a timer.
- **Do NOT touch the SDLC router's stale-critique handling** (#1639 row 2b/row 3). That is a separate, working mechanism; conflating it expands scope.
- **Do NOT persist run artifacts.** `.critique-runs/` is ephemeral and git-ignored; do not write critic results into Redis, Popoto, or any committed path.
- **Do NOT renumber the critics or change critic lenses.** The roster is dynamic (six or seven per CRITICS.md selection rules); the barrier checks membership against the *frozen manifest*, not a hardcoded number.

## Risks

### Risk 1: Critics may forget to write their result file
**Impact:** A critic that finishes its analysis but omits the result-file write looks "incomplete" to the gate, triggering a re-dispatch (or, after the cap, a CRITIQUE INCOMPLETE STOP).
**Mitigation:** The result-file write is the **last, mandatory instruction** in every critic prompt template (CRITICS.md), phrased as the critic's terminal action: write the body, then append the two-line terminal fence `<<<CRITIQUE-RESULT-COMPLETE>>>` then `STATUS: COMPLETED` as the last lines, via atomic `.tmp`→rename. The bounded re-dispatch (2 rounds; 1 initial + up to 2 re-dispatches = 3 attempts max) absorbs an occasional miss. Crucially, this failure mode is **safe-by-construction**: a forgotten write — OR a truncated/crashed write that never reaches the terminal fence — OR a body that merely quotes the bare sentinel without the deliberate delimiter — can only cause a STOP/re-dispatch (loud), never a silent green; this is the opposite of the original bug, and the NEW-B1/NEW-B1b terminal two-line fence is what guarantees it (a first-line sentinel would have let a truncated body pass; a bare last-line substring would have been forgeable by quoted prose). The test covers "present-but-no-fence", "fence-on-line-1-only-truncated-after", and "bare-`STATUS: COMPLETED`-without-delimiter" to lock this in.

### Risk 2: `${CRITIQUE_RUN_DIR}` collisions across concurrent critique runs
**Impact:** Two critique runs for different issues writing to the same dir would cross-contaminate result files.
**Mitigation:** The run dir embeds the issue-or-slug and a **high-resolution timestamp** (`date +%s%N`, nanoseconds: `.critique-runs/{issue-or-slug}-{timestamp}/`), making collisions effectively impossible. The dir is created with `mkdir` **without `-p`**, so on the vanishingly rare collision the create fails loudly (non-zero exit) instead of silently reusing a stale dir's result files. The roster manifest is per-run inside that dir. Cleanup after Step 5.5 (on the `complete: true` path only) keeps the tree small; the dir is preserved on the incomplete path for forensics.

### Risk 3: The helper itself could regress and wrongly report "complete"
**Impact:** A bug in `critique_roster_check.py` could re-open the silent-drop hole.
**Mitigation:** This is exactly why the helper is plain stdlib Python with a direct unit test exercising complete / missing / no-fence / fence-on-line-1-truncated / bare-sentinel-without-delimiter / under-dispatch / bad-manifest cases. The barrier's correctness is now *tested*, not asserted — the core demand of B1.

## Race Conditions

### Race 1: Verdict recorded before all critic findings are collected (the original bug)
**Location:** `.claude/skills-global/do-plan-critique/SKILL.md` Steps 3 → 3.5 → 5.5
**Trigger:** Critics spawned with `run_in_background: true`; driver proceeds to Step 4/5/5.5 (verdict record) before late critics finish, dropping their findings.
**Data prerequisite:** Every frozen-roster critic's `{name}.result.md` (with the terminal two-line fence `<<<CRITIQUE-RESULT-COMPLETE>>>` then `STATUS: COMPLETED`) must exist before aggregation (Step 4) reads them.
**State prerequisite:** The driver must not record a verdict (Step 5.5) until `critique-roster-check` reports `complete: true` (or the cap is hit, yielding a CRITIQUE INCOMPLETE verdict).
**Mitigation:** The verdict is gated on a **filesystem membership check against the frozen manifest**, not on driver-await. A critic still running (or mid-write) has not yet emitted its terminal two-line fence (`<<<CRITIQUE-RESULT-COMPLETE>>>` then `STATUS: COMPLETED`) via the atomic rename, so the gate reports incomplete and the driver cannot proceed to a normal verdict. This makes "verdict before findings complete" detectable independent of spawn mode, and the failure direction is always loud (STOP), never silent. The driver's obligation to call the gate before Step 5.5, and to aggregate from every roster file in Step 4, is itself enforced by grep-checkable prose invariants in the regression test (NEW-B2) — the same enforcement class the plan applies to the spawn pattern; the plan does not claim the harness forces these driver steps, only that the invariants are test-asserted.

## No-Gos (Out of Scope)

This plan changes only the in-skill critic barrier and adds the roster-check helper + its test. The following are deliberately out of scope and are **decisions, not deferrals** — every item the first critique flagged as "undecided" (the fail-loud contract, the roster granularity) is now decided in **Resolved Questions** above and embedded in the Solution; nothing required for this fix is left open:

- **SDLC router's stale-critique dead-end handling (row 2b / row 3, #1639):** untouched — a working, separately-owned mechanism, not deferred work for this plan.
- **Converting the war room to deterministic Python orchestration:** a separate, larger project (see Rabbit Holes), explicitly not pursued here.
- **Persisting critique run artifacts:** intentionally ephemeral; not a future feature.

## Update System

Skill files sync automatically: `.claude/skills-global/do-plan-critique/` is hardlinked to `~/.claude/skills/` on every machine by `scripts/update/hardlinks.py::sync_claude_dirs()` — editing the existing files in place needs no new registration and no `RENAMED_REMOVALS` entry (nothing is renamed or moved). The next `/update` propagates the edited SKILL.md / CRITICS.md.

There is **no new external dependency** (consistent with "New dependencies: None" in Architectural Impact — the `critique-roster-check` helper is in-repo, pure-stdlib code, not a third-party package). The one thing to propagate is the new **in-repo CLI entry** `critique-roster-check` in `pyproject.toml [project.scripts]`. This installs on each machine via the standard `uv sync` / editable-install step that `/update` already runs — no extra update-script change is required, because adding a `[project.scripts]` entry is picked up by the existing dependency-sync step. No new external package, no new secret, no Popoto model, so `scripts/update/migrations.py` is not involved.

## Agent Integration

One new CLI entry point is required: `critique-roster-check = "tools.critique_roster_check:main"` in `pyproject.toml [project.scripts]`. The critique skill invokes it via the Bash tool during Step 3.5; this is the agent-facing surface. No `.mcp.json` change and no new bridge import — the helper is a leaf utility the skill calls, not something the bridge imports. The critique skill itself is already wired into the SDLC pipeline (`/sdlc` → `/do-plan-critique`). The regression test (`tests/unit/test_do_plan_critique_barrier.py`) verifies both that the skill prose invokes the gate and that the helper the agent calls behaves correctly.

## Documentation

### Feature Documentation
- [ ] Update `docs/sdlc/do-plan-critique.md` §"Wait-and-Collect + Mandatory Finalize (#1654)" to describe the **artifact-based roster barrier**: per-critic result files written atomically (`.tmp`→rename, same filesystem), the frozen `_roster.json` manifest, the `critique-roster-check` membership gate, the **two-line terminal completion fence** (penultimate `<<<CRITIQUE-RESULT-COMPLETE>>>`, last `STATUS: COMPLETED` — structurally unforgeable; a truncated or token-colliding write can only STOP, never pass), the Step 3.5-before-Step 4 ordering with Step 4 iterating the full manifest, the bounded `MAX_CRITIC_REDISPATCH` cap (1+2=3 attempts max), the cleanup-gated-on-`complete` forensics preservation, and the `MAJOR REWORK (CRITIQUE INCOMPLETE)` STOP verdict (G1-consumable). Replace the background-poll/await description. Add a reference to #1690.
- [ ] Update the `## Version history` block in `.claude/skills-global/do-plan-critique/SKILL.md` with a v1.4.0 entry referencing #1690: "Replace fire-and-forget `run_in_background` critic spawn + prose await with an artifact-based roster barrier: each critic atomically writes a result file ending in a two-line terminal fence (`<<<CRITIQUE-RESULT-COMPLETE>>>` then `STATUS: COMPLETED`); synthesis gates on a filesystem membership check (terminal two-line fence, structurally unforgeable) against a frozen roster manifest, run before aggregation; incomplete roster after a bounded re-dispatch cap records `MAJOR REWORK (CRITIQUE INCOMPLETE)`."

### External Documentation Site
- No external docs site for this repo's skills.

### Inline Documentation
- [ ] The skill prose IS the documentation; ensure Step 3a, Step 3, Step 3.5, and CRITICS.md "How to Spawn" read coherently after the edit (no dangling references to "background agents", `TaskOutput` polling, count-match equality, or a single-line/bare `STATUS: COMPLETED` sentinel — the marker is the two-line fence everywhere).

## Success Criteria

- [ ] No `run_in_background: true` remains in the critic-spawn instructions of `SKILL.md` Step 3 or `CRITICS.md` "How to Spawn" (verified by grep / by the new test).
- [ ] `SKILL.md` has a Step 3a that freezes a roster manifest (`_roster.json`) before any critic is dispatched.
- [ ] `SKILL.md` Step 3 and `CRITICS.md` instruct each critic to write `{name}.result.md` ending with the **two-line terminal fence** (`<<<CRITIQUE-RESULT-COMPLETE>>>` then `STATUS: COMPLETED` as the last two lines), via an atomic `.tmp`→rename write (same run-dir / same filesystem).
- [ ] `CRITICS.md` carries the rule that critics must never quote the fence token `<<<CRITIQUE-RESULT-COMPLETE>>>` (or the bare `STATUS: COMPLETED` line) inside findings text.
- [ ] `SKILL.md` Step 3a creates `${CRITIQUE_RUN_DIR}` with a high-resolution timestamp (`date +%s%N`) and `mkdir` WITHOUT `-p` (collision fails loudly).
- [ ] `SKILL.md` Step 3.5 invokes `critique-roster-check` BEFORE Step 4, re-dispatches only missing critics (no `run_in_background: true` in the re-dispatch block), names the bounded `MAX_CRITIC_REDISPATCH` cap (1 initial + up to 2 re-dispatches = 3 attempts max), and on still-incomplete records `MAJOR REWORK (CRITIQUE INCOMPLETE: roster N/M — missing: ...)`.
- [ ] `SKILL.md` Step 4 aggregates by iterating EVERY roster member in `_roster.json` (not "the present files"); Step 5.5 verdict-record is gated on the gate's `complete: true` OR the `CRITIQUE INCOMPLETE` fallback (NEW-B2 ordering/aggregation invariants, grep-asserted by the test).
- [ ] Run-dir cleanup is gated on `complete: true`; the dir is preserved on the incomplete / `CRITIQUE INCOMPLETE` path for forensics.
- [ ] `tools/critique_roster_check.py` checks the **last two non-empty lines** for the fence (penultimate `<<<CRITIQUE-RESULT-COMPLETE>>>`, last `STATUS: COMPLETED`) — a line-1-only fence with truncated body, OR a bare last-line `STATUS: COMPLETED` without the delimiter, ⇒ NOT complete — and ignores stray `.tmp` files.
- [ ] `SKILL.md` Steps 5.5 (verdict record + co-located completion marker) and 5.6 (plan_revising lock) are unchanged and still present.
- [ ] `tools/critique_roster_check.py` exists; `critique-roster-check` is registered in `pyproject.toml [project.scripts]`.
- [ ] `.critique-runs/` is in `.gitignore`.
- [ ] `docs/sdlc/do-plan-critique.md` §Wait-and-Collect describes the artifact-based roster barrier, referencing #1690.
- [ ] `tests/unit/test_do_plan_critique_barrier.py` exists and passes, asserting both the prose invariants and the helper's gate behavior (complete / missing / no-fence / fence-on-line-1-truncated / bare-sentinel-without-delimiter / no-findings-completed / under-dispatch / bad-manifest).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (critique-barrier)**
  - Name: critique-barrier-builder
  - Role: Add Step 3a (roster manifest), rewrite Step 3 (result-file write) and Step 3.5 (membership gate + bounded re-dispatch + CRITIQUE INCOMPLETE verdict) in `SKILL.md`; update `CRITICS.md` "How to Spawn" + prompt template; add `.critique-runs/` to `.gitignore`; bump the version history.
  - Agent Type: builder
  - Resume: true

- **Builder (roster-helper)**
  - Name: roster-helper-builder
  - Role: Write `tools/critique_roster_check.py` + register `critique-roster-check` in `pyproject.toml [project.scripts]`.
  - Agent Type: builder
  - Resume: true

- **Builder (barrier-test)**
  - Name: barrier-test-builder
  - Role: Write `tests/unit/test_do_plan_critique_barrier.py` (prose invariants + direct helper-behavior cases), modeled on `test_do_patch_ticks.py`.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (barrier)**
  - Name: barrier-validator
  - Role: Verify all Success Criteria — no `run_in_background` in spawn sections, roster manifest + result-file + membership-gate prose present, helper + CLI registered, `.gitignore` updated, Steps 5.5/5.6 intact, test passes.
  - Agent Type: validator
  - Resume: true

### Available Agent Types

(Standard roster — builder, test-engineer, validator, documentarian used here.)

## Step by Step Tasks

### 1. Add roster manifest + result-file spawn (Steps 3a/3) and CRITICS.md convention
- **Task ID**: build-spawn-pattern
- **Depends On**: none
- **Validates**: tests/unit/test_do_plan_critique_barrier.py (create)
- **Assigned To**: critique-barrier-builder
- **Agent Type**: builder
- **Parallel**: true
- In `SKILL.md`, add **Step 3a** before Step 3: compute the roster from CRITICS.md selection rules, create `${CRITIQUE_RUN_DIR}` (`.critique-runs/{issue-or-slug}-{timestamp}/`) using a **high-resolution timestamp (`date +%s%N`) and `mkdir` WITHOUT `-p`** (so a collision fails loudly), write `_roster.json` (frozen list of expected critic names + count) *before* dispatch.
- Rewrite **Step 3** (~line 154): remove `run_in_background: true`; instruct each critic to write its findings body first and append the **two-line terminal fence** (`<<<CRITIQUE-RESULT-COMPLETE>>>` then `STATUS: COMPLETED` as the last two lines), using an atomic write (`{name}.result.md.tmp` → rename to `{name}.result.md`; `.tmp` and canonical file are in the same run-dir / same filesystem, so the rename is atomic). Note foreground dispatch is a latency preference, not load-bearing for correctness. Fix the stale "six" to "all critics in the frozen roster".
- Add the **Step 4 aggregation invariant**: Step 4 must iterate EVERY roster member in `_roster.json` (or name all roster members) when reading result files — never "aggregate from the present files." This is the NEW-B2 fix that prevents the hole moving up from Step 3.5 to Step 4.
- In `CRITICS.md` "How to Spawn" (line 7) and the prompt template: replace the `run_in_background` wording with the result-file convention; add the terminal instruction "write your findings to `{name}.result.md.tmp`, then rename to `{name}.result.md`, ending with `<<<CRITIQUE-RESULT-COMPLETE>>>` then `STATUS: COMPLETED` as the last two lines" to the template, plus the rule **never quote the fence token `<<<CRITIQUE-RESULT-COMPLETE>>>` (or the bare `STATUS: COMPLETED` line) inside findings text**.
- Add `.critique-runs/` to `.gitignore`. Add a `## Version history` entry (v1.4.0) in SKILL.md referencing #1690.

### 2. Write the roster-check helper + CLI
- **Task ID**: build-helper
- **Depends On**: none
- **Validates**: tests/unit/test_do_plan_critique_barrier.py (create)
- **Assigned To**: roster-helper-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/critique_roster_check.py` (stdlib only): read `_roster.json` + `{name}.result.md` files; a member is "completed" iff its file exists AND its **last two non-empty lines** are, in order, `<<<CRITIQUE-RESULT-COMPLETE>>>` (penultimate) then `STATUS: COMPLETED` (last) (NEW-B1/NEW-B1b: terminal two-line fence — a sentinel only on line 1, OR a bare last-line `STATUS: COMPLETED` without the preceding delimiter, must NOT count as complete); ignore stray `.tmp` files; print JSON `{"complete", "missing", "present", "roster_count", "completed_count"}` (where `completed_count` is just the number of roster members whose file passes the terminal-fence check — it proves each was fully written and stamped, nothing about body well-formedness); exit 0 if complete, 1 otherwise; missing/unparseable manifest → exit non-zero, `complete: false`, never a false complete.
- Register `critique-roster-check = "tools.critique_roster_check:main"` in `pyproject.toml [project.scripts]`.

### 3. Rewrite Step 3.5 as the membership barrier
- **Task ID**: build-step-3-5
- **Depends On**: build-spawn-pattern, build-helper
- **Validates**: tests/unit/test_do_plan_critique_barrier.py (create)
- **Assigned To**: critique-barrier-builder
- **Agent Type**: builder
- **Parallel**: false
- Rewrite `SKILL.md` Step 3.5 (~lines 171-179), positioned strictly BEFORE Step 4: call `critique-roster-check --run-dir ${CRITIQUE_RUN_DIR}`; if `complete: false`, re-dispatch only the `missing` critics (WITHOUT `run_in_background: true`), up to `MAX_CRITIC_REDISPATCH = 2` rounds; if still incomplete, go to Step 5.5 with verdict `MAJOR REWORK (CRITIQUE INCOMPLETE: roster N/M — missing: {names})`.
- Keep explicit: "never aggregate a partial set", "never record an empty verdict", "the verdict (Step 5.5) is recorded only when the gate reports `complete: true` OR as the `CRITIQUE INCOMPLETE` fallback", "the incomplete-roster STOP is a recorded `MAJOR REWORK` verdict (G1-consumable), not a silent exit".
- Confirm Steps 5.5 and 5.6 remain unchanged in shape; clean up `${CRITIQUE_RUN_DIR}` after Step 5.5 **only on the `complete: true` path — preserve the dir on the incomplete / `CRITIQUE INCOMPLETE` path for forensics**.

### 4. Write the regression test
- **Task ID**: build-test
- **Depends On**: build-helper
- **Validates**: tests/unit/test_do_plan_critique_barrier.py
- **Assigned To**: barrier-test-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/unit/test_do_plan_critique_barrier.py` modeled on `tests/unit/test_do_patch_ticks.py`.
- **Prose half**: (a) no `run_in_background: true` in spawn sections; (b) Step 3a freezes a roster manifest; (c) Step 3/CRITICS.md mandate result-file + two-line terminal fence (`<<<CRITIQUE-RESULT-COMPLETE>>>` then `STATUS: COMPLETED` as last lines) + atomic `.tmp`→rename + the CRITICS.md no-quote-the-fence rule; (d) Step 3.5 calls `critique-roster-check`, names `MAX_CRITIC_REDISPATCH` (and the 1+2=3-attempts framing), re-dispatch block has no `run_in_background: true`, records `MAJOR REWORK (CRITIQUE INCOMPLETE...)`; (e) "### Step 5.5"/"### Step 5.6" headers present; (e2) run-dir cleanup gated on `complete: true` (preserved on incomplete path).
- **Ordering/aggregation invariants half (NEW-B2):** (f) Step 3.5 / `critique-roster-check` appears BEFORE Step 4 in `SKILL.md` (offset comparison); (g) the gating block states verdict-record is gated on `complete: true` OR emits `CRITIQUE INCOMPLETE`; (h) Step 4 instructs reading every roster member / iterating `_roster.json`, NOT "the present files" (positive grep on manifest-iteration phrasing + negative grep on "present files only" phrasing); (i) no `run_in_background: true` in the Step 3.5 re-dispatch block.
- **Helper half** (direct, the verifiable check): complete (terminal two-line fence) ⇒ `complete: true`; missing file ⇒ named in `missing`; **fence-on-line-1-only + truncated body ⇒ NOT completed** (NEW-B1 guard); **bare `STATUS: COMPLETED` last line without the preceding `<<<CRITIQUE-RESULT-COMPLETE>>>` delimiter ⇒ NOT completed** (NEW-B1b token-collision guard); present-no-fence ⇒ not completed; `No findings.`+terminal two-line fence ⇒ completed; manifest=7/files=5 ⇒ `complete: false` (under-dispatch); missing/bad `_roster.json` ⇒ non-zero exit, no false complete.

### 5. Update the repo addendum doc
- **Task ID**: document-addendum
- **Depends On**: build-spawn-pattern, build-step-3-5
- **Assigned To**: critique-barrier-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/sdlc/do-plan-critique.md` §"Wait-and-Collect + Mandatory Finalize" to describe the artifact-based roster barrier (result files, frozen manifest, `critique-roster-check`, sentinel, bounded cap, CRITIQUE INCOMPLETE STOP), referencing #1690.

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-step-3-5, build-test, document-addendum
- **Assigned To**: barrier-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_do_plan_critique_barrier.py -q` → exit 0.
- Grep-confirm no `run_in_background` in the critic-spawn sections; confirm helper + CLI registered, `.gitignore` updated.
- Verify all Success Criteria, including Steps 5.5/5.6 intact and addendum updated.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Barrier test passes | `pytest tests/unit/test_do_plan_critique_barrier.py -q` | exit code 0 |
| Helper exercisable | `python -m tools.critique_roster_check --help` | exit code 0 |
| No background spawn in critic instructions | `grep -n 'run_in_background' .claude/skills-global/do-plan-critique/SKILL.md .claude/skills-global/do-plan-critique/CRITICS.md` | no `run_in_background: true` in Step 3 / How-to-Spawn (only the version-history line may reference the removed pattern) |
| Roster manifest + membership gate present | `grep -in '_roster.json\|critique-roster-check\|MAX_CRITIC_REDISPATCH' .claude/skills-global/do-plan-critique/SKILL.md` | output > 0 |
| Terminal fence delimiter specified | `grep -in 'CRITIQUE-RESULT-COMPLETE' .claude/skills-global/do-plan-critique/SKILL.md .claude/skills-global/do-plan-critique/CRITICS.md` | output > 0 |
| Terminal status line specified | `grep -in 'STATUS: COMPLETED' .claude/skills-global/do-plan-critique/SKILL.md .claude/skills-global/do-plan-critique/CRITICS.md` | output > 0 |
| Atomic .tmp→rename write specified | `grep -in 'result.md.tmp\|rename' .claude/skills-global/do-plan-critique/SKILL.md .claude/skills-global/do-plan-critique/CRITICS.md` | output > 0 |
| No-quote-the-fence rule in CRITICS.md | `grep -in 'never quote\|do not quote\|only as.*terminal' .claude/skills-global/do-plan-critique/CRITICS.md` | output > 0 |
| Run-dir created high-res, no `-p` | `grep -in 'date +%s%N\|%s%N\|without .-p' .claude/skills-global/do-plan-critique/SKILL.md` | output > 0 |
| Cleanup gated on complete | `grep -in 'preserve\|complete: true' .claude/skills-global/do-plan-critique/SKILL.md` | output > 0 |
| Step 3.5 precedes Step 4 (ordering) | `grep -n 'critique-roster-check\|### Step 3.5\|### Step 4' .claude/skills-global/do-plan-critique/SKILL.md` | roster-check / 3.5 line numbers < Step 4 line number |
| Step 4 iterates the manifest, not present-files | `grep -in '_roster.json\|every roster\|each roster member' .claude/skills-global/do-plan-critique/SKILL.md` | output > 0 |
| CRITIQUE INCOMPLETE verdict wired | `grep -in 'CRITIQUE INCOMPLETE' .claude/skills-global/do-plan-critique/SKILL.md` | output > 0 |
| Steps 5.5/5.6 intact | `grep -c '### Step 5.5\|### Step 5.6' .claude/skills-global/do-plan-critique/SKILL.md` | output > 1 |
| CLI registered | `grep -n 'critique-roster-check' pyproject.toml` | output > 0 |
| Run-dir git-ignored | `grep -n '.critique-runs' .gitignore` | output > 0 |
| Lint clean | `python -m ruff check tools/critique_roster_check.py tests/unit/test_do_plan_critique_barrier.py` | exit code 0 |
| Format clean | `python -m ruff format --check tools/critique_roster_check.py tests/unit/test_do_plan_critique_barrier.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | (re-critique #2) | First-line `STATUS: COMPLETED` reopens the silent-drop hole: a critic writing line 1 then truncating passes the gate with empty findings. | Terminal sentinel + atomic write (superseded by the two-line fence below) | Sentinel was moved to the LAST non-empty line, written after the body; critics write `.tmp`→rename. Test guards "sentinel-on-line-1-only-truncated" ⇒ NOT complete. |
| BLOCKER | (re-critique #2) | Gate-call and Step-4 aggregation invariants were unenforced prose; the hole moves up rather than closing. | Grep-checkable prose invariants + tests | Plan honestly scopes "independent of driver" to the *helper*; driver's call-the-gate (Step 3.5 < Step 4), gated Step 5.5, Step 4 iterates the full manifest, and no `run_in_background` in re-dispatch are all asserted by grep-based SKILL.md tests, same enforcement class as the spawn pattern. |
| BLOCKER | (re-critique #3) | Bare last-line `STATUS: COMPLETED` is forgeable by ordinary critic output — critics here routinely quote that exact string, so a body ending on it then truncating before the deliberate append passes the gate with a partial body (token collision). | Structurally-unforgeable two-line terminal fence | Completion marker is now a two-line terminal fence: penultimate line `<<<CRITIQUE-RESULT-COMPLETE>>>` (a unique token no critic emits in prose) then last line `STATUS: COMPLETED`; helper verifies BOTH in terminal position. CRITICS.md rule forbids quoting the fence token in findings. Test guards "bare `STATUS: COMPLETED` without the preceding delimiter ⇒ NOT complete". |

---

## Resolved (no open questions)

This revision has **no open questions**. The two questions in the prior draft are decided and incorporated above (see **Resolved Questions** near the top, and the **No-Gos** reconciliation). The fail-loud contract is decided (`MAJOR REWORK (CRITIQUE INCOMPLETE)`, G1-consumable) and the roster granularity is decided (frozen manifest membership). This is the single statement of the B5 resolution — the redundant No-Gos trailer and the prior HTML comment were removed.
