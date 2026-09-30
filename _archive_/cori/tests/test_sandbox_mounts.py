"""Task 7: the host directories behind the three profiles. Plan 06, task 7.

Nothing here needs the container runtime or the network: a repository root is a
git repository in `tmp_path`, so the mirror, the companion checkout, and the
worktree clone are the real code paths on any machine.
"""

import asyncio
import hashlib
import inspect
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from infra.sandbox import mounts
from schemas.sandbox import SnapshotRef
from schemas.space import Secret, Space


@pytest.fixture(autouse=True)
def sandbox_root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "sandboxes"
    root.mkdir()
    monkeypatch.setenv("CORI_SANDBOX_ROOT", str(root))
    return root


def git(*args: str, cwd: Path) -> str:
    import subprocess

    r = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return r.stdout.strip()


@pytest.fixture
def upstream(tmp_path) -> Path:
    """A repository root: what a GitHub root resolves to, without a network."""
    repo = tmp_path / "psyoptimal"
    repo.mkdir()
    git("init", "-b", "main", cwd=repo)
    git("config", "user.email", "person@example.com", cwd=repo)
    git("config", "user.name", "The Person", cwd=repo)
    (repo / "README.md").write_text("the first commit\n")
    git("add", "-A", cwd=repo)
    git("commit", "-m", "first", cwd=repo)
    return repo


@pytest.fixture
def plain(tmp_path) -> Path:
    """A plain directory root. The vendored repository inside it is what the
    copy must leave behind: a directory root is not a repository root."""
    d = tmp_path / "notes"
    (d / "vendor" / ".git").mkdir(parents=True)
    (d / "vendor" / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (d / "brief.md").write_text("what the person asked for\n")
    return d


def space_of(*roots: str, secrets: list[Secret] | None = None) -> Space:
    return Space(
        id="s1",
        kind="client",
        roots=list(roots),
        max_effect_class="propose",
        secrets=secrets or [],
    )


async def test_worktree_is_a_clone_on_cori_branch(upstream, sandbox_root):
    space = space_of(str(upstream))
    profile = await mounts.prepare_worktree(space, "o1", artifact_kind="code")

    worktree = Path(profile.mount_source)
    assert worktree == sandbox_root / "s1" / "worktrees" / "o1"
    assert profile.name == "worktree" and profile.readonly is False
    assert profile.network == "hostonly" and profile.key == "o1"
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=worktree) == "cori/o1"
    assert (worktree / "README.md").read_text() == "the first commit\n"
    # A mirror and a companion checkout were made, and the worktree's origin is
    # the root itself, never the mirror inside the sandbox root.
    mirror = sandbox_root / "s1" / "repos" / upstream.parent.name / "psyoptimal.git"
    assert mirror.is_dir() and mounts.companion_of(mirror).is_dir()
    assert git("remote", "get-url", "origin", cwd=worktree) == str(upstream)
    # Objects are copied, so nothing the sandbox does can reach the mirror.
    assert not any(
        p.stat().st_nlink > 1
        for p in (worktree / ".git" / "objects").rglob("*")
        if p.is_file()
    )


async def test_worktree_starts_at_the_fetched_tip(upstream, sandbox_root):
    space = space_of(str(upstream))
    first = await mounts.prepare_worktree(space, "o1", artifact_kind="code")
    assert (Path(first.mount_source) / "README.md").exists()

    (upstream / "later.md").write_text("landed after the first objective\n")
    git("add", "-A", cwd=upstream)
    git("commit", "-m", "second", cwd=upstream)
    tip = git("rev-parse", "HEAD", cwd=upstream)

    second = await mounts.prepare_worktree(space, "o2", artifact_kind="code")
    assert git("rev-parse", "HEAD", cwd=Path(second.mount_source)) == tip
    assert (Path(second.mount_source) / "later.md").exists()

    # The companion checkout scratch reads was reset to the same tip.
    scratch = await mounts.prepare_scratch(space, "b1", artifact_kind="code")
    checkout = Path(scratch.mount_source)
    assert git("rev-parse", "HEAD", cwd=checkout) == tip
    assert (checkout / "later.md").exists()

    # And the first objective's worktree is untouched by any of it.
    assert not (Path(first.mount_source) / "later.md").exists()


async def test_worktree_reused_for_same_objective(upstream):
    space = space_of(str(upstream))
    first = await mounts.prepare_worktree(space, "o1", artifact_kind="code")
    marker = Path(first.mount_source) / "work-in-progress.md"
    marker.write_text("what the stopped Brief got done\n")

    again = await mounts.prepare_worktree(space, "o1", artifact_kind="code")
    assert again.mount_source == first.mount_source
    assert marker.read_text() == "what the stopped Brief got done\n"
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=Path(again.mount_source)) == (
        "cori/o1"
    )


async def test_clone_config_holds_no_token(upstream, sandbox_root):
    space = space_of(str(upstream))
    profile = await mounts.prepare_worktree(space, "o1", artifact_kind="code")
    mirror = sandbox_root / "s1" / "repos" / upstream.parent.name / "psyoptimal.git"
    configs = [
        mirror / "config",
        mounts.companion_of(mirror) / ".git" / "config",
        Path(profile.mount_source) / ".git" / "config",
    ]
    for config in configs:
        body = config.read_text()
        assert "x-access-token" not in body, config
        assert "@github.com" not in body, config


async def test_directory_root_is_copied_without_git(plain):
    space = space_of(str(plain))
    profile = await mounts.prepare_worktree(space, "o1", artifact_kind="document")
    copy = Path(profile.mount_source)
    assert (copy / "brief.md").read_text() == "what the person asked for\n"
    assert not (copy / "vendor" / ".git").exists()
    assert (copy / "vendor").is_dir()
    # The root itself is untouched: the worktree is a copy.
    assert (plain / "brief.md").exists()

    # scratch mounts the directory itself, read-only, with no copy.
    scratch = await mounts.prepare_scratch(space, "b1", artifact_kind="document")
    assert Path(scratch.mount_source) == plain
    assert scratch.readonly is True and scratch.env == {}


async def test_env_holds_names_filtered_by_data_class(plain):
    space = space_of(
        str(plain),
        secrets=[
            Secret(
                name="PGPASSWORD", keychain_name="psyoptimal_db", data_class="PROJECT"
            ),
            Secret(
                name="BANK_TOKEN", keychain_name="operator_bank", data_class="OPERATOR"
            ),
        ],
    )
    at_project = await mounts.prepare_worktree(space, "o1", artifact_kind="code")
    assert at_project.env == {
        "PGPASSWORD": "psyoptimal_db",
        "CORI_DATA_CLASS_PGPASSWORD": "PROJECT",
    }

    at_operator = await mounts.prepare_worktree(
        space, "o2", artifact_kind="code", max_data_class="OPERATOR"
    )
    assert at_operator.env == {
        "PGPASSWORD": "psyoptimal_db",
        "CORI_DATA_CLASS_PGPASSWORD": "PROJECT",
        "BANK_TOKEN": "operator_bank",
        "CORI_DATA_CLASS_BANK_TOKEN": "OPERATOR",
    }
    # Names, never values (seams §1.9).
    assert "operator_bank" not in at_operator.model_dump_json().replace(
        '"operator_bank"', ""
    )


def write_snapshot(tmp_path: Path, files: dict[str, bytes]) -> SnapshotRef:
    mount = tmp_path / "mount"
    mount.mkdir(exist_ok=True)
    for rel, body in files.items():
        p = mount / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
    (mount / "escape-link").symlink_to("/etc/passwd")
    archive = tmp_path / "snap.tar.gz"
    with tarfile.open(archive, "w:gz", dereference=False) as tar:
        for path in sorted(mount.rglob("*")):
            tar.add(path, arcname=str(path.relative_to(mount)), recursive=False)
    return SnapshotRef(
        id="01990000-0000-7000-8000-0000000000aa",
        handle_id="cori-worktree-o1-abc123",
        path=str(archive),
        sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        files={rel: hashlib.sha256(body).hexdigest() for rel, body in files.items()},
        taken_at=datetime.now(UTC),
    )


async def test_verify_extraction_refuses_hash_mismatch(tmp_path, sandbox_root):
    space = space_of(str(tmp_path / "mount"))
    ref = write_snapshot(tmp_path, {"report.md": b"the artifact\n"})
    good = await mounts.prepare_verify(space, "checks", ref)
    assert (Path(good.mount_source) / "report.md").read_bytes() == b"the artifact\n"
    # The symlink was refused, and what it pointed at is not there either.
    assert not (Path(good.mount_source) / "escape-link").exists(follow_symlinks=False)

    lying = ref.model_copy(update={"files": {"report.md": "0" * 64}})
    with pytest.raises(mounts.SnapshotMismatch, match="hashes to"):
        await mounts.prepare_verify(space, "checks", lying)
    # A refused extraction leaves nothing behind for a container to mount.
    assert not (sandbox_root / "s1" / "verify" / "checks").exists()

    missing = ref.model_copy(update={"files": {"absent.md": "0" * 64}})
    with pytest.raises(mounts.SnapshotMismatch, match="not in the extraction"):
        await mounts.prepare_verify(space, "checks", missing)


async def test_verify_twice_with_two_keys_is_two_extractions(tmp_path, sandbox_root):
    space = space_of(str(tmp_path / "mount"))
    ref = write_snapshot(tmp_path, {"report.md": b"the artifact\n"})
    checks = await mounts.prepare_verify(space, "checks", ref)
    brief = await mounts.prepare_verify(space, "b7", ref)

    assert checks.mount_source != brief.mount_source
    assert Path(checks.mount_source) == sandbox_root / "s1" / "verify" / "checks"
    assert Path(brief.mount_source) == sandbox_root / "s1" / "verify" / "b7"
    assert checks.readonly is True and brief.readonly is True

    # What the checks container leaves behind is not in the Verifier's mount.
    (Path(checks.mount_source) / "pytest-output.txt").write_text("42 passed\n")
    assert not (Path(brief.mount_source) / "pytest-output.txt").exists()


async def test_profile_for_refuses_verify_without_snapshot(tmp_path, plain):
    space = space_of(str(plain))
    with pytest.raises(ValueError, match="built from a snapshot"):
        await mounts.profile_for("verify", space, "b1", artifact_kind="code")

    ref = write_snapshot(tmp_path, {"report.md": b"x\n"})
    for name in ("worktree", "scratch"):
        with pytest.raises(ValueError, match="not built from a snapshot"):
            await mounts.profile_for(
                name, space, "k", artifact_kind="code", snapshot=ref
            )

    # And the happy path of each dispatch.
    w = await mounts.profile_for("worktree", space, "o1", artifact_kind="code")
    s = await mounts.profile_for("scratch", space, "b1", artifact_kind="code")
    v = await mounts.profile_for(
        "verify", space, "checks", artifact_kind="code", snapshot=ref
    )
    assert (w.name, s.name, v.name) == ("worktree", "scratch", "verify")
    assert w.space == s.space == v.space == "s1"


async def test_root_choice_by_artifact_kind(upstream, plain):
    two = space_of(str(upstream), str(plain))
    assert await mounts.choose_root(two, "code") == str(upstream)
    assert await mounts.choose_root(two, "document") == str(plain)
    assert await mounts.choose_root(two, "message") == str(plain)

    # A space with one root uses it for every kind.
    one = space_of(str(upstream))
    assert await mounts.choose_root(one, "message") == str(upstream)

    # And the kind decides which root the profile actually mounts.
    doc = await mounts.prepare_worktree(two, "o1", artifact_kind="document")
    assert (Path(doc.mount_source) / "brief.md").exists()
    code = await mounts.prepare_worktree(two, "o2", artifact_kind="code")
    assert (Path(code.mount_source) / "README.md").exists()


async def test_explicit_root_wins_and_must_be_in_space_roots(upstream, plain):
    two = space_of(str(upstream), str(plain))
    # The person approved the directory root for code work: it wins over the
    # default by kind.
    chosen = await mounts.prepare_worktree(
        two, "o1", artifact_kind="code", root=str(plain)
    )
    assert (Path(chosen.mount_source) / "brief.md").exists()
    scratch = await mounts.prepare_scratch(
        two, "b1", artifact_kind="document", root=str(upstream)
    )
    assert (Path(scratch.mount_source) / "README.md").exists()

    stray = str(plain.parent / "somewhere-else")
    with pytest.raises(ValueError, match="is not one of space"):
        await mounts.resolve_root(two, stray)
    for name in ("worktree", "scratch"):
        with pytest.raises(ValueError, match="is not one of space"):
            await mounts.profile_for(name, two, "k", artifact_kind="code", root=stray)


def test_profile_functions_are_coroutines():
    """`tree.delegate` awaits these under the objective's lock in the process
    that serves the gateway, so none of them may block the loop."""
    for name in (
        "sandbox_root",
        "resolve_root",
        "choose_root",
        "prepare_worktree",
        "prepare_scratch",
        "prepare_verify",
        "profile_for",
        "paths_for_space",
    ):
        fn = getattr(mounts, name)
        assert inspect.iscoroutinefunction(fn), name


async def test_destroy_never_removes_a_root(plain, tmp_path, sandbox_root, monkeypatch):
    """A scratch profile mounts the root itself, so a destroy that removed its
    mount would delete the person's directory. Only a verify extraction goes."""
    from adapters.apple_container import AppleContainer
    from schemas.sandbox import SandboxHandle

    space = space_of(str(plain))
    ref = write_snapshot(tmp_path, {"report.md": b"the artifact\n"})
    scratch = await mounts.prepare_scratch(space, "b1", artifact_kind="document")
    verify = await mounts.prepare_verify(space, "checks", ref)

    c = AppleContainer(sandbox_root=sandbox_root)

    async def gone(*args, timeout=None):
        from infra.sandbox.proc import Completed

        return Completed(0, b"", b"", 1, False)

    monkeypatch.setattr(c, "_cli", gone)
    monkeypatch.setattr(c, "_state", lambda name: asyncio.sleep(0, result=None))

    for profile in (scratch, verify):
        await c.destroy(
            SandboxHandle(
                id=f"cori-{profile.name}-{profile.key}-abc123",
                profile=profile,
                created_at=datetime.now(UTC),
            )
        )

    assert plain.is_dir() and (plain / "brief.md").exists()
    assert not Path(verify.mount_source).exists()
    # The space's own directories survive a destroy; they go with the space.
    assert (await mounts.paths_for_space("s1"))[0] == sandbox_root / "s1"


async def test_failed_scrub_removes_the_mirror(upstream, sandbox_root, monkeypatch):
    """`git clone` writes the tokenized URL into the mirror's config, so the
    scrub that removes it is the only thing between a GitHub token and the
    person's disk. A scrub that fails takes the mirror with it."""
    space = space_of(str(upstream))
    real = mounts.run

    async def flaky(*args, **kwargs):
        if "set-url" in args:
            from infra.sandbox.proc import Completed

            return Completed(1, b"", b"the runtime is busy", 5, False)
        return await real(*args, **kwargs)

    monkeypatch.setattr(mounts, "run", flaky)
    with pytest.raises(mounts.RootError, match="kept a credential"):
        await mounts.resolve_root(space, str(upstream))

    mirror = sandbox_root / "s1" / "repos" / upstream.parent.name / "psyoptimal.git"
    assert not mirror.exists(), "a mirror that may hold a token stayed on disk"

    # With the scrub working again, the next resolve clones it afresh.
    monkeypatch.setattr(mounts, "run", real)
    assert await mounts.resolve_root(space, str(upstream)) == mirror
    assert "x-access-token" not in (mirror / "config").read_text()


async def test_a_mirror_holding_a_credential_is_removed(upstream, sandbox_root):
    """The check runs on every resolve, not only the one that clones, so a
    mirror an earlier call left tokenized does not sit there forever."""
    space = space_of(str(upstream))
    mirror = await mounts.resolve_root(space, str(upstream))
    config = mirror / "config"
    config.write_text(
        config.read_text().replace(
            f"url = {upstream}",
            "url = https://x-access-token:ghs_notarealtoken@github.com/o/n.git",
        )
    )
    assert mounts._config_holds_credentials(mirror) is True

    with pytest.raises(mounts.RootError, match="kept a credential"):
        await mounts.resolve_root(space, str(upstream))
    assert not mirror.exists()

    again = await mounts.resolve_root(space, str(upstream))
    assert again == mirror and not mounts._config_holds_credentials(mirror)


def test_clone_url_carries_the_token_only_for_github(monkeypatch):
    monkeypatch.setattr(mounts, "read_secret", lambda name: "ghs_notarealtoken")
    tokenized = mounts._clone_url("https://github.com/yudame/cori.git")
    assert tokenized == (
        "https://x-access-token:ghs_notarealtoken@github.com/yudame/cori.git"
    )
    # A path root is its own origin and no secret is read for it.
    monkeypatch.setattr(
        mounts, "read_secret", lambda name: pytest.fail("a path root read a secret")
    )
    assert mounts._clone_url("/Users/person/psyoptimal") == "/Users/person/psyoptimal"

    # And that URL in a config is what the check refuses, redacted when it
    # reaches a message.
    assert mounts._redact(f"fatal: could not read {tokenized}") == (
        "fatal: could not read https://github.com/yudame/cori.git"
    )
