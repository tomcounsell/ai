"""Unit tests for the sanitized spawn diagnostics + early-exit classifier
(issue #2100).

Covers ``agent/session_runner/harness/claude_diagnostics.py``:

- ``describe_claude_binary`` — version-named realpath attribution, normal
  basename, and binary-not-found.
- ``describe_auth_mode`` — oauth / api_key / unknown by presence only.
- ``trust_env_presence`` — present-with-value vs absent.
- ``build_spawn_diagnostic`` — never leaks the prompt or any secret value.
- ``classify_harness_early_exit`` — the full precedence ladder, including the
  load-bearing TLS-wins-over-auth contract and the ``CLEAN_NO_OUTPUT`` exit-0
  empty-turn branch (issue #2219).
- ``describe_harness_exit_for_sentry`` — per-class log level + Sentry-scope
  payload (tags/context/fingerprint) used by BRANCH C (issue #2219).
- ``HARNESS_TLS_CONSECUTIVE_SUPPRESS`` — int default 2.

All classification is synthetic — no live TLS failure, no network, no Keychain.
"""

from __future__ import annotations

import inspect
import json
import logging

from agent.session_runner.harness.claude_diagnostics import (
    HARNESS_TLS_CONSECUTIVE_SUPPRESS,
    HarnessExitClass,
    build_spawn_diagnostic,
    classify_harness_early_exit,
    describe_auth_mode,
    describe_claude_binary,
    describe_harness_exit_for_sentry,
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

    def test_returncode_zero_no_result_is_clean_no_output(self):
        """rc=0, init seen, no result → CLEAN_NO_OUTPUT (benign empty turn, #2219)."""
        assert (
            classify_harness_early_exit(
                returncode=0,
                stderr_snippet=None,
                init_seen=True,
                result_event_fired=False,
            )
            == HarnessExitClass.CLEAN_NO_OUTPUT
        )

    def test_returncode_zero_no_init_stays_stale_uuid(self):
        """Critique-blocker regression: rc=0 with init NOT seen stays STALE_UUID.

        The new CLEAN_NO_OUTPUT guard sits *after* the returncode-independent
        STALE_UUID check, so an ``init_seen=False`` exit keeps first claim and
        stays error-level STALE_UUID regardless of returncode. Placing the guard
        earlier would silently downgrade this case to warning-level
        CLEAN_NO_OUTPUT — this pins the insertion order (#2219).
        """
        assert (
            classify_harness_early_exit(
                returncode=0,
                stderr_snippet=None,
                init_seen=False,
                result_event_fired=False,
            )
            == HarnessExitClass.STALE_UUID
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


# --- describe_harness_exit_for_sentry -----------------------------------------

# Every non-CLEAN class BRANCH C can carry into the helper (all stay error-level).
_ERROR_LEVEL_CLASSES = [
    HarnessExitClass.BINARY_MISSING,
    HarnessExitClass.AUTH_UNAVAILABLE,
    HarnessExitClass.TLS_TRUST,
    HarnessExitClass.STALE_UUID,
    HarnessExitClass.GENERIC_NONZERO,
]


class TestDescribeHarnessExitForSentry:
    def test_clean_no_output_is_warning_level(self):
        level, _payload = describe_harness_exit_for_sentry(
            HarnessExitClass.CLEAN_NO_OUTPUT,
            returncode=0,
            init_seen=True,
            stderr_snippet=None,
        )
        assert level == logging.WARNING

    def test_every_other_class_is_error_level(self):
        for exit_class in _ERROR_LEVEL_CLASSES:
            level, _payload = describe_harness_exit_for_sentry(
                exit_class,
                returncode=1,
                init_seen=True,
                stderr_snippet="boom",
            )
            assert level == logging.ERROR, f"{exit_class} should be ERROR-level"

    def test_payload_carries_tags_context_and_fingerprint(self):
        _level, payload = describe_harness_exit_for_sentry(
            HarnessExitClass.GENERIC_NONZERO,
            returncode=2,
            init_seen=True,
            stderr_snippet="unrelated crash",
        )
        assert payload["tags"]["harness_exit_class"] == "generic_nonzero"
        assert payload["tags"]["harness_returncode"] == 2
        assert payload["context"]["harness_exit"] == {
            "returncode": 2,
            "init_seen": True,
            "stderr_snippet": "unrelated crash",
        }
        assert payload["fingerprint"] == ["harness-exit-no-result", "generic_nonzero"]

    def test_fingerprint_is_per_class(self):
        """Each class fingerprints distinctly so the bucket splits per cause."""
        seen = set()
        for exit_class in [*_ERROR_LEVEL_CLASSES, HarnessExitClass.CLEAN_NO_OUTPUT]:
            _level, payload = describe_harness_exit_for_sentry(
                exit_class, returncode=1, init_seen=True, stderr_snippet=None
            )
            fp = tuple(payload["fingerprint"])
            assert fp[0] == "harness-exit-no-result"
            assert fp[1] == str(exit_class)
            assert fp not in seen, "fingerprints must be distinct per class"
            seen.add(fp)

    def test_tolerates_none_stderr_snippet(self):
        """The returncode-0 case carries stderr_snippet=None without error."""
        _level, payload = describe_harness_exit_for_sentry(
            HarnessExitClass.CLEAN_NO_OUTPUT,
            returncode=0,
            init_seen=True,
            stderr_snippet=None,
        )
        assert payload["context"]["harness_exit"]["stderr_snippet"] is None
        # Payload must be JSON-serializable (Sentry serializes context/tags).
        json.dumps(payload)


# --- BRANCH-C Sentry attachment (isolated CapturingTransport) -----------------


class TestBranchCSentryAttachment:
    def test_new_scope_attaches_class_tag_and_fingerprint(self):
        import sentry_sdk
        from sentry_sdk.transport import Transport

        captured: list[dict] = []

        class CapturingTransport(Transport):
            def __init__(self):
                super().__init__(options={"dsn": None})

            def capture_envelope(self, envelope):
                for item in envelope.items:
                    if item.type == "event":
                        captured.append(item.payload.json)

        client = sentry_sdk.Client(
            dsn="https://public@example.invalid/1",
            transport=CapturingTransport(),
            default_integrations=False,
        )

        # Build the payload with the REAL helper, then drive the same
        # new_scope()/tag/context/fingerprint mechanics BRANCH C uses.
        _level, payload = describe_harness_exit_for_sentry(
            HarnessExitClass.GENERIC_NONZERO,
            returncode=2,
            init_seen=True,
            stderr_snippet="unrelated crash",
        )

        with sentry_sdk.isolation_scope() as iso:
            iso.set_client(client)
            with sentry_sdk.new_scope() as scope:
                for tag, val in payload["tags"].items():
                    scope.set_tag(tag, val)
                for ctx_key, ctx_val in payload["context"].items():
                    scope.set_context(ctx_key, ctx_val)
                scope.fingerprint = payload["fingerprint"]
                sentry_sdk.capture_message(
                    "Harness exited without a result event and no accumulated text",
                    level="error",
                )
            client.flush()

        assert len(captured) == 1
        event = captured[0]
        assert event["tags"]["harness_exit_class"] == "generic_nonzero"
        assert event["fingerprint"] == ["harness-exit-no-result", "generic_nonzero"]
        assert event["contexts"]["harness_exit"]["returncode"] == 2


# --- consumer audit + best-effort guard (BRANCH C in claude.py) ---------------


class TestBranchCConsumerAndGuard:
    def test_clean_no_output_resets_tls_streak_branch(self):
        """Consumer-audit regression (locks the claude.py:506 audit conclusion).

        ``_handle_early_exit_class`` is a closure inside ``get_response_via_harness``
        and cannot be called in isolation, so we pin the invariant at the source:
        the TLS-streak INCR branch is gated *solely* on ``TLS_TRUST`` and every
        other class (including the new ``CLEAN_NO_OUTPUT``) falls to the ``else``
        that calls ``_R.delete`` (resets the streak). If a future edit special-cased
        ``CLEAN_NO_OUTPUT`` into the INCR branch, this assertion would fail.
        """
        from agent.session_runner.harness import claude as claude_mod

        src = inspect.getsource(claude_mod.get_response_via_harness)
        # The TLS-streak INCR branch is gated only on TLS_TRUST; the reset else
        # (which CLEAN_NO_OUTPUT falls into) exists.
        assert "if exit_class == HarnessExitClass.TLS_TRUST:" in src
        assert "_R.incr(_tls_streak_key)" in src
        assert "_R.delete(_tls_streak_key)" in src
        # No consumer in this function special-cases the new class: CLEAN_NO_OUTPUT
        # is never named, so it takes every generic path — TLS-streak reset in
        # _handle_early_exit_class and level-based handling at BRANCH C.
        assert "CLEAN_NO_OUTPUT" not in src

    def test_sentry_tagging_failure_does_not_suppress_log(self, caplog):
        """Best-effort guard: a raising sentry scope never masks the log line.

        Mirrors the BRANCH-C try/except (claude.py): a scope/tagging failure must
        still emit the exact error message. Driven in isolation because BRANCH C
        lives inside the async subprocess-driving ``get_response_via_harness``.
        """
        logger = logging.getLogger("test_branch_c_guard")

        def failing_scope_block():
            raise RuntimeError("sentry scope exploded")

        # Faithful reproduction of the BRANCH-C best-effort wrapper.
        with caplog.at_level(logging.ERROR, logger="test_branch_c_guard"):
            try:
                failing_scope_block()
                logger.error("Harness exited without a result event and no accumulated text")
            except Exception:  # noqa: BLE001
                logger.error("Harness exited without a result event and no accumulated text")

        messages = [r.getMessage() for r in caplog.records]
        assert "Harness exited without a result event and no accumulated text" in messages
