"""The Verifier fixture set parses, its pre-recorded check records satisfy
the excerpt rule, and the current seat is screened against the set through
the real `render_verifier_slice`. Plan 11 task 8; tech stack §4.1; prereqs
item 18. The live screen reports agreement and asserts nothing: the
threshold is set when the seat is first used at M1.

The screen has no store: the four store readers of `kernel.verify` are
pinned to a `report.landed` and a `snapshot.taken` built from the fixture's
artifact files, the `checks` rows built from `test_output.txt` or
`resolution.yaml` (or, for a message, from the real pure checks against the
fixture manifest), and empty tool log and effect ledger blocks, which still
appear under their headings, so the slice carries the seven kinds.
"""

import asyncio
import hashlib
import json
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from infra.models import load as load_seats
from infra.secrets import MissingSecret, read_secret
from kernel import verify
from schemas.events import Event
from schemas.ids import new_id
from schemas.report import ArtifactRef, CheckResult, CitationResolution, Report
from schemas.sandbox import SnapshotRef
from schemas.space import load_space
from schemas.verifier_fixture import (
    FIXTURE_ROOT,
    Fixture,
    load_fixture,
    load_manifest,
)
from tests.test_verify_checks import make_contract, make_objective

SPACE = "psyoptimal"
BRIEF = "brief-executor-screen"
SPACE_YAML = FIXTURE_ROOT / "space.yaml"


def test_manifest_and_fixtures_parse():
    manifest = load_manifest()
    fixtures = [load_fixture(e.kind, e.slug) for e in manifest.fixtures]
    assert len(fixtures) == 9
    for entry, fixture in zip(manifest.fixtures, fixtures):
        assert (fixture.kind, fixture.slug) == (entry.kind, entry.slug)
        assert fixture.expected_verdict == entry.expected_verdict
        assert (fixture.defect is None) == (fixture.expected_verdict == "pass")
        assert fixture.artifact_files(), fixture.slug
        assert fixture.artifact_paths(), fixture.slug
    verdicts = [f.expected_verdict for f in fixtures]
    assert verdicts.count("fail") == 6 and verdicts.count("pass") == 3
    kinds = {f.kind for f in fixtures}
    assert kinds == {"code", "document", "message"}


def folded(text: str) -> str:
    return " ".join(text.split())


def test_recorded_resolutions_satisfy_the_excerpt_rule():
    """Every `resolves: true` entry in a `resolution.yaml` carries an excerpt
    and every other entry none (seams §1.6; the carried requirement of
    prereqs item 18). Each record names the target its brief cites, and for
    a URL that resolves the brief quotes a passage and the recorded excerpt
    carries it, so the record says what the real check would find."""
    records = sorted(FIXTURE_ROOT.glob("document/*/resolution.yaml"))
    assert len(records) == 3
    urls = 0
    for record in records:
        brief = (record.parent / "brief.md").read_text()
        _, cited = verify.parse_citations(brief)
        for c in yaml.safe_load(record.read_text())["citations"]:
            entry = cited[c["id"]]
            assert entry.target == c["target"], (record, c["id"])
            if not c["resolves"]:
                assert not c.get("excerpt"), (record, c["id"])
                continue
            assert c.get("excerpt"), (record, c["id"])
            if entry.url:
                urls += 1
                assert entry.passage, (record, c["id"])
                assert folded(entry.passage) in folded(c["excerpt"]), (record, c["id"])
    assert urls == 2


# --- the records as CheckResults ---------------------------------------------


def _resolutions(fixture: Fixture) -> list[CitationResolution]:
    entries = yaml.safe_load((fixture.directory / "resolution.yaml").read_text())
    return [
        verify._resolution(f"{c['id']}. {c['target']}", c.get("excerpt"))
        for c in entries["citations"]
    ]


def checks_for(fixture: Fixture) -> tuple[list[CheckResult], list[dict]]:
    """The kernel's records for the fixture, as the check list and the
    `checks` rows the render reads them back from."""
    if fixture.kind == "code":
        output = (fixture.directory / "test_output.txt").read_text()
        passed = " passed" in output and " failed" not in output
        checks = [
            verify.check_result("build", True, "compileall: ok"),
            verify.check_result("tests", passed, output),
        ]
        rows = [_row(i, c) for i, c in enumerate(checks, 1)]
    elif fixture.kind == "document":
        text = (fixture.directory / "brief.md").read_text()
        resolutions = _resolutions(fixture)
        checks = [
            verify._citations_check(resolutions),
            verify.length_check(text, "document"),
        ]
        rows = [
            _row(1, checks[0], [r.model_dump(mode="json") for r in resolutions]),
            _row(2, checks[1]),
        ]
    else:
        text = (fixture.directory / "draft.md").read_text()
        checks = verify.message_checks(text, load_space(SPACE_YAML))
        rows = [_row(i, c) for i, c in enumerate(checks, 1)]
    return checks, rows


def _row(id_: int, c: CheckResult, resolutions=None) -> dict:
    return dict(
        id=id_,
        name=c.name,
        passed=c.passed,
        output_sha256=c.output_sha256,
        detail=c.detail,
        resolutions=resolutions,
    )


def _event(id_: int, type_: str, payload: dict) -> Event:
    return Event(
        id=id_,
        space_id=SPACE,
        type=type_,
        schema_version=1,
        occurred_at=datetime(2026, 9, 22, tzinfo=UTC),
        payload=payload,
    )


def pack(fixture: Fixture, tmp_path: Path) -> tuple[Report, SnapshotRef]:
    """The artifact files as a snapshot archive and the Report's refs."""
    mount = tmp_path / fixture.slug
    mount.mkdir()
    refs = []
    files = {}
    for path in fixture.artifact_paths():
        body = path.read_bytes()
        (mount / path.name).write_bytes(body)
        digest = hashlib.sha256(body).hexdigest()
        files[path.name] = digest
        refs.append(
            ArtifactRef(kind=fixture.kind, path=f"/work/{path.name}", sha256=digest)
        )
    archive = tmp_path / f"{fixture.slug}.tar.gz"
    with tarfile.open(archive, "w:gz", dereference=False) as tar:
        for path in sorted(mount.rglob("*")):
            tar.add(path, arcname=str(path.relative_to(mount)), recursive=False)
    snapshot = SnapshotRef(
        id=new_id(),
        handle_id="cori-worktree-screen",
        path=str(archive),
        sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        files=files,
        taken_at=datetime.now(UTC),
    )
    report = Report(
        artifact_refs=refs,
        evidence=[],
        assumption_deltas=[],
        summary="the screen never renders this",
    )
    return report, snapshot


def render_fixture(fixture: Fixture, tmp_path: Path, monkeypatch):
    """The slice for one fixture through the real render, with the store
    pinned to what the fixture records."""
    objective = make_objective(
        SPACE,
        contract=make_contract(
            artifact_kind=fixture.kind, criteria=fixture.success_criteria
        ),
        brief_id=BRIEF,
    )
    report, snapshot = pack(fixture, tmp_path)
    checks, rows = checks_for(fixture)
    events = [
        _event(1, "objective.approved", {"objective_id": objective.id, "revision": 1}),
        _event(
            2,
            "report.landed",
            {
                "brief_id": BRIEF,
                "objective_id": objective.id,
                "report": report.model_dump(mode="json"),
            },
        ),
        _event(
            3,
            "snapshot.taken",
            {
                "brief_id": BRIEF,
                "objective_id": objective.id,
                "snapshot": snapshot.model_dump(mode="json"),
            },
        ),
        _event(4, "checks.recorded", {"objective_id": objective.id, "brief_id": BRIEF}),
    ]

    async def _events_of(_):
        return events

    async def _tool_rows(brief_id, generation):
        return []

    async def _ledger_rows(_):
        return []

    async def _check_rows(_, brief_id):
        return [dict(r) for r in rows]

    monkeypatch.setattr(verify, "_events_of", _events_of)
    monkeypatch.setattr(verify, "_tool_rows", _tool_rows)
    monkeypatch.setattr(verify, "_ledger_rows", _ledger_rows)
    monkeypatch.setattr(verify, "_check_rows", _check_rows)
    return asyncio.run(verify.render_verifier_slice(objective, checks))


def slice_text(slice_) -> str:
    return "\n\n".join(f"## {b.kind}\n\n{b.text.strip()}" for b in slice_.blocks)


@pytest.fixture
def screen_secret(monkeypatch):
    monkeypatch.setattr(verify, "read_secret", lambda name: "retainer-floor-7731")


def test_every_fixture_renders_the_seven_blocks(tmp_path, monkeypatch, screen_secret):
    manifest = load_manifest()
    for entry in manifest.fixtures:
        fixture = load_fixture(entry.kind, entry.slug)
        slice_ = render_fixture(fixture, tmp_path, monkeypatch)
        assert [b.kind for b in slice_.blocks] == list(verify.BLOCK_KINDS), entry.slug
        assert all(b.data_class == "PROJECT" for b in slice_.blocks)
        by_kind = {b.kind: b for b in slice_.blocks}
        assert by_kind["tool_log"].text == verify.NONE
        assert by_kind["effect_ledger"].text == verify.NONE
        assert "the screen never renders this" not in slice_text(slice_)
        for path in fixture.artifact_paths():
            assert f"/work/{path.name}" in by_kind["artifacts"].text, entry.slug
        if fixture.kind == "code":
            assert path.read_text().strip() in by_kind["artifacts"].text
        if fixture.kind == "document":
            assert "excerpt:" in by_kind["checks"].text, entry.slug


SCREEN_INSTRUCTION = """Judge the artifact against the criteria and the kernel's records above. Reply with one JSON object and nothing else:
{"outcome": "pass" | "fail" | "abstain", "reason": "<one sentence>"}"""


def test_screen_current_verifier_seat(tmp_path, monkeypatch, screen_secret):
    try:
        key = read_secret("anthropic_api_key")
    except MissingSecret:
        pytest.skip("no Anthropic key in the Keychain")
    import anthropic

    client = anthropic.Anthropic(api_key=key)
    seat = load_seats()
    model_id = seat.seats.verifier.model
    manifest = load_manifest()
    rows = []
    usage = {"input": 0, "output": 0}
    for entry in manifest.fixtures:
        fixture = load_fixture(entry.kind, entry.slug)
        slice_ = render_fixture(fixture, tmp_path, monkeypatch)
        response = client.messages.create(
            model=model_id,
            max_tokens=1024,
            system=slice_text(slice_),
            messages=[{"role": "user", "content": SCREEN_INSTRUCTION}],
        )
        usage["input"] += response.usage.input_tokens
        usage["output"] += response.usage.output_tokens
        text = "".join(b.text for b in response.content if b.type == "text")
        start, end = text.find("{"), text.rfind("}")
        try:
            got = json.loads(text[start : end + 1])
            outcome = got.get("outcome", "?")
            reason = got.get("reason", "")
        except ValueError, AttributeError:
            outcome, reason = "unparsed", text[:120]
        rows.append(
            (fixture.kind, fixture.slug, fixture.expected_verdict, outcome, reason)
        )

    agree = sum(1 for _, _, exp, got, _ in rows if exp == got)
    print(
        f"\nverifier seat: {model_id} (screened={seat.seats.verifier.screened}) "
        f"render: slice"
    )
    print(f"{'kind':9} {'slug':18} {'expected':9} {'got':9} reason")
    for kind, slug, exp, got, reason in rows:
        mark = " " if exp == got else "!"
        print(f"{mark}{kind:8} {slug:18} {exp:9} {got:9} {reason[:90]}")
    print(
        f"screen: {agree}/{len(rows)} agree; tokens in {usage['input']} out {usage['output']}"
    )
