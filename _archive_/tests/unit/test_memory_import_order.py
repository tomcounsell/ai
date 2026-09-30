"""Import-order regression tests for the apply_defaults embedding-provider cycle.

Issue #3310: any process whose first import is ``models.memory`` silently ran
with no embedding provider for its entire lifetime, because
``config/memory_defaults.py::apply_defaults()`` swallowed the ImportError
raised when the ``agent`` package init reached back into the half-initialized
``models.memory`` module. These tests lock the fix against reintroduction.

The import-order probes run in subprocesses because import order is
process-global state: once ``models.memory`` or ``agent`` is imported in this
test process, the condition under test no longer exists here.

Both provider probes require an ``OPENAI_API_KEY`` reachable via the
environment or the repo ``.env`` fallback (the same resolution
``configure_embedding_provider()`` performs). When no key resolves, the
provider is legitimately unset and the probes skip gracefully instead of
failing.
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _key_resolves() -> bool:
    """Mirror the key resolution configure_embedding_provider() performs."""
    if os.getenv("OPENAI_API_KEY"):
        return True
    try:
        from dotenv import dotenv_values

        return bool((dotenv_values(REPO_ROOT / ".env") or {}).get("OPENAI_API_KEY"))
    except Exception:
        return False


def _run_first_import(first_import: str) -> subprocess.CompletedProcess:
    code = (
        f"import {first_import}; "
        "from popoto.fields.embedding_field import get_default_provider; "
        "print(type(get_default_provider()).__name__)"
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=180,
    )


class TestMemoryImportOrder(unittest.TestCase):
    def test_models_memory_first_configures_provider(self):
        """Fresh process importing models.memory first gets the provider."""
        if not _key_resolves():
            self.skipTest("OPENAI_API_KEY not resolvable; providerless operation is intended")
        proc = _run_first_import("models.memory")
        self.assertEqual(proc.returncode, 0, f"probe failed: {proc.stderr[-2000:]}")
        self.assertIn("OpenAIProvider", proc.stdout.strip())

    def test_agent_first_still_configures_provider(self):
        """Reverse order keeps working after the fix."""
        if not _key_resolves():
            self.skipTest("OPENAI_API_KEY not resolvable; providerless operation is intended")
        proc = _run_first_import("agent, models.memory")
        self.assertEqual(proc.returncode, 0, f"probe failed: {proc.stderr[-2000:]}")
        self.assertIn("OpenAIProvider", proc.stdout.strip())

    def test_provider_failure_is_logged_not_swallowed(self):
        """A genuine configuration failure emits a log record."""
        from unittest.mock import patch

        from config import memory_defaults

        with patch(
            "agent.embedding_provider.configure_embedding_provider",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertLogs("config.memory_defaults", level="WARNING") as captured:
                memory_defaults.apply_defaults()
        self.assertTrue(
            any("embedding" in message.lower() for message in captured.output),
            f"expected an embedding-provider warning, got: {captured.output}",
        )


if __name__ == "__main__":
    unittest.main()
