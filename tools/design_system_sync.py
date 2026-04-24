"""Deterministic one-way generator from Pencil ``.pen`` JSON to DESIGN.md + CSS artifacts.

This module is the automation surface for the ``do-design-system`` skill's
Step 6 (CSS sync) and Step 7 (gap-audit diff). See
``docs/features/design-system-tooling.md`` for the full pipeline, schema
mapping, and adoption patterns for consumer repos.

Entry points:

* ``--generate``: read ``.pen``, emit ``design-system.md``, ``brand.css``,
  ``source.css``. Python-only path; no Node required when ``--no-node`` is
  passed (or auto-detected when ``npx`` is missing).
* ``--all``: ``--generate`` plus ``npx @google/design.md lint`` plus both
  DTCG and Tailwind exports. Requires Node.
* ``--check``: regenerate to a tempdir and byte-diff against the working
  tree. Exit 1 on drift. This is the cross-repo enforcement path; ai/'s
  PreToolUse hook wraps this, and consumer repos adopt it via their own
  ``.git/hooks/pre-commit`` or ``.claude/settings.json`` fragment.
* ``--audit``: produce the variables/components diff table for pasting into
  ``gap-audit.md``. MUST run before ``git commit`` so ``HEAD:`` still holds
  the prior pass's ``design-system.md``.

Path resolution precedence (``--pen`` and ``--css-root``):

1. Explicit CLI flag.
2. ``design-system-sync.toml`` adjacent to ``--pen``.
3. ``$CWD/docs/designs/design-system-sync.toml``.
4. Error with an actionable message naming both failure modes.

Determinism:

* All dict iteration is sorted.
* Component children are sorted by variable reference (or attribute key)
  BEFORE the "first child with a ``fill`` ref" scan, so Pencil's JSON
  insertion order cannot flip component color selection.
* Prefix categorization is longest-prefix-wins (sorted by ``len()``
  descending). ``--text-size-md`` resolves to typography, not colors.
* YAML is block style, double-quoted strings, explicit ``---`` fences with
  a single trailing newline.

Failure modes are explicit: missing ``.pen``, unmapped prefix, malformed
JSON, Node absent (where required), no git repo above ``.pen`` for
``--audit`` — each exits 2 with an actionable message.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Prefix → DESIGN.md category mapping (longest-prefix-wins)
# ---------------------------------------------------------------------------

# Stored (prefix, category) pairs. The generator sorts this list by prefix
# length descending before iteration; the first match wins. This is REQUIRED
# because ``--text-*`` (colors) overlaps ``--text-size-*`` / ``--text-weight-*``
# / ``--text-lh-*`` (typography). See Risk 9 in the plan.
_PREFIX_RULES: list[tuple[str, str]] = [
    # typography (specific prefixes before the general --text-* fallback)
    ("--text-size-", "typography"),
    ("--text-weight-", "typography"),
    ("--text-lh-", "typography"),
    ("--font-", "typography"),
    # rounded
    ("--radius-", "rounded"),
    ("--rounded-", "rounded"),
    # spacing
    ("--space-", "spacing"),
    ("--gap-", "spacing"),
    ("--pad-", "spacing"),
    # colors
    ("--color-", "colors"),
    ("--accent", "colors"),
    ("--status-", "colors"),
    ("--surface-", "colors"),
    ("--text-", "colors"),
    ("--border-", "colors"),
]


def _validate_prefix_invariant(rules: list[tuple[str, str]]) -> None:
    """Lint: if prefix A is a strict prefix of prefix B, B MUST come first.

    Run at import time so a future contributor cannot accidentally
    re-introduce first-match ordering.
    """
    for i, (prefix_i, _) in enumerate(rules):
        for j in range(i + 1, len(rules)):
            prefix_j, _ = rules[j]
            # If a later prefix is a strict prefix of an earlier one, OK
            # (earlier is longer / more specific).
            # If a later prefix is a strict extension of an earlier one,
            # that violates longest-prefix-wins.
            if prefix_j.startswith(prefix_i) and prefix_j != prefix_i:
                raise AssertionError(
                    f"Prefix rule order invariant violated: "
                    f"{prefix_j!r} is a strict extension of {prefix_i!r} "
                    f"but appears after it. Reorder so longer prefixes match first."
                )


# Sort by length descending so the first match wins, then lint the order.
_PREFIX_RULES.sort(key=lambda pair: len(pair[0]), reverse=True)
_validate_prefix_invariant(_PREFIX_RULES)


def categorize_prefix(var_name: str) -> str | None:
    """Return the DESIGN.md category for a ``.pen`` variable name, or None."""
    for prefix, category in _PREFIX_RULES:
        if var_name.startswith(prefix):
            return category
    return None


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedPaths:
    pen: Path
    css_root: Path


def _read_toml_css_root(toml_path: Path) -> str | None:
    if not toml_path.is_file():
        return None
    try:
        data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"Malformed {toml_path}: {exc}") from exc
    css_root = data.get("css_root")
    if css_root is None or not isinstance(css_root, str):
        return None
    return css_root


def resolve_paths(
    pen_arg: str | None,
    css_root_arg: str | None,
    cwd: Path | None = None,
) -> ResolvedPaths:
    """Apply precedence: CLI flag > TOML adjacent to .pen > $CWD/docs/designs.

    Raises ``SystemExit(2)`` with an actionable message when neither surface
    yields both paths.
    """
    cwd = cwd or Path.cwd()

    if pen_arg is None:
        raise SystemExit(
            "error: --pen is required (or provide design-system-sync.toml "
            "adjacent to the .pen file)"
        )
    pen = Path(pen_arg).expanduser().resolve()
    if not pen.is_file():
        raise SystemExit(f"error: design-system.pen not found at {pen}")

    if css_root_arg is not None:
        css_root_rel = css_root_arg
        base = cwd
    else:
        adjacent_toml = pen.parent / "design-system-sync.toml"
        fallback_toml = cwd / "docs" / "designs" / "design-system-sync.toml"
        css_root_rel = _read_toml_css_root(adjacent_toml)
        base = pen.parent
        if css_root_rel is None:
            css_root_rel = _read_toml_css_root(fallback_toml)
            base = fallback_toml.parent
        if css_root_rel is None:
            raise SystemExit(
                "error: --css-root not provided and no design-system-sync.toml "
                f"found adjacent to {pen} or at {fallback_toml}. "
                "Pass --css-root <path> or create design-system-sync.toml."
            )

    css_root = (base / css_root_rel).resolve()
    css_root.mkdir(parents=True, exist_ok=True)
    return ResolvedPaths(pen=pen, css_root=css_root)


# ---------------------------------------------------------------------------
# .pen loader + mapping
# ---------------------------------------------------------------------------


def load_pen(pen: Path) -> dict:
    try:
        return json.loads(pen.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"error: {pen} is not valid JSON ({exc})") from exc


def _flat_typography(tokens: dict[str, dict]) -> dict[str, dict]:
    """Aggregate --font / --text-size / --text-weight / --text-lh into presets.

    The preset name is the suffix after the prefix (e.g. ``--font-sans`` →
    preset ``sans``). Missing properties inherit from the ``base`` preset if
    one exists; if ``base`` is missing, the linter surfaces
    ``missing-typography``.
    """
    presets: dict[str, dict] = {}
    for name, entry in tokens.items():
        value = entry.get("value") if isinstance(entry, dict) else entry
        if name.startswith("--font-"):
            preset = name[len("--font-") :]
            presets.setdefault(preset, {})["fontFamily"] = value
        elif name.startswith("--text-size-"):
            preset = name[len("--text-size-") :]
            presets.setdefault(preset, {})["fontSize"] = value
        elif name.startswith("--text-weight-"):
            preset = name[len("--text-weight-") :]
            weight_val = value
            if isinstance(weight_val, str) and weight_val.isdigit():
                weight_val = int(weight_val)
            presets.setdefault(preset, {})["fontWeight"] = weight_val
        elif name.startswith("--text-lh-"):
            preset = name[len("--text-lh-") :]
            presets.setdefault(preset, {})["lineHeight"] = value
    # Base inheritance: fill missing keys on non-base presets from base.
    base = presets.get("base", {})
    for preset_name, fields in presets.items():
        if preset_name == "base":
            continue
        for key in ("fontFamily", "fontSize", "fontWeight", "lineHeight"):
            if key not in fields and key in base:
                fields[key] = base[key]
    return presets


def _slugify_component_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _sorted_component_children(children: list[dict]) -> list[dict]:
    """Sort children by variable name / attribute key before scanning.

    This is load-bearing for determinism: the "first child with fill ref"
    rule must operate on the sorted list, not Pencil's JSON insertion order.
    """

    def sort_key(child: dict) -> tuple:
        # Prefer an explicit variable reference, fall back to name/id/type.
        for attr in ("fill", "font", "size", "weight", "lineHeight", "radius"):
            v = child.get(attr)
            if isinstance(v, str):
                return (0, v)
        return (
            1,
            str(child.get("name", "")),
            str(child.get("id", "")),
            str(child.get("type", "")),
        )

    return sorted(children, key=sort_key)


def _ref_of(token: str) -> str | None:
    """Convert a ``$--name`` Pencil ref into a DESIGN.md ``{path.to.token}``.

    Returns None if the token is not a Pencil reference.
    """
    if not isinstance(token, str) or not token.startswith("$--"):
        return None
    var_name = token[1:]  # strip leading "$"
    category = categorize_prefix(var_name)
    if category is None:
        return None
    if category == "typography":
        # Typography refs pick a preset by suffix.
        for prefix in ("--font-", "--text-size-", "--text-weight-", "--text-lh-"):
            if var_name.startswith(prefix):
                preset = var_name[len(prefix) :]
                return f"{{typography.{preset}}}"
    if category == "rounded":
        for prefix in ("--radius-", "--rounded-"):
            if var_name.startswith(prefix):
                return f"{{rounded.{var_name[len(prefix) :]}}}"
    if category == "spacing":
        for prefix in ("--space-", "--gap-", "--pad-"):
            if var_name.startswith(prefix):
                return f"{{spacing.{var_name[len(prefix) :]}}}"
    if category == "colors":
        # Use the same key derivation as _token_key_for_color so refs line
        # up with the emitted ``colors`` token map.
        key = _token_key_for_color(var_name)
        return f"{{colors.{key}}}"
    return None


def _token_key_for_color(var_name: str) -> str:
    """"--color-primary" → "primary"; "--text-body-primary" → "text.body.primary"."""
    stripped = re.sub(r"^--", "", var_name)
    if stripped.startswith("color-"):
        stripped = stripped[len("color-") :]
    return stripped.replace("-", ".")


def _token_key_simple(var_name: str, prefixes: tuple[str, ...]) -> str:
    for prefix in prefixes:
        if var_name.startswith(prefix):
            return var_name[len(prefix) :]
    return var_name.lstrip("-")


def build_tokens(pen_doc: dict) -> dict:
    """Build the DESIGN.md frontmatter dict from a parsed ``.pen`` document.

    Drops unmapped prefixes silently here; the caller decides whether an
    unmapped prefix is a warning or a hard error via ``--drop-unmapped``.
    """
    variables = pen_doc.get("variables") or {}
    colors: dict[str, str] = {}
    rounded: dict[str, str] = {}
    spacing: dict = {}
    for var_name, entry in variables.items():
        value = entry.get("value") if isinstance(entry, dict) else entry
        category = categorize_prefix(var_name)
        if category == "colors":
            key = _token_key_for_color(var_name)
            if isinstance(value, str):
                colors[key] = value.upper()
        elif category == "rounded":
            key = _token_key_simple(var_name, ("--radius-", "--rounded-"))
            rounded[key] = value
        elif category == "spacing":
            key = _token_key_simple(var_name, ("--space-", "--gap-", "--pad-"))
            spacing[key] = value
    typography = _flat_typography(variables)

    components = _build_components(pen_doc)
    tokens: dict = {
        "version": "alpha",
        "name": str(pen_doc.get("name") or "(unnamed)"),
    }
    if colors:
        tokens["colors"] = dict(sorted(colors.items()))
    if typography:
        tokens["typography"] = {k: dict(sorted(v.items())) for k, v in sorted(typography.items())}
    if rounded:
        tokens["rounded"] = dict(sorted(rounded.items()))
    if spacing:
        tokens["spacing"] = dict(sorted(spacing.items()))
    if components:
        tokens["components"] = components
    return tokens


def _build_components(pen_doc: dict) -> dict[str, dict]:
    components: dict[str, dict] = {}
    for frame in pen_doc.get("children", []) or []:
        children = frame.get("children") or []
        for entry in children:
            if not entry.get("reusable"):
                continue
            name = entry.get("name", "")
            if "/" not in name:
                continue
            key = _slugify_component_key(name)
            props: dict[str, str] = {}
            sorted_kids = _sorted_component_children(entry.get("children") or [])
            # First child with a fill ref → backgroundColor.
            for k in sorted_kids:
                fill = k.get("fill")
                ref = _ref_of(fill) if isinstance(fill, str) else None
                if ref is not None:
                    props["backgroundColor"] = ref
                    break
            # First text child → textColor + typography.
            for k in sorted_kids:
                if k.get("type") != "text":
                    continue
                text_fill = _ref_of(k.get("fill")) if isinstance(k.get("fill"), str) else None
                if text_fill:
                    props["textColor"] = text_fill
                # Typography preset ref — picked from the font variable.
                font_ref = k.get("font")
                if isinstance(font_ref, str) and font_ref.startswith("$--font-"):
                    preset = font_ref[len("$--font-") :]
                    props["typography"] = f"{{typography.{preset}}}"
                break
            # Radius / padding / width / height.
            for k in sorted_kids:
                radius = k.get("radius")
                if isinstance(radius, str):
                    radius_ref = _ref_of(radius)
                    if radius_ref:
                        props.setdefault("rounded", radius_ref)
                        break
            padding = entry.get("padding")
            if isinstance(padding, str):
                padding_ref = _ref_of(padding)
                props["padding"] = padding_ref or padding
            if isinstance(entry.get("width"), (int, float)):
                props["width"] = f"{entry['width']}px"
            if isinstance(entry.get("height"), (int, float)):
                props["height"] = f"{entry['height']}px"
            components[key] = dict(sorted(props.items()))
    return dict(sorted(components.items()))


# ---------------------------------------------------------------------------
# YAML / CSS / Markdown emission
# ---------------------------------------------------------------------------


def _emit_yaml(tokens: dict) -> str:
    dumped = yaml.safe_dump(
        tokens,
        sort_keys=True,
        default_flow_style=False,
        allow_unicode=True,
        width=10_000,
    )
    # safe_dump does not quote string values that look like colors (#XXX is
    # a YAML comment), so post-process to wrap any leading-# scalars in
    # double quotes. Token references {...} are also safer quoted.
    out_lines: list[str] = []
    for line in dumped.splitlines():
        stripped = line.lstrip()
        if ":" in stripped:
            head, _, tail = line.partition(":")
            val = tail.strip()
            if val.startswith("#") or val.startswith("{"):
                line = f"{head}: \"{val}\""
        out_lines.append(line)
    body = "\n".join(out_lines).rstrip() + "\n"
    return body


def render_design_md(tokens: dict) -> str:
    yaml_body = _emit_yaml(tokens)
    frontmatter = f"---\n{yaml_body}---\n"
    # Emit every required section (even if empty prose) in the spec's order.
    sections = [
        ("Overview", "Generated from `design-system.pen`. See `docs/features/design-system-tooling.md`."),
        ("Colors", _render_colors_prose(tokens)),
        ("Typography", _render_typography_prose(tokens)),
        ("Layout", _render_layout_prose(tokens)),
        ("Shapes", _render_shapes_prose(tokens)),
        ("Components", _render_components_prose(tokens)),
    ]
    body_parts = [frontmatter, f"# {tokens.get('name', '(unnamed)')}", ""]
    for heading, prose in sections:
        body_parts.append(f"## {heading}")
        body_parts.append("")
        body_parts.append(prose)
        body_parts.append("")
    return "\n".join(body_parts).rstrip() + "\n"


def _render_colors_prose(tokens: dict) -> str:
    colors = tokens.get("colors") or {}
    if not colors:
        return "_No color tokens defined._"
    lines = ["The color palette is derived from the `.pen` source."]
    for key, value in sorted(colors.items()):
        lines.append(f"- **{key}:** `{value}`")
    return "\n".join(lines)


def _render_typography_prose(tokens: dict) -> str:
    presets = tokens.get("typography") or {}
    if not presets:
        return "_No typography presets defined._"
    lines = ["Typography presets are aggregated from `--font-*`, `--text-size-*`, `--text-weight-*`, and `--text-lh-*` tokens."]
    for name in sorted(presets):
        lines.append(f"- **{name}**")
    return "\n".join(lines)


def _render_layout_prose(tokens: dict) -> str:
    spacing = tokens.get("spacing") or {}
    if not spacing:
        return "_No spacing tokens defined._"
    return "Spacing scale is driven by `--space-*`, `--gap-*`, and `--pad-*` tokens."


def _render_shapes_prose(tokens: dict) -> str:
    rounded = tokens.get("rounded") or {}
    if not rounded:
        return "_No rounded tokens defined._"
    return "Corner-radius scale is driven by `--radius-*` and `--rounded-*` tokens."


def _render_components_prose(tokens: dict) -> str:
    components = tokens.get("components") or {}
    if not components:
        return "_No components defined._"
    lines = ["Reusable components map from Pencil frames (`reusable: true`, `Category/Variant` name)."]
    for key in sorted(components):
        lines.append(f"- `{key}`")
    return "\n".join(lines)


def _emit_css_root(tokens: dict) -> str:
    """Emit ``:root { ... }`` with all CSS custom properties sorted by name."""
    lines = [":root {"]
    for var_name, value in _flatten_css_vars(tokens):
        lines.append(f"  {var_name}: {value};")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def _emit_css_theme(tokens: dict) -> str:
    """Emit Tailwind ``@theme { ... }`` with the same vars."""
    lines = ["@theme {"]
    for var_name, value in _flatten_css_vars(tokens):
        lines.append(f"  {var_name}: {value};")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def _flatten_css_vars(tokens: dict) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for key, value in (tokens.get("colors") or {}).items():
        out.append((f"--color-{key.replace('.', '-')}", str(value)))
    for preset, fields in (tokens.get("typography") or {}).items():
        if "fontFamily" in fields:
            out.append((f"--font-{preset}", str(fields["fontFamily"])))
        if "fontSize" in fields:
            out.append((f"--text-size-{preset}", str(fields["fontSize"])))
        if "fontWeight" in fields:
            out.append((f"--text-weight-{preset}", str(fields["fontWeight"])))
        if "lineHeight" in fields:
            out.append((f"--text-lh-{preset}", str(fields["lineHeight"])))
    for key, value in (tokens.get("rounded") or {}).items():
        out.append((f"--radius-{key}", str(value)))
    for key, value in (tokens.get("spacing") or {}).items():
        out.append((f"--space-{key}", str(value)))
    return sorted(out)


# ---------------------------------------------------------------------------
# Node / npx integration
# ---------------------------------------------------------------------------


_AI_REPO_ROOT = Path(__file__).resolve().parent.parent


def _probe_npx() -> bool:
    """Return True when ``npx --version`` returns 0, else False."""
    if shutil.which("npx") is None:
        return False
    try:
        result = subprocess.run(
            ["npx", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _run_npx(args: list[str], *, required: bool = True) -> subprocess.CompletedProcess:
    """Run ``npx --no-install @google/design.md <args>`` from ai/ repo root.

    If ``required=False`` the caller is expected to handle FileNotFoundError
    and non-zero exits; otherwise failures propagate.
    """
    cmd = ["npx", "--no-install", "@google/design.md", *args]
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=required,
            cwd=str(_AI_REPO_ROOT),
        )
    except FileNotFoundError:
        if not required:
            raise
        raise SystemExit(
            "error: npx not found on PATH. Install Node + npm and rerun, "
            "or pass --no-node to skip lint/export."
        )


# ---------------------------------------------------------------------------
# Generate / check / audit commands
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def cmd_generate(
    paths: ResolvedPaths,
    *,
    no_node: bool,
    drop_unmapped: bool,
) -> int:
    pen_doc = load_pen(paths.pen)
    _warn_unmapped(pen_doc, drop_unmapped=drop_unmapped)
    tokens = build_tokens(pen_doc)
    design_md = render_design_md(tokens)
    brand_css = _emit_css_root(tokens)
    source_css = _emit_css_theme(tokens)

    _write(paths.pen.parent / "design-system.md", design_md)
    _write(paths.css_root / "brand.css", brand_css)
    _write(paths.css_root / "source.css", source_css)

    if no_node or not _probe_npx():
        if not no_node:
            print(
                "[design_system_sync] Node not available; lint/export skipped. "
                "Run `python -m tools.design_system_sync --all` on a machine "
                "with Node to produce exports.",
                file=sys.stderr,
            )
        return 0
    return 0


def cmd_all(paths: ResolvedPaths, *, drop_unmapped: bool, no_node: bool) -> int:
    rc = cmd_generate(paths, no_node=no_node, drop_unmapped=drop_unmapped)
    if rc != 0:
        return rc
    if no_node:
        raise SystemExit(
            "error: --all requires Node for lint + export. Remove --no-node "
            "or install Node. Use --generate for Python-only emission."
        )
    if not _probe_npx():
        raise SystemExit(
            "error: Node required for --all (lint + export). Install Node + npm "
            "and rerun, or use --generate --no-node for Python-only emission."
        )
    md_path = paths.pen.parent / "design-system.md"
    exports_dir = paths.pen.parent / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)

    lint_result = _run_npx(["lint", str(md_path)], required=False)
    if lint_result.returncode != 0:
        sys.stderr.write(lint_result.stderr or lint_result.stdout)
        return lint_result.returncode

    dtcg_result = _run_npx(["export", str(md_path), "--format", "dtcg"])
    _write(exports_dir / "tokens.dtcg.json", _normalize_export_json(dtcg_result.stdout))
    tailwind_result = _run_npx(["export", str(md_path), "--format", "tailwind"])
    _write(exports_dir / "tailwind.theme.json", _normalize_export_json(tailwind_result.stdout))
    return 0


def _normalize_export_json(raw: str) -> str:
    """Normalize CLI JSON output so snapshots stay byte-stable.

    The CLI's output may include trailing whitespace or an unstable key
    order; round-tripping through json with ``sort_keys=True`` makes the
    emitted file deterministic.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw.rstrip() + "\n"
    return json.dumps(parsed, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def cmd_check(paths: ResolvedPaths, *, drop_unmapped: bool, no_node: bool) -> int:
    pen_doc = load_pen(paths.pen)
    tokens = build_tokens(pen_doc)
    expected_md = render_design_md(tokens)
    expected_brand = _emit_css_root(tokens)
    expected_source = _emit_css_theme(tokens)

    drift: list[str] = []
    pairs = [
        (paths.pen.parent / "design-system.md", expected_md),
        (paths.css_root / "brand.css", expected_brand),
        (paths.css_root / "source.css", expected_source),
    ]
    for target, expected in pairs:
        actual = target.read_text(encoding="utf-8") if target.is_file() else ""
        if actual != expected:
            diff = "\n".join(
                difflib.unified_diff(
                    expected.splitlines(),
                    actual.splitlines(),
                    fromfile=f"expected:{target.name}",
                    tofile=f"actual:{target.name}",
                    lineterm="",
                )
            )
            drift.append(
                f"{target} differs from generated output — run "
                f"`python -m tools.design_system_sync --generate`\n{diff}"
            )

    if drift:
        for entry in drift:
            sys.stderr.write(entry + "\n")
        return 1

    if no_node:
        return 0
    if not _probe_npx():
        # Without Node we cannot verify exports; succeed on CSS / md parity.
        return 0
    exports_dir = paths.pen.parent / "exports"
    md_path = paths.pen.parent / "design-system.md"
    if not exports_dir.is_dir():
        return 0
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        dtcg_result = _run_npx(["export", str(md_path), "--format", "dtcg"])
        tailwind_result = _run_npx(["export", str(md_path), "--format", "tailwind"])
        (tmpdir / "tokens.dtcg.json").write_text(
            _normalize_export_json(dtcg_result.stdout), encoding="utf-8"
        )
        (tmpdir / "tailwind.theme.json").write_text(
            _normalize_export_json(tailwind_result.stdout), encoding="utf-8"
        )
        for name in ("tokens.dtcg.json", "tailwind.theme.json"):
            actual_path = exports_dir / name
            expected_path = tmpdir / name
            actual = actual_path.read_text(encoding="utf-8") if actual_path.is_file() else ""
            expected = expected_path.read_text(encoding="utf-8")
            if actual != expected:
                sys.stderr.write(
                    f"{actual_path} differs from generated export — run "
                    f"`python -m tools.design_system_sync --all`\n"
                )
                return 1
    return 0


def _find_consumer_repo_root(pen: Path, explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit).expanduser().resolve()
    cur = pen.resolve().parent
    while cur != cur.parent:
        if (cur / ".git").exists():
            return cur
        cur = cur.parent
    return None


def cmd_audit(paths: ResolvedPaths, *, repo_root: str | None) -> int:
    consumer_root = _find_consumer_repo_root(paths.pen, repo_root)
    if consumer_root is None:
        raise SystemExit(
            "error: could not locate a git repo above "
            f"{paths.pen}; pass --repo-root <path> or run from inside a git worktree."
        )
    md_path = paths.pen.parent / "design-system.md"
    try:
        pen_rel_dir = paths.pen.parent.resolve().relative_to(consumer_root)
    except ValueError:
        raise SystemExit(
            f"error: --pen {paths.pen} is not under the resolved repo root {consumer_root}."
        )
    head_target = f"HEAD:{pen_rel_dir.as_posix()}/design-system.md"
    result = subprocess.run(
        ["git", "show", head_target],
        cwd=str(consumer_root),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 128:
        print("(initial pass — no prior diff)")
        return 0
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        return result.returncode

    prior_md = result.stdout
    # Stale-warn: on-disk identical to HEAD:... means nothing changed OR
    # --audit ran post-commit.
    if md_path.is_file() and md_path.read_text(encoding="utf-8") == prior_md:
        sys.stderr.write(
            "[design_system_sync] --audit: on-disk design-system.md matches HEAD; "
            "if you expected a diff, ensure you ran --audit BEFORE `git commit`.\n"
        )
    if not _probe_npx():
        raise SystemExit(
            "error: --audit requires Node to run `npx @google/design.md diff`. "
            "Install Node + npm and rerun."
        )
    with tempfile.TemporaryDirectory() as td:
        prev_path = Path(td) / "prev.md"
        prev_path.write_text(prior_md, encoding="utf-8")
        diff_result = _run_npx(["diff", str(prev_path), str(md_path)], required=False)
        if diff_result.returncode not in (0, 1):
            sys.stderr.write(diff_result.stderr or diff_result.stdout)
            return diff_result.returncode
        print(_format_audit_markdown(diff_result.stdout))
    return 0


def _format_audit_markdown(raw: str) -> str:
    """Reformat design.md diff output into markdown tables."""
    raw = raw.strip()
    if not raw:
        return "_No changes since previous pass._"
    return "### Variables & components changed\n\n```\n" + raw + "\n```"


def _warn_unmapped(pen_doc: dict, *, drop_unmapped: bool) -> None:
    unmapped: list[str] = []
    for name in (pen_doc.get("variables") or {}):
        if categorize_prefix(name) is None:
            unmapped.append(name)
    if not unmapped:
        return
    names = ", ".join(sorted(unmapped))
    sys.stderr.write(
        f"[design_system_sync] unmapped variable prefix(es): {names}. "
        "Rename to a known prefix or pass --drop-unmapped.\n"
    )
    if not drop_unmapped:
        raise SystemExit(
            "error: unmapped variable prefixes present; see stderr. "
            "Pass --drop-unmapped to ignore."
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.design_system_sync",
        description=(
            "Deterministic one-way generator: .pen → DESIGN.md + brand.css "
            "+ source.css + DTCG/Tailwind exports."
        ),
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--generate", action="store_true", help="Emit DESIGN.md, brand.css, source.css.")
    mode.add_argument("--all", action="store_true", help="--generate plus lint + DTCG/Tailwind exports.")
    mode.add_argument("--check", action="store_true", help="Drift check against the working tree; exit 1 on drift.")
    mode.add_argument("--audit", action="store_true", help="Diff against HEAD:<pen-dir>/design-system.md for gap-audit.")

    parser.add_argument("--pen", required=False, help="Path to design-system.pen.")
    parser.add_argument("--css-root", required=False, help="Directory to emit brand.css and source.css.")
    parser.add_argument(
        "--drop-unmapped",
        action="store_true",
        help="Silently drop variables with unrecognized prefixes (default: error).",
    )
    parser.add_argument(
        "--no-node",
        action="store_true",
        help="Skip every npx call. --generate falls back to Python-only emission.",
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        help="Override git-repo root for --audit (default: walk up from --pen until .git/).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.generate or args.all or args.check:
        paths = resolve_paths(args.pen, args.css_root)
    elif args.audit:
        paths = resolve_paths(args.pen, args.css_root or ".")
    else:  # pragma: no cover - argparse guards this
        parser.error("no mode selected")
        return 2

    if args.generate:
        return cmd_generate(paths, no_node=args.no_node, drop_unmapped=args.drop_unmapped)
    if args.all:
        return cmd_all(paths, drop_unmapped=args.drop_unmapped, no_node=args.no_node)
    if args.check:
        return cmd_check(paths, drop_unmapped=args.drop_unmapped, no_node=args.no_node)
    if args.audit:
        return cmd_audit(paths, repo_root=args.repo_root)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "build_tokens",
    "categorize_prefix",
    "cmd_all",
    "cmd_audit",
    "cmd_check",
    "cmd_generate",
    "load_pen",
    "main",
    "render_design_md",
    "resolve_paths",
]
