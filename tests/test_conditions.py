"""The conditions routine on real Postgres and real git: each business's
committed decisions are read from that business's own repository, numeric
conditions through a source adapter, observation conditions through an
observer, every check a row on the run, and a met condition put once on the
next strategy meeting's agenda with the decision's original reasoning, the
condition, and what changed. No message to Tom, no model call.

The source and observer here are fakes for tests only: no business data
source is granted yet (docs/routines.md, Watched conditions)."""

import base64
import dataclasses
import json
import secrets
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest

from core import agenda, conditions, credentials, ledger, routines, workspace
from core.settings import settings
from routines.conditions import runner as conditions_runner
from tests.smart_http import Server
from tests.test_objective_tree import run
from tests.test_routines import unique

pytestmark = pytest.mark.spend(usd=0)

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2027, 3, 1, 12, 0, tzinfo=UTC)

PRICING = """
[[decision]]
id = "pro-price-49"
decided = 2026-09-14
reasoning = "decisions/2026-09-14-pro-price.md"

[[decision.condition]]
id = "churn"
text = "Pro monthly churn rises above 6%"
source = "billing"
metric = "pro_monthly_churn_pct"
above = 6.0

[[decision.condition]]
id = "competitor"
text = "A direct competitor prices a comparable plan under $30"
source = "web"
"""

REASONING = "We raise Pro to $49: churn is 3% and the plan is underpriced against peers.\n"


class FakeSource:
    """A business's figures, by metric, for tests only."""

    def __init__(self, values: dict[str, float]):
        self.values = values
        self.asked: list[tuple[str, str]] = []

    async def read(self, business, metric, at):
        self.asked.append((business, metric))
        return conditions.Reading(self.values[metric], f"fake billing account of {business}", at)


class FakeObserver:
    """What Valor saw, for tests only."""

    def __init__(self, saw: str, met: bool):
        self.saw, self.met = saw, met

    async def observe(self, business, condition, at):
        return conditions.Observation(self.saw, "https://competitor.example/pricing", at, self.met)


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def business_repo(tmp_path: Path, files: dict[str, str], *, branch: str = "main") -> Path:
    repo = tmp_path / f"biz-{unique()}"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("a business\n")
    git(repo, "add", "README.md")
    git(repo, "commit", "-q", "-m", "start")
    if branch != "main":
        git(repo, "checkout", "-q", "-b", branch)
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
        git(repo, "add", rel)
    git(repo, "commit", "-q", "-m", "decide")
    git(repo, "checkout", "-q", "main")
    return repo


def spec_for(repo: Path, name: str | None = None) -> workspace.Spec:
    return workspace.Spec(name=name or unique(), repo=str(repo), kind="plain", suite="true", branch="main")


@pytest.fixture
def where(tmp_path, monkeypatch) -> Path:
    """The kernel's own conditions routine, in a routines directory of the test's."""
    routines_dir = tmp_path / "routines"
    (routines_dir / "conditions").mkdir(parents=True)
    (routines_dir / "conditions" / "routine.toml").write_bytes(
        (ROOT / "routines" / "conditions" / "routine.toml").read_bytes()
    )
    monkeypatch.setattr(routines, "settings", dataclasses.replace(settings, routines_dir=str(routines_dir)))
    return routines_dir


def runners(adapters, specs):
    return {"conditions": conditions_runner.runner(adapters, projects=lambda: specs)}


async def run_rows(conn, run_id: str) -> list[dict]:
    return [r for r in await ledger.read(conn, run_id) if r["type"].startswith("condition.")]


async def last_ran(conn) -> dict:
    reg = (await routines.registrations(conn, "conditions"))[-1]
    return (await routines.runs(conn, [reg["objective"]]))[-1]


def test_the_kernels_routine_holds_no_business_condition_and_its_ceiling_is_propose():
    r = routines.load("conditions", ROOT / "routines")
    assert (r.runner, r.ceiling) == ("conditions", "propose")
    raw = (ROOT / "routines" / "conditions" / "routine.toml").read_text()
    assert "[[decision" not in raw and "metric" not in raw


def test_a_met_numeric_condition_goes_on_the_agenda_once_with_its_reasoning_and_what_changed(
    dsn, tmp_path, where
):
    repo = business_repo(
        tmp_path,
        {conditions.PATH: PRICING, "decisions/2026-09-14-pro-price.md": REASONING},
    )
    spec = spec_for(repo)
    source = FakeSource({"pro_monthly_churn_pct": 7.5})
    adapters = {"billing": source, "web": FakeObserver("cheapest comparable plan is $39", False)}

    async def go():
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            before = (await (await conn.execute("SELECT coalesce(max(id), 0) FROM events")).fetchone())[0]
            said = await routines.run(conn, "conditions", runners(adapters, [spec]), now=NOW)
            assert "finished" in said
            first = await last_ran(conn)
            assert first["summary"].startswith("2 conditions checked, 1 met, 1 put on the agenda")
            rows = await run_rows(conn, first["run"])
            read = next(r["payload"] for r in rows if r["type"] == "condition.read")
            assert (read["value"], read["above"], read["met"]) == (7.5, 6.0, True)
            assert read["where"] == f"fake billing account of {spec.name}"
            seen = next(r["payload"] for r in rows if r["type"] == "condition.observed")
            assert (seen["saw"], seen["met"]) == ("cheapest comparable plan is $39", False)
            assert seen["where"] == "https://competitor.example/pricing" and seen["at"] == NOW.isoformat()

            items = await agenda.open_items(conn, spec.name)
            assert len(items) == 1
            item = items[0]
            assert item["decision"] == "pro-price-49"
            assert item["reasoning"] == "decisions/2026-09-14-pro-price.md"
            assert item["reasoning_text"] == REASONING
            assert item["condition"]["text"] == "Pro monthly churn rises above 6%"
            assert item["changed"] == {
                "value": 7.5,
                "where": f"fake billing account of {spec.name}",
                "at": NOW.isoformat(),
            }
            assert item["commit"] == git(repo, "rev-parse", "main")
            assert read["agenda_item"] == item["id"]

            # The next firing reads the condition again and does not raise it twice.
            await routines.run(conn, "conditions", runners(adapters, [spec]), now=NOW + timedelta(days=1))
            second = await last_ran(conn)
            assert second["run"] != first["run"]
            assert "0 put on the agenda" in second["summary"]
            again = next(
                r["payload"] for r in await run_rows(conn, second["run"]) if r["type"] == "condition.read"
            )
            assert again["agenda_item"] == item["id"]
            assert len(await agenda.open_items(conn, spec.name)) == 1

            # Tom gets no message: every row the runs wrote is on the run, the
            # objective, the routines stream, or the agenda.
            reg = (await routines.registrations(conn, "conditions"))[-1]
            written = await (
                await conn.execute("SELECT task_id, type FROM events WHERE id > %s", (before,))
            ).fetchall()
            assert {t for t, _ in written} == {
                reg["objective"],
                first["run"],
                second["run"],
                routines.STREAM,
                agenda.STREAM,
            }
            assert not [
                k for _, k in written if k.startswith(("notice.", "outbox.", "message.", "question."))
            ]

    run(go())


def test_a_met_observation_is_recorded_and_raised_and_an_unmet_number_is_not(dsn, tmp_path, where):
    repo = business_repo(tmp_path, {conditions.PATH: PRICING})
    spec = spec_for(repo)
    adapters = {
        "billing": FakeSource({"pro_monthly_churn_pct": 4.0}),
        "web": FakeObserver("Rival launched a comparable plan at $25", True),
    }

    async def go():
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            await routines.run(conn, "conditions", runners(adapters, [spec]), now=NOW)
            ran = await last_ran(conn)
            rows = {r["payload"]["condition"]: r for r in await run_rows(conn, ran["run"])}
            assert rows["churn"]["type"] == "condition.read" and rows["churn"]["payload"]["met"] is False
            assert "agenda_item" not in rows["churn"]["payload"]
            assert rows["competitor"]["type"] == "condition.observed"
            items = await agenda.open_items(conn, spec.name)
            assert [i["condition"]["id"] for i in items] == ["competitor"]
            assert items[0]["changed"]["saw"] == "Rival launched a comparable plan at $25"
            assert items[0]["changed"]["where"] == "https://competitor.example/pricing"
            assert items[0]["reasoning_text"] is None  # the reference is not a file at the commit

    run(go())


def test_a_condition_no_adapter_reaches_is_recorded_unread_not_dropped(dsn, tmp_path, where):
    repo = business_repo(tmp_path, {conditions.PATH: PRICING})
    spec = spec_for(repo)

    async def go():
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            await routines.run(conn, "conditions", runners({}, [spec]), now=NOW)
            ran = await last_ran(conn)
            assert ran["outcome"] == "finished"
            rows = await run_rows(conn, ran["run"])
            assert [(r["type"], r["payload"]["condition"]) for r in rows] == [
                ("condition.unread", "churn"),
                ("condition.unread", "competitor"),
            ]
            assert rows[0]["payload"]["why"] == "no access to billing is granted"
            assert await agenda.open_items(conn, spec.name) == []

    run(go())


def test_conditions_are_read_from_the_committed_branch_and_a_malformed_file_does_not_stop_the_rest(
    dsn, tmp_path, where
):
    uncommitted = spec_for(business_repo(tmp_path, {conditions.PATH: PRICING}, branch="proposal"))
    broken = spec_for(business_repo(tmp_path, {conditions.PATH: "[[decision]]\nid = 'x'\n"}))
    good = spec_for(business_repo(tmp_path, {conditions.PATH: PRICING}))
    none = spec_for(business_repo(tmp_path, {"notes.md": "no decisions yet\n"}))
    source = FakeSource({"pro_monthly_churn_pct": 9.0})

    async def go():
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            await routines.run(
                conn, "conditions", runners({"billing": source}, [uncommitted, broken, good, none]), now=NOW
            )
            ran = await last_ran(conn)
            assert f"not read, {broken.name}: decision x: decided is a date" in ran["summary"]
            assert source.asked == [(good.name, "pro_monthly_churn_pct")]
            assert len(await agenda.open_items(conn, good.name)) == 1
            assert await agenda.open_items(conn, uncommitted.name) == []

            await routines.run(conn, "conditions", runners({"billing": source}, [uncommitted, none]), now=NOW)
            nothing = await last_ran(conn)
            assert (nothing["run"], nothing["outcome"]) == (None, "nothing_due")

    run(go())


def test_an_item_raised_at_a_meeting_leaves_the_agenda_and_counts_as_the_routines_use(dsn, tmp_path, where):
    spec = spec_for(business_repo(tmp_path, {conditions.PATH: PRICING}))

    async def go():
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            await routines.run(
                conn,
                "conditions",
                runners({"billing": FakeSource({"pro_monthly_churn_pct": 8.0})}, [spec]),
                now=NOW,
            )
            reg = (await routines.registrations(conn, "conditions"))[-1]
            assert await routines._used(conn, [reg["objective"]], datetime.now(UTC) - timedelta(days=1))
            items = await agenda.open_items(conn)
            await agenda.raised(conn, [i["id"] for i in items], meeting="strategy meeting of 2027-03-02")
            assert await agenda.open_items(conn) == []

    run(go())


DECISION = "[[decision]]\nid = 'd'\ndecided = 2026-01-01\nreasoning = 'r.md'\n[[decision.condition]]\n"


@pytest.mark.parametrize(
    "body, error",
    [
        ("[[decision]]\nid = 'd'\ndecided = 2026-01-01\n", "reasoning is text"),
        (
            DECISION + "id = 'c'\ntext = 't'\nsource = 's'\nmetric = 'm'\n",
            "a metric takes one of above, below",
        ),
        (DECISION + "id = 'c'\ntext = 't'\nsource = 's'\nbelow = 3\n", "a threshold needs a metric"),
        (DECISION + "id = 'c'\ntext = 't'\n", "source is text"),
        (
            "[[decision]]\nid = 'd'\ndecided = 2026-01-01\nreasoning = 'r.md'\n[decision.condition]\nid = 'c'\n",
            "is an array of tables",
        ),
        ("decision = ['d']\n", "decision 0 is a table"),
    ],
)
def test_a_conditions_file_missing_a_term_is_malformed(body, error):
    with pytest.raises(conditions.Malformed, match=error):
        conditions.parse(body)


def test_a_below_threshold_is_met_under_it_and_a_changed_condition_is_a_new_key():
    [d] = conditions.parse(
        DECISION
        + "id = 'c'\ntext = 'signups fall under 100 a week'\nsource = 'analytics'\n"
        + "metric = 'weekly_signups'\nbelow = 100\n"
    )
    [c] = d.conditions
    assert c.met_by(99) and not c.met_by(100)
    moved = dataclasses.replace(c, below=80.0)
    assert conditions.key("b", "d", c) != conditions.key("b", "d", moved)


# -- a private business repository, read as Valor's GitHub account ------------------------
#
# A loopback smart-HTTP server (`tests/smart_http.py`) stands in for a private
# GitHub repository: every request without the expected header gets 401. The
# token is made up per test; github.com is never reached.


def private_remote(server: Server, tmp_path: Path, files: dict[str, str]) -> str:
    name = f"biz-{unique()}.git"
    bare = server.bare(name)
    git(business_repo(tmp_path, files), "push", "-q", str(bare), "main")
    return server.url(name)


def github_keys(tmp_path: Path, token: str) -> Path:
    keys = tmp_path / "keys"
    keys.mkdir(mode=0o700, exist_ok=True)
    path = keys / "github-keys"
    path.write_text(f"{credentials.GITHUB_KEY}={token}\n")
    path.chmod(0o600)
    return path


def keyed_runners(adapters, specs, tmp_path: Path, credential):
    return {
        "conditions": conditions_runner.runner(
            adapters,
            projects=lambda: specs,
            work=lambda: tmp_path / "work",
            credential=credential,
            loopback=True,
        )
    }


def secret_forms(token: str) -> list[str]:
    return [token, base64.b64encode(f"x-access-token:{token}".encode()).decode()]


def remote_spec(url: str) -> workspace.Spec:
    return workspace.Spec(name=unique(), repo=url, kind="plain", suite="true", branch="main")


def test_a_private_business_repository_is_read_as_valors_github_account_and_the_fetch_is_recorded(
    dsn, tmp_path, where
):
    token = "ghp_test" + secrets.token_hex(16)
    keyfile = github_keys(tmp_path, token)
    source = FakeSource({"pro_monthly_churn_pct": 7.5})
    files = {conditions.PATH: PRICING, "decisions/2026-09-14-pro-price.md": REASONING}
    with Server(tmp_path / "remote", token=token, auth_reads=True) as server:
        url = private_remote(server, tmp_path, files)
        spec = remote_spec(url)

        async def go():
            async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
                given = keyed_runners({"billing": source}, [spec], tmp_path, keyfile)
                await routines.run(conn, "conditions", given, now=NOW)
                ran = await last_ran(conn)
                return ran, await ledger.read(conn, ran["run"]), await agenda.open_items(conn, spec.name)

        ran, rows, items = run(go())
        log = list(server.log)
    assert ran["outcome"] == "finished" and "not read" not in ran["summary"]
    assert source.asked == [(spec.name, "pro_monthly_churn_pct")]
    assert len(items) == 1 and items[0]["reasoning_text"] == REASONING
    [fetched] = [r["payload"] for r in rows if r["type"] == "repository.fetched"]
    assert fetched == {
        "business": spec.name,
        "repo": url,
        "commit": items[0]["commit"],
        "credential": credentials.GITHUB_KEY,
    }
    # Every request carried Valor's header; the token is nowhere in the
    # ledger or the summary, and no header file is left on disk.
    assert log and all(r["headers"].get("authorization") == server.expected for r in log)
    written = json.dumps([r["payload"] for r in rows]) + ran["summary"]
    assert not any(form in written for form in secret_forms(token))
    assert list(keyfile.parent.glob("github-push-*")) == []


def test_without_the_credential_or_with_a_refused_one_a_private_repository_is_named_not_read(
    dsn, tmp_path, where
):
    token = "ghp_test" + secrets.token_hex(16)
    wrong_token = "ghp_wrong" + secrets.token_hex(16)
    wrong = github_keys(tmp_path, wrong_token)
    with Server(tmp_path / "remote", token=token, auth_reads=True) as server:
        spec = remote_spec(private_remote(server, tmp_path, {conditions.PATH: PRICING}))

        async def go():
            async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
                # Anonymous, as provisioning fetches: refused, and no header is sent.
                await routines.run(conn, "conditions", keyed_runners({}, [spec], tmp_path, None), now=NOW)
                anonymous = await last_ran(conn)
                sent_anonymously = list(server.authorized())
                await routines.run(conn, "conditions", keyed_runners({}, [spec], tmp_path, wrong), now=NOW)
                return anonymous, sent_anonymously, await last_ran(conn)

        anonymous, sent_anonymously, refused = run(go())
        authorized = server.authorized()
    assert (anonymous["run"], anonymous["outcome"]) == (None, "nothing_due")
    assert "private repositories are not fetched" in anonymous["summary"]
    assert sent_anonymously == []
    assert (refused["run"], refused["outcome"]) == (None, "nothing_due")
    assert f"{spec.repo} refused Valor's GitHub credential" in refused["summary"]
    assert authorized and all(r["headers"]["authorization"] != server.expected for r in authorized)
    assert not any(form in refused["summary"] for form in secret_forms(wrong_token))


def test_valors_github_key_goes_to_github_only_and_a_failed_head_lookup_is_named_not_raised(
    tmp_path, monkeypatch
):
    """A spec naming another host is fetched anonymously; a lookup of the
    remote's HEAD that fails after the fetch refuses that one repository."""
    keyfile = github_keys(tmp_path, "ghp_test" + secrets.token_hex(16))
    real = workspace.git.trusted

    def trusted(cwd, *args, **kw):
        if args[0] == "ls-remote":
            raise workspace.git.GitError("ls-remote: the connection dropped")
        return "" if args[0] == "fetch" else real(cwd, *args, **kw)

    def header_file(*a, **kw):
        raise AssertionError("the key was offered to a host other than github.com")

    monkeypatch.setattr(workspace.git, "trusted", trusted)
    monkeypatch.setattr(workspace.credentials, "header_file", header_file)
    spec = remote_spec("https://gitlab.example/acme/business")
    with pytest.raises(workspace.Refused, match="the connection dropped"):
        workspace._cache(spec, None, tmp_path / "work", credential=keyfile)


def test_a_local_repository_carries_no_credential_and_a_firing_with_no_run_records_its_fetch_on_the_objective(
    dsn, tmp_path, where
):
    keyfile = github_keys(tmp_path, "ghp_test" + secrets.token_hex(16))
    repo = business_repo(tmp_path, {"notes.md": "no decisions yet\n"})
    spec = spec_for(repo)

    async def go():
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            await routines.run(conn, "conditions", keyed_runners({}, [spec], tmp_path, keyfile), now=NOW)
            ran = await last_ran(conn)
            reg = (await routines.registrations(conn, "conditions"))[-1]
            rows = await ledger.read(conn, reg["objective"])
            return ran, [r["payload"] for r in rows if r["type"] == "repository.fetched"]

    ran, fetched = run(go())
    assert (ran["run"], ran["outcome"]) == (None, "nothing_due")
    assert fetched[-1] == {
        "business": spec.name,
        "repo": str(repo),
        "commit": git(repo, "rev-parse", "main"),
        "credential": None,
    }
