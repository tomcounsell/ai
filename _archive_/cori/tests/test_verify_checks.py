"""The deterministic checks per artifact kind. Plan 11 tasks 3, 4, 5; seams
§1.6, §3.6; architecture §5.

Task 3 runs the code checks in a real verify container from the image the
fixture's lockfile names; it skips where apple/container is not running.
Tasks 4 and 5 are pure functions over text and a manifest, and a local
`http.server` for the URL citation, so they run anywhere.
"""

import hashlib
import tarfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest

from adapters.apple_container import AppleContainer
from kernel import runs, verify
from kernel.events import append, read_for
from schemas.budget import Budget, Ceilings
from schemas.ids import new_id
from schemas.objective import Contract, Objective, ReportRef
from schemas.sandbox import SnapshotRef
from schemas.space import Space, load_space
from schemas.verifier_fixture import FIXTURE_ROOT, load_fixture
from tests.conftest import dsn, requires_container, requires_postgres

PROJECT = FIXTURE_ROOT / "code" / "project"


def connect():
    return psycopg.AsyncConnection.connect(dsn("kernel_rw"))


# --- builders shared by the verifier tests -----------------------------------


def make_space(space_id: str, **fields) -> Space:
    fields.setdefault("roots", ["/repo"])
    fields.setdefault("max_effect_class", "propose")
    return Space(id=space_id, kind="client", **fields)


def make_contract(
    *, artifact_kind="code", criteria=("pytest green",), premise="do the work"
) -> Contract:
    return Contract(
        premise=premise,
        non_goals=["refactor"],
        success_criteria=list(criteria),
        assumptions=[],
        budget=Budget(usd_micros=5_000_000),
        basis="one Executor turn and one Verifier turn",
        ceilings=Ceilings(
            max_effect_class="propose",
            deadline=datetime.now(UTC) + timedelta(hours=1),
            max_data_class="PROJECT",
        ),
        task_class="code.change" if artifact_kind == "code" else "message.draft",
        artifact_kind=artifact_kind,
        root="/repo",
    )


def make_objective(
    space: str, *, contract: Contract, brief_id: str, event_id: int = 0, **fields
) -> Objective:
    """A projection-shaped Objective a check can run against without a node
    in the store."""
    values = dict(
        id=f"obj-{uuid.uuid4().hex[:8]}",
        parent_id=None,
        depth=0,
        space=space,
        conversation_id="c1",
        contract=contract,
        contract_revision=1,
        approved_revision=1,
        state="VERIFYING",
        state_reason="verifying",
        generation=1,
        budget_consumed=Budget(usd_micros=0),
        budget_allocated=Budget(usd_micros=0),
        owner_brief=None,
        reports=[ReportRef(brief_id=brief_id, event_id=event_id, summary="")],
        evidence=[],
        children=[],
    )
    values.update(fields)
    return Objective(**values)


def pack_snapshot(
    tmp_path: Path, files: dict[str, bytes], *, name="snap"
) -> SnapshotRef:
    """A mount directory with `files`, archived the way the adapter archives
    a worktree, with the host-side hashes the kernel records."""
    mount = tmp_path / f"{name}-mount"
    mount.mkdir(exist_ok=True)
    for rel, body in files.items():
        p = mount / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
    archive = tmp_path / f"{name}.tar.gz"
    with tarfile.open(archive, "w:gz", dereference=False) as tar:
        for path in sorted(mount.rglob("*")):
            tar.add(path, arcname=str(path.relative_to(mount)), recursive=False)
    return SnapshotRef(
        id=new_id(),
        handle_id="cori-worktree-o1-abc123",
        path=str(archive),
        sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        files={rel: hashlib.sha256(body).hexdigest() for rel, body in files.items()},
        taken_at=datetime.now(UTC),
    )


def code_fixture_files(slug: str) -> dict[str, bytes]:
    """A code fixture's source and tests, plus the pyproject and lock that
    declare pytest, so `profile_for` names a built image."""
    fixture = load_fixture("code", slug)
    files = {
        p.name: p.read_bytes() for p in fixture.directory.iterdir() if p.suffix == ".py"
    }
    for name in ("pyproject.toml", "uv.lock"):
        files[name] = (PROJECT / name).read_bytes()
    return files


class RecordingSandbox:
    """The real adapter with every exec command, every container name, and
    every destroy recorded, plus one probe: the first exec in each container
    also tries a write to /work and records whether it was refused."""

    def __init__(self, inner: AppleContainer):
        self.inner = inner
        self.commands: list[str] = []
        self.created: list[str] = []
        self.destroyed: list[str] = []
        self.write_refused: dict[str, bool] = {}

    async def create(self, profile):
        h = await self.inner.create(profile)
        self.created.append(h.id)
        return h

    async def exec(self, h, cmd, *, timeout):
        if h.id not in self.write_refused:
            probe = await self.inner.exec(h, "touch /work/.cori-probe", timeout=30)
            self.write_refused[h.id] = probe.exit_status != 0
        self.commands.append(cmd)
        return await self.inner.exec(h, cmd, timeout=timeout)

    async def destroy(self, h):
        self.destroyed.append(h.id)
        await self.inner.destroy(h)

    def __getattr__(self, name):
        return getattr(self.inner, name)


@pytest.fixture
def sandbox_root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "sandboxes"
    root.mkdir()
    monkeypatch.setenv("CORI_SANDBOX_ROOT", str(root))
    return root


@pytest.fixture
def bound(space, monkeypatch, sandbox_root):
    """`kernel.verify` wired to the real adapter, the test database, and a
    one-space manifest set."""
    sandbox = RecordingSandbox(AppleContainer(sandbox_root=sandbox_root))
    monkeypatch.setattr(runs, "sandbox", sandbox)
    monkeypatch.setattr(runs, "worker", object())
    monkeypatch.setattr(runs, "door", object())
    monkeypatch.setattr(runs, "connect", connect)
    monkeypatch.setattr(verify, "load_all", lambda: {space: make_space(space)})
    return sandbox


# --- task 3: code checks in a fresh sandbox ----------------------------------


@requires_postgres
@requires_container
async def test_code_checks_run_in_fresh_sandbox(tmp_path, space, bound):
    snapshot = pack_snapshot(tmp_path, code_fixture_files("planted-bug"))
    brief_id = f"brief-{uuid.uuid4().hex[:8]}"
    objective = make_objective(space, contract=make_contract(), brief_id=brief_id)
    before = {h.id for h in await bound.running()}

    checks = await verify.run_checks(objective, snapshot)

    by_name = {c.name: c for c in checks}
    assert set(by_name) == {"build", "tests"}
    assert by_name["build"].passed
    assert by_name["tests"].passed and "4 passed" in by_name["tests"].detail
    assert verify.TESTS_COMMAND in bound.commands
    assert verify.BUILD_COMMAND in bound.commands
    assert len(bound.created) == 1
    (name,) = bound.created
    assert name not in before
    assert name.startswith("cori-verify-")
    assert bound.write_refused[name] is True
    assert bound.destroyed == [name]
    assert name not in {h.id for h in await bound.running()}
    assert not Path(bound.inner.sandbox_root / space / "verify").exists() or not any(
        (bound.inner.sandbox_root / space / "verify").iterdir()
    )

    async with await connect() as conn:
        rows = await (
            await conn.execute(
                "SELECT name, passed, detail, output_sha256, brief_id FROM checks "
                "WHERE objective_id = %s ORDER BY id",
                (objective.id,),
            )
        ).fetchall()
        assert [(r[0], r[1]) for r in rows] == [("build", True), ("tests", True)]
        assert all(r[4] == brief_id for r in rows)
        events = await read_for(
            conn, space_id=space, key="objective_id", value=objective.id
        )
        recorded = [e for e in events if e.type == "checks.recorded"]
        assert len(recorded) == 1
        assert recorded[0].payload["brief_id"] == brief_id
        assert [c["name"] for c in recorded[0].payload["checks"]] == ["build", "tests"]
        later = await append(
            conn,
            space_id=space,
            type="verification.sampled",
            payload={
                "objective_id": objective.id,
                "effect_class": "propose",
                "leaves_space": False,
                "selected": True,
                "probability": 1.0,
            },
        )
        await conn.commit()
    assert recorded[0].id < later


# --- task 4: message checks -------------------------------------------------

SPACE_YAML = FIXTURE_ROOT / "space.yaml"
LIVE_YAML = Path(__file__).resolve().parents[1] / "infra" / "spaces" / "psyoptimal.yaml"
FLOOR = "retainer-floor-7731"


@pytest.fixture
def fixture_space(monkeypatch) -> Space:
    monkeypatch.setattr(verify, "read_secret", lambda name: FLOOR)
    return load_space(SPACE_YAML)


def draft(slug: str) -> str:
    return (FIXTURE_ROOT / "message" / slug / "draft.md").read_text()


def by_name(checks):
    return {c.name: c for c in checks}


def test_message_checks(fixture_space):
    for slug in ("clean-reply", "operator-leak", "wrong-audience"):
        checks = by_name(verify.message_checks(draft(slug), fixture_space))
        assert set(checks) == {"recipient_allowed", "length", "no_operator_content"}
        assert all(c.passed for c in checks.values()), (slug, checks)

    text = draft("clean-reply")
    elsewhere = text.replace("bfetzer@psyoptimal.com", "someone@example.com", 1)
    assert not verify.recipient_allowed(elsewhere, fixture_space).passed

    listed = make_space(
        "listed", audience={"addresses": ["someone@example.com"], "domains": []}
    )
    assert verify.recipient_allowed(elsewhere, listed).passed

    cased = text.replace("bfetzer@psyoptimal.com", "Bryan@PsyOptimal.com", 1)
    assert not verify.recipient_allowed(cased, listed).passed
    assert verify.recipient_allowed(cased, fixture_space).passed

    connector_only = make_space(
        "connector",
        connectors=[
            {
                "kind": "gmail",
                "account": "tom@yuda.me",
                "route": {"sender_domain": "psyoptimal.com"},
            }
        ],
    )
    assert (
        connector_only.audience.addresses == []
        and connector_only.audience.domains == []
    )
    assert not verify.recipient_allowed(text, connector_only).passed

    mailto = make_space("mailto", allowed_targets=["mailto:tom@yuda.me"])
    assert verify.recipient_allowed(
        text.replace("bfetzer@psyoptimal.com", "tom@yuda.me", 1), mailto
    ).passed

    leaking = text + f"\nOur floor is {FLOOR}.\n"
    leak = verify.no_operator_content(leaking, fixture_space)
    assert not leak.passed
    assert FLOOR not in leak.detail and "RETAINER_FLOOR" in leak.detail

    parsed = verify.parse_message(text)
    assert parsed.to == "bfetzer@psyoptimal.com" and parsed.subject.startswith("Re:")
    assert parsed.other_headers == [] and parsed.body.startswith("Hi Bryan,")

    # One recipient, in the To: line, and no other header: a second address
    # on the To: line, named or bare, in either position, fails, and so does
    # any header line other than To: and Subject:, which is judged rather
    # than dropped from the body (seams §1.6).
    to_line = "To: bfetzer@psyoptimal.com"
    for two in (
        "To: x@evil.com, bfetzer@psyoptimal.com",
        "To: Eve <x@evil.com>, bfetzer@psyoptimal.com",
        "To: bfetzer@psyoptimal.com, x@evil.com",
        "To: bfetzer@psyoptimal.com; Eve <x@evil.com>",
        "To: bfetzer@psyoptimal.com x@evil.com",
    ):
        r = verify.recipient_allowed(text.replace(to_line, two, 1), fixture_space)
        assert not r.passed and "2 recipients" in r.detail, two
    for extra in ("Cc: x@evil.com", "Bcc: x@evil.com", "To: x@evil.com"):
        with_extra = text.replace(to_line + "\n", f"{to_line}\n{extra}\n", 1)
        r = verify.recipient_allowed(with_extra, fixture_space)
        assert not r.passed and extra.split(":")[0] in r.detail, extra
        assert "evil" not in verify.parse_message(with_extra).body
    after_subject = text.replace("\n\nHi Bryan", "\nCc: x@evil.com\n\nHi Bryan", 1)
    r = verify.recipient_allowed(after_subject, fixture_space)
    assert not r.passed and "Cc" in r.detail
    assert verify.parse_message(after_subject).body == parsed.body

    cap = verify.LENGTH_CAPS["message"]
    at_cap = f"To: a@psyoptimal.com\nSubject: s\n\n{'x' * cap}"
    assert verify.message_length(at_cap).passed
    assert not verify.message_length(at_cap + "x").passed

    headless = "Hi Bryan,\n\nno headers here\n"
    assert not verify.recipient_allowed(headless, fixture_space).passed


def test_live_manifest_admits_the_clean_reply():
    space = load_space(LIVE_YAML)
    if not (space.audience.addresses or space.audience.domains):
        pytest.skip("infra/spaces/psyoptimal.yaml carries no audience yet")
    checks = by_name(verify.message_checks(draft("clean-reply"), space))
    assert checks["recipient_allowed"].passed
    assert checks["length"].passed
    assert checks["no_operator_content"].passed


# --- task 5: document checks with the excerpt rule ---------------------------

import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

PAGE = """<html><head><title>Runners</title></head><body>
<h1>Using GitHub-hosted runners</h1>
<p>When a new major version of Ubuntu becomes the latest, the -latest label
migrates to it over a period of at least two weeks.</p>
<p>Pin a specific version to avoid breaking changes.</p>
</body></html>
"""
PASSAGE = "the -latest label migrates to it over a period of at least two weeks"


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture
def web(tmp_path):
    """A local http.server over a directory holding one page; no network."""
    docroot = tmp_path / "web"
    docroot.mkdir()
    (docroot / "page.html").write_text(PAGE)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(_Quiet, directory=str(docroot))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def document(references: list[str], body_markers: str = "") -> str:
    markers = body_markers or " ".join(f"[{i + 1}]" for i in range(len(references)))
    refs = "\n".join(f"{i + 1}. {r}" for i, r in enumerate(references))
    return (
        f"# Brief\n\n## Evidence\n\n- A claim {markers}.\n\n## References\n\n{refs}\n"
    )


def snapshot_dir(tmp_path: Path, files: dict[str, str]) -> Path:
    mount = tmp_path / "doc-mount"
    mount.mkdir(exist_ok=True)
    for rel, body in files.items():
        p = mount / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return mount


async def test_citation_without_excerpt_fails_the_screen(tmp_path, web):
    space = make_space("docs")
    mount = snapshot_dir(tmp_path, {})
    text = document([f"{web}/page.html"])
    checks, resolutions = await verify.document_checks(text, mount, space)
    check = by_name(checks)["citations_resolve"]
    assert not check.passed
    (r,) = resolutions["citations_resolve"]
    assert r.resolved is False and r.excerpt is None and r.excerpt_sha256 is None
    assert by_name(checks)["length"].passed


async def test_dangling_citation_fails(tmp_path, web):
    space = make_space("docs")
    mount = snapshot_dir(tmp_path, {"ops/ci.md": "| 3 | 2026-08-19 | image moved |\n"})
    text = document(
        [
            "`ops/ci.md`, row 3.",
            f'{web}/page.html "{PASSAGE}"',
            "`ops/postmortems/2026-07.md`",
        ]
    )
    checks, resolutions = await verify.document_checks(text, mount, space)
    assert not by_name(checks)["citations_resolve"].passed
    rows = resolutions["citations_resolve"]
    assert [r.resolved for r in rows] == [True, True, False]
    assert rows[2].excerpt is None
    assert (
        "3. ops/postmortems/2026-07.md: unresolved"
        in by_name(checks)["citations_resolve"].detail
    )


async def test_file_citation_records_the_file_as_excerpt(tmp_path):
    space = make_space("docs")
    body = "# CI failures\n\n| 3 | 2026-08-19 | ubuntu-latest moved |\n"
    mount = snapshot_dir(tmp_path, {"ops/ci.md": body})
    text = document(["`ops/ci.md`, row 3."])
    checks, resolutions = await verify.document_checks(text, mount, space)
    assert by_name(checks)["citations_resolve"].passed
    (r,) = resolutions["citations_resolve"]
    assert r.resolved and r.excerpt == body
    assert r.excerpt_sha256 == hashlib.sha256(body.encode()).hexdigest()

    # A file under a local root of the space resolves too; the cap holds.
    root = tmp_path / "vault"
    (root / "notes").mkdir(parents=True)
    long = "x" * (verify.EXCERPT_CAP + 10)
    (root / "notes" / "long.md").write_text(long)
    rooted = make_space("docs", roots=[str(root)])
    _, resolutions = await verify.document_checks(
        document(["`notes/long.md`"]), mount, rooted
    )
    (r,) = resolutions["citations_resolve"]
    assert r.resolved and len(r.excerpt) == verify.EXCERPT_CAP

    # A marker with no entry, and a path that climbs out, do not resolve.
    _, resolutions = await verify.document_checks(
        document(["`../secret.md`"], body_markers="[1] [2]"), mount, space
    )
    assert [r.resolved for r in resolutions["citations_resolve"]] == [False, False]


async def test_url_citation_records_the_quoted_passage(tmp_path, web):
    space = make_space("docs")
    mount = snapshot_dir(tmp_path, {})
    text = document([f'{web}/page.html "{PASSAGE}"'])
    checks, resolutions = await verify.document_checks(text, mount, space)
    assert by_name(checks)["citations_resolve"].passed
    (r,) = resolutions["citations_resolve"]
    assert r.resolved and r.excerpt == PASSAGE
    assert r.excerpt_sha256 == hashlib.sha256(PASSAGE.encode()).hexdigest()

    # A passage the page does not carry does not resolve; nor does a 404.
    _, resolutions = await verify.document_checks(
        document([f'{web}/page.html "a sentence the page never says"']), mount, space
    )
    assert resolutions["citations_resolve"][0].resolved is False
    _, resolutions = await verify.document_checks(
        document([f'{web}/missing.html "{PASSAGE}"']), mount, space
    )
    assert resolutions["citations_resolve"][0].resolved is False

    # The length cap is on the whole document.
    over = text + "y" * verify.LENGTH_CAPS["document"]
    checks, _ = await verify.document_checks(over, mount, space)
    assert not by_name(checks)["length"].passed
