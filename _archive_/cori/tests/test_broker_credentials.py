"""The credential table is keyed by the pair, and the manifest reader is the
one door to a space's ceiling and targets. Plan 07 task 4; tech stack §7.

No test here reads a secret value. The table names Keychain entries, so what
these assert is the shape of the key and the refusal, which is what keeps one
space from using another's token on a shared target (blind-spot finding 9).
"""

import pytest

from broker.credentials import CREDENTIALS, credential_for
from broker.errors import EffectRefused
from broker.manifest import space_for

GITHUB = "github.com/yudame"
GMAIL = "mailto:tom@yuda.me"


def test_the_m0_pairs_resolve_to_their_keychain_names():
    github = credential_for("psyoptimal", GITHUB)
    assert github.names == ("github_token",)
    assert (github.space, github.target) == ("psyoptimal", GITHUB)
    assert credential_for("psyoptimal", GMAIL).names == (
        "google_oauth_client_id",
        "google_oauth_client_secret",
        "gmail_refresh_token",
    )


def test_another_space_on_the_same_target_is_refused():
    with pytest.raises(EffectRefused) as info:
        credential_for("other", GITHUB)
    assert GITHUB in str(info.value)


def test_the_same_space_on_an_unlisted_target_is_refused():
    with pytest.raises(EffectRefused):
        credential_for("psyoptimal", "mailto:someone@example.com")


def test_no_table_value_is_a_secret_value():
    """The table holds names. A value would be a secret in the source tree."""
    for names in CREDENTIALS.values():
        for name in names:
            assert name.islower() and " " not in name


def test_a_credential_names_only_its_own_secrets():
    with pytest.raises(EffectRefused):
        credential_for("psyoptimal", GITHUB).value("gmail_refresh_token")


def test_the_manifest_carries_both_m0_targets():
    space = space_for("psyoptimal")
    assert space.id == "psyoptimal"
    assert GITHUB in space.allowed_targets
    assert GMAIL in space.allowed_targets
    assert space.max_effect_class == "propose"


def test_a_space_with_no_manifest_is_refused():
    with pytest.raises(EffectRefused) as info:
        space_for("no-such-space")
    assert "no-such-space" in str(info.value)


def test_every_credential_target_is_allowed_by_its_space():
    """A row for a target the manifest does not list would be a credential no
    check can reach, and a quiet contradiction between the two files."""
    for space_id, target in CREDENTIALS:
        assert target in space_for(space_id).allowed_targets
