"""Tests for check_valor_alias_shadow() in scripts/update/verify.py.

A stale `alias valor=...` in ~/.zshrc shadows the venv binary .venv/bin/valor
in interactive shells. The /update verify step warns (never blocks) when such
an alias exists. See issue #1619.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.update.verify import check_valor_alias_shadow


def _write_rc(tmp_path: Path, content: str) -> Path:
    rc = tmp_path / ".zshrc"
    rc.write_text(content)
    return rc


def test_passes_when_only_prefixed_and_commented_aliases(tmp_path: Path) -> None:
    """`alias valor-session=...` and commented `# alias valor=...` must NOT warn."""
    rc = _write_rc(
        tmp_path,
        "alias valor-session='python -m tools.valor_session'\n"
        '# alias valor="cd /Users/valorengels/src/ai && ./scripts/telegram_run.sh"\n'
        "alias valor-telegram='vt'\n"
        "alias valordash=x\n",
    )
    check = check_valor_alias_shadow(rc)
    assert check.available is True
    assert check.error is None


@pytest.mark.parametrize(
    "alias_line",
    [
        'alias valor="cd /Users/valorengels/src/ai && ./scripts/telegram_run.sh"',
        "  alias valor='old'",  # indented
        "alias valor ='old'",  # space before =
        "\talias valor\t='old'",  # tab indentation and spacing
    ],
)
def test_warns_when_shadowing_alias_present(tmp_path: Path, alias_line: str) -> None:
    rc = _write_rc(tmp_path, f"export FOO=1\n{alias_line}\n")
    check = check_valor_alias_shadow(rc)
    assert check.available is False
    assert check.error is not None
    # Message must contain line number, offending line, and copy-paste fix.
    assert "line 2" in check.error
    assert alias_line.strip() in check.error
    assert "source ~/.zshrc" in check.error


def test_commented_out_alias_only_passes(tmp_path: Path) -> None:
    rc = _write_rc(tmp_path, '   # alias valor="old"\n#alias valor=old\n')
    check = check_valor_alias_shadow(rc)
    assert check.available is True
    assert check.error is None


def test_missing_rc_file_skips_cleanly(tmp_path: Path) -> None:
    check = check_valor_alias_shadow(tmp_path / ".zshrc")
    assert check.available is True
    assert check.error is None
    assert "skipped" in (check.version or "")


def test_unreadable_rc_file_skips_cleanly(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("running as root — chmod 000 does not block reads")
    rc = _write_rc(tmp_path, 'alias valor="old"\n')
    rc.chmod(0o000)
    try:
        check = check_valor_alias_shadow(rc)
    finally:
        rc.chmod(0o644)
    assert check.available is True
    assert check.error is None
    assert "skipped" in (check.version or "")
