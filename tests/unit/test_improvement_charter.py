"""Tests for the charter seed loader (#3255, lane 2b).

``docs/improvement-charter.md`` is the north star and Tom is its only author.
``ImprovementCharter.load_from_file`` projects that file into an immutable,
digest-addressed row. These tests hold the four properties the projection has
to have: it is idempotent per digest, it is append-only, it refuses a file Tom
does not own, and it resolves the same charter regardless of the caller's cwd.

Uses the autouse ``redis_test_db`` fixture (tests/conftest.py), which claims a
per-worker test DB — production Redis is never touched. Rows are written under
a test-scoped ``project_key``, except the one case that must call
``load_from_file()`` with no arguments at all to exercise the module-anchored
default; that row lands under the default ``valor`` key inside the claimed test
DB, which is not the production partition.
"""

from __future__ import annotations

import pytest

from models.improvement_charter import _CHARTER_PATH, ImprovementCharter
from tools.sdlc_verdict import compute_plan_hash

PK = "test-3255-charter"


@pytest.fixture
def charter_bytes() -> bytes:
    """The real charter's bytes, so fixtures stay faithful to what ships."""
    return _CHARTER_PATH.read_bytes()


@pytest.fixture
def charter_copy(tmp_path, charter_bytes):
    """An LF copy of the real charter in a temporary directory."""
    path = tmp_path / "improvement-charter.md"
    path.write_bytes(charter_bytes.replace(b"\r\n", b"\n"))
    return path


def _rows(project_key: str = PK) -> list:
    return list(ImprovementCharter.query.filter(project_key=project_key))


def _snapshot(row) -> dict:
    """Every stored field of a row, so a later comparison is exhaustive."""
    return {name: getattr(row, name, None) for name in ImprovementCharter._meta.fields}


def _refetch(row_id, project_key: str = PK):
    for row in _rows(project_key):
        if getattr(row, "id", None) == row_id:
            return row
    return None


class TestIdempotentSeed:
    def test_loading_twice_creates_exactly_one_row(self, charter_copy):
        first = ImprovementCharter.load_from_file(charter_copy, project_key=PK)
        second = ImprovementCharter.load_from_file(charter_copy, project_key=PK)

        assert first is not None
        assert second is not None
        assert second.id == first.id
        assert len(_rows()) == 1

    def test_the_row_carries_the_digest_version_and_effective_date(self, charter_copy):
        row = ImprovementCharter.load_from_file(charter_copy, project_key=PK)

        assert row.digest == compute_plan_hash(charter_copy)
        assert row.version == 2
        assert row.effective == "2026-09-07"
        assert row.state == "active"
        assert "north star" in row.text.lower()

    def test_a_crlf_copy_digests_identically_to_an_lf_copy(self, tmp_path, charter_copy):
        crlf = tmp_path / "charter-crlf.md"
        crlf.write_bytes(charter_copy.read_bytes().replace(b"\n", b"\r\n"))

        lf_row = ImprovementCharter.load_from_file(charter_copy, project_key=PK)
        crlf_row = ImprovementCharter.load_from_file(crlf, project_key=PK)

        assert crlf_row is not None
        assert crlf_row.id == lf_row.id
        assert len(_rows()) == 1


class TestAppendOnly:
    def test_a_changed_byte_appends_and_leaves_the_first_untouched(self, tmp_path, charter_copy):
        first = ImprovementCharter.load_from_file(charter_copy, project_key=PK)
        # Snapshot what is persisted, not the in-memory instance: a ContentField
        # holds plaintext on the row that was just assigned and a store
        # reference on every row read back, so comparing the two shapes would
        # fail on representation rather than on mutation.
        before = _snapshot(_refetch(first.id))

        amended = tmp_path / "charter-amended.md"
        amended.write_bytes(charter_copy.read_bytes() + b"\nOne amended byte.\n")
        second = ImprovementCharter.load_from_file(amended, project_key=PK)

        assert second is not None
        assert second.id != first.id
        assert second.digest != first.digest
        assert len(_rows()) == 2

        after = _snapshot(_refetch(first.id))
        assert after == before, "the loader mutated an existing charter row"

    def test_a_seeded_row_persists_its_text_to_the_content_store(self, charter_copy):
        """The charter text survives the round trip, not just the digest.

        popoto hydrates a queried row lazily, so a ``ContentField`` reads back
        as its ``$CF:`` store reference rather than as content — the same shape
        ``ImprovementExperiment.manifest`` has. Resolving the reference through
        the store is what proves the text was really written.
        """
        seeded = ImprovementCharter.load_from_file(charter_copy, project_key=PK)

        reference = _refetch(seeded.id).text
        assert reference.startswith("$CF:")
        stored = ImprovementCharter._meta.fields["text"].store.load(reference)
        assert stored.decode("utf-8") == charter_copy.read_text()

    def test_the_earlier_row_keeps_its_active_state(self, tmp_path, charter_copy):
        first = ImprovementCharter.load_from_file(charter_copy, project_key=PK)
        amended = tmp_path / "charter-amended.md"
        amended.write_bytes(charter_copy.read_bytes() + b"\nAnother amendment.\n")
        ImprovementCharter.load_from_file(amended, project_key=PK)

        assert _refetch(first.id).state == "active", "the loader superseded a prior row"


class TestRefusal:
    @pytest.mark.parametrize(
        "owner_line",
        ["owner: Someone Else", "owner:", ""],
        ids=["wrong-owner", "empty-owner", "no-owner-key"],
    )
    def test_a_file_tom_does_not_own_is_refused(self, tmp_path, charter_bytes, owner_line):
        text = charter_bytes.decode().replace("owner: Tom Counsell", owner_line, 1)
        path = tmp_path / "foreign-charter.md"
        path.write_text(text)

        assert ImprovementCharter.load_from_file(path, project_key=PK) is None
        assert _rows() == []

    def test_a_missing_file_returns_none_and_writes_nothing(self, tmp_path):
        assert ImprovementCharter.load_from_file(tmp_path / "absent.md", project_key=PK) is None
        assert _rows() == []

    def test_malformed_yaml_returns_none_and_writes_nothing(self, tmp_path):
        path = tmp_path / "malformed.md"
        path.write_text("---\nowner: Tom Counsell\nversion: [unclosed\n---\n\n# body\n")

        assert ImprovementCharter.load_from_file(path, project_key=PK) is None
        assert _rows() == []

    def test_absent_frontmatter_returns_none_and_writes_nothing(self, tmp_path):
        path = tmp_path / "no-frontmatter.md"
        path.write_text("# Valor recursive self-improvement charter\n\nowner: Tom Counsell\n")

        assert ImprovementCharter.load_from_file(path, project_key=PK) is None
        assert _rows() == []


class TestTolerantFields:
    @pytest.mark.parametrize("version_line", ["version: not-a-number", ""], ids=["bad", "absent"])
    def test_a_malformed_version_does_not_refuse_the_seed(
        self, tmp_path, charter_bytes, version_line
    ):
        """The digest is the identity; a version number is a display detail."""
        text = charter_bytes.decode().replace("version: 2", version_line, 1)
        path = tmp_path / "odd-version.md"
        path.write_text(text)

        row = ImprovementCharter.load_from_file(path, project_key=PK)

        assert row is not None
        assert row.digest == compute_plan_hash(path)
        assert len(_rows()) == 1


class TestPinned:
    def test_pinned_returns_the_newest_row(self, tmp_path, charter_copy):
        ImprovementCharter.load_from_file(charter_copy, project_key=PK)
        amended = tmp_path / "charter-amended.md"
        amended.write_bytes(charter_copy.read_bytes() + b"\nLater amendment.\n")
        newest = ImprovementCharter.load_from_file(amended, project_key=PK)

        pinned = ImprovementCharter.pinned(project_key=PK)

        assert pinned is not None
        assert pinned.id == newest.id

    def test_pinned_is_none_when_nothing_is_seeded(self):
        assert ImprovementCharter.pinned(project_key=PK) is None


class TestModuleAnchoredDefault:
    def test_a_no_argument_call_resolves_the_repo_charter_from_any_cwd(self, tmp_path, monkeypatch):
        """A cwd-relative default would seed a different file, or none, per caller."""
        monkeypatch.chdir(tmp_path)

        row = ImprovementCharter.load_from_file()

        assert row is not None
        assert row.digest == compute_plan_hash(_CHARTER_PATH)


class TestNoImportCycle:
    def test_the_sdlc_tools_import_cleanly_in_a_fresh_interpreter(self):
        """`models.improvement_charter` must not close an import cycle.

        ``tools.sdlc_verdict`` reaches ``agent.sdlc_router``, which imports
        ``agent/__init__``, which imports ``models/__init__``. A module-level
        ``compute_plan_hash`` import here therefore breaks any process that
        imports the SDLC tools before ``models`` — which every ``sdlc-tool``
        entry point does. A subprocess is the only honest check: this test
        process has already imported ``models``, so the cycle cannot reproduce
        in it.
        """
        import subprocess
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        result = subprocess.run(
            [sys.executable, "-c", "import tools.sdlc_meta_set"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert result.returncode == 0, result.stderr[-2000:]
