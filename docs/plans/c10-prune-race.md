---
tracking: none
slug: c10-prune-race
type: plan
status: built
critique_rounds: 0
review_rounds: 0
---

# A dependency image "no longer held" between build and run

A bug fix from the emulator sweep, at stakes 1. It adds no check, gate,
hook or guard.

## The finding

Task `74a2b6ef14a1` (replay pop-b, clarify arm), test database row 3228 at
23:21:26.835Z: the review check's VM step failed kernel-side with
`valor/pop-b-clarify-fbad0f38ad54:fbd62ba1… is no longer held`. The task
has no `verify.ran`.

## The reading put to the test: a prune race

The reading was that `container.prune` deletes an image a step has built
but not yet named in a `verify.ran`, when a parallel step's prune runs in
that window. The evidence refutes it.

- `verify` holds the machine lock from `container system start` through
  the builds, the read-back before each run, the runs, the `verify.ran`
  append, the prune and `container system stop`. The window between build
  and run is inside that one hold. Every image delete in the kernel
  (`checked`, `prune`) runs inside a `verify`. The sweep's `reap` runs only
  when the lock is free.
- The image was never deleted. The runtime's own image index
  (`~/Library/Application Support/com.apple.container/state.json`) still
  holds the tag at `sha256:239fd67f…`, the manifest list the build
  exported at 23:21:25Z (`checks/vm-deps-fbd62ba1219f.out`).
- No prune ran for that database after 23:01Z: the image of stopped task
  `3602e75d7aed` (`valor/pop-b-bare-…`) is still recorded and still held.
- `images.json` was last written at 23:21:26Z and lacks the clarify tag:
  `checked` dropped the record. The sweep runs `3cad320f9`, whose
  `checked` reads an `image inspect` that fails as "not held" and drops the
  record, and the step said "no longer held".

## What happened: a test session stopped the machine's runtime

The macOS unified log (no command lines, only the calls' timing and the
services they reached):

- During the build, from 23:20:24Z, a client other than the kernel made
  three CLI calls every 0.35 to 1.2 s: status, list, list. A kernel of the
  sweep's database cannot have made them: its sweep's `reap` takes the
  machine lock first, and the verification held it. The pattern is
  `container.reap` (`system status`, `list`, then `list` again before a
  stop) called by `Router.sweep` at the start of every run.
- The C9 test check's host suite ran from about 23:05 to 23:29Z.
  `tests/conftest.py` gives every test not marked `container` a machine
  lock of the session's own, so its `reap` found the lock free while the
  kernel held the machine's. The runtime it reached was the machine's.
- At 23:21:25.339Z the kernel deleted the builder (until 26.688). The other
  client then ran status (49318), list (49321), list (49325, returning at
  26.689 when the builder was gone, so nothing was listed) and a fourth
  call (49327) whose connection was interrupted as launchd removed
  `com.apple.container.apiserver` at 26.722. With nothing listed and no
  builder of its own, `reap` runs `container system stop`.
- The kernel's calls fit around it: the build's `inspect` (49328) succeeded
  and the digest was recorded; the read-back `inspect` (49333) found no
  runtime, so the record was dropped and the step failed; the `system
  stop` in `verify`'s `finally` (49338) found nothing to stop. The step's
  row was appended at 26.835.

The pid-to-call mapping is an inference from timing and services, as the
log keeps no command lines. That the runtime was stopped at that moment,
that the image is still held, and that a lock-free client was calling the
runtime throughout are read directly.

## The bug and the change

A test not marked `container` takes a lock of the session's own but reaches
the machine's runtime. `Router.sweep` calls `container.reap` at the start
of every run. With the session's lock free, `reap` reads the runtime and,
when no container is listed and the session owns no builder, stops it. A
host suite on the machine stops a kernel's verification whenever it falls
between the builder's delete and the VM's start.

**Change.** Outside a `container` test, `tests/conftest.py` makes the
runtime read as absent (`container.present` is false), as it does under
the check profile, so `reap` returns before any CLI call. A `container`
test still shares the machine lock and the runtime.

**Test.** `tests/test_container.py`: outside a `container` test, with a
CLI that records its calls, `present()` is false and `reap` returns
nothing without a call.

**Docs.** `docs/sandbox.md` (tests that use the runtime) and the
`tests/conftest.py` docstring.

## Not changed

- `checked` on the sweep's commit reads a failed `inspect` as "not held";
  on this branch it says "the container runtime is down"
  and keeps the record when `system status` finds the runtime stopped.
- `prune` keeps what no open task's latest `verify.ran` names and what the
  running step holds (`keep`); inside the one lock hold nothing else
  prunes, so no change is needed there.

## Questions for Tom

None.

## Patch rounds

None yet.
