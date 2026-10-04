# 4.1 record: merged

The record of [m4-1-objective-tree.md](m4-1-objective-tree.md) after patch
round 1.

## Patch round 1 checks

- test-4-1-p1: pass, 1351 passed, 23 skipped; both deadlock tests fail
  without the fix. Concurrent steer, answer, and approve replies are not
  driven; they share the stop reply's locked path.
- review-4-1-p1: pass. Governance boolean no. F1 to F3 closed; a lock order
  audit of the kernel found no cycle. The answers line was reworded at
  merge.
- docs-4-1-p1: updated. The tree's built text moved to
  `docs/objective-tree.md` at merge.

## Merged

- Lead suite on 04d6dbef1: 1413 passed, 25 skipped; ruff clean.
- Backup `valor_rebuild-20261004T153458Z.dump`.
- `valor-cori-rebuild` fast-forwarded 67ac9a5fc to eb3174c4e.
- Rollout: the kernel restarted, no migrate. Root `3d29ecb75b6f` and child
  `db34112c17db` at `--ceiling read`; stopping the root stopped both; the
  child names its parent and the root's tree spending covers both nodes.
