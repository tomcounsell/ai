"""The kernel-built image. Tech stack §6, architecture §5, plan 06 task 6.

"Dependencies come from the kernel-built image, which installs them from the
lockfile at build time, so a sandbox never needs a package registry." The tag
is a function of the lockfile and nothing else, so a `verify` sandbox for an
artifact whose lockfile is unchanged gets the same image the Executor had, and
one whose lockfile changed gets a fresh build from the new lock. That is what
"dependencies from a lockfile, never from the Executor's environment" asks for.

Everything here is `async`: `profile_for` calls it under the objective's
advisory lock in the process that also serves the gateway (tech stack §2), and
a first build takes minutes.
"""

import asyncio
import hashlib
import shutil
import tempfile
from pathlib import Path

from infra.sandbox.proc import run

BASE_IMAGE = "cori-base:3.14"
UV_IMAGE_PREFIX = "cori-uv"
LOCK = "uv.lock"
PYPROJECT = "pyproject.toml"
HERE = Path(__file__).resolve().parent

# UV_NO_SYNC in the base image keeps `uv run --frozen` from building the
# project itself, which would fetch a build backend from an index the host-only
# network cannot reach. Tests import from the checkout at /work instead.
DOCKERFILE = f"""FROM {BASE_IMAGE}
COPY --chown=agent:agent {PYPROJECT} {LOCK} /opt/project/
RUN cd /opt/project && uv sync --frozen --no-install-project
WORKDIR /work
"""


async def lock_digest(root: Path) -> str | None:
    """The first twelve hex characters of sha256 over the root's pyproject and
    lockfile, or None when the root has no lockfile."""
    root = Path(root)
    lock = root / LOCK
    if not lock.is_file():
        return None
    return await asyncio.to_thread(_digest, root)


def _digest(root: Path) -> str:
    h = hashlib.sha256()
    for name in (PYPROJECT, LOCK):
        path = root / name
        h.update(path.read_bytes() if path.is_file() else b"")
    return h.hexdigest()[:12]


async def image_for_root(root: Path) -> str:
    digest = await lock_digest(root)
    return BASE_IMAGE if digest is None else f"{UV_IMAGE_PREFIX}:{digest}"


async def image_exists(tag: str) -> bool:
    r = await run("container", "image", "inspect", tag, timeout=60.0)
    return r.returncode == 0


def write_build_context(root: Path, dest: Path) -> Path:
    """The context holds the lockfile, the pyproject, and a generated
    Dockerfile, and nothing else: no source, so no client code reaches the
    image and a source edit never invalidates it."""
    dest.mkdir(parents=True, exist_ok=True)
    for name in (PYPROJECT, LOCK):
        source = Path(root) / name
        if source.is_file():
            shutil.copy2(source, dest / name)
    dockerfile = dest / "Dockerfile"
    dockerfile.write_text(DOCKERFILE)
    return dockerfile


async def build_for_root(root: Path) -> str:
    """Build the image this root's lockfile names, or return the tag when it is
    already built. PyPI is the allowlisted registry, reached here on the host at
    build time and never from a running sandbox."""
    tag = await image_for_root(root)
    if tag == BASE_IMAGE or await image_exists(tag):
        return tag
    tmp = await asyncio.to_thread(tempfile.mkdtemp, "cori-build-")
    context = Path(tmp)
    try:
        await asyncio.to_thread(write_build_context, Path(root), context)
        r = await run(
            "container",
            "build",
            "-t",
            tag,
            "-f",
            str(context / "Dockerfile"),
            str(context),
            timeout=1800.0,
        )
        if r.returncode != 0:
            raise RuntimeError(
                f"building {tag} from {root}'s lockfile failed: "
                f"{r.stderr.decode('utf-8', 'replace').strip()}"
            )
    finally:
        await asyncio.to_thread(shutil.rmtree, context, True)
    return tag


async def build_base() -> str:
    r = await run(
        "container",
        "build",
        "-t",
        BASE_IMAGE,
        "-f",
        str(HERE / "base.Dockerfile"),
        str(HERE),
        timeout=1800.0,
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"building {BASE_IMAGE} failed: "
            f"{r.stderr.decode('utf-8', 'replace').strip()}"
        )
    return BASE_IMAGE
