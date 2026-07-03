"""
Contract tests for ``.claude/skills/setup/SKILL.md``.

The setup skill is read by Claude (the agent) on `/setup`. These tests guard
the structural invariants the vault-path-config refactor introduced:

  - A "Step 0: Vault Location" section that resolves VALOR_VAULT_DIR before
    any subsequent step touches a vault file.
  - The skill calls `python -m tools.install.prompt vault-picker` so the
    five-option picker stays the single source of truth for the option list.
  - Subsequent steps reference `${VALOR_VAULT_DIR}` instead of literal
    `~/Desktop/Valor` paths (except for the Step 0 fallback detection,
    which is allowed to name the established default).
"""

import re
from pathlib import Path

import pytest

SKILL = Path(".claude/skills/setup/SKILL.md").read_text()


def test_setup_skill_step_zero_exists():
    assert re.search(r"^## Step 0:", SKILL, flags=re.MULTILINE), (
        "setup skill must have a Step 0 section"
    )
    # Step 0 must come before Step 1.
    step_0 = SKILL.index("## Step 0:")
    step_1 = SKILL.index("## Step 1:")
    assert step_0 < step_1, "Step 0 must precede Step 1"


def test_setup_skill_invokes_vault_picker():
    """Step 0 must call the prompt shim — keeps the option list DRY."""
    assert "tools.install.prompt vault-picker" in SKILL, (
        "Step 0 must invoke `python -m tools.install.prompt vault-picker` "
        "to share the option list with the prompt shim"
    )


def test_setup_skill_documents_cascade_order():
    """Step 0 must explain the four cascade tiers."""
    cascade_section = SKILL[SKILL.index("## Step 0:") : SKILL.index("## Step 1:")]
    for marker in (
        "VALOR_VAULT_DIR",
        "--vault-dir",
        "Desktop/Valor",  # the established-default fallback tier
    ):
        assert marker in cascade_section, f"Step 0 cascade section must mention {marker!r}"


def test_setup_skill_persists_vault_dir_to_env():
    """Step 0 must write VALOR_VAULT_DIR into the vault .env so future processes pick it up."""
    step_zero = SKILL[SKILL.index("## Step 0:") : SKILL.index("## Step 1:")]
    assert "VALOR_VAULT_DIR=" in step_zero, "Step 0 must write VALOR_VAULT_DIR= to disk"
    assert "/.env" in step_zero, "Step 0 must write VALOR_VAULT_DIR into the vault .env"


@pytest.mark.parametrize(
    "section_label",
    [
        "Step 3:",
        "### 4.1",
        "Step 6:",
    ],
)
def test_post_step_zero_sections_use_vault_var(section_label):
    """Subsequent steps must use ${VALOR_VAULT_DIR} rather than literal paths."""
    # Locate the section's body (between this header and the next "## Step ").
    header_match = re.search(rf"^## ?{re.escape(section_label)}", SKILL, flags=re.MULTILINE)
    if header_match is None:
        # Some sub-steps live under a parent (e.g., Step 4.1 under Step 4). Fall
        # back to a substring search for the label as it appears.
        idx = SKILL.find(section_label)
        assert idx >= 0, f"section {section_label} not found"
        body_start = idx
    else:
        body_start = header_match.end()
    next_header = re.search(r"^## ", SKILL[body_start + 1 :], flags=re.MULTILINE)
    body_end = body_start + 1 + next_header.start() if next_header else len(SKILL)
    body = SKILL[body_start:body_end]

    # Allow naming the established default once in informational copy, but
    # any bash command/path reference must use ${VALOR_VAULT_DIR}.
    bash_ref_pattern = re.compile(r"~/Desktop/Valor/[A-Za-z0-9_./-]+")
    bare_literals = bash_ref_pattern.findall(body)
    assert not bare_literals, (
        f"section '{section_label}' still has bare ~/Desktop/Valor literals: "
        f"{bare_literals}. Use ${{VALOR_VAULT_DIR}} instead."
    )


@pytest.mark.parametrize(
    "skill_path",
    [
        ".claude/skills/update/SKILL.md",
        ".claude/skills/do-deploy/SKILL.md",
        ".claude/skills-global/do-pr-review/SKILL.md",
        ".claude/agents/baseline-verifier.md",
    ],
)
def test_other_skill_files_are_vault_aware(skill_path):
    """M8: every skill that references the vault must use VALOR_VAULT_DIR.

    A bare ``~/Desktop/Valor`` literal anywhere outside parenthesized
    "default vault" / "default ~/Desktop/Valor" prose is a regression.
    """
    text = Path(skill_path).read_text()
    if "Desktop/Valor" not in text:
        pytest.skip(f"{skill_path} has no vault references")
    # Allow it to appear only in:
    #   - a comment containing the word "default"
    #   - a fallback expansion `${VALOR_VAULT_DIR:-$HOME/Desktop/Valor}` or
    #     similar `os.environ.get('VALOR_VAULT_DIR', ...)`
    #   - parenthesized prose like "(default vault `~/Desktop/Valor/`)"
    bad_lines = []
    for ln in text.splitlines():
        if "Desktop/Valor" not in ln:
            continue
        norm = ln.lstrip()
        is_comment = norm.startswith("#") or norm.startswith("<!--")
        has_vault_var = "VALOR_VAULT_DIR" in ln
        is_default_prose = "default" in ln.lower()
        if not (is_comment or has_vault_var or is_default_prose):
            bad_lines.append(ln.strip())
    assert not bad_lines, f"{skill_path} has bare ~/Desktop/Valor refs: {bad_lines}"


def test_no_unconfigured_desktop_literal_outside_step_zero():
    """Step 0 may name ~/Desktop/Valor as the fallback. Other sections may not."""
    step_zero = SKILL[SKILL.index("## Step 0:") : SKILL.index("## Step 1:")]
    rest = SKILL[SKILL.index("## Step 1:") :]
    # Allow descriptive copy that quotes paths in markdown italics or backticks
    # only if it's a fallback explanation. The real risk is *bash command lines*
    # with hardcoded paths.
    bash_lines = [
        line
        for line in rest.splitlines()
        if "~/Desktop/Valor" in line and not line.lstrip().startswith(("#", ">", "|"))
    ]
    assert not bash_lines, (
        f"Bash commands outside Step 0 must use ${{VALOR_VAULT_DIR}}; offending lines: {bash_lines}"
    )
    # Sanity: Step 0 *does* have references (we want them).
    assert "~/Desktop/Valor" in step_zero, "Step 0 should describe the default fallback"
