"""Verify mcp_reflections._expected_entry parity with mcp_memory's shape."""

from __future__ import annotations

from scripts.update import mcp_memory, mcp_reflections


def test_expected_entry_has_4_fields():
    expected = mcp_reflections._expected_entry("/tmp/repo")
    assert set(expected.keys()) == {"type", "command", "args", "env"}


def test_args_includes_reflections_module():
    expected = mcp_reflections._expected_entry("/tmp/repo")
    assert "mcp_servers.reflections_server" in expected["args"]


def test_parity_with_memory_shape():
    mem = mcp_memory._expected_entry("/tmp/repo")
    refl = mcp_reflections._expected_entry("/tmp/repo")
    assert set(mem.keys()) == set(refl.keys())
    assert mem["type"] == refl["type"]
    assert mem["command"] == refl["command"]
    assert set(mem["env"].keys()) == set(refl["env"].keys())


def test_validate_mcp_entry_4_fields():
    expected = mcp_reflections._expected_entry("/tmp/repo")
    # Exact match → True
    assert mcp_reflections._validate_mcp_entry(expected, expected) is True
    # Missing type
    bad = dict(expected)
    bad["type"] = "wrong"
    assert mcp_reflections._validate_mcp_entry(bad, expected) is False
    # Wrong env
    bad2 = dict(expected, env={"PYTHONPATH": "/elsewhere"})
    assert mcp_reflections._validate_mcp_entry(bad2, expected) is False
    # Wrong args
    bad3 = dict(expected, args=["-m", "wrong.module"])
    assert mcp_reflections._validate_mcp_entry(bad3, expected) is False
