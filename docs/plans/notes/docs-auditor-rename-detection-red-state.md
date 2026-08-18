# Red-state transcripts — #2741 test migration

Risk 2 of `docs/plans/docs-auditor-rename-detection.md` calls the demonstrated-red proof
"the only real guard" for this migration. Path-token suppression (#2744) makes a **vacuous
green** the default failure mode when a `TestExistenceInvariant` case is re-expressed on the
regex channel: a pattern that matches a path token is suppressed *before* the existence
invariant runs, so the case reports `applied == 0` / `withheld == []` and asserts nothing.

The proof procedure, applied per case:

1. Neuter `_absent_new_path_refs` in `reflections/docs_auditor.py` — insert `return []` as its
   first statement, so the invariant reports no absent refs and withholds nothing.
2. Run the case. It must FAIL.
3. Capture the transcript verbatim (below).
4. Revert the neutering.

Worktree: `.worktrees/sdlc-2741`, branch `session/sdlc-2741`, at commit `7df2f0abe`
(`test(docs-auditor): migrate the substrate suite to the single regex channel`).

## The neutering applied

```diff
--- a/reflections/docs_auditor.py
+++ b/reflections/docs_auditor.py
@@ def _absent_new_path_refs(
     owners is absent.
     """
+    return []  # NEUTERED FOR RED-STATE PROOF (#2741) — reverted immediately after
     absent: list[str] = []
     for ref in sorted(set(_PATH_REF_RE.findall(candidate)) - original_refs):
```

## The four mandated cases

Risk 2 scopes the mandate by assertion, not class membership: every case that both asserts
`target-absent` **and** calls `_apply_fixes_to_file` — on `main` those are `:850`, `:926`,
`:1010`, `:1099`. `:1099` belongs to `test_dir_prefixed_decisions_unaffected_by_degraded_index`
(the twelfth `TestExistenceInvariant` call site) and is explicitly in scope.

| `main` line | Case |
|---|---|
| `:850` | `TestExistenceInvariant::test_fix_introducing_absent_path_is_rejected_and_reported` |
| `:926` | `TestExistenceInvariant::test_fix_introducing_absent_bare_name_is_withheld` |
| `:1010` | `TestExistenceInvariant::test_regex_channel_is_also_guarded` |
| `:1099` | `TestExistenceInvariant::test_dir_prefixed_decisions_unaffected_by_degraded_index` (both `failure` params) |

Command:

```
scripts/pytest-clean.sh tests/unit/test_docs_auditor_substrate.py -p no:randomly -q \
  -k "test_fix_introducing_absent_path_is_rejected_and_reported or \
      test_fix_introducing_absent_bare_name_is_withheld or \
      test_regex_channel_is_also_guarded or \
      test_dir_prefixed_decisions_unaffected_by_degraded_index"
```

Verbatim output:

```
bringing up nodes...
bringing up nodes...

FFFFF                                                                    [100%]
=================================== FAILURES ===================================
_ TestExistenceInvariant.test_fix_introducing_absent_path_is_rejected_and_reported _
[gw0] darwin -- Python 3.14.3 /Users/valorengels/src/ai/.worktrees/sdlc-2741/.venv/bin/python
tests/unit/test_docs_auditor_substrate.py:796: in test_fix_introducing_absent_path_is_rejected_and_reported
    assert applied == 0
E   assert 1 == 0
------------------------------ Captured log setup ------------------------------
WARNING  POPOTO-PYTEST:pytest_plugin.py:250 popoto pytest plugin: DB 0 is non-idle (29821 keys) at session start — DB-0 tripwire SKIPPED.  Run against a clean Redis instance to enable the tripwire.
___ TestExistenceInvariant.test_fix_introducing_absent_bare_name_is_withheld ___
[gw0] darwin -- Python 3.14.3 /Users/valorengels/src/ai/.worktrees/sdlc-2741/.venv/bin/python
tests/unit/test_docs_auditor_substrate.py:880: in test_fix_introducing_absent_bare_name_is_withheld
    assert applied == 0
E   assert 1 == 0
__________ TestExistenceInvariant.test_regex_channel_is_also_guarded ___________
[gw0] darwin -- Python 3.14.3 /Users/valorengels/src/ai/.worktrees/sdlc-2741/.venv/bin/python
tests/unit/test_docs_auditor_substrate.py:965: in test_regex_channel_is_also_guarded
    assert applied == 0
E   assert 1 == 0
_ TestExistenceInvariant.test_dir_prefixed_decisions_unaffected_by_degraded_index[nonzero-rc] _
[gw0] darwin -- Python 3.14.3 /Users/valorengels/src/ai/.worktrees/sdlc-2741/.venv/bin/python
tests/unit/test_docs_auditor_substrate.py:1054: in test_dir_prefixed_decisions_unaffected_by_degraded_index
    assert applied == 1
E   assert 2 == 1
_ TestExistenceInvariant.test_dir_prefixed_decisions_unaffected_by_degraded_index[oserror] _
[gw0] darwin -- Python 3.14.3 /Users/valorengels/src/ai/.worktrees/sdlc-2741/.venv/bin/python
tests/unit/test_docs_auditor_substrate.py:1054: in test_dir_prefixed_decisions_unaffected_by_degraded_index
    assert applied == 1
E   assert 2 == 1
test-db claims (#2628): gw8=db10 gw9=db9 gw4=db5 gw6=db7 gw5=db6 gw7=db8 gw2=db3 gw1=db2 gw3=db4 gw0=db1
=========================== short test summary info ===========================
FAILED tests/unit/test_docs_auditor_substrate.py::TestExistenceInvariant::test_fix_introducing_absent_path_is_rejected_and_reported
FAILED tests/unit/test_docs_auditor_substrate.py::TestExistenceInvariant::test_fix_introducing_absent_bare_name_is_withheld
FAILED tests/unit/test_docs_auditor_substrate.py::TestExistenceInvariant::test_regex_channel_is_also_guarded
FAILED tests/unit/test_docs_auditor_substrate.py::TestExistenceInvariant::test_dir_prefixed_decisions_unaffected_by_degraded_index[nonzero-rc]
FAILED tests/unit/test_docs_auditor_substrate.py::TestExistenceInvariant::test_dir_prefixed_decisions_unaffected_by_degraded_index[oserror]
5 failed in 9.94s
```

Read the failures, not just their count. Each says the invariant is what produces the assertion:
with it neutered the rewrite **applies** (`assert 1 == 0`, and `assert 2 == 1` where a valid
sibling fix already applied). A vacuously-green migration would instead have stayed green here,
because path-token suppression would have refused the rewrite before the invariant ran at all.

## Whole-file sweep under the same neutering

Not required by the plan, but run because it is the cheapest evidence that the *entire* migrated
`TestExistenceInvariant` block is live rather than only the four mandated cases — and that the
two re-pointed `audit()`-driven withheld tests still genuinely depend on the invariant after
being moved onto `_detect_stale_term_fixes`.

```
scripts/pytest-clean.sh tests/unit/test_docs_auditor_substrate.py -p no:randomly -q
```

```
_____ TestExistenceInvariant.test_audit_surfaces_withheld_without_writing ______
[gw0] darwin -- Python 3.14.3 /Users/valorengels/src/ai/.worktrees/sdlc-2741/.venv/bin/python
tests/unit/test_docs_auditor_substrate.py:1101: in test_audit_surfaces_withheld_without_writing
    assert result["fixes_withheld"] == 1
E   assert 0 == 1
_ TestWithheldBlocksAutoMerge.test_bare_name_withhold_propagates_to_pr_body_telegram_and_liveness _
[gw0] darwin -- Python 3.14.3 /Users/valorengels/src/ai/.worktrees/sdlc-2741/.venv/bin/python
tests/unit/test_docs_auditor_substrate.py:1205: in test_bare_name_withhold_propagates_to_pr_body_telegram_and_liveness
    assert audit_result["fixes_withheld"] == 1
E   assert 0 == 1
test-db claims (#2628): gw8=db9 gw7=db8 gw1=db3 gw4=db6 gw3=db7 gw5=db5 gw2=db2 gw6=db4 gw9=db10 gw0=db1
=========================== short test summary info ===========================
FAILED tests/unit/test_docs_auditor_substrate.py::TestExistenceInvariant::test_fix_introducing_absent_path_is_rejected_and_reported
FAILED tests/unit/test_docs_auditor_substrate.py::TestExistenceInvariant::test_rejection_is_logged_with_offending_path
FAILED tests/unit/test_docs_auditor_substrate.py::TestExistenceInvariant::test_sibling_valid_fix_still_applies
FAILED tests/unit/test_docs_auditor_substrate.py::TestExistenceInvariant::test_all_fixes_rejected_writes_nothing
FAILED tests/unit/test_docs_auditor_substrate.py::TestExistenceInvariant::test_fix_introducing_absent_bare_name_is_withheld
FAILED tests/unit/test_docs_auditor_substrate.py::TestExistenceInvariant::test_ambiguous_bare_name_passes_and_debug_logs
FAILED tests/unit/test_docs_auditor_substrate.py::TestExistenceInvariant::test_regex_channel_is_also_guarded
FAILED tests/unit/test_docs_auditor_substrate.py::TestExistenceInvariant::test_dir_prefixed_decisions_unaffected_by_degraded_index[nonzero-rc]
FAILED tests/unit/test_docs_auditor_substrate.py::TestExistenceInvariant::test_dir_prefixed_decisions_unaffected_by_degraded_index[oserror]
FAILED tests/unit/test_docs_auditor_substrate.py::TestExistenceInvariant::test_audit_surfaces_withheld_without_writing
FAILED tests/unit/test_docs_auditor_substrate.py::TestWithheldBlocksAutoMerge::test_bare_name_withhold_propagates_to_pr_body_telegram_and_liveness
11 failed, 120 passed in 28.40s
```

Eleven cases red, all of them existence-invariant coverage. The two `audit()`-driven tests fail
on `fixes_withheld == 1` -> `0`, which is the specific thing Test Impact required of the
re-pointing: a real `audit()` run still produces a withheld fix through the regex channel.

## Revert confirmed

```
$ git -C .worktrees/sdlc-2741 checkout -- reflections/docs_auditor.py
$ git -C .worktrees/sdlc-2741 status --porcelain
$ grep -c NEUTERED reflections/docs_auditor.py
0
```

Green after revert: `131 passed in 84.09s`.

## No-Gos (Out of Scope)

Nothing deferred — this file is not a plan. It is the durable holding place for the
demonstrated-red transcripts required by Task 2 of
`docs/plans/docs-auditor-rename-detection.md`, which Task 6 reads to author the PR body.
It lives under `docs/plans/notes/` because that prefix is exempt from the issue-disposition
commit hook and `find_plan_path` iterates `docs/plans/` non-recursively, so a `notes/`
subdirectory cannot be mistaken for a rival plan for #2741.

## Post-review patch re-proof (2026-08-17)

The review's tech-debt finding 1 deleted `test_regex_channel_is_also_guarded` as
redundant: it drove the identical fix pair `(re.compile(r"\breal\b"),
"agent/ghost.py")` as `test_fix_introducing_absent_path_is_rejected_and_reported`
and asserted a strict subset of its assertions. That case was one of the four
mandated demonstrated-red cases (`:1010` on `main`), so the proof was re-run after
the deletion to confirm the coverage did not go with it.

Same neutering as above (`return []` at the head of `_absent_new_path_refs`):

```
FAILED ...TestExistenceInvariant::test_fix_introducing_absent_path_is_rejected_and_reported
FAILED ...TestExistenceInvariant::test_rejection_is_logged_with_offending_path
FAILED ...TestExistenceInvariant::test_sibling_valid_fix_still_applies
FAILED ...TestExistenceInvariant::test_all_fixes_rejected_writes_nothing
FAILED ...TestExistenceInvariant::test_fix_introducing_absent_bare_name_is_withheld
FAILED ...TestExistenceInvariant::test_ambiguous_bare_name_passes_and_debug_logs
FAILED ...TestExistenceInvariant::test_dir_prefixed_decisions_unaffected_by_degraded_index[nonzero-rc]
FAILED ...TestExistenceInvariant::test_dir_prefixed_decisions_unaffected_by_degraded_index[oserror]
FAILED ...TestExistenceInvariant::test_audit_surfaces_withheld_without_writing
FAILED ...TestWithheldBlocksAutoMerge::test_bare_name_withhold_propagates_to_pr_body_telegram_and_liveness
10 failed, 120 passed in 22.94s
```

Ten red instead of eleven — exactly the deleted case, and nothing else. The three
surviving mandated cases (`test_fix_introducing_absent_path_is_rejected_and_reported`,
`test_fix_introducing_absent_bare_name_is_withheld`,
`test_dir_prefixed_decisions_unaffected_by_degraded_index` in both parametrisations)
are all still red, and the deleted case's code path is covered by the first of them,
which asserts the full withheld dict plus file content rather than the reason alone.
Revert confirmed (`grep -c NEUTERED_RED_PROOF` → 0); green after revert: 130 passed.
