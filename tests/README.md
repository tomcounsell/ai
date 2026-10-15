# tests

The suite proves the kernel is solid and that Telegram carries messages both ways, and nothing else. Real Postgres, no mocks: a model provider is a local HTTP upstream speaking its wire format (`scripted_upstream.py`, `judgement_upstream.py`), Telegram is a local server in its own process (`telegram_emulator.py`).

## What it asserts

1. State machine: a task's fold reaches the right state from its rows, including stop, waiting on a question, patch after a failed check, and merge.
2. Merge predicate: a merge needs one current candidate, a passed test, and a passed review.
3. Ledger: rows are written once and read back; the ledger role cannot update or delete.
4. Kernel resident: `python -m core serve` starts, takes a task, runs a scripted turn, collects it, and survives a restart with the task resumed.
5. Stop: stopping a task kills its turn and everything the turn started.
6. Telegram in: an operator message reaches the kernel once and starts a task; a reply to Valor's message binds to that task.
7. Telegram out: a send leaves through the outbox exactly once, across a bridge crash and restart.
8. End to end: a Telegram message starts a task, a scripted turn builds, check and review pass, the kernel merges, Tom's chat gets the report.
9. Rollout: a merge of the kernel's own branch restarts it on the new commit, syncing dependencies when `uv.lock` changed.
10. Metering: a turn's model call through the gateway writes its opened and charged rows with the turn's id.

## Running

    VALOR_TEST_DB=mine VALOR_TEST_PORTS=7600-7799 uv run pytest -q -n auto -p no:cacheprovider

Each xdist worker gets its own database (`<VALOR_TEST_DB>_<worker>`) and an equal slice of `VALOR_TEST_PORTS`; unset, a worker takes 20 ports from 6600. The `MACHINE_ONLY` settings in `conftest.py` are scrubbed from the environment. Every test declares its live spend with `@pytest.mark.spend(usd=...)`; live tests run only with `VALOR_LIVE=1`.

## Budget

At most 15 test files and 5,000 lines under `tests/`, and no more test lines than production lines. `test_budget.py` fails when either is passed (CLAUDE.md rule 8).
