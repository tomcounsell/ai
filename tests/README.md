# tests

The suite proves the kernel is solid, that Telegram carries messages both ways, and that the public site holds together, and nothing else. Real Postgres, no mocks: a model provider is a local HTTP upstream speaking its wire format (`scripted_upstream.py`, `judgement_upstream.py`), Telegram is a local server in its own process (`telegram_emulator.py`).

## What it asserts

1. State machine: a task's fold reaches the right state from its rows, including stop, waiting on a question, patch after a failed check, and merge.
2. Merge predicate: a merge needs one current candidate, a passed test, and a passed review.
3. Ledger: rows are written once and read back; the ledger role cannot update or delete.
4. Kernel resident: `python -m core serve` starts, takes a task, runs a scripted turn, collects it, and survives a restart with the task resumed.
5. Stop: stopping a task kills its turn and everything the turn started.
6. Telegram in: Tom's message reaches the kernel once and binds to his chat's session, starting no task itself; a reply to a task's message binds to that task; a stranger in Tom's DM binds to nothing; his exact `stop` there stops at once every task reporting to the session; a root task with no session reports to the operator chat's session.
7. Telegram out: a send leaves through the outbox exactly once, across a bridge crash and restart.
8. End to end: Tom's DM message reaches his DM session, whose scripted turn starts a task (`task.start`); the task builds, check and review pass, the kernel merges, the report reaches the session (`child.reported`), and the session's next turn tells Tom's DM. A chat session's steer of a task that ended or merged reopens it for a second round in the same workspace and harness session, reporting again.
9. Rollout: a merge of the kernel's own branch restarts it on the new commit, syncing dependencies when `uv.lock` changed, and the start that ends the rollout restarts each bridge whose code the merge reaches.
10. Metering: a turn's model call through the gateway writes its opened and charged rows with the turn's id.
11. Orchestration: only `valor:web` reaches the web, its guard is ledgered with its grant and expiry, and a turn loads Valor's plugin first, nests subagents without Claude Code's cap, and gives every subagent the Brief.
12. Memory import: an imported old record is ingested in its own project and client and recalled for that project's later tasks only; `memory import` appends each old record once, and only for projects served here; the prep script keeps human and agent records, drops summaries, maps a Cyndra-labelled record to cyndra, and collapses near-exact duplicates with Tom's record kept.
13. Public site: every internal link and anchor in `site/` lands, every source link names a path in this checkout, `sitemap.xml` lists exactly the pages, each removed page redirects to one that exists, and no chat id, token or other email address is published.

## Running

    VALOR_TEST_DB=mine VALOR_TEST_PORTS=7600-7799 uv run pytest -q -n auto -p no:cacheprovider

Each xdist worker gets its own database (`<VALOR_TEST_DB>_<worker>`) and an equal slice of `VALOR_TEST_PORTS`; unset, a worker takes 20 ports from 6600. The `MACHINE_ONLY` settings in `conftest.py` are scrubbed from the environment. Every test declares its live spend with `@pytest.mark.spend(usd=...)`; live tests run only with `VALOR_LIVE=1`.

## Budget

No line count bounds the suite (CLAUDE.md rule 8). `test_bloat.py` runs vulture (configured in `pyproject.toml`) over production code alone and fails on anything nothing in production uses, so code kept alive only by a test fails too.

## Not here

**Governance is restrained by structure, not sentiment.** Before any check, gate, hook, validator, review round, approval step, isolation, sandbox rule, denial, allowlist, refusal, capability removal, or verification layer is added, the change names the mission item it serves and the incident that already happened without it; missing either, it is not added. A bug fix never adds a guard; it fixes the code. Adding governance needs Tom's grant, one per instance, written in the plan file that adds it. Every guard is ledgered with the incident it prevents, the mission item it serves, and a ninety-day expiry; a guard that has not fired by expiry is listed for Tom, and deleted unless he keeps it. The reviewer asks whether the diff adds governance; a yes with no grant in the plan file is a refused merge. The same paragraph, in the same words, sits at the top of `CLAUDE.md`, in the persona rendered into every turn, and in the Not-here section of every directory README. No restraint skill, no hook that blocks hooks, no governance dashboard: each is the disease presenting as the cure.
