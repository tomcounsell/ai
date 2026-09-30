"""Task 6: the image a root gets is a function of that root's lockfile.

The first three tests are host-side and need no runtime. The fourth builds a
real image from a fixture project and runs it on the host-only network, which
is the only way to show that `uv run --frozen` inside a sandbox reaches no
index.
"""

import hashlib
import subprocess
import textwrap
from pathlib import Path

import pytest

from infra.sandbox import images
from infra.sandbox.proc import run
from tests.conftest import requires_container

FIXTURE_PYPROJECT = """\
[project]
name = "fixture-pkg"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = ["packaging"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
"""


def make_root(path: Path, *, lock: str | None = "version = 1\n") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "pyproject.toml").write_text(FIXTURE_PYPROJECT)
    (path / "src.py").write_text("print('source is not build context')\n")
    if lock is not None:
        (path / "uv.lock").write_text(lock)
    return path


async def test_image_tag_is_lock_digest(tmp_path):
    a = make_root(tmp_path / "a")
    b = make_root(tmp_path / "b")

    digest = hashlib.sha256(
        (a / "pyproject.toml").read_bytes() + (a / "uv.lock").read_bytes()
    ).hexdigest()[:12]

    assert await images.lock_digest(a) == digest
    assert await images.image_for_root(a) == f"cori-uv:{digest}"
    # Same lockfile and pyproject, different directory: the same image.
    assert await images.image_for_root(b) == await images.image_for_root(a)

    # A source edit does not move the tag; a lockfile edit does.
    (a / "src.py").write_text("print('edited')\n")
    assert await images.image_for_root(a) == f"cori-uv:{digest}"
    (a / "uv.lock").write_text("version = 1\n# a dependency moved\n")
    assert await images.image_for_root(a) != f"cori-uv:{digest}"


async def test_root_without_lock_uses_base(tmp_path):
    root = make_root(tmp_path / "nolock", lock=None)
    assert await images.lock_digest(root) is None
    assert await images.image_for_root(root) == images.BASE_IMAGE
    # And nothing is built for it: the base image is already there.
    assert await images.build_for_root(root) == images.BASE_IMAGE


def test_build_context_holds_lock_and_pyproject_only(tmp_path):
    root = make_root(tmp_path / "root")
    (root / "secrets.env").write_text("ANTHROPIC_API_KEY=not-a-real-key\n")
    (root / "sub").mkdir()
    (root / "sub" / "more.py").write_text("x = 1\n")

    dest = tmp_path / "context"
    dockerfile = images.write_build_context(root, dest)

    assert sorted(p.name for p in dest.iterdir()) == [
        "Dockerfile",
        "pyproject.toml",
        "uv.lock",
    ]
    body = dockerfile.read_text()
    assert body.startswith(f"FROM {images.BASE_IMAGE}\n")
    assert "uv sync --frozen --no-install-project" in body
    assert "WORKDIR /work" in body


@requires_container
async def test_build_for_lock_produces_image(tmp_path):
    """The dependency is in the image and the project is not, so a sandbox with
    no path to an index still imports both its dependency and its checkout."""
    root = tmp_path / "fixture"
    root.mkdir()
    (root / "pyproject.toml").write_text(FIXTURE_PYPROJECT)
    (root / "fixture_pkg.py").write_text(textwrap.dedent("""\
            \"\"\"The checkout, importable from /work and never installed.\"\"\"

            name = "fixture-pkg"
            """))
    lock = subprocess.run(
        ["uv", "lock"], cwd=root, capture_output=True, text=True, timeout=300
    )
    if lock.returncode != 0:
        pytest.skip(f"uv lock needs an index: {lock.stderr.strip()[:200]}")

    tag = await images.build_for_root(root)
    assert tag == await images.image_for_root(root)
    assert tag.startswith("cori-uv:")
    try:
        assert await images.image_exists(tag)
        # A second call is a no-op: the tag is already built.
        assert await images.build_for_root(root) == tag

        r = await run(
            "container",
            "run",
            "--rm",
            "--network",
            "cori-hostonly",
            "--mount",
            f"type=bind,source={root},target=/work",
            tag,
            "sh",
            "-c",
            'cd /work && uv run --frozen python -c "import packaging, fixture_pkg"',
            timeout=300.0,
        )
        assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    finally:
        await run("container", "image", "rm", tag, timeout=120.0)
