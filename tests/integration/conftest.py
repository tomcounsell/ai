# Feature markers are auto-applied by the root tests/conftest.py

import pytest

#: Test modules whose rows write ``drill_log`` bytes through the verifying
#: artifact store; each gets a throwaway retention root instead of the
#: production ``~/.popoto/improvement_content`` (#3218).
_RETENTION_ROOT_MODULES = ("test_improvement_release_",)


@pytest.fixture(autouse=True)
def _improvement_retention_root(request, tmp_path, monkeypatch):
    """Point the improvement artifact store at ``tmp_path`` for the release tests.

    ``POPOTO_IMPROVEMENT_CONTENT_PATH`` covers a CLI child process, which
    builds its own store from the environment; the in-process singleton was
    built at import, so its ``base_path`` is redirected directly.
    """
    if not request.node.fspath.basename.startswith(_RETENTION_ROOT_MODULES):
        yield None
        return
    from models.verifying_artifact_store import verifying_artifact_store

    root = tmp_path / "improvement_content"
    root.mkdir()
    monkeypatch.setenv("POPOTO_IMPROVEMENT_CONTENT_PATH", str(root))
    monkeypatch.setattr(verifying_artifact_store, "base_path", str(root))
    yield root
