"""
Tests for `scripts/install/inject_plist_env.py` — the helper that bakes env
vars into launchd plists at install time. Two injection modes:

* Lean (vault NOT on a TCC path): only an operational allowlist
  (VALOR_VAULT_DIR + a handful of vars) is baked in. Plist stays at 0644.
* Full (vault on a TCC path — ~/Desktop, ~/Documents, ~/iCloud Drive): the
  entire .env is baked in and the plist is chmod'd to 0600.
"""

import plistlib
import stat
from pathlib import Path

import pytest

from scripts.install.inject_plist_env import inject


def _write_plist(path: Path, env_vars: dict[str, str] | None = None) -> None:
    """Write a minimal plist with the given EnvironmentVariables."""
    body: dict = {
        "Label": "com.example.test",
        "ProgramArguments": ["/usr/bin/true"],
    }
    if env_vars is not None:
        body["EnvironmentVariables"] = dict(env_vars)
    with open(path, "wb") as f:
        plistlib.dump(body, f)
    # Reset to 0644 so chmod assertions are meaningful.
    path.chmod(0o644)


def _read_plist(path: Path) -> dict:
    with open(path, "rb") as f:
        return plistlib.load(f)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture
def fake_home(monkeypatch, tmp_path):
    """Pin Path.home() so TCC-path checks resolve under tmp_path."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


class TestLeanInjection:
    """Vault on a non-TCC path: only allowlisted vars get injected."""

    def test_lean_injection_drops_non_allowlisted_secrets(self, fake_home, tmp_path):
        plist = tmp_path / "test.plist"
        _write_plist(plist)
        env_file = tmp_path / ".env"
        env_file.write_text(
            "VALOR_VAULT_DIR=/Users/test/.valor\n"
            "VALOR_PROJECT_KEY=valor\n"
            "ANTHROPIC_API_KEY=sk-secret-do-not-bake\n"
            "OPENAI_API_KEY=sk-also-secret\n"
        )
        vault_dir = tmp_path / ".valor"

        injected, secrets_mode = inject(
            plist, env_file, os_environ={}, vault_dir=vault_dir
        )

        assert secrets_mode is False
        env = _read_plist(plist)["EnvironmentVariables"]
        assert "VALOR_VAULT_DIR" in env
        assert "VALOR_PROJECT_KEY" in env
        assert "ANTHROPIC_API_KEY" not in env, "secret leaked into plist on non-TCC vault"
        assert "OPENAI_API_KEY" not in env

    def test_lean_injection_leaves_plist_at_0644(self, fake_home, tmp_path):
        """No secrets baked → no need to tighten permissions."""
        plist = tmp_path / "test.plist"
        _write_plist(plist)
        env_file = tmp_path / ".env"
        env_file.write_text("VALOR_VAULT_DIR=/Users/test/.valor\n")
        vault_dir = tmp_path / ".valor"

        _, secrets_mode = inject(plist, env_file, os_environ={}, vault_dir=vault_dir)

        assert secrets_mode is False
        assert _mode(plist) == 0o644

    def test_lean_injection_allowlist_keys(self, fake_home, tmp_path):
        """All allowlisted operational vars survive the filter."""
        plist = tmp_path / "test.plist"
        _write_plist(plist)
        env_file = tmp_path / ".env"
        env_file.write_text(
            "VALOR_VAULT_DIR=/v\n"
            "VALOR_PROJECT_KEY=valor\n"
            "VALOR_LAUNCHD=1\n"
            "ACTIVE_PROJECTS=valor,other\n"
            "SERVICE_LABEL_PREFIX=com.example\n"
            "PATH=/usr/local/bin\n"
            "HOME=/Users/test\n"
            "SECRET_KEY=should-not-appear\n"
        )
        vault_dir = tmp_path / ".valor"

        inject(plist, env_file, os_environ={}, vault_dir=vault_dir)

        env = _read_plist(plist)["EnvironmentVariables"]
        for k in (
            "VALOR_VAULT_DIR",
            "VALOR_PROJECT_KEY",
            "VALOR_LAUNCHD",
            "ACTIVE_PROJECTS",
            "SERVICE_LABEL_PREFIX",
            "PATH",
            "HOME",
        ):
            assert k in env, f"{k} should be in lean-injection allowlist"
        assert "SECRET_KEY" not in env


class TestFullInjection:
    """Vault on a TCC path: full .env bake-in + chmod 0600."""

    def test_full_injection_bakes_all_secrets(self, fake_home, tmp_path):
        plist = tmp_path / "test.plist"
        _write_plist(plist)
        env_file = tmp_path / ".env"
        env_file.write_text("ANTHROPIC_API_KEY=sk-test-123\nFOO=bar\n")
        vault_dir = tmp_path / "Desktop" / "Valor"

        injected, secrets_mode = inject(
            plist, env_file, os_environ={}, vault_dir=vault_dir
        )

        assert secrets_mode is True
        env = _read_plist(plist)["EnvironmentVariables"]
        assert env["ANTHROPIC_API_KEY"] == "sk-test-123"
        assert env["FOO"] == "bar"

    def test_full_injection_chmods_plist_to_0600(self, fake_home, tmp_path):
        plist = tmp_path / "test.plist"
        _write_plist(plist)
        env_file = tmp_path / ".env"
        env_file.write_text("ANTHROPIC_API_KEY=sk-test-123\n")
        vault_dir = tmp_path / "Desktop" / "Valor"

        _, secrets_mode = inject(plist, env_file, os_environ={}, vault_dir=vault_dir)

        assert secrets_mode is True
        assert _mode(plist) == 0o600, "TCC vault must tighten plist permissions"

    def test_atomic_write_no_world_readable_window(
        self, fake_home, tmp_path, monkeypatch
    ):
        """Plist must never be 0644 during a TCC-mode injection — even momentarily.

        Regression test for the race window between ``plistlib.dump`` and
        ``os.chmod``: a concurrent reader hitting the file in that window
        would see secrets at 0644. The fix is to write to a temp file with
        mode 0600 set at creation, then rename atomically. We verify by
        wrapping ``os.replace`` and checking the mode of the source temp
        file at rename time.
        """
        import os as _os

        from scripts.install import inject_plist_env as _ipe

        plist = tmp_path / "test.plist"
        _write_plist(plist)
        env_file = tmp_path / ".env"
        env_file.write_text("ANTHROPIC_API_KEY=sk-test-123\n")
        vault_dir = tmp_path / "Desktop" / "Valor"

        observed_modes: list[int] = []
        real_replace = _os.replace

        def spy_replace(src, dst):
            observed_modes.append(stat.S_IMODE(_os.stat(src).st_mode))
            return real_replace(src, dst)

        monkeypatch.setattr(_ipe.os, "replace", spy_replace)

        inject(plist, env_file, os_environ={}, vault_dir=vault_dir)

        assert observed_modes, "expected atomic write to go through os.replace"
        assert all(m == 0o600 for m in observed_modes), (
            f"temp file must be 0600 before rename; saw {observed_modes!r}"
        )

    @pytest.mark.parametrize(
        "vault_subpath",
        [
            "Desktop/Valor",
            "Documents/Valor",
            "iCloud Drive/Valor",
            "Library/Mobile Documents/com~apple~CloudDocs/Valor",
            "Library/CloudStorage/Dropbox/Valor",
            "Library/CloudStorage/OneDrive-Personal/Valor",
        ],
    )
    def test_each_tcc_root_triggers_full_mode(self, fake_home, tmp_path, vault_subpath):
        plist = tmp_path / "test.plist"
        _write_plist(plist)
        env_file = tmp_path / ".env"
        env_file.write_text("SECRET=value\n")
        vault_dir = tmp_path / vault_subpath

        _, secrets_mode = inject(plist, env_file, os_environ={}, vault_dir=vault_dir)

        env = _read_plist(plist)["EnvironmentVariables"]
        assert secrets_mode is True
        assert env["SECRET"] == "value"


class TestModeDecision:
    """``vault_dir`` argument and os.environ fallback both feed the TCC check."""

    def test_os_environ_valor_vault_dir_decides_mode(self, fake_home, tmp_path):
        """When --vault-dir not passed, VALOR_VAULT_DIR from os.environ drives the check."""
        plist = tmp_path / "test.plist"
        _write_plist(plist)
        env_file = tmp_path / ".env"
        env_file.write_text("SECRET=x\n")

        _, secrets_mode = inject(
            plist,
            env_file,
            os_environ={"VALOR_VAULT_DIR": str(tmp_path / "Desktop" / "Valor")},
        )
        assert secrets_mode is True

    def test_env_file_parent_used_when_nothing_else(self, fake_home, tmp_path):
        """Fallback inference: vault dir is the .env file's parent."""
        plist = tmp_path / "test.plist"
        _write_plist(plist)
        env_file = tmp_path / "Desktop" / "Valor" / ".env"
        env_file.parent.mkdir(parents=True)
        env_file.write_text("SECRET=x\n")

        _, secrets_mode = inject(plist, env_file, os_environ={})
        assert secrets_mode is True

    def test_default_lean_when_nothing_resolves(self, fake_home, tmp_path):
        """No vault_dir, no env_file, no os_environ.VALOR_VAULT_DIR → lean."""
        plist = tmp_path / "test.plist"
        _write_plist(plist)

        _, secrets_mode = inject(plist, env_file=None, os_environ={})
        assert secrets_mode is False


class TestBehavioralInvariants:
    """Tests that hold across both modes."""

    def test_idempotent_second_call(self, fake_home, tmp_path):
        plist = tmp_path / "test.plist"
        _write_plist(plist)
        vault_dir = tmp_path / ".valor"

        inject(plist, env_file=None, os_environ={"VALOR_VAULT_DIR": "/v"}, vault_dir=vault_dir)
        n2, _ = inject(
            plist, env_file=None, os_environ={"VALOR_VAULT_DIR": "/v"}, vault_dir=vault_dir
        )

        assert n2 == 0, "second call must add zero new keys"
        env = _read_plist(plist)["EnvironmentVariables"]
        assert env == {"VALOR_VAULT_DIR": "/v"}

    def test_existing_plist_keys_preserved(self, fake_home, tmp_path):
        plist = tmp_path / "test.plist"
        _write_plist(plist, {"PATH": "/template/path", "VALOR_LAUNCHD": "1"})
        env_file = tmp_path / ".env"
        env_file.write_text("PATH=/dotenv/path\nVALOR_PROJECT_KEY=hello\n")
        vault_dir = tmp_path / ".valor"

        inject(plist, env_file=env_file, os_environ={"VALOR_VAULT_DIR": "/v"}, vault_dir=vault_dir)

        env = _read_plist(plist)["EnvironmentVariables"]
        assert env["PATH"] == "/template/path", "template PATH must be preserved"
        assert env["VALOR_LAUNCHD"] == "1"
        assert env["VALOR_PROJECT_KEY"] == "hello"
        assert env["VALOR_VAULT_DIR"] == "/v"

    def test_no_env_file_and_no_vault_dir_is_noop(self, fake_home, tmp_path):
        plist = tmp_path / "test.plist"
        _write_plist(plist, {"PATH": "/x"})

        n, _ = inject(plist, env_file=None, os_environ={})

        assert n == 0
        env = _read_plist(plist)["EnvironmentVariables"]
        assert env == {"PATH": "/x"}


@pytest.mark.parametrize(
    "install_script",
    [
        "scripts/install_worker.sh",
        "scripts/install_autoexperiment.sh",
        "scripts/install_nightly_tests.sh",
        "scripts/install_sdlc_reflection.sh",
    ],
)
def test_install_script_invokes_inject_plist_env(install_script):
    """Each install script must invoke the inject_plist_env helper with --vault-dir."""
    text = Path(install_script).read_text()
    assert "inject_plist_env" in text, (
        f"{install_script} must call scripts/install/inject_plist_env.py to bake "
        "VALOR_VAULT_DIR into the generated plist"
    )
    assert "--vault-dir" in text, (
        f"{install_script} must pass --vault-dir so the helper can decide "
        "lean vs full injection mode"
    )
