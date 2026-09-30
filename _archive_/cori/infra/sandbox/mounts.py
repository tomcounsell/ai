"""The host directories the profiles mount. Plan 06 task 7, seams §2.2.

`profile_for` is the one way a profile is built: `tree.delegate` calls it under
the objective's advisory lock (seams §3.2), in the process that also serves the
gateway (tech stack §2), so every function here is a coroutine and every
subprocess goes through `infra.sandbox.proc.run` with copy and archive work in
`asyncio.to_thread`. The cost is stated in the plan: a first mirror clone of a
repository root and a first image build take minutes, and the objective's lock
is held for that long.

Layout per space:

    <sandbox_root>/<space_id>/
      repos/<owner>/<name>.git/  a bare mirror of a repository root
      repos/<owner>/<name>/      its companion checkout, what scratch reads
      worktrees/<objective_id>/  the writable objective worktree
      verify/<key>/              an extracted snapshot, read-only
      snapshots/<id>.tar.gz
"""

import asyncio
import hashlib
import os
import re
import shutil
import tarfile
from pathlib import Path

from infra.sandbox import images
from infra.sandbox.proc import run
from infra.secrets import read_secret
from schemas.sandbox import SandboxProfile, SandboxProfileName, SnapshotRef
from schemas.space import DataClass, Space

CORI_BRANCH_PREFIX = "cori/"
DATA_CLASS_ENV_PREFIX = "CORI_DATA_CLASS_"
# Architecture §4: PROJECT is the work's own class, OPERATOR the person's.
# "at or below max_data_class" compares these ranks, never the strings.
DATA_RANK: dict[DataClass, int] = {"PROJECT": 0, "OPERATOR": 1}

_URL_ROOT = re.compile(
    r"^(?:https://)?github\.com/(?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?/?$"
)


class RootError(ValueError):
    """A root that is not the space's, or is not there."""


def default_sandbox_root() -> Path:
    return Path(
        os.environ.get("CORI_SANDBOX_ROOT", str(Path.home() / ".cori" / "sandboxes"))
    ).expanduser()


async def sandbox_root() -> Path:
    return default_sandbox_root()


async def paths_for_space(space_id: str) -> list[Path]:
    """The space's directories, for the spaces plan's `space.destroyed`
    (architecture §9). Only the ones that exist."""
    base = (await sandbox_root()) / space_id
    candidates = [base / n for n in ("repos", "worktrees", "verify", "snapshots")]
    return [p for p in [base, *candidates] if p.exists()]


# ---- roots ---------------------------------------------------------------


def is_repository_root(root: str) -> bool:
    if _URL_ROOT.match(root):
        return True
    path = _expand(root)
    return path is not None and ((path / ".git").exists() or _is_bare(path))


def _expand(root: str) -> Path | None:
    if root.startswith(("~", "/")):
        return Path(root).expanduser()
    return None


def _is_bare(path: Path) -> bool:
    return (path / "HEAD").is_file() and (path / "objects").is_dir()


def _repo_names(root: str) -> tuple[str, str, str]:
    """(owner, name, origin) for a repository root. A path root is its own
    origin and takes its parent directory's name as the owner, so two local
    repositories with the same basename do not share a mirror."""
    m = _URL_ROOT.match(root)
    if m:
        owner, name = m.group("owner"), m.group("name")
        return owner, name, f"https://github.com/{owner}/{name}.git"
    path = _expand(root)
    assert path is not None
    name = path.name[:-4] if path.name.endswith(".git") else path.name
    return path.parent.name or "local", name, str(path)


async def resolve_root(space: Space, root: str) -> Path:
    """The host path a root resolves to: the bare mirror for a repository root
    (created or fetched here), the directory itself for a plain directory root.
    Membership in `Space.roots` is checked here and nowhere else."""
    if root not in space.roots:
        raise RootError(
            f"root {root!r} is not one of space {space.id}'s roots: {space.roots}"
        )
    if not is_repository_root(root):
        path = _expand(root)
        if path is None:
            raise RootError(f"root {root!r} is neither a path nor a GitHub repository")
        if not path.is_dir():
            raise RootError(f"root {root!r} is not there: {path}")
        return path
    return await _mirror(space, root)


async def _mirror(space: Space, root: str) -> Path:
    owner, name, origin = _repo_names(root)
    repos = (await sandbox_root()) / space.id / "repos" / owner
    mirror = repos / f"{name}.git"
    if not mirror.exists():
        await asyncio.to_thread(repos.mkdir, parents=True, exist_ok=True)
        r = await run(
            "git",
            "clone",
            "--mirror",
            "--no-hardlinks",
            _clone_url(origin),
            str(mirror),
            timeout=1800.0,
        )
        if r.returncode != 0:
            raise RootError(
                f"mirroring {root!r} failed: "
                f"{_redact(r.stderr.decode('utf-8', 'replace'))}"
            )
        # `git clone` writes the URL it was given, token and all, into the
        # mirror's config. The token is used once, for the clone, and the
        # scrub below is the only thing that takes it off the disk, so a scrub
        # that fails takes the mirror with it.
        scrub = await run(
            "git", "--git-dir", str(mirror), "remote", "set-url", "origin", origin
        )
        await _require_scrubbed(mirror, scrub.returncode == 0)
    else:
        # A mirror an earlier call left with a token in its config is a
        # credential on disk, whatever else is true of it.
        await _require_scrubbed(mirror, True)
        # A mirror fetch advances every ref, the default branch included. A
        # failure here is the network, not the objective: work from what is on
        # disk rather than refusing to start.
        await run("git", "--git-dir", str(mirror), "fetch", "origin", timeout=900.0)
    await _companion(mirror)
    return mirror


def _clone_url(origin: str) -> str:
    if not origin.startswith("https://github.com/"):
        return origin
    token = read_secret("github_token")
    return origin.replace("https://", f"https://x-access-token:{token}@", 1)


_CREDENTIAL_URL = re.compile(r"://[^/@\s]+@")


def _config_holds_credentials(mirror: Path) -> bool:
    config = mirror / "config"
    if not config.is_file():
        return True  # a mirror with no config is not one this system wrote
    return bool(_CREDENTIAL_URL.search(config.read_text(errors="replace")))


async def _require_scrubbed(mirror: Path, scrubbed: bool) -> None:
    """The mirror keeps no credential, or it does not keep anything. A raise
    here costs one clone; a token left in a config on the person's disk is a
    secret outside the Keychain, which is what tech stack §6 forbids."""
    if scrubbed and not await asyncio.to_thread(_config_holds_credentials, mirror):
        return
    await asyncio.to_thread(shutil.rmtree, mirror, True)
    raise RootError(
        f"the mirror at {mirror} kept a credential in its config, so it was "
        "removed; the next resolve clones it again"
    )


def _redact(text: str) -> str:
    return re.sub(r"https://[^@\s]+@", "https://", text).strip()


def companion_of(mirror: Path) -> Path:
    return mirror.with_name(mirror.name[: -len(".git")])


async def _default_branch(mirror: Path) -> str:
    r = await run("git", "--git-dir", str(mirror), "symbolic-ref", "--short", "HEAD")
    branch = r.stdout.decode("utf-8", "replace").strip()
    return branch or "main"


async def _companion(mirror: Path) -> Path:
    """The read-only checkout scratch mounts, kept at the fetched tip."""
    checkout = companion_of(mirror)
    branch = await _default_branch(mirror)
    if not checkout.exists():
        r = await run(
            "git", "clone", "--no-hardlinks", str(mirror), str(checkout), timeout=900.0
        )
        if r.returncode != 0:
            raise RootError(
                f"the companion checkout of {mirror} failed: "
                f"{r.stderr.decode('utf-8', 'replace').strip()}"
            )
    else:
        await run("git", "-C", str(checkout), "fetch", "origin", timeout=900.0)
    await run("git", "-C", str(checkout), "reset", "--hard", f"origin/{branch}")
    return checkout


async def choose_root(space: Space, artifact_kind: str) -> str:
    """Seams §1.4's default, used only when the Contract names no root: `code`
    takes the first repository root, every other kind the first plain
    directory. Decided in the plan: one root serves every kind, because with a
    single repository root and a `message` contract the rule by kind yields
    nothing and the one root is the only place the work can happen."""
    if len(space.roots) == 1:
        return space.roots[0]
    if artifact_kind == "code":
        for root in space.roots:
            if is_repository_root(root):
                return root
    else:
        for root in space.roots:
            if not is_repository_root(root) and _expand(root) is not None:
                return root
    # Decided in build: fall back to the first root rather than refuse. The
    # seams' rule decides every case the plan describes; this is the case it
    # does not, and the work has to happen somewhere the person named.
    return space.roots[0]


# ---- the three profiles --------------------------------------------------


def _env_for(space: Space, max_data_class: DataClass) -> dict[str, str]:
    """Names, never values (seams §1.9). Each secret rides with a
    `CORI_DATA_CLASS_` companion so the sandbox sees the label the manifest
    gave it, which is what "class-labeled values" asks for (tech stack §6)."""
    ceiling = DATA_RANK[max_data_class]
    env: dict[str, str] = {}
    for secret in space.secrets:
        if DATA_RANK[secret.data_class] <= ceiling:
            env[secret.name] = secret.keychain_name
            env[f"{DATA_CLASS_ENV_PREFIX}{secret.name}"] = secret.data_class
    return env


def _copy_without_git(source: Path, dest: Path) -> None:
    shutil.copytree(source, dest, symlinks=True, ignore=shutil.ignore_patterns(".git"))


async def prepare_worktree(
    space: Space,
    objective_id: str,
    *,
    artifact_kind: str,
    max_data_class: DataClass = "PROJECT",
    root: str | None = None,
) -> SandboxProfile:
    """The writable mount, one per Objective. A replacement Brief after a stop
    shares the directory, so the work the stopped Brief did is still there."""
    chosen = root or await choose_root(space, artifact_kind)
    resolved = await resolve_root(space, chosen)
    worktree = (await sandbox_root()) / space.id / "worktrees" / objective_id
    if not worktree.exists():
        await asyncio.to_thread(worktree.parent.mkdir, parents=True, exist_ok=True)
        if is_repository_root(chosen):
            await _clone_worktree(resolved, worktree, objective_id, chosen)
        else:
            await asyncio.to_thread(_copy_without_git, resolved, worktree)
    return SandboxProfile(
        name="worktree",
        space=space.id,
        mount_source=str(worktree),
        readonly=False,
        network="hostonly",
        key=objective_id,
        env=_env_for(space, max_data_class),
        image=await images.build_for_root(worktree),
    )


async def _clone_worktree(
    mirror: Path, worktree: Path, objective_id: str, root: str
) -> None:
    branch = await _default_branch(mirror)
    r = await run(
        "git",
        "clone",
        "--no-hardlinks",
        "--branch",
        branch,
        str(mirror),
        str(worktree),
        timeout=1800.0,
    )
    if r.returncode != 0:
        raise RootError(
            f"cloning the worktree of {root!r} failed: "
            f"{r.stderr.decode('utf-8', 'replace').strip()}"
        )
    await run(
        "git",
        "-C",
        str(worktree),
        "checkout",
        "-b",
        f"{CORI_BRANCH_PREFIX}{objective_id}",
    )
    _, _, origin = _repo_names(root)
    await run("git", "-C", str(worktree), "remote", "set-url", "origin", origin)
    # The system acts as the person (README), and the host is the person's
    # machine, so commits inside the sandbox carry the host's git identity.
    for key in ("user.name", "user.email"):
        value = (await run("git", "config", "--global", "--get", key)).stdout
        text = value.decode("utf-8", "replace").strip()
        if text:
            await run("git", "-C", str(worktree), "config", key, text)


async def prepare_scratch(
    space: Space,
    brief_id: str,
    *,
    artifact_kind: str,
    root: str | None = None,
) -> SandboxProfile:
    """A read-only slice of the space: the directory itself for a path root,
    the companion checkout for a repository root. Nothing is copied."""
    chosen = root or await choose_root(space, artifact_kind)
    resolved = await resolve_root(space, chosen)
    mount = companion_of(resolved) if is_repository_root(chosen) else resolved
    return SandboxProfile(
        name="scratch",
        space=space.id,
        mount_source=str(mount),
        readonly=True,
        network="hostonly",
        key=brief_id,
        env={},
        image=await images.build_for_root(mount),
    )


class SnapshotMismatch(ValueError):
    """An extracted file does not hash to what the snapshot recorded."""


def _extract(archive: Path, dest: Path) -> None:
    """Symlinks and hard links are refused: a link inside a read-only verify
    mount can name a host path the snapshot never held. A member whose name
    climbs out of the destination is an error, not a skipped file."""
    with tarfile.open(archive) as tar:
        members = []
        for member in tar.getmembers():
            name = Path(member.name)
            if name.is_absolute() or ".." in name.parts:
                raise SnapshotMismatch(f"{archive} holds a member outside it: {name}")
            if member.issym() or member.islnk():
                continue
            members.append(member)
        tar.extractall(dest, members=members, filter="data")


def _verify_files(dest: Path, files: dict[str, str]) -> None:
    for rel, digest in files.items():
        path = dest / rel
        if not path.is_file():
            raise SnapshotMismatch(f"{rel} is not in the extraction of the snapshot")
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        if h.hexdigest() != digest:
            raise SnapshotMismatch(
                f"{rel} hashes to {h.hexdigest()}, the snapshot recorded {digest}"
            )


async def prepare_verify(
    space: Space, key: str, snapshot: SnapshotRef
) -> SandboxProfile:
    """One extraction per key. A verification calls this twice (seams §3.5,
    §3.6), once for the checks and once for the Verifier's own Brief, so the
    Verifier's VM never saw the checks run."""
    dest = (await sandbox_root()) / space.id / "verify" / key
    if dest.exists():
        await asyncio.to_thread(shutil.rmtree, dest)
    await asyncio.to_thread(dest.mkdir, parents=True, exist_ok=True)
    try:
        await asyncio.to_thread(_extract, Path(snapshot.path), dest)
        await asyncio.to_thread(_verify_files, dest, snapshot.files)
    except BaseException:
        await asyncio.to_thread(shutil.rmtree, dest, True)
        raise
    return SandboxProfile(
        name="verify",
        space=space.id,
        mount_source=str(dest),
        readonly=True,
        network="hostonly",
        key=key,
        env={},
        image=await images.build_for_root(dest),
    )


async def profile_for(
    name: SandboxProfileName,
    space: Space,
    key: str,
    *,
    artifact_kind: str,
    max_data_class: DataClass = "PROJECT",
    snapshot: SnapshotRef | None = None,
    root: str | None = None,
) -> SandboxProfile:
    """The one way a profile is built (seams §2.2). `space` is the manifest,
    because a worktree needs `Space.roots` and `Space.secrets`; the profile's
    `space` field is `space.id`."""
    if name == "verify":
        if snapshot is None:
            raise ValueError("a verify profile is built from a snapshot (seams §3.6)")
        return await prepare_verify(space, key, snapshot)
    if snapshot is not None:
        raise ValueError(f"a {name} profile is not built from a snapshot")
    if name == "worktree":
        return await prepare_worktree(
            space,
            key,
            artifact_kind=artifact_kind,
            max_data_class=max_data_class,
            root=root,
        )
    if name == "scratch":
        return await prepare_scratch(space, key, artifact_kind=artifact_kind, root=root)
    raise ValueError(f"no such profile: {name!r}")
