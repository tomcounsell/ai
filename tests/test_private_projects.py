"""The real project specs live in the private folder every Mac syncs, not
the public repository (Tom, 2026-10-29; docs/plans/private-projects.md).
While the Macs move, the default folder falls back to the repository's
`projects/` for a name it lacks or cannot list; a configured folder never
does."""

import os
import pathlib

import pytest

from core import intake, workspace
from core import settings as settings_module
from core.settings import settings
from tests import bridges

pytestmark = pytest.mark.spend(usd=0)


def write(folder: pathlib.Path, name: str, extra: str = "") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.toml").write_text(
        f'name = "{name}"\nrepo = "{folder.name}/{name}"\nkind = "plain"\nsuite = "true"\n{extra}'
    )


@pytest.fixture
def private(tmp_path, monkeypatch):
    """The default folder (private) and the repository's (repo), both made up."""
    private, repo = tmp_path / "private", tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(workspace, "PRIVATE_PROJECTS", str(private))
    monkeypatch.setattr(workspace, "REPO_PROJECTS", repo)
    with bridges.configure(projects_dir=str(private), machine="m-here", default_machine="m-here"):
        yield private, repo


def test_the_default_is_the_private_folder_and_the_env_wins(monkeypatch):
    monkeypatch.delenv("VALOR_PROJECTS")
    assert settings_module.Settings().projects_dir == str(
        pathlib.Path.home() / "Desktop" / "Valor" / "projects"
    )
    monkeypatch.setenv("VALOR_PROJECTS", "/elsewhere")
    assert settings_module.Settings().projects_dir == "/elsewhere"


def test_the_suite_reads_a_folder_of_its_own():
    assert settings.projects_dir == os.environ["VALOR_PROJECTS"]
    assert not settings.projects_dir.startswith(str(pathlib.Path.home() / "Desktop"))


def test_the_private_copy_wins_and_the_repo_fills_what_it_lacks(private):
    folder, repo = private
    write(folder, "both")
    write(repo, "both")
    write(repo, "only-repo")
    assert workspace.Spec.load("both").repo == "private/both"
    assert workspace.Spec.load("only-repo").repo == "repo/only-repo"
    assert {(s.name, s.repo) for s in workspace.Spec.all()} == {
        ("both", "private/both"),
        ("only-repo", "repo/only-repo"),
    }


def test_a_missing_or_unlistable_private_folder_serves_the_repo(private):
    folder, repo = private
    write(repo, "kept")
    assert [s.repo for s in workspace.Spec.all()] == ["repo/kept"]  # never synced
    write(folder, "kept")
    folder.chmod(0)
    try:
        assert [s.repo for s in workspace.Spec.all()] == ["repo/kept"]
        assert workspace.Spec.load("kept").repo == "repo/kept"
    finally:
        folder.chmod(0o755)


def test_a_configured_folder_sees_no_repo_spec(private, tmp_path):
    _, repo = private
    write(repo, "only-repo")
    with bridges.configure(projects_dir=str(tmp_path / "configured")):
        assert workspace.Spec.all() == []
        with pytest.raises(workspace.Refused, match="configured/only-repo.toml"):
            workspace.Spec.load("only-repo")


def test_intake_serves_a_chat_listed_only_in_the_private_copy_on_its_machine(private):
    folder, repo = private
    write(repo, "team", 'chats = ["telegram:-100"]\n')
    write(
        folder,
        "team",
        'chats = ["telegram:-100", "telegram:-200"]\nrooms."telegram:-200" = "The new room."\n',
    )
    write(folder, "far", 'machine = "m-far"\nchats = ["telegram:-300"]\nrooms."telegram:-300" = "Far."\n')
    assert intake.room("telegram", "-200") == "The new room."
    assert intake.room("telegram", "-300") is None  # another machine's
