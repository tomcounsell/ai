# xfail Hygiene System

**Status**: Active
**Created**: 2026-03-11
**Owner**: Valor Engels

## Problem

When bug fixes land, their corresponding `pytest.mark.xfail` or `pytest.xfail()` markers should be removed and converted to hard assertions. Without automated checks, stale xfails accumulate - tests pass but remain marked as "expected failures", meaning regressions would silently pass instead of failing.

**Real example from popoto**:
- PR #159 fixed SortedField ghost entries on partition key change
- TC1 in `test_field_index_edge_cases.py:222` had a runtime `pytest.xfail()` fallback
- After #159 landed, TC1 should have been converted to a hard assertion
- Instead, the xfail remained - if the bug regressed, the test would silently pass as "xfail"

## Solution

Three-layer xfail hygiene system integrated into the SDLC pipeline:

### 1. Planning Phase (`/do-plan`)

**Step 4.5: xfail test search** - For bug fixes, scan the test suite for xfail markers related to the bug:

```bash
grep -rn 'pytest.mark.xfail\|pytest.xfail(' tests/ --include="*.py"
```

For each related xfail found:
- Add a task to the plan: "Convert TC{N} xfail to hard assertion"
- Document the test location in Success Criteria
- Ensure the plan includes explicit xfail → assertion conversion work

**Template change**: Added success criterion checkbox:
```markdown
- [ ] [If bug fix: All related xfail/xpass tests converted to hard assertions]
```

### 2. Testing Phase (`/do-test`)

**Stale xfail Hygiene Scan** - Post-test quality check that detects xpass (passing tests still marked xfail):

```bash
# Find all xfail markers
grep -rn 'pytest.mark.xfail\|pytest.xfail(' tests/ --include="*.py"
```

For each xfail found, check if pytest reports it as `XPASS` (unexpected pass). If detected:
- Flag prominently: "⚠️ Stale xfail: tests/foo/test_bar.py::test_baz is passing but still marked xfail"
- Include file and line number for easy removal
- Suggest: "This test should have its xfail marker removed and converted to a hard assertion"

### 3. Verification Phase (`/do-build`)

**Plan verification table** - Machine-readable check executed after the build:

```markdown
| Check | Command | Expected |
|-------|---------|----------|
| No stale xfails | `grep -rn 'xfail' tests/ \| grep -v '# open bug'` | exit code 1 |
```

This catches any xfail that doesn't have an explicit "# open bug" comment justifying why it's still marked.

## Workflow

### Bug Fix Scenario

1. **Planning**: `/do-plan` for bug fix
   - Step 4.5 finds xfail tests for the bug
   - Plan includes task: "Convert TC1 xfail to hard assertion at line 222"
   - Success criteria: "All related xfail/xpass tests converted to hard assertions"

2. **Implementation**: `/do-build` executes the plan
   - Builder fixes the bug
   - Builder removes xfail marker and converts to hard assertion
   - Tests run with `/do-test`

3. **Testing**: `/do-test` runs post-test quality checks
   - Stale xfail scan detects any remaining xpass
   - If found: flags as quality issue in report
   - If clean: test phase completes

4. **Verification**: `/do-build` runs verification table
   - `grep -rn 'xfail' tests/` checks for stale markers
   - Exit code 1 (no matches) = pass
   - Exit code 0 (matches found) = fail

### Escape Hatches

**Legitimate xfails** (open bugs not yet fixed) should include a comment:
```python
@pytest.mark.xfail  # open bug: issue #42 - performance regression on large datasets
def test_bulk_insert_performance():
    ...
```

The verification check `grep -v '# open bug'` skips these.

## Architecture

### Files Modified

- `.claude/skills/do-plan/SKILL.md` - Added step 4.5 xfail test search
- `.claude/skills/do-plan/PLAN_TEMPLATE.md` - Added success criterion and verification row
- `.claude/skills/do-test/SKILL.md` - Added stale xfail hygiene scan

### Integration Points

- **Planning**: Runs before implementation, catches xfails upfront
- **Testing**: Runs after tests pass, detects new stale xfails
- **Verification**: Runs at PR time, blocks merge if stale xfails found

## Limitations

1. **Requires pytest output parsing**: The stale xfail scan depends on detecting `XPASS` in pytest output. If pytest output format changes, the scan may need adjustment.

2. **Cross-repo detection**: Currently scoped to the repo being tested. If a fix in repo A affects an xfail in repo B, the scan won't detect it.

3. **Comment convention dependency**: The "# open bug" escape hatch relies on developers following the convention. A strict linter rule could enforce this.

## Success Metrics

**Goal**: Zero stale xfails in merged PRs.

**Measurement**:
- Count of xpass flags in `/do-test` reports
- Count of verification table failures on "No stale xfails" check
- Reduction in "fix lands but test not updated" incidents

## Future Enhancements

1. **Automated conversion**: Instead of just flagging, automatically remove xfail markers and convert to assertions when tests pass
2. **Cross-repo scanning**: Detect when a fix in one repo affects xfails in related repos
3. **CI enforcement**: Run the stale xfail scan in CI on all PRs
4. **Metrics dashboard**: Track xfail hygiene over time across projects
