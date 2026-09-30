import subprocess
import sys

import pytest

from infra.secrets import MissingSecret, load_secrets, read_secret

darwin = pytest.mark.skipif(sys.platform != "darwin", reason="needs the macOS Keychain")


def test_missing_secret_fails_loudly_with_its_name(monkeypatch):
    def absent(*a, **k):
        return subprocess.CompletedProcess(a, 44, stdout="", stderr="not found")

    monkeypatch.setattr(subprocess, "run", absent)
    with pytest.raises(MissingSecret) as e:
        load_secrets(["present_or_not", "nope"])
    assert e.value.name == "present_or_not"
    assert "nope" not in str(e.value) and "present_or_not" in str(e.value)


@darwin
def test_missing_secret_against_the_real_keychain():
    with pytest.raises(MissingSecret):
        read_secret("cori-test-secret-that-does-not-exist")


@darwin
def test_anthropic_key_is_in_the_keychain():
    try:
        key = read_secret("anthropic_api_key")
    except MissingSecret:
        pytest.skip("anthropic_api_key is not stored on this machine")
    assert key.startswith("sk-ant-")
