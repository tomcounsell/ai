## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `scripts/pytest-clean.sh tests/unit/test_docs_auditor_substrate.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Rename query gone | `grep -rn "_git_log_follow_renames" --include="*.py" --include="*.md" reflections/ tests/ docs/features/ docs/sdlc/ .claude/ tools/ agent/ \| wc -l` | match count == 0 |
| Link detector gone | `grep -rn "_detect_renamed_link_fixes" --include="*.py" --include="*.md" reflections/ tests/ docs/features/ docs/sdlc/ .claude/ tools/ agent/ \| wc -l` | match count == 0 |
| Symbol detector gone | `grep -rn "_detect_renamed_symbol_fixes" --include="*.py" --include="*.md" reflections/ tests/ docs/features/ docs/sdlc/ .claude/ tools/ agent/ \| wc -l` | match count == 0 |
| README detector gone | `grep -rn "_detect_readme_broken_entries" --include="*.py" --include="*.md" reflections/ tests/ docs/features/ docs/sdlc/ .claude/ tools/ agent/ \| wc -l` | match count == 0 |
| Follow cap constant gone | `grep -rn "GIT_LOG_FOLLOW_CAP" --include="*.py" --include="*.md" reflections/ tests/ docs/features/ docs/sdlc/ .claude/ tools/ agent/ \| wc -l` | match count == 0 |
| Rename query counter gone | `grep -rn "_RENAME_QUERY_COUNT" --include="*.py" --include="*.md" reflections/ tests/ docs/features/ docs/sdlc/ .claude/ tools/ agent/ \| wc -l` | match count == 0 |
| Reporter docstring un-blinded | `grep -c "(non-renamed)" reflections/docs_auditor.py` | match count == 0 |
| Literal channel gone | `grep -c 'new == ""' reflections/docs_auditor.py` | match count == 0 |
| Reporter makes no rename query | `sed -n '/def _detect_deleted_target_issues/,/^def /p' reflections/docs_auditor.py \| grep -c "_git_log"` | match count == 0 |
| Reporter stays subprocess-free (non-regression guard — already green pre-change) | `sed -n '/def _detect_deleted_target_issues/,/^def /p' reflections/docs_auditor.py \| grep -c "subprocess"` | match count == 0 |
| Basename cache reset survives | `grep -c "_BASENAME_INDEX_CACHE.clear()" reflections/docs_auditor.py` | output > 0 |
| Rename regression exists | `grep -c "rename destination" tests/unit/test_docs_auditor_substrate.py` | output > 0 |
| Existence-invariant coverage survives the channel collapse | `grep -c "target-absent" tests/unit/test_docs_auditor_substrate.py` | output > 3 |

**Every row above was executed against unmodified `main` on 2026-08-17** — validated by running,
not by reasoning about grep's output format. Three rounds of critique broke these rows via three
different mechanisms (unquoted `--include` globs aborting under zsh; a `^./docs/plans/` anchor
that matched nothing; then a `^docs/plans/` anchor that was equally inert because `grep -r .`
emits `./`-prefixed paths, while that same `.` descended into 29 sibling worktrees). The durable
fix is to stop grepping `.` at all: the rows enumerate real source roots, which removes the
`docs/plans/` exclusion, the anchor, and the worktree contamination in one move. Confirmed by
execution — the survivor set for every symbol is exactly `reflections/docs_auditor.py` and
`tests/unit/test_docs_auditor_substrate.py`, with zero lines from this plan document or any
worktree.

Red/green state on unmodified `main`, so a reviewer can tell which rows prove the work happened:

| Row | Pre-change | Meaning |
|---|---|---|
| The six symbol-absence rows | 12, 4, 8, 7, 7, 9 | demonstrated red — must reach 0 |
| Reporter docstring un-blinded | 1 | demonstrated red — must reach 0 |
| Literal channel gone | 5 | demonstrated red — must reach 0 |
| Reporter makes no rename query | 1 | demonstrated red — must reach 0 |
| Rename regression exists | 0 | demonstrated red — must become > 0 |
| Reporter stays subprocess-free | 0 | **already green** — a non-regression guard, not evidence of completion |
| Basename cache reset survives | 1 | already green — non-regression guard |
| Existence-invariant coverage | 5 | already green — non-regression guard, floor of 3 |
| Lint / format clean | pass | `python -m ruff check .` verified clean and does not descend into worktrees |

**Two execution hazards found while validating these rows, both recorded so the builder does not
rediscover them.** First, `grep` on this machine is **ugrep 7.5.0**, not GNU grep — the rows above
were executed under it and behave correctly, but do not assume GNU-only flags will work if a row
is edited. Second, and more dangerous: if the root list is ever collapsed into a single shell
word (for example by putting it in an unquoted variable), ugrep emits `No such file or directory`
on **stderr** and `0` on **stdout**. A `match count == 0` row reads stdout, so a completely broken
command **false-passes**. Keep the roots as literal arguments in the row, never behind a variable,
and treat an unexpected `0` on a row that was previously red as a reason to inspect stderr rather
than as success.

