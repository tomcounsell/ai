"""A Verifier fixture: one artifact with its success criteria and the verdict
it should receive. Tech stack §4.1, architecture §5. Fixtures live under
tests/fixtures/verifier/<kind>/<slug>/ beside a manifest."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from schemas.objective import ArtifactKind

# The verdict a fixture expects. `abstain` is never expected of a fixture:
# each one is known-good or known-defective (tech stack §4.1).
ExpectedVerdict = Literal["pass", "fail"]
# The record files beside an artifact: the kernel's own check results,
# pre-recorded so the screen renders without a sandbox or a network.
RECORD_FILES = ("fixture.yaml", "test_output.txt", "resolution.yaml", "context.yaml")

FIXTURE_ROOT = Path(__file__).parents[1] / "tests" / "fixtures" / "verifier"


class Fixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ArtifactKind
    slug: str
    success_criteria: list[str] = Field(min_length=1)
    expected_verdict: ExpectedVerdict
    expected_reason: str
    defect: str | None

    @property
    def directory(self) -> Path:
        return FIXTURE_ROOT / self.kind / self.slug

    def artifact_files(self) -> dict[str, str]:
        """Every file in the fixture directory except fixture.yaml, by name.
        These are what the Verifier sees; the expected verdict never is."""
        return {
            p.name: p.read_text()
            for p in sorted(self.directory.iterdir())
            if p.is_file() and p.name != "fixture.yaml"
        }

    def artifact_paths(self) -> list[Path]:
        """The artifact itself: every file except the fixture and its
        pre-recorded check records, which the screen renders as checks."""
        return [
            p
            for p in sorted(self.directory.iterdir())
            if p.is_file() and p.name not in RECORD_FILES
        ]


class ManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ArtifactKind
    slug: str
    expected_verdict: ExpectedVerdict


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixtures: list[ManifestEntry] = Field(min_length=1)


def load_manifest(root: Path = FIXTURE_ROOT) -> Manifest:
    return Manifest.model_validate(yaml.safe_load((root / "manifest.yaml").read_text()))


def load_fixture(kind: str, slug: str, root: Path = FIXTURE_ROOT) -> Fixture:
    path = root / kind / slug / "fixture.yaml"
    return Fixture.model_validate(yaml.safe_load(path.read_text()))
