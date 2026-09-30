"""Plan 05 task 2: the two Protocols import only `schemas/` and the
standard library."""

import ast
import sys
from pathlib import Path

import ports.kernel
import ports.worker

PORTS = [ports.worker, ports.kernel]


def imported_modules(module) -> set[str]:
    tree = ast.parse(Path(module.__file__).read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    return names


def test_ports_import_only_schemas():
    stdlib = sys.stdlib_module_names
    for module in PORTS:
        for name in imported_modules(module):
            top = name.split(".", 1)[0]
            assert (
                top == "schemas" or top in stdlib
            ), f"{module.__name__} imports {name}"


def test_the_protocols_name_the_seams_methods():
    assert {"run", "answer", "abort"} <= set(vars(ports.worker.Worker))
    assert {
        "record_tool",
        "raise_question",
        "request_effect",
        "read_slice",
        "write_episode",
        "propose_belief",
        "delegate",
    } <= set(vars(ports.kernel.KernelAPI))
