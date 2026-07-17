"""Unit tests for the sanitized spawn diagnostics + early-exit classifier
(issue #2100).

Covers ``agent/session_runner/harness/claude_diagnostics.py``:

- ``describe_claude_binary`` — version-named realpath attribution, normal
  basename, and binary-not-found.
- ``describe_auth_mode`` — oauth / api_key / unknown by presence only.
- ``trust_env_presence`` — present-with-value vs absent.
- ``build_spawn_diagnostic`` — never leaks the prompt or any secret value.
- ``classify_harness_early_exit`` — the full precedence ladder, including the
  load-bearing TLS-wins-over-auth contract.
- ``HARNESS_TLS_CONSECUTIVE_SUPPRESS`` — int default 2.

All classification is synthetic — no live TLS failure, no network, no Keychain.
"""

from __future__ import annotations

import json

from agent.session_runner.harness.claude_diagnostics import (
    HARNESS_TLS_CONSECUTIVE_SUPPRESS,
    HarnessExitClass,
    build_spawn_diagnostic,
    classify_harness_early_exit,
    describe_auth_mode,
    describe_claude_binary,
    trust_env_presence,
)

# --- describe_claude_binary ---------------------------------------------------


class TestDescribeClaudeBinary:
    def test_version_named_realpath_attributes_to_claude_code(self, monkeypatch):
        """A ``/…/versions/2.1.202`` realpath renders as 'Claude Code CLI 2.1.202'."""
        monkeypatch.setattr(
            "agent.session_runner.harness.claude_diagnostics.shutil.which",
            lambda cmd: "/Users/x/.local/bin/claude",
        )
        monkeypatch.setattr(
            "agent.session_runner.harness.claude_diagnostics.os.path.realpath",
            lambda p: "/Users/x/.local/share/claude/versions/2.1.202",
        )

        out = describe_claude_binary("claude")

        assert out["display"] == "Claude Code CLI 2.1.202"
        assert out["version"] == "2.1.202"
        assert out["basename"] == "2.1.202"
        assert out["which"] == "/Users/x/.local/bin/claude"
        assert out["realpath"] == "/Users/x/.local/share/claude/versions/2.1.202"

    def test_normal_basename_has_no_version(self, monkeypatch):
        """A normal basename (``claude``) → version None, display == basename."""
        monkeypatch.setattr(
            "agent.session_runner.harness.claude_diagnostics.shutil.which",
            lambda cmd: "/usr/local/bin/claude",
        )
        monkeypatch.setattr(
            "agent.session_runner.harness.claude_diagnostics.os.path.realpath",
            lambda p: "/usr/local/bin/claude",
        )

        out = describe_claude_binary("claude")

        assert out["version"] is None
        assert out["display"] == "claude"
        assert out["basename"] == "claude"

    def test_binary_not_found(self, monkeypatch):
        """``which`` returns None → which/realpath None, basename == cmd0."""
        monkeypatch.setattr(
            "agent.session_runner.harness.claude_diagnostics.shutil.which",
            lambda cmd: None,
        )

        out = describe_claude_binary("claude")

        assert out["which"] is None
        assert out["realpath"] is None
        assert out["basename"] == "claude"
        assert out["version"] is None
        assert out["display"] == "claude"


# --- describe_auth_mode -------------------------------------------------------


class TestDescribeAuthMode:
    def test_oauth_present(self):
        assert describe_auth_mode({"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-x"}) == "oauth"

    def test_oauth_precedence_over_api_key(self):
        """OAuth takes precedence — the intended subscription posture."""
        env = {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-x", "ANTHROPIC_API_KEY": "sk-x"}
        assert describe_auth_mode(env) == "oauth"

    def test_api_key_only(self):
        assert describe_auth_mode({"ANTHROPIC_API_KEY": "sk-ant-x"}) == "api_key"

    def test_neither_is_unknown(self):
        assert describe_auth_mode({}) == "unknown"


# --- trust_env_presence -------------------------------------------------------


class TestTrustEnvPresence:
    def test_present_var_reports_value(self):
        out = trust_env_presence({"SSL_CERT_FILE": "/etc/ssl/cert.pem"})
        assert out["SSL_CERT_FILE"] == {"present": True, "value": "/etc/ssl/cert.pem"}

    def test_absent_var_reports_absent(self):
        out = trust_env_presence({})
        assert out["SSL_CERT_FILE"] == {"present": False}
        # Every tracked var is reported, absent ones without a value key.
        assert out["NODE_TLS_REJECT_UNAUTHORIZED"] == {"present": False}

    def test_all_tracked_vars_present(self):
        env = {
            "SSL_CERT_FILE": "/a",
            "SSL_CERT_DIR": "/b",
            "NODE_EXTRA_CA_CERTS": "/c",
            "REQUESTS_CA_BUNDLE": "/d",
            "NODE_TLS_REJECT_UNAUTHORIZED": "0",
        }
        out = trust_env_presence(env)
        for name, val in env.items():
            assert out[name] == {"present": True, "value": val}


# --- build_spawn_diagnostic ---------------------------------------------------


class TestBuildSpawnDiagnostic:
    def test_never_leaks_prompt_or_secret_values(self, monkeypatch):
        """The diagnostic must never contain the prompt or any secret value."""
        monkeypatch.setattr(
            "agent.session_runner.harness.claude_diagnostics.shutil.which",
            lambda cmd: "/usr/local/bin/claude",
        )
        monkeypatch.setattr(
            "agent.session_runner.harness.claude_diagnostics.os.path.realpath",
            lambda p: "/usr/local/bin/claude",
        )

        secret_api = "sk-ant-SECRET-api-key-value-DO-NOT-LEAK"
        secret_oauth = "sk-ant-oat01-SECRET-oauth-token-DO-NOT-LEAK"
        prompt = "PROMPT-BODY-WITH-CONFIDENTIAL-USER-CONTENT"
        cmd = ["claude", "-p", "--model", "opus", prompt]
        env = {
            "ANTHROPIC_API_KEY": secret_api,
            "CLAUDE_CODE_OAUTH_TOKEN": secret_oauth,
        }

        diag = build_spawn_diagnostic(
            cmd=cmd,
            proc_env=env,
            working_dir="/tmp/work",
            session_id="sess-1",
            worker_label="standalone@host",
        )

        blob = json.dumps(diag)
        assert prompt not in blob, "prompt (cmd[-1]) leaked into the diagnostic"
        assert secret_api not in blob, "ANTHROPIC_API_KEY value leaked into the diagnostic"
        assert secret_oauth not in blob, "OAuth token value leaked into the diagnostic"

    def test_reports_auth_mode_presence_only(self, monkeypatch):
        """auth_mode is a mode string (present/absent), never a value."""
        monkeypatch.setattr(
            "agent.session_runner.harness.claude_diagnostics.shutil.which",
            lambda cmd: "/usr/local/bin/claude",
        )
        monkeypatch.setattr(
            "agent.session_runner.harness.claude_diagnostics.os.path.realpath",
            lambda p: "/usr/local/bin/claude",
        )

        with_oauth = build_spawn_diagnostic(
            cmd=["claude", "hi"],
            proc_env={"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-x"},
            working_dir="/tmp",
            session_id="s",
            worker_label="w",
        )
        assert with_oauth["auth_mode"] == "oauth"

        without_auth = build_spawn_diagnostic(
            cmd=["claude", "hi"],
            proc_env={},
            working_dir="/tmp",
            session_id="s",
            worker_label="w",
        )
        assert without_auth["auth_mode"] == "unknown"

    def test_carries_context_fields(self, monkeypatch):
        monkeypatch.setattr(
            "agent.session_runner.harness.claude_diagnostics.shutil.which",
            lambda cmd: None,
        )
        diag = build_spawn_diagnostic(
            cmd=["claude", "hi"],
            proc_env={},
            working_dir="/tmp/work",
            session_id="sess-9",
            worker_label="standalone@host",
        )
        assert diag["worker_label"] == "standalone@host"
        assert diag["session_id"] == "sess-9"
        assert diag["working_dir"] == "/tmp/work"
        assert "binary" in diag
        assert "trust_env" in diag


# --- classify_harness_early_exit ----------------------------------------------


class TestClassifyHarnessEarlyExit:
    def test_normal_completion_returns_none(self):
        assert (
            classify_harness_early_exit(
                returncode=0,
                stderr_snippet="",
                init_seen=True,
                result_event_fired=True,
            )
            is None
        )

    def test_returncode_none_is_binary_missing(self):
        assert (
            classify_harness_early_exit(
                returncode=None,
                stderr_snippet=None,
                init_seen=False,
                result_event_fired=False,
            )
            == HarnessExitClass.BINARY_MISSING
        )

    def test_tls_token_in_stderr_is_tls_trust(self):
        assert (
            classify_harness_early_exit(
                returncode=1,
                stderr_snippet="error: MissingIntermediate anchor not trusted",
                init_seen=True,
                result_event_fired=False,
            )
            == HarnessExitClass.TLS_TRUST
        )

    def test_tls_wins_over_auth_when_both_present(self):
        """TLS-wins precedence is load-bearing (the destructive-dialog class)."""
        result = classify_harness_early_exit(
            returncode=1,
            stderr_snippet="401 unauthorized: certificate verify failed",
            init_seen=True,
            result_event_fired=False,
        )
        assert result == HarnessExitClass.TLS_TRUST

    def test_auth_token_only_is_auth_unavailable(self):
        assert (
            classify_harness_early_exit(
                returncode=1,
                stderr_snippet="401 Unauthorized: invalid api key",
                init_seen=True,
                result_event_fired=False,
            )
            == HarnessExitClass.AUTH_UNAVAILABLE
        )

    def test_nonzero_no_init_no_tokens_is_stale_uuid(self):
        assert (
            classify_harness_early_exit(
                returncode=1,
                stderr_snippet="some unrelated crash",
                init_seen=False,
                result_event_fired=False,
            )
            == HarnessExitClass.STALE_UUID
        )

    def test_nonzero_with_init_no_tokens_is_generic(self):
        assert (
            classify_harness_early_exit(
                returncode=2,
                stderr_snippet="some unrelated crash",
                init_seen=True,
                result_event_fired=False,
            )
            == HarnessExitClass.GENERIC_NONZERO
        )


# --- module constant ----------------------------------------------------------


def test_tls_consecutive_suppress_default_is_int_2():
    assert isinstance(HARNESS_TLS_CONSECUTIVE_SUPPRESS, int)
    assert HARNESS_TLS_CONSECUTIVE_SUPPRESS == 2


# --- stripped_harness_env (env-strip lives with the harness, asserted here too)


def test_stripped_harness_env_pops_all_three_anthropic_vars():
    """All three ANTHROPIC_* vars are popped from the harness proc_env (AC7)."""
    from agent.session_runner.harness.claude import stripped_harness_env

    base = {
        "ANTHROPIC_API_KEY": "sk-x",
        "ANTHROPIC_BASE_URL": "https://proxy.example",
        "ANTHROPIC_AUTH_TOKEN": "tok-x",
        "KEEP_ME": "yes",
    }
    out = stripped_harness_env(base)
    assert "ANTHROPIC_API_KEY" not in out
    assert "ANTHROPIC_BASE_URL" not in out
    assert "ANTHROPIC_AUTH_TOKEN" not in out
    assert out["KEEP_ME"] == "yes"
    # Source dict is not mutated.
    assert "ANTHROPIC_API_KEY" in base
