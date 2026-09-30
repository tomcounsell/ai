"""Staleness checks for the generated site knowledge graph (#2531).

``site/assets/graph.js`` is a generated artifact with no in-repo generator,
so it drifts silently as the codebase moves. Two drift modes have shipped to
visitors already:

- Stale claims: the graph described ``ui/app.py`` as Flask long after the
  dashboard migrated to FastAPI.
- Missing nodes: a ``data-files`` chip on the public site referenced
  ``agent/pid_fence.py``, which had no graph node, so the chip silently
  dropped it from its file count.

These tests pin the cheap invariants that catch both modes at PR time:
every file the site's chips reference must resolve to a graph node, and
every framework the graph names must still be a declared dependency.
(A stricter invariant — every file node points at an existing file — is
already violated by seven phantom nodes for since-deleted files; pruning
them and their layer/tour references is a separate cleanup, so that check
is deliberately not asserted here.)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GRAPH_PATH = REPO_ROOT / "site" / "assets" / "graph.js"
SITE_DIR = REPO_ROOT / "site"


@pytest.fixture(scope="module")
def graph() -> dict:
    """Parse window.VALOR_GRAPH out of the generated graph.js."""
    raw = GRAPH_PATH.read_text(encoding="utf-8")
    _, _, payload = raw.partition("=")
    return json.loads(payload.strip().rstrip(";"))


def _chip_file_refs() -> set[str]:
    """Every ``file:...`` reference in a data-files chip across site/*.html."""
    refs: set[str] = set()
    for page in SITE_DIR.glob("*.html"):
        for match in re.finditer(r'data-files="([^"]*)"', page.read_text(encoding="utf-8")):
            for item in match.group(1).split(","):
                item = item.strip()
                if item.startswith("file:"):
                    refs.add(item[len("file:") :])
    return refs


def test_every_chip_reference_resolves_to_a_graph_node(graph):
    """A file the site links via a chip must have a node in the graph —
    otherwise the chip silently drops it from its rendered file count."""
    node_ids = {node["id"] for node in graph["nodes"]}
    refs = _chip_file_refs()
    assert refs, "no data-files chips found — extraction regex is broken"
    missing = sorted(ref for ref in refs if f"file:{ref}" not in node_ids)
    assert not missing, (
        f"data-files chips reference files with no graph node: {missing}. "
        "Add nodes to site/assets/graph.js (or regenerate it) so the chips render."
    )


def test_graph_frameworks_are_declared_dependencies(graph):
    """Every framework the graph claims must appear in pyproject.toml.
    Catches the class of drift where the graph kept describing the
    dashboard as Flask after the FastAPI migration."""
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()
    stale = [fw for fw in graph["project"]["frameworks"] if fw.lower() not in pyproject]
    assert not stale, (
        f"graph.js names frameworks absent from pyproject.toml: {stale}. "
        "The graph has drifted from the codebase."
    )
