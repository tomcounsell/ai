# tests

Tests for everything in the repository.

## Scope

The rule: integration first, no mocks.

- Real Postgres.
- Real containers.
- Real bridges against test accounts.
- No mocks, fakes, or patched clients. A model provider is a real local HTTP upstream speaking its wire format, its bodies shaped by responses recorded live (`tests/judgement_upstream.py`, `tests/fixtures/record_judgement.py`).
- Local performers the tests register live here (`tests/performers.py`: `workspace_write`, `outbox_send`), never in `tools/`.
- Every test declares its live spend: the money it may cost per run (`pytest.mark.spend`). Live tests run only with `VALOR_LIVE=1`.

The emulator (human-originated historical requests, labelled by the human decision, scored by cheap judgement) lives in `tests/emulator/`, a package run as modules (`python -m tests.emulator.replay`), never collected by pytest. Its stand-in and judge calls go through the kernel's gateway onto an emulator task's spending. `tests/emulator/replay.py` forces its `bare` and `clarify` arms by running `tests/judgement_upstream.py` (`python -m tests.judgement_upstream --answer precise|thin`) and pointing the kernel's judgement legs at it.

The Telegram bridge's tests run Telegram as a local server in its own process (`python -m tests.telegram_emulator`, which binds a free port and prints it) reached through `EmulatorWire`, kill the bridge by its pid in `tests/telegram_child.py`, and run over the real port and the test database through `tests/telegram_port.py`, which gives each test chat ids of its own; `tests/test_telegram_pipeline.py` carries Tom's replies through the bridge to the kernel's binding. The real wire runs on Telegram's test servers in `tests/test_live_telegram_dc.py` (`VALOR_LIVE=1`, `VALOR_TELEGRAM_TEST_DC=1`) and on Valor's account only in Tom's window (`VALOR_TELEGRAM_WINDOW=1`).

Governed by [docs/emulator.md](../docs/emulator.md), [docs/data.md](../docs/data.md) (Test databases), and [docs/tech-stack.md](../docs/tech-stack.md) (Tests).

## Imports

- May import: everything.
- Imported by: nothing.

## Effect classes

Tests run with the effect classes of what they exercise. A test that performs an `act` does so only against a test account, with the ledger recording it like any other.

## Not here

**Governance is restrained by structure, not sentiment.** Before any check, gate, hook, validator, review round, or approval step is added, the change names the mission item it serves and the incident that already happened without it; missing either, it is not added. A bug fix never adds a guard; it fixes the code. Adding governance is an `act`-class effect: the Brief carries a `governance_grant` field, default none, and a diff that adds any of the above needs Tom's tap, one approval per instance, through the same approval surface as a merge or a send. Every guard is ledgered with the incident it prevents, the mission item it serves, and a ninety-day expiry; a guard that has not fired by expiry is deleted by default. The blind verifier asks one Jev-class boolean over every diff, "does this add a check, gate, hook, round, or review step", and a yes with no grant is a refused merge. The same paragraph, in the same words, sits at the top of `CLAUDE.md`, in the persona rendered into every turn, and in the Not-here section of every directory README. No restraint skill, no hook that blocks hooks, no governance dashboard: each is the disease presenting as the cure.

- Mock or stub modules, however convenient.
- Tests that assert on prompt text or keyword matches instead of behavior.
- Tests that exist to police other tests.
