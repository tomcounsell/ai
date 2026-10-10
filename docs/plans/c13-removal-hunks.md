---
tracking: none
slug: c13-removal-hunks
type: plan
status: planned
critique_rounds: 0
review_rounds: 0
---

# `governance.adds` reads hunks that only remove lines

A bug fix at stakes 1, from the Follow-ups of
`docs/plans/c11-governance-precision.md`. It adds no check, gate, hook,
validator, round or guard: the governance site already asks one question
per hunk of a diff, and this makes it ask that question of every hunk. The
CLAUDE.md governance paragraph is unchanged.

## The finding

`core/judgement_sites.py::diff_hunks` returns only hunks with added lines,
so a hunk that only removes lines is never asked `governance.adds`.
Removing an approval step adds none, but removing an exemption from one
adds instances of it:

```python
def needs_tap(path):
    if is_docs(path):
        return False
    return require_tap(path)
```

Deleting the two `is_docs` lines makes every docs change need Tom's tap.
The question has said since C11 that narrowing an exemption from an
existing step counts, and the site never puts that case to it.

Motivation (the fix serves it; it adds nothing to it): the governance
constraint, whose boolean `docs/mission.md` says is "asked over every hunk
of a diff", and the C11 review probe of a narrowed exemption, which the
site could ask only because the probe's hunk also added a line.

## Threat model

The turn writes the candidate's commits, so it controls every hunk's
content. The kernel reads hunks from its own mirror with git and never
from text a turn supplies. A turn that hides an added step inside a
removal is the case this fixes: the removal is now judged.

## The fix

- `git.Hunk` carries its removed lines. A hunk with added lines keeps the
  id it had (path, function context, added lines), so every recorded
  instance id and grant stands. A hunk with no added lines has an id over
  its path, function context and removed lines, so two removals in one
  function context get distinct ids.
- `git.hunk_at` returns the hunk whose new range holds the line, whether it
  adds or only removes lines. A removal-only hunk's new range is its
  context lines (`+0,0` for a deleted or emptied file, read at line 0). A
  judged instance on it is named `InstanceSpec(path, start)` as any other
  and `verdicts._instances` reads its id from git, so a caution routes to
  the same grant surface as an added hunk. A reviewer may name one too.
- `diff_hunks` returns every hunk, its text the `git diff -W` hunk holding
  it, as before. A deleted file is one removal-only hunk.
- `governance.json` lists each instance's `removed` lines beside `added`.
- The question's one sentence on what to read becomes "Judge what the
  added lines add, or for a hunk that only removes lines, what removing
  them adds; the other lines are context." The task digest changes, so
  every hunk is asked fresh under the new question; reuse stays keyed on
  hunk id, input digest and task digest.

## Done

- The `is_docs` removal is a judged hunk; its caution is an instance whose
  id is `git.hunk_at`'s and the review is `governance_refused` until
  granted.
- A removal-only hunk that deletes an approval step is asked, and its
  proceed answer makes no instance.
- An added hunk's id is unchanged.
- Focused tests, the host suite, ruff check and format.

## Questions for Tom

None.

## Leaves out

The bare guard-row miss in the same Follow-ups.
