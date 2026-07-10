"""Public API contract snapshots (issue #2004 Task 4).

One designated module snapshots ``inspect.signature`` of the public callables
the test suite leans on most heavily. When someone renames a symbol or reshapes
a signature, THIS test fails first with a named, actionable message -- instead
of the 18-scattered-failures blast radius #1958's ``_build_draft_prompt``
rename produced.

Updating a snapshot here is a DELIBERATE act: do it only when the public
signature genuinely changed on purpose, and update the callers in the same
change. If this test surprises you, the rename is the bug, not the snapshot.
"""

from __future__ import annotations

import importlib
import inspect

# {(module, qualname): literal signature string}
#
# The signature strings are the exact ``str(inspect.signature(obj))`` output.
# Annotation quoting differs by module (``from __future__ import annotations``
# stringifies them) -- snapshot literally, do not "clean up" the quoting.
PUBLIC_API_SIGNATURES: dict[tuple[str, str], str] = {
    ("models.agent_session", "AgentSession.create_eng"): (
        "(*, session_id: str, project_key: str, working_dir: str, chat_id: str, "
        "telegram_message_id: int, message_text: str, sender_name: str | None = None, "
        "sender_id: int | None = None, chat_title: str | None = None, "
        "telegram_message_key: str | None = None, **kwargs) -> 'AgentSession'"
    ),
    ("agent.steering", "push_steering_message"): (
        "(session_id: 'str', text: 'str', sender: 'str', is_abort: 'bool' = False, "
        "target_agent: 'str | None' = None, front: 'bool' = False) -> 'None'"
    ),
    ("agent.steering", "pop_all_steering_messages"): "(session_id: 'str') -> 'list[dict]'",
    ("tools.doc_impact_finder", "find_affected_docs"): (
        "(change_summary: 'str', top_n: 'int' = 15, repo_root: 'Path | None' = None) "
        "-> 'tuple[list[AffectedDoc], ImpactFinderMeta]'"
    ),
    ("tools.code_impact_finder", "find_affected_code"): (
        "(change_summary: 'str', top_n: 'int' = 20, repo_root: 'Path | None' = None) "
        "-> 'tuple[list[AffectedCode], ImpactFinderMeta]'"
    ),
    ("scripts._baseline_common", "staleness"): (
        "(envelope: 'ArtifactEnvelope | dict', *, now: 'datetime | None' = None, "
        "commits_behind: 'int | None' = None) -> 'list[str]'"
    ),
    ("scripts._baseline_common", "read_envelope"): "(artifact: 'object') -> 'ArtifactEnvelope'",
    ("scripts.baseline_gate", "compute_gate_verdict"): (
        "(baseline: 'dict', pr_failures: 'set[str]') -> 'dict'"
    ),
    ("bridge.utc", "utc_now"): "() -> datetime.datetime",
}


def _resolve(module_name: str, qualname: str):
    """Import ``module_name`` and walk ``qualname`` (supports ``Class.method``).

    Raises AttributeError/ImportError with the symbol context intact -- a
    missing symbol IS the contract violation this module exists to catch.
    """
    obj = importlib.import_module(module_name)
    for part in qualname.split("."):
        obj = getattr(obj, part)
    return obj


def test_public_api_signatures_are_stable() -> None:
    """Every snapshot symbol resolves and its signature matches exactly.

    Collects ALL violations before asserting so a sweeping refactor reports the
    complete blast radius in one failure, naming each symbol.
    """
    violations: list[str] = []

    for (module_name, qualname), expected in PUBLIC_API_SIGNATURES.items():
        symbol = f"{module_name}.{qualname}"
        try:
            obj = _resolve(module_name, qualname)
        except (ImportError, AttributeError) as exc:
            violations.append(f"{symbol}: no longer importable ({exc})")
            continue

        actual = str(inspect.signature(obj))
        if actual != expected:
            violations.append(f"{symbol}:\n    expected {expected}\n    actual   {actual}")

    assert not violations, (
        "PUBLIC API CONTRACT VIOLATION -- a signature the test suite depends on "
        "changed:\n\n"
        + "\n".join(violations)
        + "\n\nIf this rename/reshape is intentional, update the snapshot in "
        "tests/unit/test_public_api_contract.py DELIBERATELY (and fix every "
        "caller in the same change). If it is not intentional, revert the "
        "rename -- this one failure is standing in for many scattered ones."
    )
