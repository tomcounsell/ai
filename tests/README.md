# tests

Tests for everything in the repository.

## Scope

The rule: integration first, no mocks.

- Real Postgres.
- Real containers.
- Real bridges against test accounts. Email's run against Dovecot, started as the test's user behind a TLS terminator signed by a test CA, and a local SMTP server that files what it accepts in Dovecot's `\Sent` folder (`tests/mailserver.py`; ports from `VALOR_TEST_PORTS` when set). The kill tests run the bridge as a process of its own (`tests/email_child.py`) and kill it by its PID.
- No mocks, fakes, or patched clients. A model provider is a real local HTTP upstream speaking its wire format, its bodies shaped by responses recorded live (`tests/judgement_upstream.py`, `tests/fixtures/record_judgement.py`).
- The kernel's bridge-port tests use `tests/bridges.py` (`configure`, which sets the operator's settings for one test, and `FakeBridge`, a bridge with no platform).
- Local performers the tests register live here (`tests/performers.py`: `workspace_write`, `outbox_send`), never in `tools/`.
- `VALOR_TEST_PORTS` (`LOW-HIGH`) moves every server the tests start into one span (`tests/ports.py`): `listen()` hands each test server a port of it, turning round the span and passing over ports a live listener holds and ports reserved for a task's services, and the test-started Postgres, Redis, and mail servers draw from it too. A port `ports.service()` gives a task's service stays reserved until the test ends, in a file every process the tests start reads (`VALOR_TEST_RESERVED_PORTS`), so no server takes it before the service starts. Unset, servers listen where the OS puts them. `projects/valor.toml` sets it to the dev ports, 8000 to 8009, the only ports the check and turn profiles let the suite listen on and reach, so the suite runs in a task's workspace.
- In a task's workspace the kernel runs the suite under the check profile, which denies some of what tests do on the host: `/bin/ps`, a port the OS chooses, the shared `/private/tmp`, a sandbox inside the sandbox, DiskArbitration. A test that fails on one is reported skipped with a reason naming it, only when its traceback, the output of the command whose failure it raised, or its stderr shows the denial's own error and trying the operation in that run meets the same denial (`tests/denials.py`). On the host nothing is denied, so nothing is skipped this way.
- Two markers say where a test can run: `macos` (needs macOS itself: sandbox-exec, `sandbox_check`, the Command Line Tools, `security`; skipped off Darwin, so this repository's suite runs in a verification VM with these skipped) and `container` (needs Apple's `container` at `/usr/local/bin/container`; skipped where it cannot be run, absent or denied by the sandbox the suite runs under: Valor's Mac and Tom's Mac have it, and the check profile denies it). Every other test takes the machine lock and the kernel's image records in a directory of the session's own. A parametrized test carries `macos` only on the parameters that need it, and a test the VM cannot pass for any other reason is made portable. Scratch clusters start from `VALOR_PG_BIN`, which the session sets to Debian's PostgreSQL 18 where the Homebrew one is absent. `tests/test_container.py` runs real VMs and builds; its first test builds the base image when its tag is missing.
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
