"""Guard (#3588): reflections never write to human chat through a side door.

Reflections hand structured findings to agent sessions via
``reflections.agent_handoff.hand_off``; the persona layer speaks. This AST
walk fails on any code (never docstrings or comments) under ``reflections/``,
``scripts/`` and ``tools/improvement.py`` that

* names ``valor-telegram`` or ``tools.valor_telegram`` in a string constant
  outside a docstring (a subprocess argv element, a ``-m`` target),
* imports ``tools.valor_telegram``, or
* defines, imports, or calls a ``send_*telegram*`` helper.
"""

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
SCAN_ROOTS = ("reflections", "scripts", "tools/improvement.py")
_SENDER_NAME = re.compile(r"^_?send_\w*telegram\w*$")
_SIDE_DOOR_STRING = re.compile(r"valor-telegram|tools\.valor_telegram")


def _py_files(root: Path) -> list[Path]:
    targets = []
    for rel in SCAN_ROOTS:
        p = root / rel
        if p.is_file():
            targets.append(p)
        elif p.is_dir():
            targets.extend(sorted(p.rglob("*.py")))
    return targets


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def side_door_violations(root: Path) -> list[str]:
    found: list[str] = []
    for path in _py_files(root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        docs = _docstring_nodes(tree)
        rel = path.relative_to(root)
        for node in ast.walk(tree):
            line = getattr(node, "lineno", 0)
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) not in docs and _SIDE_DOOR_STRING.search(node.value):
                    found.append(f"{rel}:{line} string names the valor-telegram side door")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.endswith("valor_telegram"):
                        found.append(f"{rel}:{line} imports {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").endswith("valor_telegram"):
                    found.append(f"{rel}:{line} imports from {node.module}")
                for alias in node.names:
                    if _SENDER_NAME.match(alias.name):
                        found.append(f"{rel}:{line} imports {alias.name}")
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                if _SENDER_NAME.match(node.name):
                    found.append(f"{rel}:{line} defines {node.name}")
            elif isinstance(node, ast.Call):
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
                if _SENDER_NAME.match(name):
                    found.append(f"{rel}:{line} calls {name}")
    return found


def test_reflections_have_no_telegram_side_door():
    violations = side_door_violations(REPO)
    assert not violations, "reflection chat side door found:\n" + "\n".join(violations)


def test_guard_detects_a_side_door(tmp_path):
    """The walker itself is live: a synthetic offender is caught, a docstring is not."""
    pkg = tmp_path / "reflections"
    pkg.mkdir()
    (pkg / "bad.py").write_text(
        "import subprocess\n"
        "def _send_telegram_notification(m):\n"
        "    subprocess.run(['valor-telegram', 'send', m])\n"
        "_send_telegram_notification('x')\n"
    )
    (pkg / "ok.py").write_text('"""Mentions valor-telegram only in prose."""\nx = 1\n')
    found = side_door_violations(tmp_path)
    assert any("bad.py" in v for v in found) and len(found) >= 3
    assert not any("ok.py" in v for v in found)
