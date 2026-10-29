"""The real project specs live in the private folder every Mac syncs, not
the public repository, which keeps one made-up example (Tom, 2026-10-29;
docs/plans/private-projects.md)."""

import os
import pathlib

import pytest

from core import intake, workspace
from core import settings as settings_module
from core.settings import settings
from tests import bridges

pytestmark = pytest.mark.spend(usd=0)
EXAMPLE = pathlib.Path(__file__).parent.parent / "projects" / "example.toml"


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


def test_the_repository_holds_only_the_made_up_example():
    assert sorted(p.name for p in EXAMPLE.parent.glob("*.toml")) == ["example.toml"]
    spec = workspace.Spec.load(str(EXAMPLE))
    assert spec.name == "example" and spec.board in spec.chats


@pytest.mark.parametrize("state", ["missing", "unreadable"])
def test_without_its_folder_tom_is_still_heard_and_a_load_names_the_folder(tmp_path, state):
    folder = tmp_path / "projects"
    if state == "unreadable":
        folder.mkdir()
        (folder / "valor.toml").write_text(bridges.VALOR_SPEC)
        folder.chmod(0)
    try:
        with bridges.operator(tmp_path), bridges.configure(projects_dir=str(folder)):
            assert workspace.Spec.all() == []
            with pytest.raises(workspace.Refused, match=str(folder / "valor.toml")):
                workspace.Spec.load("valor")
            assert intake.room("telegram", bridges.OPERATOR) == intake.TOMS_OWN_ROOM
            assert bridges.OPERATOR_CHAT in intake.owned("telegram")
    finally:
        if folder.exists():
            folder.chmod(0o755)


def test_update_chat_is_one_of_the_specs_chats():
    base = {"name": "p", "repo": "r", "kind": "plain", "suite": "true", "chats": ["telegram:-1001"]}
    assert workspace.Spec.from_dict(base | {"update_chat": "telegram:-1001"}).update_chat == "telegram:-1001"
    assert workspace.Spec.from_dict(base).update_chat is None
    with pytest.raises(workspace.Refused, match="update_chat"):
        workspace.Spec.from_dict(base | {"update_chat": "telegram:-1002"})
