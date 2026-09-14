"""Candidate-surface denylist (#3218, lane 6).

A release proposal names the repo-relative surfaces a candidate changes. The
denylist is the gate that keeps the charter, the charter model, the identity
file, the release machinery itself, the secrets files, and the git hooks off
that list. A denylist that a path spelling can walk around is not a denylist,
so normalization and the refusal of escapes and globs are tested alongside the
entries themselves.

Pure functions, no Redis.
"""

from __future__ import annotations

import pytest

from tools.improvement_release.denylist import (
    CANDIDATE_SURFACE_DENYLIST,
    InvalidSurface,
    SurfaceDenied,
    denied_surfaces,
    normalize_surface,
    refuse_denied,
)


class TestEntries:
    def test_the_verification_row_shape(self):
        denied = denied_surfaces(
            ["docs/improvement-charter.md", "models/improvement_charter.py", "tools/x.py"]
        )
        assert denied == ["docs/improvement-charter.md", "models/improvement_charter.py"]

    @pytest.mark.parametrize(
        "surface",
        [
            "docs/improvement-charter.md",
            "models/improvement_charter.py",
            "config/identity.json",
            "tools/improvement_release/promotion.py",
            "tools/improvement_release",
            ".env",
            ".env.example",
            ".githooks/commit-msg",
        ],
    )
    def test_each_entry_is_denied(self, surface):
        assert denied_surfaces([surface]) == [surface]
        with pytest.raises(SurfaceDenied) as excinfo:
            refuse_denied([surface])
        assert surface in str(excinfo.value)

    @pytest.mark.parametrize(
        "surface",
        [
            "tools/x.py",
            "docs/improvement-charter.md.bak",
            "docs/features/improvement-release.md",
            ".envrc",
            "tools/improvement_release_notes.md",
            "models/improvement_release.py",
            "config/settings.py",
        ],
    )
    def test_a_neighbouring_path_passes(self, surface):
        assert denied_surfaces([surface]) == []
        refuse_denied([surface])

    def test_offenders_come_back_in_input_order(self):
        surfaces = [
            "tools/x.py",
            ".githooks/pre-push",
            "agent/steering.py",
            "docs/improvement-charter.md",
        ]
        assert denied_surfaces(surfaces) == [".githooks/pre-push", "docs/improvement-charter.md"]

    def test_the_denylist_names_the_charter_and_its_model(self):
        assert "docs/improvement-charter.md" in CANDIDATE_SURFACE_DENYLIST
        assert "models/improvement_charter.py" in CANDIDATE_SURFACE_DENYLIST


class TestNormalization:
    def test_a_dot_dot_spelling_of_the_charter_is_still_denied(self):
        bypass = "./docs/../docs/improvement-charter.md"
        assert denied_surfaces([bypass]) == [bypass]

    def test_a_trailing_slash_directory_spelling_is_denied(self):
        assert denied_surfaces(["tools/improvement_release/"]) == ["tools/improvement_release/"]

    def test_normalize_collapses_dot_segments(self):
        assert normalize_surface("./tools/./x.py") == "tools/x.py"

    @pytest.mark.parametrize(
        "surface",
        [
            "..",
            "../docs/improvement-charter.md",
            "docs/../../etc/passwd",
            "/etc/passwd",
            "/Users/anyone/src/ai/tools/x.py",
            "tools/*.py",
            "tools/x?.py",
            "tools/[a-z].py",
            "",
            ".",
        ],
    )
    def test_escapes_globs_and_absolutes_are_refused_outright(self, surface):
        with pytest.raises(InvalidSurface):
            normalize_surface(surface)
        with pytest.raises(InvalidSurface):
            denied_surfaces([surface])
        with pytest.raises(InvalidSurface):
            refuse_denied([surface])

    def test_invalid_surface_is_a_surface_denied(self):
        """A caller that catches ``SurfaceDenied`` catches the invalid case too."""
        assert issubclass(InvalidSurface, SurfaceDenied)
