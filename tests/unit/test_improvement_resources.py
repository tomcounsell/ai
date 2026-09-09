"""Tests for the charter §8 resource probe (#3255, lane 2b).

Charter §8 names the resources the loop may use and then says the quiet part:
"These are expected capabilities, not a claim that every credential or
integration currently works. Verify availability before relying on it." The
probe is that verification, and it has two obligations that pull against each
other — report enough to be useful, and never emit a credential.

Two properties carry most of the weight here. ``unknown`` is the honest default
on any uncertainty, because reporting ``absent`` for a resource that exists
sends the next lane out to acquire something already sitting in the vault. And
a credential that reaches the probe must never leave it: this repo runs
``op run --no-masking`` by design, so op's own masking is not a backstop and
the probe's output discipline is the only guard.
"""

from __future__ import annotations

import hashlib
import subprocess

import pytest

from tools.improvement_resources import RESOURCES, STATES, probe

FAKE_CREDENTIAL = "cf-tok-ZZQQ-distinctive-sentinel-9174"

VAULT_LISTING = """[
  {"id": "a1", "title": "Google Workspace (personal)", "vault": {"name": "m-valor"}},
  {"id": "a2", "title": "Google Workspace work account", "vault": {"name": "m-valor"}},
  {"id": "a3", "title": "Virtual debit card", "vault": {"name": "m-valor"}},
  {"id": "a4", "title": "Cloudflare API token", "vault": {"name": "m-valor"}}
]"""


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["op"], returncode=returncode, stdout=stdout, stderr="")


def make_runner(*, listing=VAULT_LISTING, listing_rc=0, credential=FAKE_CREDENTIAL, wrangler=True):
    """A runner that answers each argv shape the probe issues."""

    def runner(argv):
        if argv[0] == "op" and "list" in argv:
            return _completed(listing, listing_rc)
        if argv[0] == "op" and "read" in argv:
            return _completed(credential)
        if argv[0] == "wrangler":
            if not wrangler:
                raise FileNotFoundError("wrangler")
            return _completed("wrangler 3.0.0")
        return _completed("", 1)

    return runner


def _strings(value) -> list[str]:
    """Every string anywhere in a nested structure, at any depth."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)] + [
            s for k in value for s in _strings(k)
        ]
    if isinstance(value, list | tuple):
        return [s for v in value for s in _strings(v)]
    return []


class TestShape:
    def test_every_named_resource_is_classified(self):
        report = probe(runner=make_runner())

        assert set(report) == set(RESOURCES)
        for name, entry in report.items():
            assert entry["state"] in STATES, f"{name} reported {entry['state']!r}"
            assert entry["detail"], f"{name} reported no detail"
            assert set(entry) == {"state", "detail", "fingerprint"}

    def test_a_present_vault_item_is_verified(self):
        report = probe(runner=make_runner())

        assert report["workspace_personal"]["state"] == "verified"
        assert report["cloudflare_account"]["state"] == "verified"

    def test_a_missing_title_is_absent_when_the_listing_was_readable(self):
        report = probe(runner=make_runner(listing='[{"id": "a1", "title": "unrelated"}]'))

        assert report["virtual_debit_card"]["state"] == "absent"


class TestNeverLeaks:
    def test_a_seeded_credential_appears_nowhere_in_the_report(self):
        report = probe(runner=make_runner())

        assert FAKE_CREDENTIAL not in str(report)
        assert all(FAKE_CREDENTIAL not in s for s in _strings(report))

    def test_no_prefix_of_the_credential_is_emitted_either(self):
        report = probe(runner=make_runner())

        prefix = FAKE_CREDENTIAL[:8]
        assert all(prefix not in s for s in _strings(report))

    def test_the_credential_is_reported_as_a_sha256_fingerprint(self):
        report = probe(runner=make_runner())

        expected = "sha256:" + hashlib.sha256(FAKE_CREDENTIAL.encode()).hexdigest()
        assert report["cloudflare_account"]["fingerprint"] == expected

    def test_resources_that_read_no_credential_carry_no_fingerprint(self):
        report = probe(runner=make_runner())

        assert report["workspace_personal"]["fingerprint"] is None


class TestUnknownIsTheHonestDefault:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"listing_rc": 1},
            {"listing": ""},
            {"listing": "not json"},
            {"listing": '{"unexpected": "shape"}'},
        ],
        ids=["non-zero-exit", "empty", "unparseable", "unexpected-shape"],
    )
    def test_an_unusable_listing_is_unknown_never_absent(self, kwargs):
        """Exit 0 alone proves nothing, and `absent` sends a lane shopping."""
        report = probe(runner=make_runner(**kwargs))

        for name in ("workspace_personal", "workspace_work", "virtual_debit_card"):
            assert report[name]["state"] == "unknown", name

    def test_a_runner_that_raises_yields_unknown_rather_than_an_exception(self):
        def exploding(argv):
            raise RuntimeError("op is not authenticated")

        report = probe(runner=exploding)

        assert set(report) == set(RESOURCES)
        assert report["workspace_personal"]["state"] == "unknown"

    def test_a_timeout_is_unknown(self):
        def timing_out(argv):
            raise subprocess.TimeoutExpired(cmd=argv, timeout=15)

        assert probe(runner=timing_out)["cloudflare_account"]["state"] == "unknown"


class TestOneFailureDoesNotBlankTheRest:
    def test_a_dead_vault_still_leaves_the_cli_classified(self):
        report = probe(runner=make_runner(listing_rc=1))

        assert report["workspace_personal"]["state"] == "unknown"
        assert report["cloudflare_cli"]["state"] == "verified"

    def test_a_missing_wrangler_does_not_disturb_the_vault_answers(self):
        report = probe(runner=make_runner(wrangler=False))

        assert report["cloudflare_cli"]["state"] == "absent"
        assert report["workspace_personal"]["state"] == "verified"

    def test_a_failed_fingerprint_leaves_the_account_verified(self):
        """Presence and fingerprint are separate claims; losing one keeps the other."""
        report = probe(runner=make_runner(credential=""))

        assert report["cloudflare_account"]["state"] == "verified"
        assert report["cloudflare_account"]["fingerprint"] is None


class TestNeverRaises:
    @pytest.mark.parametrize(
        "runner",
        [
            lambda argv: (_ for _ in ()).throw(RuntimeError("boom")),
            lambda argv: _completed("", 127),
            lambda argv: None,
        ],
        ids=["raises", "exit-127", "returns-none"],
    )
    def test_probe_returns_a_full_report_whatever_the_runner_does(self, runner):
        report = probe(runner=runner)

        assert set(report) == set(RESOURCES)

    def test_the_default_runner_is_used_when_none_is_given(self):
        """No injection: it shells out for real and still returns a full report."""
        report = probe()

        assert set(report) == set(RESOURCES)
        for entry in report.values():
            assert entry["state"] in STATES
