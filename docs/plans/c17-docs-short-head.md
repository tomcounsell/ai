---
tracking: none
slug: c17-docs-short-head
type: plan
status: merged
critique_rounds: 0
review_rounds: 0
---

# A docs head written as an abbreviated commit id

A bug fix from the D5 sweep, at stakes 1. It adds no check, gate, hook or
guard, and no refusal, cap or retry.

## The finding

In the D5 sweep, 2 of 11 docs check turns wrote a 12-character commit id in
the verdict's `head`. `fresh._docs_fields` refused it ("the head is not a
full commit id"), the step was recorded as `step.failed`, and the run
paused until the next firing:

- cut-a clarify, task `4794b009e34b`, head `ffaa9192fc77`;
- pop-a routed, ledger `valor_rebuild_test_d5`, row 2194, task
  `ef5baef0d24c`, turn `9296efd3e46a`: `verdict refused: the head is not a
  full commit id` (read in a read-only transaction).

The stage file `skills/sdlc/docs.md` already asked for "the full commit
id"; the turns abbreviated it anyway.

## The change

The head is resolved where the turn's commits live, in the docs clone,
before the fetch. An abbreviation cannot be fetched (upload-pack wants a
full id), so resolving after the fetch is not possible.

- `git.commit_named(workspace, name, profile, mark)`: the full id of the one
  commit a hex `name` (4 to 40 characters) names, read with `git rev-parse
  --verify --quiet <name>^{commit}` under the turn's sandbox profile, with
  the kernel's pinned git config and environment (replace refs and grafts
  off), as `git.hostile` reads the same clone; None when it names none or
  is ambiguous.
- `workspace.fetch_into_mirror` takes a commit id or an abbreviation of one
  and returns the full id. An abbreviation is resolved after the clone's
  borrowed objects and config are read, under the same profile and mark;
  one naming no single commit raises `FetchRefused`, the path a full id
  the clone does not hold already takes (`docs.kept` with `refused`, a
  `changes` verdict "no docs commit kept").
- `fresh._docs_fields` accepts a hex head of 4 to 40 characters; the docs
  runner passes the full id `fetch_into_mirror` returns to `kept_prefix`,
  the mirror ref and `docs.kept`.
- `skills/sdlc/docs.md` asks for the full 40-character id as `git rev-parse
  HEAD` prints it; `docs/sdlc-state-machine.md` states that an abbreviation
  the clone resolves to one commit is taken as that commit.

A clone ref whose name is the abbreviation's hex resolves to that ref's
commit, as git resolves it; the turn owns the clone and could name that
commit in full anyway, and `kept_prefix` judges whatever it names.

## Review and test verdicts

Neither has the defect. The review verdict (`_review_fields`) carries no
commit id; the review and test checks run on `f.candidate.sha`, a full id
the kernel recorded from the working session's head (`git.head`). The
other caller of `fetch_into_mirror`, `session._keep`, passes that full id.

## Tests

- `tests/test_docs_runner.py`: a docs turn naming its head by 12
  characters is kept as the full commit (`updated`, `docs.kept` with no
  refusal); a short or full head naming no commit in the clone keeps
  nothing with "no docs commit kept"; a head that is not hex (`HEAD~1`) is
  still a malformed verdict.
- `tests/test_workspace.py`: `fetch_into_mirror` fetches by a 12-character
  abbreviation and returns the full id; an abbreviation naming nothing and
  a name that is not hex are refused.

## Records

Build 1: tests red first (3 failed: the abbreviation fetch, the abbreviated
docs head, the short head naming nothing), then green.
Suite (`-m "not container"`): 1907 passed, 24 skipped, 3 failed, all in
`tests/test_ports.py` (port draws racing a concurrent suite); rerun alone,
3 passed. `ruff check` and `ruff format --check` clean.
