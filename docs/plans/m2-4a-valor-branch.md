---
tracking: none
slug: m2-4a-valor-branch
type: bug
status: merged
critique_rounds: 0
review_rounds: 1
governance_grant: none
---

# 2.4a: the valor project names its branch

Stakes 0: a reversible config change.

## Incident

On the build Mac, Tom sent a message on the local chat bridge (2.4). Intake
started a task under project `valor` (`_start`, `_project_for` in
`core/intake.py`). The kernel's provisioning then failed with "main is the
default branch of https://github.com/tomcounsell/ai.git; a merge never lands
there". The failure is in `core/workspace.py`: `branch = spec.branch or
_remote_head(cache)`, then `_not_default`. `projects/valor.toml` sets no
`branch`, and its comment says the branch is given at start with `--branch`;
a task started by a message has no `--branch`. So every message-started
`valor` task fails provisioning, on any channel.

## Fix

- `projects/valor.toml` sets `branch = "valor-cori-rebuild"`, where the
  rebuild lands until cutover, and its comment says so.
- `--branch` at start still wins: `core/__main__.py` replaces both `branch`
  and `target_branch` with it.
- `target_branch` stays unset: provisioning uses `spec.target_branch or
  branch`, so the merge target is the same branch.
- No test asserts the valor spec's fields; no code changes.

## Test

A task provisioned from a spec with `branch` set, against a remote whose
default branch is `main` (the shape a message start has: no `--branch`),
targets that branch and is not refused. The same spec without `branch` is
refused as the default branch. The real `projects/valor.toml` loads with the
rebuild branch.

## Decided by default

- The branch is the rebuild's own, `valor-cori-rebuild`; at cutover the
  spec changes in the same step that moves the rebuild to the default
  branch.
- A message start does not check the (URL, branch) grant at provisioning;
  the broker refuses an ungranted merge. Not changed here.

## Build record

Changed: `projects/valor.toml` (branch and comment), `projects/README.md`
(the `branch` line), `tests/test_targets.py` (two tests: provisioning from
a spec with a branch against a remote whose default is `main` targets that
branch and the same spec without it is refused; the real valor spec names
the rebuild branch). The second test failed before the toml change.

Suite: 1467 passed, 55 skipped, 2 failed, 62 errors. The errors are the
email tests (dovecot is not installed on this Mac); the failures are
`test_mailserver` (dovecot) and `test_pi` (node under the profile), none
touching this change. Ruff check and format pass.

## Checks

All three checks ran on 82d824059.

- **Test:** pass. The head shows the same failures as the base: the email tests and test_mailserver because dovecot is missing, and test_pi, which also fails alone at 36bb6d146. Two gaps are left open: no end-to-end intake test runs with the real valor spec, and no test covers a `--branch` override of a spec branch.
- **Review:** pass, governance no. Message-started valor tasks (local and Telegram) now clone and target `valor-cori-rebuild`. The default-branch refusal and the merge grant at release are unchanged.
- **Docs:** no_change.

## Merged

The lead's decision: merge, since every check passed. The branch was rebased onto valor-cori-rebuild (plan-file commits only) and fast-forwarded. No rollout is needed beyond the kernel reading the spec at the next message start.
