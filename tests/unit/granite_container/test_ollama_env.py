"""Deterministic unit coverage for the shared ollama-substrate env helper.

These tests carry the load-bearing blocker-fix contract (plan critique
BLOCKER / constraint 2) as an ALWAYS-ON assertion — no ollama, no model, no
spawn required:

  * ``build_ollama_child_env`` sets the three ollama vars AND pops
    ``CLAUDE_CODE_OAUTH_TOKEN`` (the PR #1612 reproduction credential).
  * ``assert_no_oauth_leak`` raises when a token survives and passes when it
    does not — the pre-``spawn()`` guard both Substrate B and the golden
    recorder run on the assembled child env.
  * ``pick_ollama_model`` drops the tool-incapable ``gemma*`` chat tags (which
    return HTTP 400 "does not support tools" to the claude binary — observed
    live in Task 0).
"""

from __future__ import annotations

import unittest

from tests.granite_faults.ollama_env import (
    OAUTH_TOKEN_VAR,
    OLLAMA_BASE_URL,
    assert_no_oauth_leak,
    build_ollama_child_env,
    pick_ollama_model,
)


class TestBuildOllamaChildEnv(unittest.TestCase):
    def test_sets_three_ollama_vars(self) -> None:
        env = build_ollama_child_env(base={"PATH": "/usr/bin"})
        self.assertEqual(env["ANTHROPIC_BASE_URL"], OLLAMA_BASE_URL)
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "ollama")
        self.assertEqual(env["ANTHROPIC_API_KEY"], "")
        # The base env is preserved apart from the injected keys.
        self.assertEqual(env["PATH"], "/usr/bin")

    def test_pops_forwarded_oauth_token(self) -> None:
        """The blocker fix: a forwarded OAuth token must NOT survive.

        ``env.update()`` overlays never remove a key, so if the token is not
        popped here it coexists with the ollama base URL and reproduces the
        PR #1612 'issue with the selected model' failure.
        """
        base = {"PATH": "/usr/bin", OAUTH_TOKEN_VAR: "sk-oauth-live-token"}
        env = build_ollama_child_env(base=base)
        self.assertNotIn(OAUTH_TOKEN_VAR, env)

    def test_missing_token_is_a_noop_pop(self) -> None:
        env = build_ollama_child_env(base={"PATH": "/usr/bin"})
        self.assertNotIn(OAUTH_TOKEN_VAR, env)


class TestAssertNoOauthLeak(unittest.TestCase):
    def test_raises_when_token_present(self) -> None:
        with self.assertRaises(AssertionError):
            assert_no_oauth_leak({OAUTH_TOKEN_VAR: "leaked"})

    def test_passes_on_clean_env(self) -> None:
        # Must not raise.
        assert_no_oauth_leak(build_ollama_child_env(base={"PATH": "/usr/bin"}))

    def test_build_then_assert_is_the_spawn_contract(self) -> None:
        """The exact pre-spawn contract: build with a live token, assert clean."""
        env = build_ollama_child_env(base={"PATH": "/usr/bin", OAUTH_TOKEN_VAR: "sk-oauth-live"})
        assert_no_oauth_leak(env)  # would raise if the pop regressed


class TestPickOllamaModel(unittest.TestCase):
    def test_prefers_qwen_over_gpt_oss(self) -> None:
        names = ["gpt-oss:20b", "qwen3.6:35b-a3b-coding-nvfp4"]
        self.assertEqual(pick_ollama_model(names), "qwen3.6:35b-a3b-coding-nvfp4")

    def test_drops_tool_incapable_gemma_tags(self) -> None:
        """gemma/embedding tags cannot carry tools → never picked for claude."""
        names = ["gemma3:27b", "gemma3n:latest", "nomic-embed-text:latest"]
        self.assertIsNone(pick_ollama_model(names))

    def test_qwen_only_no_fallback_until_ornith(self) -> None:
        """Pinned to qwen: non-qwen tool-capable tags (gpt-oss, granite) are NOT
        picked — a clean self-skip beats running the canary on an inappropriate
        backend. Revisit when the ``ornith`` model ships."""
        self.assertIsNone(pick_ollama_model(["gpt-oss:20b", "granite4.1:3b"]))
        self.assertIsNone(pick_ollama_model(["gemma3:27b", "some-other-instruct:latest"]))

    def test_empty_returns_none(self) -> None:
        self.assertIsNone(pick_ollama_model([]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
