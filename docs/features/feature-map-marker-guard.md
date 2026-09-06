---
tracking: https://github.com/tomcounsell/ai/issues/3010
status: Shipped
---

# FEATURE_MAP Marker-Regression Guard

## What it guards

`tests/marker_map.py` holds `FEATURE_MAP` and `resolve_marker()`, the single
resolution function `tests/conftest.py::pytest_collection_modifyitems` calls
at collection time to auto-tag every test with a pytest feature marker
(`pytest -m sdlc`, `pytest -m "messaging or sessions"`, and so on).
`resolve_marker()` takes a module basename, strips `test_` and `.py`
(`basename.replace("test_", "").replace(".py", "")`, a global replace of
every occurrence, copied verbatim from the shipped hook), and substring-matches
the remainder against `FEATURE_MAP` in insertion order, first hit wins.

Nothing checked that the marker a file landed on was the marker anyone
intended. A file could be renamed, moved, or split and silently change
marker, or lose one, with the collection total unchanged and the suite
staying green — the mistag is invisible until someone runs `pytest -m
<marker>` and wonders why their new test never ran.
`tests/unit/test_feature_map_markers.py` is the guard: it fails when a
tracked test file's marker does not resolve as intended.

## Three mistag mechanisms

1. **Ordering collision.** `FEATURE_MAP` is a first-hit-wins dict. A generic
   key positioned early beats a specific key positioned late.
   `test_worktree_manager_config.py` tags `config`, not `git`, because
   `config` sits ahead of `worktree_manager` in insertion order.
   `tests/unit/reflections/test_sdlc_progress_check.py` tags `sdlc` for the
   same reason (`sdlc` is ahead of `reflection`). **Guard rule R1** below
   catches this — but only inside a themed package directory.
2. **Fragment match.** The match is a bare substring, not a whole token, so a
   pattern can match inside a longer word. `config` is a literal substring
   of `configured`, so `test_pm_briefings_no_slots_configured.py` tags
   `config`. `checkpoint` is a prefix of `checkpointing`, so
   `test_long_task_checkpointing.py` tags `validation`. **Guard rule R3**
   catches this suite-wide.
3. **Mangled stem.** The stem expression is a *global* `str.replace("test_",
   "")`, not a prefix strip, so a basename containing `test_` a second time
   has that occurrence eaten out of the middle: `tests/tools/test_test_judge.py`
   yields the stem `judge`, missing the `test_judge` key written for it.
   **No rule below can see this** — it surfaces as an unmarked file with no
   directory or sibling signal to contradict it, the same shape a
   presence-only rule cannot catch either (see "What was considered and
   rejected"). Tracked separately as
   [#3184](https://github.com/tomcounsell/ai/issues/3184) and deliberately
   left unfixed here: correcting the strip would retag
   `tests/tools/test_test_judge.py` and
   `tests/unit/test_validate_test_impact.py`, which is a marker-assignment
   change, not a regression-prevention one.

## The three rules, and what each can and cannot see

**Rule R1, directory intent.** For a test file whose parent directory is not
a known root (`tests`, `unit`, `integration`, `e2e`, `tools`, `performance`,
`ai_judge`): if the parent directory's own name resolves to marker M, the
file's basename must also resolve to M. The directory is a declaration of
intent the basename cannot forge.

**Rule R2, sibling uniformity.** For a package directory whose own name does
*not* resolve to a marker, every file in it must resolve to the same marker
(`None` included). The violating set is every sibling **outside the largest
marker group** — the majority is the package's de facto intent, so a single
file drifting away from eighteen consistent siblings is reported as one
violation, not twenty-three. **On a tie for largest, every file in the
package is reported as ambiguous** rather than letting dict-iteration order
silently pick a side.

**Rule R3, whole-token match.** Suite-wide, no directory signal required: the
winning `FEATURE_MAP` key must appear in the stem as a contiguous run of
`_`-delimited tokens, not as a fragment inside a longer word.

**The coverage boundary, stated plainly.** R1 and R2 need a package directory
to compare against, so together they reach only **80 of 834 tracked test
files (9.6%)** — R1 47 files, R2 33. The other 754 files sit directly under a
root directory (`tests/unit/`, `tests/integration/`, ...) and are covered by
R3 alone, which cannot see an ordering collision: an ordering collision is by
definition a genuine whole-token match that happens to belong to the wrong
key, so R3 agrees with it. **Worked example:**
`test_worktree_manager_config.py`, if it sat directly under `tests/unit/`
instead of inside a themed package, would resolve to `config`, pass R3
(`config` is a whole token), and never reach R1 or R2 (its parent `unit` is a
known root) — all three rules stay green on a genuine mistag. The rules are
not widened to close this: doing so would need a declaration of intent that
does not exist for those 754 files.

## Exemptions: `KNOWN_MISTAGS`, keyed by path only

`KNOWN_MISTAGS: dict[str, str]` maps a repo-relative path to a prose reason.
It is the **only** exemption mechanism — nothing is keyed by line number,
index, or ordinal position, and there is no whole-package exemption. Two
assertions bracket it:

1. Every violation the audit finds must be in `KNOWN_MISTAGS` (no new
   mistag).
2. Every `KNOWN_MISTAGS` entry must correspond to a real violation (**no
   stale exemptions**) — fixing a file without deleting its baseline entry
   fails the guard.

Path-keying follows [#2805](https://github.com/tomcounsell/ai/issues/2805),
where a line-number-keyed `ALLOWLIST` silently un-exempted call sites when
unrelated merges shifted line numbers. The stale-exemption assertion follows
[#3031](https://github.com/tomcounsell/ai/issues/3031), where an exemption
that no longer matched anything was a silent hole rather than a caught
regression. The baseline holds 24 pre-existing paths (21 from R1, 2 from R2,
1 further from R3) and can only shrink; draining it is
[#3175](https://github.com/tomcounsell/ai/issues/3175).

## What was considered and rejected

- **Requiring every file to carry a marker.** 554 of 834 files (66.4%)
  resolve to no marker at all. A must-be-marked rule would need a 554-entry
  exemption list on day one — a manifest pretending to be a guard, not a
  guard.
- **A whole-package exemption (`EXEMPT_DIRS`-shaped mechanism).** The
  measured baseline populates zero such entries, so it would ship with
  neither bracketing assertion ever exercised — the exact silent-hole shape
  #3031 warns against, reintroduced inside the mechanism meant to close it.
- **Making the package directory authoritative over the basename.** Gains 39
  markers and corrects 6 with zero losses, but it makes rule R1 tautological
  (the file's marker would be *defined* as the directory's marker, so "they
  match" proves nothing). Filed as #3175, which needs this guard to exist
  first so it can measure its own before/after.

## Responding to a red guard

`python tests/marker_map.py --audit` (standard library only, runs without a
venv) or `pytest tests/unit/test_feature_map_markers.py` names every
offending path with its resolved marker, expected marker, and the
`FEATURE_MAP` key responsible. Three remediations, in order of preference:

1. Rename the file so its basename resolves correctly.
2. Reorder or extend `FEATURE_MAP` (e.g. insert a more specific key ahead of
   the generic one currently winning).
3. Add a `KNOWN_MISTAGS` entry with a prose reason, when 1 and 2 are out of
   scope for the change at hand.

`python tests/marker_map.py --report` prints `path<TAB>marker` (`NONE` for
unmarked) for every tracked test file; `--count` prints the population size.
