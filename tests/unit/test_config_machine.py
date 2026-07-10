"""Unit tests for the centralized machine-identity resolver (config/machine.py, #1997).

Covers the three fail-soft contracts that used to be copy-pasted (and drifted)
across five modules:
  * ``get_machine_name`` → stripped ComputerName on success, ``""`` on any failure
    (no ``platform.node()`` fallback — the ownership guard depends on ``""``).
  * ``get_machine_slug`` → filesystem-safe, guaranteed non-empty (``platform.node()``
    fallback lives here so a per-machine token filename is never empty).
  * ``get_machine_project_keys`` → case-insensitive ownership match, ``[]`` on any
    read failure, and the #1834 empty-machine fail-to-development guard.
  * ``get_machine_display_name`` → human-facing ComputerName → hostname →
    ``"unknown"`` fallback chain (absorbed from the retired
    ``tools/machine_identity.py`` hub).
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import config.machine as machine


def _fake_run(stdout: str = "", returncode: int = 0):
    def _run(*_args, **_kwargs):
        return SimpleNamespace(stdout=stdout, returncode=returncode)

    return _run


# --- get_machine_name --------------------------------------------------------


def test_get_machine_name_success(monkeypatch):
    monkeypatch.setattr(machine.subprocess, "run", _fake_run(stdout="Prod Box\n"))
    assert machine.get_machine_name() == "Prod Box"


def test_get_machine_name_nonzero_exit_returns_empty(monkeypatch):
    monkeypatch.setattr(machine.subprocess, "run", _fake_run(stdout="junk", returncode=1))
    assert machine.get_machine_name() == ""


def test_get_machine_name_scutil_raises_returns_empty(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="scutil", timeout=5)

    monkeypatch.setattr(machine.subprocess, "run", _boom)
    assert machine.get_machine_name() == ""


# --- get_machine_slug --------------------------------------------------------


def test_get_machine_slug_transforms_name(monkeypatch):
    monkeypatch.setattr(machine, "get_machine_name", lambda: "Prod Box")
    assert machine.get_machine_slug() == "prod-box"


def test_get_machine_slug_falls_back_to_display_chain(monkeypatch):
    monkeypatch.setattr(machine, "get_machine_name", lambda: "")
    monkeypatch.setattr(machine.socket, "gethostname", lambda: "Fallback-Host.local")
    slug = machine.get_machine_slug()
    assert slug == "fallback-host"
    assert slug  # invariant: never empty


def test_get_machine_slug_never_empty_even_when_all_lookups_fail(monkeypatch):
    """The non-empty invariant must hold with no ComputerName AND no hostname."""
    monkeypatch.setattr(machine, "get_machine_name", lambda: "")
    monkeypatch.setattr(machine.socket, "gethostname", lambda: "")
    assert machine.get_machine_slug() == "unknown"


# --- get_machine_display_name --------------------------------------------------


def test_get_machine_display_name_prefers_computer_name(monkeypatch):
    monkeypatch.setattr(machine, "get_machine_name", lambda: "Prod Box")
    assert machine.get_machine_display_name() == "Prod Box"


def test_get_machine_display_name_falls_back_to_hostname(monkeypatch):
    monkeypatch.setattr(machine, "get_machine_name", lambda: "")
    monkeypatch.setattr(machine.socket, "gethostname", lambda: "fallback-host.local")
    assert machine.get_machine_display_name() == "fallback-host.local"


def test_get_machine_display_name_unknown_when_all_fail(monkeypatch):
    def _boom():
        raise OSError("no hostname")

    monkeypatch.setattr(machine, "get_machine_name", lambda: "")
    monkeypatch.setattr(machine.socket, "gethostname", _boom)
    assert machine.get_machine_display_name() == "unknown"


# --- get_machine_project_keys ------------------------------------------------


def _write_projects(tmp_path, monkeypatch, data: dict):
    (tmp_path / "projects.json").write_text(json.dumps(data))
    monkeypatch.setattr(machine, "VALOR_DIR", tmp_path)


def test_get_machine_project_keys_case_insensitive_match(tmp_path, monkeypatch):
    _write_projects(
        tmp_path,
        monkeypatch,
        {"projects": {"p1": {"machine": "Prod-Box"}, "p2": {"machine": "Other"}}},
    )
    assert machine.get_machine_project_keys("prod-box") == ["p1"]
    assert machine.get_machine_project_keys("Unowned-Laptop") == []


def test_get_machine_project_keys_empty_machine_guard(tmp_path, monkeypatch):
    """#1834: an empty machine must NOT match a ``"machine": ""`` entry."""
    _write_projects(tmp_path, monkeypatch, {"projects": {"p1": {"machine": ""}}})
    assert machine.get_machine_project_keys("") == []


def test_get_machine_project_keys_resolves_name_when_none(tmp_path, monkeypatch):
    _write_projects(tmp_path, monkeypatch, {"projects": {"p1": {"machine": "Auto-Box"}}})
    monkeypatch.setattr(machine, "get_machine_name", lambda: "auto-box")
    assert machine.get_machine_project_keys() == ["p1"]


def test_get_machine_project_keys_read_failure_returns_empty(tmp_path, monkeypatch):
    """Missing/unreadable projects.json → [] (fail-to-development)."""
    # tmp_path has no projects.json written → read_text raises → [].
    monkeypatch.setattr(machine, "VALOR_DIR", tmp_path)
    assert machine.get_machine_project_keys("Prod-Box") == []


def test_get_machine_project_keys_malformed_json_returns_empty(tmp_path, monkeypatch):
    (tmp_path / "projects.json").write_text("{not valid json")
    monkeypatch.setattr(machine, "VALOR_DIR", tmp_path)
    assert machine.get_machine_project_keys("Prod-Box") == []
