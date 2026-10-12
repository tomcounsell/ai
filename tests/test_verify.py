"""The `verify` routine (`routines/verify/runner.py`): the project's suite at
the head of its branch, in the kernel's check sandbox, each red group
started as a fix task through `core/findings.py`.

The firings provision real workspaces from a toy repository under the
test's own work directory, on a database of the module's own (keys such as
`verify:suite` are global to a ledger)."""

import asyncio
import dataclasses
import os
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

from core import checks, db, findings, ledger, routines, slot, tasks, workspace
from core.__main__ import _routine_project
from core.settings import settings
from routines.verify import runner as verify
from tests import scripted
from tests.conftest import TEST_DB
from tests.scripted import commit, git
from tests.test_checks import PYTHON, REPO, _under_check_profile
from tests.test_objective_tree import run

DB = f"{TEST_DB}_verify"

# A toy suite runner that writes JUnit as pytest does: a failure's message
# attribute and traceback, and a module that does not load as one errored
# case with pytest's message "collection failure".
RUN_PY = r"""
import importlib.util, pathlib, sys, traceback
from xml.sax.saxutils import escape, quoteattr
out = sys.argv[1]
root = pathlib.Path.cwd()
cases, failed = [], False
for path in sorted((root / "tests").glob("test_*.py")):
    name = ".".join(path.relative_to(root).with_suffix("").parts)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:
        cases.append(("", name, "error", "collection failure", traceback.format_exc()))
        failed = True
        continue
    for attr in sorted(dir(mod)):
        fn = getattr(mod, attr)
        if not attr.startswith("test_") or not callable(fn):
            continue
        params = getattr(fn, "params", None)
        each = [(f"{attr}[{p}]", (p,)) for p in params] if params is not None else [(attr, ())]
        for tid, args in each:
            try:
                fn(*args)
                cases.append((name, tid, None, "", ""))
            except AssertionError as exc:
                cases.append((name, tid, "failure", str(exc), traceback.format_exc()))
                failed = True
            except Exception as exc:
                cases.append((name, tid, "error", repr(exc), traceback.format_exc()))
                failed = True
body = "".join(
    f"<testcase classname={quoteattr(c)} name={quoteattr(t)}>"
    + (f"<{k} message={quoteattr(m)}>{escape(x)}</{k}>" if k else "")
    + "</testcase>"
    for c, t, k, m, x in cases
)
pathlib.Path(out).write_text(f'<testsuite tests="{len(cases)}">{body}</testsuite>')
sys.exit(1 if failed else 0)
"""
SUITE = f"{PYTHON} -B run.py {{junit}}"
SLOW_SUITE = f"sleep 4; {SUITE}"
PARAMETRIZE = "def parametrize(*params):\n    def mark(fn):\n        fn.params = params\n        return fn\n\n    return mark\n\n\n"
GREEN = "def test_ok():\n    assert True\n\n\ndef test_red():\n    assert True\n"
RED = "def test_ok():\n    assert True\n\n\ndef test_red():\n    assert 1 == 2, 'the greeting is wrong'\n"
RED_KEY = "verify:tests.test_a::test_red"


@dataclasses.dataclass
class Toy:
    dsn: str
    src: Path
    work: Path
    name: str
    toml: Path

    def spec(self, **fields) -> None:
        """Rewrite the toy project's spec with these fields."""
        lines = ['name = "toy"', f'repo = "{self.src}"', 'kind = "plain"', f'suite = "{SUITE}"']
        lines = [ln for ln in lines if ln.split(" =")[0] not in fields]
        lines += [f"{k} = {v!r}".replace("'", '"') for k, v in fields.items()]
        (self.toml.parent.parent.parent / "toy.toml").write_text("\n".join(lines) + "\n")

    def tests(self, files: dict[str, str]) -> str:
        """Commit these files to the toy's `main`; returns the head."""
        sha = ""
        for path, text in files.items():
            sha = commit(self.src, path, text, f"set {path}")
        return sha


@pytest.fixture
def toy(tmp_path, monkeypatch) -> Toy:
    """A fresh ledger, a work directory, a toy repository holding the
    runner, a project spec for it, and a copy of the real verify toml
    naming that spec and `main`."""
    dsn = db.migrate(DB, fresh=True)
    work = tmp_path / "work"
    monkeypatch.setattr(workspace, "settings", dataclasses.replace(settings, work_dir=str(work)))
    src = scripted.toy_repo(tmp_path)
    commit(src, "run.py", RUN_PY, "the runner")
    commit(src, "tests/test_a.py", GREEN, "a test")
    where = tmp_path / "routines"
    monkeypatch.setattr(routines, "settings", dataclasses.replace(settings, routines_dir=str(where)))
    text = (REPO / "routines" / "verify" / "routine.toml").read_text()
    toml = where / "verify" / "routine.toml"
    toml.parent.mkdir(parents=True)
    t = Toy(dsn, src, work, "verify", toml)
    t.spec()
    toml.write_text(
        text.replace('project = "valor"', f'project = "{tmp_path / "toy.toml"}"').replace(
            'branch = "valor-cori-rebuild"', 'branch = "main"'
        )
    )
    return t


async def fire(toy: Toy) -> str:
    async with await db.connect(toy.dsn) as conn:
        return await routines.run(
            conn, toy.name, {"verify": verify.run}, start_project=_routine_project, dsn=toy.dsn
        )


async def objective(toy: Toy) -> str:
    async with await db.connect(toy.dsn) as conn:
        obj, _ = await routines.ensure(conn, routines.load(toy.name))
        return obj


async def ran(toy: Toy) -> list[dict]:
    async with await db.connect(toy.dsn) as conn:
        (reg,) = await routines.registrations(conn, toy.name)
        rows = await ledger.read(conn, reg["objective"])
        return [r["payload"] for r in rows if r["type"] == "routine.ran"]


async def run_nodes(toy: Toy) -> list[str]:
    """The objective's children that are runs (marker `objective`)."""
    async with await db.connect(toy.dsn) as conn:
        (reg,) = await routines.registrations(conn, toy.name)
        out = []
        for child in await tasks.children(conn, reg["objective"]):
            started = (await ledger.read(conn, child))[0]["payload"]
            if started.get(routines.OBJECTIVE) == toy.name:
                out.append(child)
        return out


async def fixes(toy: Toy, run_id: str) -> dict[str, tuple[list[str], str]]:
    """The fix tasks under a run: task id -> (keys, instruction)."""
    async with await db.connect(toy.dsn) as conn:
        out = {}
        for child in await tasks.children(conn, run_id):
            started = (await ledger.read(conn, child))[0]["payload"]
            out[child] = (started["finding"]["keys"], (await tasks.brief(conn, child)).instruction)
        return out


async def rows_of(toy: Toy, task_id: str, type_: str) -> list[dict]:
    async with await db.connect(toy.dsn) as conn:
        return [r["payload"] for r in await ledger.read(conn, task_id) if r["type"] == type_]


async def brief(toy: Toy, task_id: str) -> tasks.Brief:
    async with await db.connect(toy.dsn) as conn:
        return await tasks.brief(conn, task_id)


def kernel_state() -> tuple[str, str]:
    head = git(REPO, "rev-parse", "HEAD")
    status = subprocess.run(
        ["git", "-C", str(REPO), "status", "--porcelain"], capture_output=True, text=True, check=False
    )
    return head, status.stdout


# -- findings from a suite run, read cheaply --------------------------------------------------


def payload(passed=(), failed=(), errored=(), **over) -> dict:
    tests = {"passed": list(passed), "failed": list(failed), "errored": list(errored), "skipped": []}
    return {"commit": "a" * 40, "command": "suite", "exit": 1, "tests": tests, "cause": None,
            "why": None, "junit": None, "tail": "the tail", **over}  # fmt: skip


def failure(test_id: str, message: str, kind: str = "failed") -> dict:
    return {"id": test_id, "kind": kind, "message": message, "text": f"Traceback\n{message}"}


@pytest.mark.parametrize(
    ("failures", "groups"),
    [
        # a parametrized test failing in three cases, each its own message: one
        ([failure(f"tests.t::test_p[{i}]", f"{i} is bad") for i in range(3)], [["verify:tests.t::test_p"]]),
        # fifty tests failing with one message (a broken fixture): one
        ([failure(f"tests.t::test_{i}", "fixture broke\nmore") for i in range(50)], [None]),
        # two messages: two
        ([failure("tests.t::test_x", "x"), failure("tests.t::test_y", "y")],
         [["verify:tests.t::test_x"], ["verify:tests.t::test_y"]]),
        # a module that did not load: verify:suite, beside the other groups
        ([failure("tests.t_mod", "collection failure", "errored"), failure("tests.t::test_x", "x")],
         [[verify.SUITE_KEY], ["verify:tests.t::test_x"]]),
    ],
)  # fmt: skip
def test_failures_group_by_their_first_message_line_and_a_test_key(failures, groups):
    got = verify.grouped(payload(failed=[f["id"] for f in failures]), failures, "main")
    assert len(got) == len(groups)
    for found, keys in zip(got, groups, strict=True):
        if keys is not None:
            assert list(found.keys) == keys
    ids = {f["id"] for f in failures}
    named = "\n".join(f.evidence for f in got)
    assert all(i in named for i in ids)


@pytest.mark.parametrize(
    "unusable",
    [
        {"cause": "commit", "why": "setup failed", "tests": None},
        {"exit": 0, "tests": None, "junit": "no tmp/junit.xml"},
        {"exit": 0, "tests": {"passed": [], "failed": [], "errored": [], "skipped": []}},
    ],
)
def test_a_payload_with_no_usable_report_is_the_suite_finding_and_clears_nothing(unusable):
    p = payload(**unusable)
    (found,) = verify.grouped(p, [], "main")
    assert found.keys == (verify.SUITE_KEY,) and "the tail" in found.evidence
    assert verify.seen_gone(p, []) == set()


def test_a_usable_run_sees_passing_keys_and_the_suite_gone():
    p = payload(passed=["tests.t::test_p[1]", "tests.t::test_q"], failed=["tests.t::test_p[2]"])
    gone = verify.seen_gone(p, [failure("tests.t::test_p[2]", "bad")])
    assert gone == {"verify:tests.t::test_q", verify.SUITE_KEY}
    broken = [failure("tests.t_mod", verify.COLLECTION, "errored")]
    assert verify.SUITE_KEY not in verify.seen_gone(payload(passed=["tests.t::test_q"]), broken)


def test_a_resumed_run_that_cannot_read_the_junit_again_keeps_the_red_it_recorded():
    kept = payload(passed=["tests.t::test_q"], failed=["tests.t::test_p[1]"], errored=["tests.t::test_e"])
    failures = verify.failures_of(kept, [])
    (found,) = verify.grouped(kept, failures, "main")
    assert found.keys == ("verify:tests.t::test_e", "verify:tests.t::test_p")
    assert verify.UNREAD in found.text
    assert verify.seen_gone(kept, failures) == {"verify:tests.t::test_q", verify.SUITE_KEY}
    read = [failure("tests.t::test_p[1]", "bad")]
    assert verify.failures_of(kept, read) == read


# -- firings ------------------------------------------------------------------------------------


@pytest.mark.macos
def test_a_red_test_starts_one_task_and_the_next_firing_holds_it(toy):
    """The Done case, and the run never touches the kernel's checkout."""
    # pytest's basetemp follows TMPDIR, so it is inside the run's workspace.
    toy.spec(suite=f"echo tmpdir=$TMPDIR; {SUITE}")
    head = toy.tests({"tests/test_a.py": RED})
    before = kernel_state()
    first = run(fire(toy))
    assert "finished" in first, first
    (run1,) = run(run_nodes(toy))
    ((_fix, (keys, text)),) = run(fixes(toy, run1)).items()
    assert keys == [RED_KEY]
    assert head in text and "the greeting is wrong" in text
    b = run(brief(toy, run1))
    started = run(rows_of(toy, run1, "task.started"))[0]
    assert b.base_sha == head and started["kernel_tip"] == git(REPO, "rev-parse", "HEAD")
    assert Path(b.mirror).is_relative_to(toy.work) and not Path(b.mirror).parent.exists()
    (suite,) = run(rows_of(toy, run1, checks.SUITE))
    assert suite["commit"] == head and suite["tests"]["failed"] == ["tests.test_a::test_red"]
    tmp = Path(next(ln for ln in suite["tail"].splitlines() if ln.startswith("tmpdir="))[7:])
    assert tmp.is_relative_to(Path(b.mirror).parent)
    assert run(rows_of(toy, run1, "workspace.removed"))

    second = run(fire(toy))
    run2 = [n for n in run(run_nodes(toy)) if n != run1]
    assert len(run2) == 1 and not run(fixes(toy, run2[0]))
    last = run(ran(toy))[-1]
    assert last["run"] == run2[0] and "started 0, held 1" in last["summary"], second
    assert not Path(run(brief(toy, run2[0])).mirror).parent.exists()
    assert kernel_state() == before


@pytest.mark.macos
def test_reds_group_into_one_task_per_cause(toy):
    toy.tests(
        {
            "tests/test_b.py": "".join(f"def test_{i}():\n    assert False, 'fixture broke'\n\n\n" for i in range(50)),
            "tests/test_c.py": PARAMETRIZE + "@parametrize(1, 2, 3)\ndef test_p(x):\n    assert False, f'{x} is bad'\n",
            "tests/test_d.py": "def test_x():\n    assert False, 'x'\n\n\ndef test_y():\n    assert False, 'y'\n",
        }
    )  # fmt: skip
    assert "finished" in run(fire(toy))
    (run1,) = run(run_nodes(toy))
    got = sorted(keys for keys, _ in run(fixes(toy, run1)).values())
    assert len(got) == 4
    assert ["verify:tests.test_c::test_p"] in got
    assert ["verify:tests.test_d::test_x"] in got and ["verify:tests.test_d::test_y"] in got
    (fifty,) = [k for k in got if k[0].startswith("verify:tests.test_b")]
    assert len(fifty) == 50
    assert "55 red in 4 groups" in run(ran(toy))[-1]["summary"]


@pytest.mark.macos
def test_a_green_branch_clears_the_keys_an_ended_task_held(toy):
    toy.tests({"tests/test_a.py": RED, "tests/test_z.py": "import not_a_module\n"})
    assert "finished" in run(fire(toy))
    (run1,) = run(run_nodes(toy))
    started = run(fixes(toy, run1))
    assert sorted(k for keys, _ in started.values() for k in keys) == [verify.SUITE_KEY, RED_KEY]

    async def stop_all():
        async with await db.connect(toy.dsn) as conn:
            for task in started:
                await tasks.stop(conn, task, reason="not now")

    run(stop_all())
    git(toy.src, "rm", "-q", "tests/test_z.py")
    toy.tests({"tests/test_a.py": GREEN})
    assert "finished" in run(fire(toy))
    last = run(ran(toy))[-1]
    assert last["summary"].startswith("0 red in 0 groups")
    assert last["cleared"] == [verify.SUITE_KEY, RED_KEY]
    assert sum(len(run(fixes(toy, n))) for n in run(run_nodes(toy))) == 2


@pytest.mark.macos
@pytest.mark.parametrize(
    "spec",
    [
        {"setup": ["false"]},  # a setup line that fails
        {"suite": "true"},  # exits 0 and writes no JUnit
        {"suite": f"rm -rf tests && {SUITE}"},  # collects no tests
    ],
)
def test_no_usable_report_starts_the_suite_finding(toy, spec):
    toy.spec(**spec)
    assert "finished" in run(fire(toy))
    (run1,) = run(run_nodes(toy))
    ((keys, _),) = run(fixes(toy, run1)).values()
    assert keys == [verify.SUITE_KEY]
    assert "no usable report" in run(ran(toy))[-1]["summary"]


@pytest.mark.macos
def test_a_kernel_failure_fails_the_run_and_the_next_firing_runs_fresh(toy, monkeypatch):
    real = workspace.check_services

    class Broken:
        def __enter__(self):
            raise RuntimeError("the service cannot start")

    def broken(*a, **kw):
        return Broken()

    monkeypatch.setattr(workspace, "check_services", broken)
    toy.tests({"tests/test_a.py": RED})
    assert "failed" in run(fire(toy))
    (run1,) = run(run_nodes(toy))
    assert not run(rows_of(toy, run1, checks.SUITE)) and not run(fixes(toy, run1))
    assert run(rows_of(toy, run1, "workspace.removed"))
    assert "the service cannot start" in run(ran(toy))[-1]["summary"]

    monkeypatch.setattr(workspace, "check_services", real)
    assert "finished" in run(fire(toy))
    run2 = [n for n in run(run_nodes(toy)) if n != run1]
    assert len(run2) == 1 and run(rows_of(toy, run2[0], checks.SUITE))


@pytest.mark.macos
def test_a_hand_child_is_never_a_run_and_two_firings_at_once_make_one(toy):
    toy.spec(suite=SLOW_SUITE)

    async def go():
        obj = await objective(toy)
        async with await db.connect(toy.dsn) as conn:
            hand = await tasks.start_child(conn, obj, instruction="by hand", marker={}, routine=toy.name)
        return hand, await asyncio.gather(fire(toy), fire(toy))

    hand, said = run(go())
    nodes = run(run_nodes(toy))
    assert len(nodes) == 1 and hand not in nodes
    assert sorted(" running;" in s for s in said) == [False, True], said
    assert run(ran(toy))[0]["summary"] == "already running"


async def _in_suite(toy: Toy) -> str:
    """The run, once its check has checked out the commit."""
    await objective(toy)
    for _ in range(600):
        nodes = await run_nodes(toy)
        if nodes and list((toy.work / nodes[0]).glob("checks/test-verify-*/repo")):
            return nodes[0]
        await asyncio.sleep(0.05)
    raise AssertionError("the suite never started")


@pytest.mark.macos
def test_a_foreground_holder_preempts_the_run_and_the_next_firing_resumes_it(toy):
    toy.spec(suite=SLOW_SUITE)
    head = toy.tests({"tests/test_a.py": RED})

    async def go():
        firing = asyncio.create_task(fire(toy))
        run_id = await _in_suite(toy)
        async with await db.connect(toy.dsn) as conn:
            tom = await tasks.start(conn, tasks.Brief(instruction="Tom's"))
        async with slot.held(tom, toy.dsn):
            said = await asyncio.wait_for(firing, 60)
        return run_id, said

    run_id, said = run(go())
    assert " running;" in said and "preempted" in run(ran(toy))[-1]["summary"]
    assert not run(rows_of(toy, run_id, checks.SUITE))
    assert "finished" in run(fire(toy))
    assert run(run_nodes(toy)) == [run_id]
    (suite,) = run(rows_of(toy, run_id, checks.SUITE))
    assert suite["commit"] == head and len(run(fixes(toy, run_id))) == 1


@pytest.mark.macos
def test_a_stopped_run_fails_and_its_workspace_is_removed(toy):
    toy.spec(suite=SLOW_SUITE)

    async def go():
        firing = asyncio.create_task(fire(toy))
        run_id = await _in_suite(toy)
        async with await db.connect(toy.dsn) as conn:
            await tasks.stop(conn, run_id, reason="not now")
        return run_id, await asyncio.wait_for(firing, 60)

    run_id, said = run(go())
    assert "failed" in said and run(ran(toy))[-1]["summary"] == "stopped"
    assert run(rows_of(toy, run_id, "workspace.removed")) and not (toy.work / run_id).exists()


@pytest.mark.macos
def test_a_run_stopped_while_it_waits_for_the_slot_runs_no_suite(toy):
    toy.tests({"tests/test_a.py": RED})

    async def go():
        async with await db.connect(toy.dsn) as conn:
            # Another background holder (a sweep), so no foreground notice preempts the run.
            sweep = await tasks.start(conn, tasks.Brief(instruction="a sweep", routine="emulator"))
        taken, release = asyncio.Event(), asyncio.Event()

        async def hold():  # its own task, so the firing does not inherit the hold
            async with slot.held(sweep, toy.dsn):
                taken.set()
                await release.wait()

        holder = asyncio.create_task(hold())
        await taken.wait()
        firing = asyncio.create_task(fire(toy))
        await objective(toy)
        for _ in range(600):
            nodes = await run_nodes(toy)
            if nodes and (await brief(toy, nodes[0])).mirror:
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(1)  # the run is waiting for the slot
        async with await db.connect(toy.dsn) as conn:
            await tasks.stop(conn, nodes[0], reason="not now")
        release.set()
        await holder
        return nodes[0], await asyncio.wait_for(firing, 60)

    run_id, said = run(go())
    assert "failed" in said and run(ran(toy))[-1]["summary"] == "stopped"
    assert not run(rows_of(toy, run_id, checks.SUITE)) and not run(fixes(toy, run_id))
    assert run(rows_of(toy, run_id, "workspace.removed")) and not (toy.work / run_id).exists()


@pytest.mark.macos
@pytest.mark.skipif(not Path("/opt/homebrew/bin/pi").exists(), reason="needs Pi at /opt/homebrew/bin/pi")
def test_verify_does_not_see_the_pi_red_since_the_check_sandbox_denies_ps(tmp_path):
    """`tests/test_pi.py` as it stood at `f44d024d9` compared the installed
    Pi's version with a pinned one and was red on a Mac with a newer Pi.
    Under the check profile `/bin/ps` is denied before that comparison, and
    the conftest reads a denial as a skip, so verify does not see it."""
    with _under_check_profile(tmp_path) as call:
        tree = tmp_path / "work" / "t" / "checks" / "test-head-collect" / "repo"
        (tree / "tests").mkdir(parents=True)
        for entry in REPO.iterdir():
            if entry.name not in ("tests", ".git"):
                (tree / entry.name).symlink_to(entry)
        for entry in (REPO / "tests").iterdir():
            if entry.name != "test_pi.py":
                (tree / "tests" / entry.name).symlink_to(entry)
        (tree / "tests" / "test_pi.py").write_text(git(REPO, "show", "f44d024d9:tests/test_pi.py") + "\n")
        out = call("-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider", str(tree / "tests" / "test_pi.py"),
                   "-k", "every_loader_off")  # fmt: skip
    assert "1 skipped" in out.stdout and "/bin/ps" in out.stdout, out.stdout[-3000:] + out.stderr[-2000:]


# -- the command line and the real tomls ------------------------------------------------------


def cli(toy: Toy, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "core", "routine", toy.name, *args], cwd=REPO, capture_output=True, text=True,
        check=False, env={**os.environ, "VALOR_DB": DB, "VALOR_WORK": str(toy.work),
                          "VALOR_ROUTINES": str(toy.toml.parent.parent)},
    )  # fmt: skip


@pytest.mark.macos
def test_a_finding_started_by_hand_is_held_and_refused_on_a_stopped_routine(toy, tmp_path):
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("`env` and `cd` run without the prefix" + "x" * findings.MESSAGE)
    args = (
        "--finding",
        "C14: the command prefix",
        "--key",
        "backlog:c14-prefix",
        "--evidence",
        str(evidence),
    )
    first = cli(toy, *args)
    assert first.returncode == 0, first.stderr
    said, task = first.stdout.strip().splitlines()
    assert "started" in said
    second = cli(toy, *args)
    assert second.returncode == 0 and second.stdout.strip().splitlines()[1] == task
    assert "nothing_due" in second.stdout

    async def look():
        async with await db.connect(toy.dsn) as conn:
            (reg,) = await routines.registrations(conn, toy.name)
            b = await tasks.brief(conn, task)
            assert task in await tasks.children(conn, reg["objective"])
            await tasks.stop(conn, reg["objective"], reason="not now")
            return b

    b = run(look())
    assert "`env` and `cd`" in b.instruction and "[cut]" in b.instruction and b.routine == toy.name
    assert [(r["run"], r["outcome"]) for r in run(ran(toy))] == [(None, "started"), (None, "nothing_due")]
    third = cli(toy, *args)
    assert third.returncode != 0 and "is stopped" in third.stderr
    assert findings.key_of("backlog:c14-prefix") == "backlog:c14-prefix"


@pytest.mark.parametrize(
    ("args", "why"),
    [
        (("--key", "backlog:x"), "go with --finding"),
        (("--finding", " ", "--key", "backlog:x"), "--finding is empty"),
        (("--finding", "it is red", "--key", ""), "a --key is empty"),
        (("--finding", "it is red", "--key", "backlog:x", "--evidence", "/nonexistent/e.txt"), "--evidence"),
        (("--finding", "it is red", "--key", "backlog:x", "--evidence", "."), "--evidence"),
    ],
)
def test_a_hand_start_refuses_a_bare_key_an_empty_field_and_unreadable_evidence(toy, args, why):
    got = cli(toy, *args)
    assert got.returncode != 0 and got.stderr.startswith("routine refused:") and why in got.stderr
    assert "Traceback" not in got.stderr and got.stdout == ""


@pytest.mark.parametrize("name", ["verify", "emulator"])
def test_the_real_tomls_name_the_valor_spec_and_print_a_plist(name):
    r = routines.load(name, REPO / "routines")
    assert r.project == "valor" and r.branch == "valor-cori-rebuild"
    assert workspace.Spec.load(r.project).name == "valor"
    assert plistlib.loads(routines.plist(r))["Label"].endswith(name)
