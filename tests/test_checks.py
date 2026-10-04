"""The test runner (`core/checks.py`) on real git, a real Postgres ledger,
real check services, and the real `sandbox-exec`.

The toy project's suite is `run.py`, a small runner in the toy repository
that imports `tests/test_*.py` under the Command Line Tools' Python and
writes a JUnit report, so no test needs pytest inside the sandbox. A test
function with a `params` attribute (set by the toy `@parametrize`) runs
once per parameter (`name[p]`), and one with `skip = True` is reported
skipped. A `conftest.py` at the top is imported first.

Live spend: none.
"""

import asyncio
import dataclasses
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from core import backup, checks, db, judgement_sites, judgement_tasks, ledger, machine, router, runs, tasks
from core import workspace as kws
from core.gateway import Gateway
from core.machine import Check
from core.settings import settings
from tests import judgement_upstream, scripted
from tests.ports import listen

pytestmark = [pytest.mark.spend(usd=0)]

UP = judgement_upstream.shared()
PYTHON = "/Library/Developer/CommandLineTools/usr/bin/python3"
RUN_PY = r"""
import importlib.util, pathlib, sys
from xml.sax.saxutils import quoteattr
out = sys.argv[1]
root = pathlib.Path.cwd()
sys.path.insert(0, str(root))
if (root / "conftest.py").exists():
    spec = importlib.util.spec_from_file_location("conftest", root / "conftest.py")
    spec.loader.exec_module(importlib.util.module_from_spec(spec))
cases, failed = [], False
for path in sorted((root / "tests").glob("test_*.py")):
    name = ".".join(path.relative_to(root).with_suffix("").parts)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for attr in sorted(dir(mod)):
        fn = getattr(mod, attr)
        if not attr.startswith("test_") or not callable(fn):
            continue
        params = getattr(fn, "params", None)
        each = [(f"{attr}[{p}]", (p,)) for p in params] if params is not None else [(attr, ())]
        for tid, args in each:
            if getattr(fn, "skip", False):
                cases.append((name, tid, "skipped"))
                continue
            try:
                fn(*args)
                cases.append((name, tid, None))
            except AssertionError:
                cases.append((name, tid, "failure"))
                failed = True
            except Exception:
                cases.append((name, tid, "error"))
                failed = True
body = "".join(
    f"<testcase classname={quoteattr(c)} name={quoteattr(t)}>" + (f"<{k}/>" if k else "") + "</testcase>"
    for c, t, k in cases
)
pathlib.Path(out).write_text(f'<testsuite tests="{len(cases)}">{body}</testsuite>')
sys.exit(1 if failed else 0)
"""
SUITE = f"{PYTHON} -B run.py {{junit}}"
# The same suite where the Command Line Tools are absent: in a verification
# VM, the base image's `python3`.
VM_SUITE = f'p={PYTHON}; [ -x "$p" ] || p=python3; "$p" -B run.py {{junit}}'
BASE_TESTS = {
    ".gitignore": "setup-*\n",  # what setup writes in the builder's clone stays uncommitted
    "run.py": RUN_PY,
    "tests/test_a.py": (
        "def test_one():\n    assert True\n\n\n"
        "def test_two():\n    assert True\n\n\n"
        "def parametrize(*params):\n    def mark(fn):\n        fn.params = params\n        return fn\n\n    return mark\n\n\n"
        "@parametrize(1, 2, 3)\ndef test_p(x):\n    assert x\n"
    ),
    "tests/test_b.py": "def test_kept():\n    assert True\n",
}


def run(coro):
    return asyncio.run(coro)


async def rows(dsn, task, kind=None) -> list[dict]:
    async with await db.connect(dsn) as conn:
        got = await ledger.read(conn, task)
    return [r for r in got if kind is None or r["type"] == kind]


async def drive(dsn, task, runners) -> dict:
    gateway = Gateway(dsn)
    await gateway.start(port=listen())
    try:
        return await router.run(gateway, task, runners, dsn=dsn)
    finally:
        await gateway.close()


def change(ws: Path, writes: dict[str, str] | None = None, removes=()) -> None:
    """One builder commit in its clone: files written and removed."""
    for path, text in (writes or {}).items():
        p = ws / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        scripted.git(ws, "add", "--force", path)
    for path in removes:
        scripted.git(ws, "rm", "-q", path)
    scripted.git(ws, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "build")


def runners(ws: Path, breadth=None) -> dict:
    return {**scripted.fresh_runners(ws), Check.TEST: checks.test_runner(breadth or UP.port(fixed="false"))}


async def to_candidate(dsn, tmp_path, *, writes=None, removes=(), files=None, services=(), **spec):
    """A provisioned task whose base holds `files` (default the toy suite)
    and whose candidate makes `writes` and `removes`, left in critique."""
    task, b = await scripted.provisioned(
        dsn, tmp_path, services=services, files=BASE_TESTS if files is None else files,
        suite=spec.pop("suite", SUITE), **spec,
    )  # fmt: skip
    ws = Path(b.workspace)
    scripted.steer(ws, critique="sound", build="reasons")
    out = await drive(dsn, task, scripted.RUNNERS)
    assert out["missing"] == ["critique"], out
    change(ws, writes, removes)
    return task, b, ws


def through_test(dsn, tmp_path, *, breadth=None, **kw):
    """The candidate driven through critique, build, and the test runner."""

    async def go():
        task, b, ws = await to_candidate(dsn, tmp_path, **kw)
        out = await drive(dsn, task, runners(ws, breadth))
        return task, b, out, await rows(dsn, task)

    return run(go())


def decided(got) -> dict:
    return next(r["payload"] for r in got if r["type"] == "test.decided")


# -- JUnit ----------------------------------------------------------------------------------


GOOD = b'<testsuite><testcase classname="tests.test_a" name="test_one"/></testsuite>'


def _report(checks_dir, plant: str) -> str:
    """A check directory `test-head-x` under `checks_dir` with `plant` as its report."""
    name = "test-head-x"
    (checks_dir / name / "tmp").mkdir(parents=True)
    report = checks_dir / name / checks.JUNIT
    if plant == "symlink":
        (checks_dir / "real.xml").write_bytes(GOOD)
        report.symlink_to(checks_dir / "real.xml")
    elif plant == "fifo":
        os.mkfifo(report)  # no writer: a blocking open would hang
    elif plant == "linked dir":
        elsewhere = checks_dir / "elsewhere"
        (elsewhere / "tmp").mkdir(parents=True)
        (elsewhere / checks.JUNIT).write_bytes(GOOD)
        shutil.rmtree(checks_dir / name)
        (checks_dir / name).symlink_to(elsewhere)
    elif plant == "utf-16 doctype":
        report.write_bytes(
            '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE x [<!ENTITY a "b">]><testsuite/>'.encode(
                "utf-16"
            )
        )
    else:
        report.write_text(plant)
    return name


@pytest.mark.parametrize(
    ("plant", "why"),
    [
        ("not xml <<<", "not XML"),
        ('<!DOCTYPE x [<!ENTITY a "b">]><testsuite/>', "DOCTYPE"),
        ("utf-16 doctype", "DOCTYPE"),  # a byte search would miss it
        ("symlink", "not a plain file"),
        ("fifo", "not a regular file"),
        ("linked dir", "not a plain directory"),
    ],
)
def test_a_bad_junit_report_is_no_per_test_result(tmp_path, plant, why):
    name = _report(tmp_path, plant)
    started = time.monotonic()
    tests, got = checks.read_junit(tmp_path, name)
    assert tests is None and why in got and time.monotonic() - started < 2


def test_a_junit_report_gives_ids_by_outcome_whatever_its_size(tmp_path):
    many = "".join(f'<testcase classname="t.m" name="p{i}"/>' for i in range(20000))
    name = _report(
        tmp_path,
        '<testsuites><testsuite><testcase classname="t.m" name="a"/>'
        '<testcase classname="t.m" name="b"><failure/></testcase>'
        '<testcase classname="t.m" name="c"><error/></testcase>'
        f'<testcase classname="t.m" name="d[1]"><skipped/></testcase>{many}</testsuite></testsuites>',
    )
    tests, why = checks.read_junit(tmp_path, name)
    assert why is None
    assert tests["passed"][0] == "t.m::a" and len(tests["passed"]) == 20001
    assert (tests["failed"], tests["errored"], tests["skipped"]) == (["t.m::b"], ["t.m::c"], ["t.m::d[1]"])


def test_a_utf_16_junit_report_is_read(tmp_path):
    name = _report(tmp_path, "x")
    (tmp_path / name / checks.JUNIT).write_bytes(GOOD.decode().encode("utf-16"))
    assert checks.read_junit(tmp_path, name) == (
        {"passed": ["tests.test_a::test_one"], "failed": [], "errored": [], "skipped": []},
        None,
    )


@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be", "no-such-codec", "shift_jis"])
def test_a_junit_report_in_an_encoding_expat_cannot_read_is_not_xml(tmp_path, encoding):
    name = _report(tmp_path, "x")
    text = f'<?xml version="1.0" encoding="{encoding}"?>' + GOOD.decode()
    (tmp_path / name / checks.JUNIT).write_bytes(
        text.encode("utf-8" if encoding == "no-such-codec" else encoding)
    )
    tests, why = checks.read_junit(tmp_path, name)
    assert tests is None and "not XML" in why


# -- compare ------------------------------------------------------------------------------------


def _tests(passed=(), failed=(), errored=(), skipped=()):
    return {
        "passed": list(passed),
        "failed": list(failed),
        "errored": list(errored),
        "skipped": list(skipped),
    }


def _run(exit_=0, tests=None, cause=None, why=None):
    return {"exit": exit_, "tests": tests, "cause": cause, "why": why}


def never(_tid):
    return False


@pytest.mark.parametrize(
    ("base", "head", "failures"),
    [
        (_run(0), _run(0), []),
        (_run(0), _run(1), ["the suite exited 1 at head and 0 at base"]),
        (_run(1), _run(1), [checks.BOTH_FAIL]),
        (_run(1), _run(0), []),
        (_run(0, _tests(["m::a"])), _run(0, _tests(["m::a"])), []),
        # Per-test results at base, none at head (a conftest calling os._exit(0)).
        (_run(0, _tests(["m::a", "m::b"])), _run(0), ["m::a", "m::b"]),
        # A skip counts as an absence.
        (_run(0, _tests(["m::a"])), _run(0, _tests(skipped=["m::a"])), ["m::a"]),
        (_run(0, _tests(["m::a"])), _run(1, _tests(failed=["m::a"])), ["m::a"]),
        (_run(0, _tests(["m::a"])), _run(1, _tests(["m::a"], errored=["m::new"])), ["m::new"]),
        # The commit broke its own run: red with the cause, whatever else holds.
        (_run(0, _tests(["m::a"])), _run(1, cause="commit", why="setup failed"), ["setup failed", "m::a"]),
        (_run(1, cause="commit", why="setup"), _run(0), []),
    ],
)
def test_compare(base, head, failures):
    assert checks.compare(base, head, never)["failures"] == failures


def test_a_test_failing_at_base_and_head_is_listed_and_not_a_failure():
    base = _run(1, _tests(["m::a"], failed=["m::broken"]))
    head = _run(1, _tests(["m::a"], failed=["m::broken"]))
    assert checks.compare(base, head, never) == {
        "failures": [], "deleted_at_head": [], "failing_at_base": ["m::broken"],
    }  # fmt: skip


def test_an_absent_id_whose_definition_is_removed_is_deleted_not_failed():
    out = checks.compare(_run(0, _tests(["m::a", "m::b"])), _run(0, _tests(["m::a"])), lambda t: t == "m::b")
    assert out["failures"] == [] and out["deleted_at_head"] == ["m::b"]
    skipped = checks.compare(_run(0, _tests(["m::b"])), _run(0, _tests(skipped=["m::b"])), lambda t: True)
    assert skipped["failures"] == ["m::b"]  # a skipped test still exists; its definition was not removed


# -- what the diff removes ----------------------------------------------------------------------


P_BASE = '@pytest.mark.parametrize(\n    "x",\n    [1, 2],\n)\ndef test_p(x):\n    pass\n'
Q_BASE = 'UNRELATED = 1\n\n\n@pytest.mark.parametrize("x", [1, 2])\ndef test_q(x):\n    pass\n'


def test_removed_definitions(tmp_path):
    repo = scripted.toy_repo(tmp_path)
    for path, text in {
        "tests/test_a.py": "def test_gone():\n    pass\n\n\ndef test_edited():\n    pass\n",
        "tests/test_moved.py": "def test_moving():\n    pass\n",
        "tests/test_p.py": P_BASE,
        "tests/test_q.py": Q_BASE,
        "tests/test_c.py": "class TestK:\n    def test_m(self):\n        pass\n",
        "spec.js": "it('names a thing', () => {})\n",
    }.items():
        scripted.commit(repo, path, text)
    base = scripted.git(repo, "rev-parse", "HEAD")
    scripted.commit(repo, "tests/test_a.py", "def test_edited(y=1):\n    pass\n")
    scripted.git(repo, "rm", "-q", "tests/test_moved.py")
    scripted.commit(repo, "tests/test_other.py", "def test_moving():\n    pass\n")
    scripted.commit(repo, "tests/test_p.py", P_BASE.replace("1, 2", "1"))
    scripted.commit(repo, "tests/test_q.py", Q_BASE.replace("UNRELATED = 1\n", ""))
    scripted.commit(repo, "tests/test_c.py", "def test_free():\n    pass\n")
    scripted.commit(repo, "spec.js", "\n")
    gone = checks.removed_definitions(repo, base, scripted.git(repo, "rev-parse", "HEAD"))
    assert gone("tests.test_a::test_gone")
    assert not gone("tests.test_a::test_edited")  # its def line came back in the same file
    assert gone("tests.test_moved::test_moving")  # its file was deleted
    assert gone("tests.test_p::test_p[2]")  # a dropped case of its decorator
    # A line removed elsewhere in the file does not delete a missing case.
    assert not gone("tests.test_q::test_q[2]")
    assert gone("tests.test_c.TestK::test_m")  # its class went
    assert gone("spec.js::names a thing")  # no Python file: the name string was removed
    assert not gone("other::never_mentioned")


R_BASE = (
    "from tests.cases import IMPORTED\n"
    "BASE = [1, 2]\n"
    "CASES = BASE + [3]\n"
    "\n"
    "\n"
    "def ids(x):\n"
    "    return str(x)\n"
    "\n"
    "\n"
    '@pytest.mark.parametrize("x", CASES, ids=ids)\n'
    "def test_r(x):\n"
    "    pass\n"
    "\n"
    "\n"
    '@pytest.mark.parametrize("x", IMPORTED)\n'
    "def test_i(x):\n"
    "    pass\n"
    "\n"
    "\n"
    '@pytest.mark.parametrize("x", json.loads(Path(__file__).with_name("cases_f.json").read_text()))\n'
    "def test_f(x):\n"
    "    pass\n"
    "\n"
    "\n"
    '@pytest.mark.parametrize("x", [1, 2])\n'
    "def test_u(x):\n"
    "    pass\n"
)


@pytest.mark.parametrize(
    ("edit", "dropped", "kept"),
    [
        # A case dropped from a module-level list the decorator uses, followed
        # through the name it is built from.
        ({"tests/test_r.py": R_BASE.replace("BASE = [1, 2]", "BASE = [1]")}, "test_r[2]", "test_u[2]"),
        # The ids= function the decorator names.
        ({"tests/test_r.py": R_BASE.replace("return str(x)", "return 'n' + str(x)")}, "test_r[2]", "test_u[2]"),
        # A line inserted inside a multi-line binding.
        ({"tests/test_r.py": R_BASE.replace("CASES = BASE + [3]\n", "CASES = BASE + [\n    3,\n]\n")},
         "test_r[3]", "test_u[2]"),
        # A module the decorator's name is imported from.
        ({"tests/cases.py": "IMPORTED = [1]\n"}, "test_i[2]", "test_r[2]"),
        # A case file a string in the decorator names.
        ({"tests/cases_f.json": "[1]\n"}, "test_f[2]", "test_i[2]"),
        # A line inserted just under a decorator touches nothing it reads.
        ({"tests/test_r.py": R_BASE.replace(
            '@pytest.mark.parametrize("x", [1, 2])\n', '@pytest.mark.parametrize("x", [1, 2])\n@pytest.mark.slow\n'
        )}, None, "test_u[2]"),
    ],
)  # fmt: skip
def test_a_dropped_case_is_deleted_when_the_diff_touches_what_feeds_its_decorator(
    tmp_path, edit, dropped, kept
):
    repo = scripted.toy_repo(tmp_path)
    for path, text in {
        "tests/test_r.py": R_BASE,
        "tests/cases.py": "IMPORTED = [1, 2]\n",
        "tests/cases_f.json": "[1, 2]\n",
    }.items():
        scripted.commit(repo, path, text)
    base = scripted.git(repo, "rev-parse", "HEAD")
    for path, text in edit.items():
        scripted.commit(repo, path, text)
    gone = checks.removed_definitions(repo, base, scripted.git(repo, "rev-parse", "HEAD"))
    if dropped:
        assert gone(f"tests.test_r::{dropped}")
    assert not gone(f"tests.test_r::{kept}")


def test_a_dropped_case_is_found_in_a_base_file_that_starts_with_blank_lines(tmp_path):
    """The base text keeps its leading lines, so the decorator's span
    counts the lines the diff's hunks count."""
    repo = scripted.toy_repo(tmp_path)
    text = '\n\n\n@pytest.mark.parametrize("x", [1, 2])\ndef test_b(x):\n    pass\n'
    scripted.commit(repo, "tests/test_b.py", text)
    base = scripted.git(repo, "rev-parse", "HEAD")
    scripted.commit(repo, "tests/test_b.py", text.replace("[1, 2]", "[1]"))
    gone = checks.removed_definitions(repo, base, scripted.git(repo, "rev-parse", "HEAD"))
    assert gone("tests.test_b::test_b[2]")


F_BASE = (
    "FIX = [1, 2]\n"
    "if True:\n"
    "    NESTED = [1, 2]\n"
    "\n"
    "\n"
    "@pytest.fixture(params=FIX)\n"
    "def fx(request):\n"
    "    return request.param\n"
    "\n"
    "\n"
    "@pytest.fixture\n"
    "def dep(fx):\n"
    "    return fx\n"
    "\n"
    "\n"
    "@pytest.fixture\n"
    "def plain():\n"
    "    return 1\n"
    "\n"
    "\n"
    "def test_fx(fx):\n"
    "    pass\n"
    "\n"
    "\n"
    "def test_dep(dep):\n"
    "    pass\n"
    "\n"
    "\n"
    "def test_cf(cf):\n"
    "    pass\n"
    "\n"
    "\n"
    '@pytest.mark.parametrize("x", NESTED)\n'
    "def test_n(x):\n"
    "    pass\n"
    "\n"
    "\n"
    '@pytest.mark.parametrize("x", [1, 2])\n'
    "def test_u(x, plain):\n"
    "    pass\n"
)
M_BASE = 'MARKED = [1, 2]\npytestmark = pytest.mark.parametrize("x", MARKED)\n\n\ndef test_m(x):\n    pass\n'
G_BASE = (
    "def pytest_generate_tests(metafunc):\n"
    '    metafunc.parametrize("x", [1, 2])\n'
    "\n"
    "\n"
    "def test_g(x):\n"
    "    pass\n"
)
CONFTEST = "@pytest.fixture(params=[1, 2])\ndef cf(request):\n    return request.param\n"


@pytest.mark.parametrize(
    ("edit", "dropped", "kept"),
    [
        # A fixture's params=, followed through the name it uses.
        ({"tests/test_f.py": F_BASE.replace("FIX = [1, 2]", "FIX = [1]")}, "test_f::test_fx[2]", "test_f::test_u[2]"),
        # The same fixture requested through another fixture.
        ({"tests/test_f.py": F_BASE.replace("FIX = [1, 2]", "FIX = [1]")}, "test_f::test_dep[2]", "test_f::test_n[2]"),
        # A fixture with params= in a conftest.py above the test.
        ({"tests/conftest.py": CONFTEST.replace("[1, 2]", "[1]")}, "test_f::test_cf[2]", "test_f::test_fx[2]"),
        # A list bound inside a module-level if.
        ({"tests/test_f.py": F_BASE.replace("    NESTED = [1, 2]", "    NESTED = [1]")},
         "test_f::test_n[2]", "test_f::test_fx[2]"),
        # A module-level pytestmark that parametrizes.
        ({"tests/test_m.py": M_BASE.replace("MARKED = [1, 2]", "MARKED = [1]")}, "test_m::test_m[2]", "test_g::test_g[2]"),
        # pytest_generate_tests.
        ({"tests/test_g.py": G_BASE.replace("[1, 2]", "[1]")}, "test_g::test_g[2]", "test_m::test_m[2]"),
        # A fixture with no params= feeds no case.
        ({"tests/test_f.py": F_BASE.replace("    return 1\n", "    return 2\n")}, None, "test_f::test_u[2]"),
    ],
)  # fmt: skip
def test_a_dropped_case_is_deleted_when_the_diff_touches_a_feed_outside_the_decorator(
    tmp_path, edit, dropped, kept
):
    repo = scripted.toy_repo(tmp_path)
    for path, text in {
        "tests/test_f.py": F_BASE,
        "tests/test_m.py": M_BASE,
        "tests/test_g.py": G_BASE,
        "tests/conftest.py": CONFTEST,
    }.items():
        scripted.commit(repo, path, text)
    base = scripted.git(repo, "rev-parse", "HEAD")
    for path, text in edit.items():
        scripted.commit(repo, path, text)
    gone = checks.removed_definitions(repo, base, scripted.git(repo, "rev-parse", "HEAD"))
    if dropped:
        assert gone(f"tests.{dropped}")
    assert not gone(f"tests.{kept}")


# -- the environment digest ---------------------------------------------------------------------


def test_a_byte_in_bin_changes_the_digest_and_a_changed_digest_is_not_reused(tmp_path):
    repo = scripted.toy_repo(tmp_path)
    sha = scripted.git(repo, "rev-parse", "HEAD")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "tool").write_bytes(b"one")
    project = {"setup": ["true"], "env": {}}
    first = checks.environment_digest(repo, sha, project, bin_dir)
    (bin_dir / "tool").write_bytes(b"onf")
    second = checks.environment_digest(repo, sha, project, bin_dir)
    assert first != second
    assert first != checks.environment_digest(repo, sha, {"setup": ["false"], "env": {}}, bin_dir)
    payload = {"commit": sha, "role": "head", "command": "c", "digest": first, "cause": None}
    ran = [{"type": checks.SUITE, "payload": payload}]
    assert checks.reusable(ran, sha, "c", first, "head") is ran[0]
    assert checks.reusable(ran, sha, "c", second, "head") is None
    assert checks.reusable(ran, sha, "c", first, "review") is None  # review's head run never reuses test's
    timed_out = [{"type": checks.SUITE, "payload": {**payload, "cause": "commit"}}]
    assert checks.reusable(timed_out, sha, "c", first, "head") is None


# -- the runner through the router --------------------------------------------------------------


@pytest.mark.macos
def test_a_passing_candidate_records_pass_with_both_suite_runs(dsn, tmp_path):
    _task, _b, out, got = through_test(dsn, tmp_path, writes={"greeting.txt": "hello\n"})
    assert out["status"] == "no runner" and out["missing"] == ["review", "docs"], out
    suites = [r for r in got if r["type"] == checks.SUITE]
    assert [s["payload"]["role"] for s in suites] == ["base", "head"]
    head = suites[1]["payload"]
    assert len(head["tests"]["passed"]) == 6 and head["exit"] == 0 and head["cause"] is None
    d = decided(got)
    assert d["verdict"] == "pass" and d["leg"] == "kernel" and d["suites"] == [s["id"] for s in suites]
    assert d["command"] == SUITE and d["failures"] == [] and "provenance" not in d


@pytest.mark.macos
def test_a_conftest_that_exits_before_collecting_is_red(dsn, tmp_path):
    _task, _b, _out, got = through_test(dsn, tmp_path, writes={"conftest.py": "import os\nos._exit(0)\n"})
    d = decided(got)
    assert d["verdict"] == "red"
    assert sorted(d["failures"]) == sorted(
        ["tests.test_a::test_one", "tests.test_a::test_two", "tests.test_b::test_kept"]
        + [f"tests.test_a::test_p[{i}]" for i in (1, 2, 3)]
    )


@pytest.mark.macos
def test_skipping_a_failing_test_is_red(dsn, tmp_path):
    _task, _b, _out, got = through_test(dsn, tmp_path, writes={
        "greeting.py": "X = 0\n",
        "tests/test_b.py": "def test_kept():\n    assert False\n\n\ntest_kept.skip = True\n",
    })  # fmt: skip
    d = decided(got)
    assert d["verdict"] == "red" and d["failures"] == ["tests.test_b::test_kept"]


@pytest.mark.macos
def test_deleting_a_test_or_a_parameter_is_listed_and_not_red(dsn, tmp_path):
    a = BASE_TESTS["tests/test_a.py"].replace("def test_two():\n    assert True\n\n\n", "")
    a = a.replace("@parametrize(1, 2, 3)", "@parametrize(1, 2)")
    _task, _b, _out, got = through_test(
        dsn, tmp_path, writes={"tests/test_a.py": a}, removes=["tests/test_b.py"]
    )
    d = decided(got)
    assert d["verdict"] == "pass", d
    assert sorted(d["deleted_at_head"]) == [
        "tests.test_a::test_p[3]", "tests.test_a::test_two", "tests.test_b::test_kept",
    ]  # fmt: skip


@pytest.mark.macos
def test_a_test_failing_at_base_and_head_does_not_make_red(dsn, tmp_path):
    files = {**BASE_TESTS, "tests/test_c.py": "def test_broken():\n    assert False\n"}
    _task, _b, _out, got = through_test(dsn, tmp_path, files=files, writes={"greeting.txt": "hi\n"})
    d = decided(got)
    assert d["verdict"] == "pass" and d["failing_at_base"] == ["tests.test_c::test_broken"]


@pytest.mark.parametrize(
    ("base_suite", "verdict", "failures"),
    [
        ("exit 0", "red", ["the suite exited 1 at head and 0 at base"]),
        ("exit 1", "red", [checks.BOTH_FAIL]),
    ],
)
@pytest.mark.macos
def test_no_junit_on_either_side_is_decided_by_exit_codes(dsn, tmp_path, base_suite, verdict, failures):
    # The suite runs the commit's own script, so base and head differ by what it says.
    files = {"suite.sh": base_suite + "\n"}
    _task, _b, _out, got = through_test(
        dsn, tmp_path, files=files, writes={"suite.sh": "# head\nexit 1\n"}, suite="/bin/bash suite.sh"
    )
    d = decided(got)
    assert d["verdict"] == verdict and d["failures"] == failures


@pytest.mark.macos
def test_an_uncalibrated_breadth_behavior_is_information_and_the_join_merges(dsn, tmp_path):
    assert judgement_tasks.BREADTH.calibrated is None
    sid = UP.script(default={"probs": _gaps("gap_enum")})

    async def go():
        task, _b, ws = await to_candidate(dsn, tmp_path, writes={"greeting.txt": "hi\n"})
        await drive(dsn, task, runners(ws, UP.port(script=sid)))
        await scripted.check(dsn, task, "review", "pass")
        await scripted.check(dsn, task, "docs", "no_change")
        return await rows(dsn, task)

    got = run(go())
    d = decided(got)
    listed = ["untested: a member of an enumeration the code branches on"]
    assert d["verdict"] == "pass" and d["behaviors"] == [] and d["guard_id"] is None
    assert d["breadth"]["information"] == listed
    delivered = next(r["payload"] for r in got if r["type"] == "task.delivered")
    assert delivered["information"] == listed and delivered["gaps"] == [] and delivered["join_row"] == 1


@pytest.mark.macos
def test_a_calibrated_breadth_behavior_is_gaps(dsn, tmp_path, monkeypatch):
    monkeypatch.setattr(
        judgement_tasks, "BREADTH", dataclasses.replace(judgement_tasks.BREADTH, calibrated="0" * 64)
    )
    sid = UP.script(default={"probs": _gaps("gap_enum")})
    _task, _b, _out, got = through_test(
        dsn, tmp_path, writes={"greeting.txt": "hi\n"}, breadth=UP.port(script=sid)
    )
    d = decided(got)
    assert (
        d["verdict"] == "gaps"
        and d["guard_id"] == machine.GUARD_BREADTH
        and "information" not in d["breadth"]
    )


def _gaps(*true) -> dict:
    return {
        q: ({"true": 0.95, "false": 0.05} if q in true else {"true": 0.03, "false": 0.97})
        for q in ("gap_state", "gap_enum", "gap_bound")
    }


@pytest.mark.macos
def test_breadth_with_both_legs_down_fails_before_any_suite(dsn, tmp_path):
    sid = UP.script(default={"status": 503})
    _task, _b, out, got = through_test(
        dsn, tmp_path, writes={"greeting.txt": "hi\n"}, breadth=UP.port(script=sid)
    )
    assert out["status"] == "failed" and "breadth unanswered" in out["turn"]["result"]
    assert not [r for r in got if r["type"] == checks.SUITE]
    assert not [r for r in got if r["type"] == "test.decided"]


@pytest.mark.macos
def test_the_base_runs_once_across_two_candidates_and_the_seed_stays_clean(dsn, tmp_path):
    # Each suite fails if it finds the file a previous suite planted in its cache.
    plant = 'test ! -e "$UV_CACHE_DIR/planted" || exit 7; mkdir -p "$UV_CACHE_DIR"; touch "$UV_CACHE_DIR/planted"; '
    setup = 'mkdir -p "$UV_CACHE_DIR" && echo "$PWD" >> setup-ran'

    async def go():
        task, b, ws = await to_candidate(
            dsn, tmp_path, writes={"greeting.txt": "one\n"}, suite=plant + SUITE, setup=[setup]
        )
        rs = runners(ws)
        await drive(dsn, task, rs)
        await scripted.check(dsn, task, "review", "changes", findings=["say it twice"])
        await scripted.check(dsn, task, "docs", "no_change")
        await drive(dsn, task, rs)  # patch, then the test runner on the new candidate
        return task, b, await rows(dsn, task)

    _task, b, got = run(go())
    suites = [r["payload"] for r in got if r["type"] == checks.SUITE]
    assert [s["role"] for s in suites] == ["base", "head", "head"], suites
    assert suites[1]["commit"] != suites[2]["commit"]
    tests_ = [r["payload"] for r in got if r["type"] == "test.decided"]
    assert [t["verdict"] for t in tests_] == ["pass", "pass"]
    lay = kws.Layout(Path(b.mirror).parent)
    assert not (lay.checks / "seed" / "uv" / "planted").exists()
    for role, sha in (("base", b.base_sha), ("head", suites[2]["commit"])):
        ran = lay.checks / f"test-{role}-{sha[:12]}" / "repo" / "setup-ran"
        assert ran.read_text().strip() == str(ran.parent)  # setup ran in that checkout


def _wait(predicate, seconds=60.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return False


def _marked(mark: str) -> list[int]:
    return runs._turn_processes(mark, None)


@pytest.mark.macos
def test_a_stop_kills_the_running_suite_and_records_nothing(dsn, tmp_path, monkeypatch):
    files = {"suite.sh": "exit 0\n"}

    async def go():
        task, _b, ws = await to_candidate(
            dsn, tmp_path, files=files, writes={"suite.sh": "sleep 300 &\nsleep 300\n"},
            suite="/bin/bash suite.sh",
        )  # fmt: skip
        running = asyncio.create_task(drive(dsn, task, runners(ws)))
        mark = f"test-{task}-head"
        ok = await asyncio.to_thread(_wait, lambda: len(_marked(mark)) >= 2)
        assert ok, "the suite never started"
        before = _marked(mark)
        started = time.monotonic()
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, task, reason="test", by="test")
        out = await running
        return out, time.monotonic() - started, before, mark, await rows(dsn, task)

    out, took, before, mark, got = run(go())
    assert out["status"] == "stopped" and took < 30
    assert [s["payload"]["role"] for s in got if s["type"] == checks.SUITE] == ["base"]
    assert not [r for r in got if r["type"] == "test.decided"]
    assert _wait(lambda: not _marked(mark), 10), _marked(mark)
    for pid in before:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


# -- failures the commit controls ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("writes", "kw", "finding"),
    [
        ({"broken": "x"}, {"setup": ["test ! -e broken"]}, "setup failed at head"),
        ({"suite.sh": "exit 3\n"}, {}, "no JUnit report"),
    ],
)
@pytest.mark.macos
def test_a_head_the_commit_broke_is_red_with_the_failure(dsn, tmp_path, writes, kw, finding):
    files = {**BASE_TESTS, "suite.sh": f"{SUITE.replace('{junit}', '$1')}\n"}
    _task, _b, out, got = through_test(
        dsn, tmp_path, files=files, writes=writes, suite="/bin/bash suite.sh {junit}", **kw
    )
    d = decided(got)
    assert d["verdict"] == "red" and finding in d["failures"][0], d["failures"]
    assert out["status"] == "no runner" and out["missing"] == ["review", "docs"]  # not rerun


@pytest.mark.macos
def test_each_setup_command_keeps_its_own_output_file(dsn, tmp_path):
    setup = ["echo first-step", "echo second-step"]
    _task, b, _out, got = through_test(dsn, tmp_path, writes={"greeting.txt": "hi\n"}, setup=setup)
    checks_dir = kws.Layout(Path(b.mirror).parent).checks
    head = next(r["payload"] for r in got if r["type"] == checks.SUITE and r["payload"]["role"] == "head")
    outputs = [c["output"] for c in head["setup"]["commands"]]
    assert len(set(outputs)) == 2
    for name, said in zip(outputs, ("first-step", "second-step"), strict=True):
        assert (checks_dir / name).read_text().strip() == said
    assert decided(got)["verdict"] == "pass"


@pytest.mark.macos
def test_a_candidate_whose_tree_holds_valor_is_red_and_not_rerun(dsn, tmp_path, monkeypatch):
    # The builder's clone hides the entry from the kernel's look there (a
    # replace ref would), so the mirror holds a candidate with `.valor`.
    seen = kws.tree_has_valor
    monkeypatch.setattr(
        kws, "tree_has_valor", lambda *a, trusted, **k: trusted and seen(*a, trusted=trusted, **k)
    )
    _task, _b, out, got = through_test(dsn, tmp_path, writes={".VALOR/x": "x"})
    head = next(r["payload"] for r in got if r["type"] == checks.SUITE and r["payload"]["role"] == "head")
    assert head["cause"] == "commit" and "holds a .valor entry" in head["why"]
    d = decided(got)
    assert d["verdict"] == "red" and "holds a .valor entry" in d["failures"][0]
    assert out["status"] == "no runner" and out["missing"] == ["review", "docs"]  # not rerun


@pytest.mark.macos
def test_a_base_setup_failure_decides_its_run_and_is_never_reused(dsn, tmp_path):
    _task, _b, _out, got = through_test(dsn, tmp_path, writes={"fixed": "x"}, setup=["test -e fixed"])
    base = next(r["payload"] for r in got if r["type"] == checks.SUITE and r["payload"]["role"] == "base")
    assert base["cause"] == "commit" and "setup failed at base" in base["why"]
    assert len(base["setup"]["commands"]) == 1
    assert checks.reusable(got, base["commit"], base["command"], base["digest"], "base") is None
    assert decided(got)["verdict"] == "pass"


@pytest.mark.macos
def test_a_service_that_will_not_start_records_nothing_and_fails(dsn, tmp_path, monkeypatch):
    def refuse(*_a, **_k):
        raise kws.Refused("initdb would not run")

    async def go():
        task, _b, ws = await to_candidate(
            dsn, tmp_path, writes={"greeting.txt": "hi\n"}, services=["postgres"]
        )
        monkeypatch.setattr(kws, "_init_postgres", refuse)  # the task's own was made at provisioning
        return await drive(dsn, task, runners(ws)), await rows(dsn, task)

    out, got = run(go())
    assert out["status"] == "failed" and "initdb would not run" in out["turn"]["result"]
    assert not [r for r in got if r["type"] in (checks.SUITE, "test.decided")]


# -- the check's services -------------------------------------------------------------------------

PROBE = r"""
import os, socket, subprocess, sys
def reach(port):
    s = socket.socket(); s.settimeout(2)
    try:
        s.connect(("127.0.0.1", port)); return s
    except OSError:
        return None
out = []
live = reach(6379)
out.append(f"6379={'open' if live else 'refused'}")
r = reach(int(os.environ["REDIS_PORT"]))
r.sendall(b"*2\r\n$3\r\nGET\r\n$7\r\nbuilder\r\n")
out.append(f"builder={r.recv(100)!r}")
r.sendall(b"*3\r\n$3\r\nSET\r\n$5\r\nsuite\r\n$1\r\n1\r\n")
r.recv(100)
q = subprocess.run([sys.argv[1], "-tAc", "select to_regclass('public.builder_table')"],
                   capture_output=True, text=True, check=False)
out.append(f"table={q.stdout.strip()!r} {q.returncode}")
for k in ("VIRTUAL_ENV", "PGPASSFILE", "PATH"):
    out.append(f"{k}={os.environ.get(k)}")
open("probe.txt", "w").write("\n".join(out) + "\n")
"""


def _redis(port: int, *command: str) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(f"*{len(command)}\r\n".encode() + b"".join(
            f"${len(c)}\r\n{c}\r\n".encode() for c in command))  # fmt: skip
        return s.recv(200)


def _psql(b, sql: str) -> subprocess.CompletedProcess:
    env = {**os.environ, **b.harness["env"]}
    return subprocess.run([str(Path(settings.pg_bin) / "psql"), "-tAc", sql], env=env,
                          capture_output=True, text=True, check=False)  # fmt: skip


@pytest.mark.macos
def test_the_suite_gets_fresh_services_and_the_tasks_come_back(dsn, tmp_path, monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", "/nowhere/venv")
    monkeypatch.setenv("PGPASSFILE", "/nowhere/pgpass")
    psql = str(Path(settings.pg_bin) / "psql")
    files = {"probe.py": PROBE}

    async def go():
        task, b, ws = await to_candidate(
            dsn, tmp_path, files=files, writes={"greeting.txt": "hi\n"},
            services=["postgres", "redis"], suite=f"{PYTHON} -B probe.py {psql}",
        )  # fmt: skip
        lay = kws.Layout(Path(b.mirror).parent)
        names, ports = kws.services_of(b)
        # The builder's data in the task's own services.
        await asyncio.to_thread(kws.start_services, task, lay, names, ports)
        try:
            assert _redis(ports["redis"], "SET", "builder", "1") == b"+OK\r\n"
            made = _psql(b, "create table builder_table (x int)")
            assert made.returncode == 0, made.stderr
        finally:
            await asyncio.to_thread(kws.stop_services, task, lay)
        await drive(dsn, task, runners(ws))
        return task, b, lay, names, ports, await rows(dsn, task)

    task, b, lay, names, ports, got = run(go())
    head = next(r["payload"] for r in got if r["type"] == checks.SUITE and r["payload"]["role"] == "head")
    check_dir = lay.checks / f"test-head-{head['commit'][:12]}"
    probe = dict(line.split("=", 1) for line in (check_dir / "repo" / "probe.txt").read_text().splitlines())
    assert probe["6379"] == "refused"
    assert probe["builder"] == "b'$-1\\r\\n'"  # the fresh Redis holds no key the builder wrote
    assert probe["table"] == "'' 0"  # the fresh database has no table the builder made
    assert probe["VIRTUAL_ENV"] == "None"
    assert probe["PGPASSFILE"] == str(check_dir / "tmp" / "pgpass")
    work = lay.root.parent
    assert probe["PATH"].split(":")[1] == str(work / "bin") and str(lay.checks / "bin") not in probe["PATH"]
    assert not [d for d in lay.checks.iterdir() if d.name.endswith(kws.SVC_SUFFIX)]
    # The task's own services come back: its table is there, the suite's key is not.
    kws.start_services(task, lay, names, ports)
    try:
        assert _psql(b, "select to_regclass('public.builder_table')").stdout.strip() == "builder_table"
        assert _redis(ports["redis"], "GET", "suite") == b"$-1\r\n"
    finally:
        kws.stop_services(task, lay)


@pytest.mark.macos
def test_the_check_layout_profile_and_a_planted_pgpass_link(dsn, tmp_path):
    async def go():
        return await scripted.provisioned(dsn, tmp_path, services=["postgres"])

    task, b = run(go())
    lay = kws.Layout(Path(b.mirror).parent)
    work = lay.root.parent
    check_dir = kws.fresh_dir(lay.checks / "test-head-x")
    with kws.check_services(lay, check_dir, b.project, task) as env:
        svc = lay.checks / f"test-head-x{kws.SVC_SUFFIX}"
        profile = (svc / "home" / "profiles" / "service.sb").read_text()
        assert str(work) in profile and env["PGPASSFILE"] == str(check_dir / "tmp" / "pgpass")
        assert env["PATH"].startswith(f"{work / 'bin'}:")
    assert not svc.exists()
    check_dir = kws.fresh_dir(lay.checks / "test-head-y")
    (check_dir / "tmp" / "pgpass").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(FileExistsError), kws.check_services(lay, check_dir, b.project, task):
        pass
    assert not (tmp_path / "elsewhere").exists()
    assert kws._connects(b.project["ports"]["postgres"])  # the task's own came back up
    kws.stop_services(task, lay)


def test_the_tasks_own_services_come_back_when_the_check_services_removal_raises(dsn, tmp_path, monkeypatch):
    async def go():
        return await scripted.provisioned(dsn, tmp_path, services=["postgres"])

    task, b = run(go())
    lay = kws.Layout(Path(b.mirror).parent)
    check_dir = kws.fresh_dir(lay.checks / "test-head-x")
    with kws.check_services(lay, check_dir, b.project, task):
        svc = lay.checks / f"test-head-x{kws.SVC_SUFFIX}"
        os.chflags(svc / "home" / "profiles" / "service.sb", stat.UF_IMMUTABLE)
    assert not svc.exists()  # a locked entry no longer stops the removal

    def refuse(path):
        raise PermissionError(1, "Operation not permitted", str(path))

    monkeypatch.setattr(kws, "rmtree", refuse)
    with pytest.raises(PermissionError), kws.check_services(lay, check_dir, b.project, task):
        pass
    assert kws._connects(b.project["ports"]["postgres"])
    kws.stop_services(task, lay)


def test_a_failed_check_services_stop_is_the_error_raised_when_the_restart_then_fails(
    dsn, tmp_path, monkeypatch
):
    """A stop that raises before stopping the check's instances leaves the
    port taken, so the task's own cannot start; the stop's error is the one
    raised, the restart's refusal its cause."""

    async def go():
        return await scripted.provisioned(dsn, tmp_path, services=["postgres"])

    task, b = run(go())
    lay = kws.Layout(Path(b.mirror).parent)
    check_dir = kws.fresh_dir(lay.checks / "test-head-x")
    stop = kws.stop_services

    def refuse(task_id, layout=None):
        if layout is not lay:
            raise PermissionError(1, "Operation not permitted", "stop")
        return stop(task_id, layout)

    monkeypatch.setattr(kws, "stop_services", refuse)
    try:
        with pytest.raises(PermissionError) as raised, kws.check_services(lay, check_dir, b.project, task):
            pass
        assert isinstance(raised.value.__cause__, kws.Refused)
    finally:
        stop(task)  # every process under the task's mark, the check's included


KERNEL = r"""
import asyncio, sys
from core import checks, router
from core.gateway import Gateway
from core.machine import Check
from tests.ports import listen

async def main(dsn, task):
    gateway = Gateway(dsn)
    await gateway.start(port=listen())
    await router.run(gateway, task, {Check.TEST: checks.test_runner(None)}, dsn=dsn)

asyncio.run(main(sys.argv[1], sys.argv[2]))
"""


@pytest.mark.macos
def test_a_kernel_killed_mid_suite_leaves_nothing_on_the_tasks_port(dsn, tmp_path):
    files = {"suite.sh": "exit 0\n", "key.py": PROBE_SET}

    async def go():
        task, b, ws = await to_candidate(
            dsn, tmp_path, files=files, services=["redis"], suite="/bin/bash suite.sh",
            writes={"suite.sh": f"{PYTHON} -B key.py\nsleep 300\n"},
        )  # fmt: skip
        await drive(dsn, task, scripted.fresh_runners(ws))  # critique, build: the candidate
        await judgement_sites.breadth(UP.port(fixed="false"), dsn, task)  # answered: the kernel reuses it
        return task, b

    task, b = run(go())
    lay = kws.Layout(Path(b.mirror).parent)
    _names, ports = kws.services_of(b)
    root = Path(__file__).resolve().parent.parent
    kernel = subprocess.Popen([sys.executable, "-c", KERNEL, dsn, task], cwd=root, start_new_session=True)
    try:
        assert _wait(lambda: _has_suite_key(ports["redis"]), 120), "the suite never wrote its key"
    finally:
        os.killpg(kernel.pid, signal.SIGKILL)
        kernel.wait()
    runs.reap(f"test-{task}-head")
    assert [d.name for d in lay.checks.iterdir() if d.name.endswith(kws.SVC_SUFFIX)]  # left by the kill

    seen = {}

    async def look(ctx):
        seen["key"] = _redis(ports["redis"], "GET", "suite")
        seen["svc"] = [d.name for d in lay.checks.iterdir() if d.name.endswith(kws.SVC_SUFFIX)]
        seen["marked"] = runs.marked_services([task])
        return {"status": "failed"}

    run(drive(dsn, task, {Check.REVIEW: look}))
    assert seen["key"] == b"$-1\r\n" and seen["svc"] == []
    redis_pid = int((lay.redis / "redis.pid").read_text()) if (lay.redis / "redis.pid").exists() else None
    assert redis_pid is None or not _pid_alive(redis_pid)


PROBE_SET = r"""
import os, socket
s = socket.create_connection(("127.0.0.1", int(os.environ["REDIS_PORT"])), timeout=5)
s.sendall(b"*3\r\n$3\r\nSET\r\n$5\r\nsuite\r\n$1\r\n1\r\n")
s.recv(100)
"""


def _has_suite_key(port: int) -> bool:
    try:
        return _redis(port, "GET", "suite").startswith(b"$1")
    except OSError:
        return False


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


REPO = Path(__file__).resolve().parent.parent


def test_the_valor_suite_collects_under_the_check_profile(tmp_path):
    """The valor suite, collected under the check profile with the valor
    spec's environment, collects every module: every server it starts at
    import listens where the profile lets it. A scratch cluster stands in
    for the check's own Postgres, which a module reads at import."""
    lay = kws.Layout(tmp_path / "work" / "t")
    check_dir = lay.checks / "test-head-collect"
    (check_dir / "tmp").mkdir(parents=True)
    lay.profiles.mkdir(parents=True)
    spec = kws.Spec.load(str(REPO / "projects" / "valor.toml"))
    with backup.scratch_cluster(tcp=True, port=listen()) as cluster:
        # The profile denies `~/src`; the repository and its interpreter are
        # read back after it, and the network rules stay as rendered.
        readable = sorted({str(REPO), os.path.realpath(sys.base_prefix)})
        text = kws.check_profile(lay, check_dir, [cluster.port])
        above = sorted({str(a) for p in readable for a in Path(p).parents})
        text += "(allow file-read*\n" + "".join(f'    (subpath "{p}")\n' for p in readable) + ")\n"
        text += "(allow file-read-metadata\n" + "".join(f'    (literal "{p}")\n' for p in above) + ")\n"
        profile = lay.profiles / "collect.sb"
        profile.write_text(text)
        env = {
            k: v.replace("{port}", str(cluster.port)).replace("{passfile}", str(check_dir / "tmp" / "pgpass"))
            for k, v in spec.env.items()
        }
        env.update(PATH="/usr/bin:/bin", HOME=str(Path.home()), TMPDIR=str(check_dir / "tmp"),
                   VALOR_PG_OWNER=cluster.owner)  # fmt: skip
        argv = kws.sandboxed(profile, "collect", str(REPO / ".venv" / "bin" / "python"), "-m", "pytest",
                             "--collect-only", "-q", "-p", "no:cacheprovider", "tests")  # fmt: skip
        out = subprocess.run(argv, cwd=REPO, env=env, capture_output=True, text=True, check=False)
    assert out.returncode == 0 and "ERROR collecting" not in out.stdout, (
        out.stdout[-3000:] + out.stderr[-2000:]
    )
