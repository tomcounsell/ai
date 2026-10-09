---
tracking: none
slug: c7-test-images
type: build
status: merged
stakes: low
critique_rounds: 0
review_rounds: 0
patch_rounds: 0
governance_grant: none
---

# Container-marked tests leave the live kernel's images alone

Track C7 of the finishing run. A bug fix: it adds no check, gate, hook,
round, review step or guard, so no `governance_grant` is needed. Stakes are
low: the test configuration, the container tests' own helpers, and one name
constant in `core/container.py`.

## Incident

Task `f917b77bfdd3` on the live kernel (ledger `valor_rebuild`). Review's
first VM run at 19:11 built `valor/popoto:91b2bbc3df11...` and recorded it
in `~/Library/Application Support/valor/images.json` (`vm-deps-91b2bbc3df11.out`
in the task's checks directory; `verify.ran` row 2706). Review's second
run started at 20:07:34 and failed at 20:12:34 with "valor/popoto:91b2...
is no longer held" (row 2861, `step.failed`). No image was built in that
run: the task's checks directory has no output newer than 19:11, so the
base image and the popoto image both passed `checked` in `ensure_base` and
`ensure_deps`, and the second `checked` of the same tag, milliseconds
later and before the head run, failed.

At the same time a test check ran five container-marked tests from the
`valor-rebuild-a1` worktree (`test-a1-container-rerun.log`, 18 min 59 s,
ending 20:25; database `valor_rebuild_test_test_a1`). Its first test,
`test_the_builder_cannot_reach_the_kernels_postgres_or_a_tasks_services`,
passed.

Afterwards `images.json` lacked the popoto record and held records of
images built by test sessions under their databases' labels:
`valor/toy:de16...` (label `6a48e10e4685`, `valor_rebuild_test_test_a1`),
`valor/toy:3ef0...` (`valor_rebuild_test_c6build`), `valor/valor:ce09...`
and `valor/toy:11fe...` (one other test database), `valor/base:f730...`
(the base tag of older `core/images/base/` files).

## Cause

Container-marked tests share three things with the live kernel and use all
three outside the machine lock.

1. **The runtime, stopped outside the lock.** The kernel waited on the
   machine lock while the test built `valor/reach:test`. The test's build
   ran inside `machine_lock`; its `finally` (image delete, `_record`,
   `container.stop_system()`) ran after the lock was released. The kernel
   took the freed lock within its one-second poll, found both images held,
   and then the test's `system stop` took the runtime down under it. The
   kernel's next `container image inspect` failed, `checked` read a
   recorded tag with no held digest, dropped the record itself, and raised
   "is no longer held". Every container test does the same: `base_image()`
   starts the system before taking the lock, and the tests' `finally`
   blocks call `container.start`, `checked`, `prune`, `image delete`,
   `_record` and `stop_system` with no lock held.
2. **The image records.** `tests/conftest.py` (`session_machine_lock`)
   points `IMAGES` at the session's own directory only for tests without
   the `container` marker, so container tests read and write the kernel's
   `images.json`. Every image a test session builds is recorded there and
   never removed: the records that came back are test sessions' rebuilds
   of the same deterministic tags (`valor/toy:...`, `valor/valor:...`,
   `valor/base:...`), not a restored snapshot.
3. **The image names.** Tests build `valor/base:<digest of
   core/images/base/>`, the kernel's own tag whenever the worktree's base
   files match the kernel's (`valor-rebuild-a1` at `57c9f13ef` computes
   `valor/base:6ebb5fbf...`, the kernel's), and dependency images under
   `valor/<project>:<key>`. A test's `checked` on a shared tag deletes a
   held image it has no record of and rewrites a record it did not write;
   a test build of a shared tag replaces the kernel's image and relabels its
   record.

`prune` was not the path: it deletes only tags whose record carries the
caller's own database label, and no test passes the kernel's label.

## Fix

A test session never starts, stops, deletes, retags, or records anything
outside its own images and its own records, and it touches the shared
runtime only while it holds the machine lock. The machine lock and the
runtime stay shared, because the runtime is one per machine.

- `core/container.py`: the image repository prefix is a module name,
  `REPO = "valor"`, used by `base_tag`, `deps_tag`, and `prune`'s skip of
  the base image.
- `tests/conftest.py`: every test, container-marked or not, records images
  in the session's own `images.json` and names them under
  `valor-test-<label>`, the label being `container.owner` of the session's
  test database. Tests without the marker keep their own lock and builder
  owner file too, as now. At session end, when the session recorded any
  image, `release_images` takes the machine lock, starts the system,
  deletes every recorded image whose label is the session's, drops its
  record, and stops the system.
- `tests/test_container.py`: a `held()` context takes the machine lock as
  a kernel does, and stops the system before releasing it. Each test's own
  runtime work (start, base image, builds, checks, prunes, deletes, runs,
  the final stop) runs inside it. A test that calls `container.verify` or
  `container.reap`, which take the lock themselves, holds it only around
  its own work. The tests label their builds with the session's label, not
  the shared `"tests"`, and name them under `container.REPO`.

## Tests

- `test_a_container_test_keeps_its_own_records_and_image_names`
  (container-marked, touches no runtime): the records file is the
  session's, image names are under the session's prefix, and the machine
  lock is the machine's.
- `test_an_image_another_database_recorded_survives_a_tests_prune_and_cleanup`
  (runs anywhere, a stand-in CLI): a record carrying another database's
  label and records of the session's own label; after `prune` and
  `release_images` with the session's label, only the session's tags were
  deleted and the other record is unchanged.
- Evidence (one container test run): the machine's `images.json` hash is
  unchanged by the run, and the run's images are gone after it.

## Questions for Tom

None.

## Records

### Build, 2026-10-09

- Built as planned. `base_image()` in `tests/test_container.py` now gives
  the base build a real checks directory: with the session's own image
  names the base image is built in every session that needs it, and the
  old `/nonexistent` layout made that build fail on opening its output
  file (`container-retagged.out`, the first evidence run).
- Each session that runs container tests builds its own base image (the
  tag no longer matches the kernel's) and deletes it at session end. This
  costs one base build per such session.
- Suite `-m "not container"`: 1808 passed, 24 skipped, 52 deselected.
  ruff check and format clean.
- Container evidence: `test_a_container_test_keeps_its_own_records_and_image_names`
  passed under the marker. The rerun of
  `test_an_image_retagged_by_hand_is_a_kernel_cause_and_dropped` after the
  `base_image` fix did not run: free space on `/System/Volumes/Data` fell
  to 3.6 GB, under the 6 GB the run needs. The live `images.json` holds no
  `valor-test-` tag after the first evidence run, and the session's own
  records were empty after its release.
- Left as is, for the lead: `container.checked` reads a failed `image
  inspect` (the runtime stopped, for one) as "not held" and drops the
  record. With tests no longer stopping the runtime under a kernel this
  path is not reached by tests.

### Lead's fact, 2026-10-09

- The popoto image is still in the store. Only its record was lost. This
  matches cause 1: `checked` deletes an image only when inspect returns a
  different digest. When inspect fails because the runtime is stopped,
  `checked` drops the record and leaves the image.
- No test or kernel code copies or restores `images.json`. A search of the
  `tests/` and `core/` directories in this worktree, `valor-rebuild` and
  `valor-rebuild-a1` finds only `_images` reading it and `_record` writing it.
- The fix closes the read-modify-write path as well: tests no longer write
  the live file.
- The container evidence rerun is still owed: free space is 6.0 GB, not
  more than 6.

### Container evidence, 2026-10-09

- Rebased onto `daee94e81`. The head is the commit that adds this entry.
- `test_an_image_retagged_by_hand_is_a_kernel_cause_and_dropped`, run
  alone with 7.4 GB free: 1 passed in 5 min 55 s
  (`container-retagged-2.out`). The session's own records were empty after
  its release. The live `images.json` sha256 was the same before and after
  the run (`dc7fbf02...`), and it holds no `valor-test-` tag.
