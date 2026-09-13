#!/usr/bin/env python3
"""Validate the Codex skill collection and install its global skills as managed copies.

Requires PyYAML (included in the repository environment). Installation refuses
unmanaged or locally edited destinations;
it never edits Codex settings, installs connectors, or executes skill helper scripts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path(".agents/skills-manifest.json")
STATE_NAME = ".valor-codex-skills.json"
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
LINK = re.compile(r"(?<!!)\[[^\]\n]+\]\(([^\s)]+)(?:\s+\"[^\"]*\")?\)")


def files(directory: Path) -> dict[str, str]:
    """Fingerprint real bundled files, excluding transient interpreter/cache output."""
    result = {}
    for path in sorted(directory.rglob("*")):
        if "__pycache__" in path.parts or path.name in {".DS_Store", "metadata.json"}:
            continue
        if path.suffix == ".pyc":
            continue
        if path.is_symlink():
            raise ValueError(f"Unexpected symlink: {path}")
        if path.is_file():
            result[path.relative_to(directory).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return result


def inventory(root: Path) -> list[dict]:
    data = json.loads((root / MANIFEST).read_text())
    if data.get("version") != 1 or not isinstance(data.get("skills"), list):
        raise ValueError("Unsupported or invalid skill inventory")
    entries = data["skills"]
    names = set()
    for entry in entries:
        name, scope = entry["name"], entry["scope"]
        if not NAME.fullmatch(name) or name in names or scope not in {"project", "global"}:
            raise ValueError(f"Invalid or duplicate skill entry: {name}")
        names.add(name)
        target_base = "skills-global" if scope == "global" else "skills"
        source_base = "skills-global" if scope == "global" else "skills"
        if entry["target"] != f".agents/{target_base}/{name}":
            raise ValueError(f"Invalid target for {name}")
        if entry["source"] is None:
            if entry["source_files"]:
                raise ValueError(f"Native-only skill {name} must have empty source_files")
        elif entry["source"] != f".claude/{source_base}/{name}/SKILL.md":
            raise ValueError(f"Invalid source for {name}")
    return entries


def metadata(path: Path) -> tuple[str, str]:
    """Parse safe YAML and validate required Codex discovery metadata."""
    try:
        import yaml
    except ImportError as exc:
        raise ValueError("PyYAML is required; use the repository Python environment") from exc
    match = re.match(r"\A---[ \t]*\r?\n(.*?)^---[ \t]*\r?$", path.read_text(), re.M | re.S)
    if not match:
        raise ValueError("Expected YAML frontmatter enclosed by --- lines")
    try:
        fields = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML frontmatter: {exc}") from exc
    if not isinstance(fields, dict):
        raise ValueError("Frontmatter must be a mapping")
    name, description = fields.get("name"), fields.get("description")
    if not isinstance(name, str):
        raise ValueError("Skill name must be a string")
    if not NAME.fullmatch(name) or len(name) >= 64 or name != path.parent.name:
        raise ValueError("Skill name must match its folder and be under 64 characters")
    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
        raise ValueError("Description must be a nonempty string of at most 1024 characters")
    if "<" in description or ">" in description:
        raise ValueError("Description contains angle brackets")
    return name, description


def prose(text: str) -> str:
    """Ignore code examples when checking actionable Markdown links."""
    return re.sub(r"(?ms)^\s*(`{3,}|~{3,}).*?^\s*\1\s*$", "", text)


def check(root: Path) -> dict:
    entries = inventory(root)
    errors = []
    descriptions = {}
    source_paths = {
        p.relative_to(root).as_posix()
        for base in (".claude/skills", ".claude/skills-global")
        for p in (root / base).glob("*/SKILL.md")
    }
    target_paths = {
        p.parent.relative_to(root).as_posix()
        for base in (".agents/skills", ".agents/skills-global")
        for p in (root / base).glob("*/SKILL.md")
    }
    if source_paths != {e["source"] for e in entries if e["source"] is not None}:
        errors.append("Claude source inventory differs: review newly added/removed skills")
    if target_paths != {e["target"] for e in entries}:
        errors.append("Codex target inventory differs: every skill needs one registered target")
    for entry in entries:
        name = entry["name"]
        folder = root / entry["target"]
        try:
            _, description = metadata(folder / "SKILL.md")
            if description in descriptions:
                errors.append(f"{name}: duplicate description with {descriptions[description]}")
            descriptions[description] = name
            bundled = files(folder)
            if set(bundled) != set(entry["resources"]):
                errors.append(f"{name}: bundled resources differ from inventory")
            if entry["source"] is not None:
                source_folder = (root / entry["source"]).parent
                current_source = {
                    (source_folder / rel).relative_to(root).as_posix(): digest
                    for rel, digest in files(source_folder).items()
                }
                if current_source != entry["source_files"]:
                    errors.append(f"{name}: Claude source changed; review the Codex equivalent")
            for rel in bundled:
                path = folder / rel
                if path.suffix == ".py":
                    compile(path.read_bytes(), str(path), "exec")
                if path.suffix != ".md":
                    continue
                for raw in LINK.findall(prose(path.read_text())):
                    url = urlsplit(raw.strip("<>"))
                    if url.scheme or url.netloc or not url.path or url.path.startswith("/"):
                        continue
                    destination = (path.parent / unquote(url.path)).resolve()
                    if not destination.exists():
                        errors.append(f"{name}/{rel}: broken link {raw}")
                    # User-global copies must not depend on sibling repository files.
                    if entry["scope"] == "global" and not destination.is_relative_to(
                        root / ".agents/skills-global"
                    ):
                        errors.append(f"{name}/{rel}: nonportable global link {raw}")
        except (OSError, ValueError, SyntaxError, KeyError) as exc:
            errors.append(f"{name}: {exc}")
    return {
        "ok": not errors,
        "skills": len(entries),
        "global": sum(e["scope"] == "global" for e in entries),
        "project": sum(e["scope"] == "project" for e in entries),
        "errors": errors,
    }


def save_state(target: Path, state: dict) -> None:
    """Replace installer ownership metadata atomically."""
    fd, temporary = tempfile.mkstemp(prefix=".valor-skills-state-", dir=target)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(state, handle, indent=2)
            handle.write("\n")
        os.replace(temporary, target / STATE_NAME)
    finally:
        Path(temporary).unlink(missing_ok=True)


def install(root: Path, target: Path, dry_run: bool = False) -> dict:
    report = check(root)
    if not report["ok"]:
        raise ValueError("Validation failed:\n" + "\n".join(report["errors"]))
    target = target.expanduser().absolute()
    # Do not install through a surprise symlink or into either canonical collection.
    if target.is_symlink():
        raise ValueError(f"Install target is a symlink: {target}")
    # macOS /var and /tmp are ordinary system symlinks; resolve parent aliases.
    target = target.resolve()
    if target.is_relative_to(root.resolve() / ".agents"):
        raise ValueError("Install target cannot be the canonical source collection")
    state_path = target / STATE_NAME
    if state_path.is_symlink():
        raise ValueError(f"Installer state is a symlink: {state_path}")
    state = (
        json.loads(state_path.read_text()) if state_path.exists() else {"version": 1, "skills": {}}
    )
    if state.get("version") != 1 or not isinstance(state.get("skills"), dict):
        raise ValueError("Invalid installer ownership state")
    entries = [entry for entry in inventory(root) if entry["scope"] == "global"]
    changes, unchanged, conflicts = [], [], []
    # Preflight the entire batch before making any change.
    for entry in entries:
        name = entry["name"]
        source, destination = root / entry["target"], target / name
        wanted = files(source)
        if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
            conflicts.append(f"{name}: existing path is not a managed directory")
            continue
        if destination.exists():
            current = files(destination)
            previous = state["skills"].get(name)
            if previous is None:
                conflicts.append(f"{name}: unmanaged existing skill (left untouched)")
            elif current != previous:
                conflicts.append(f"{name}: locally edited installed skill (left untouched)")
            elif current == wanted:
                unchanged.append(name)
            else:
                changes.append((name, source, destination, wanted))
        else:
            changes.append((name, source, destination, wanted))
    if conflicts:
        raise ValueError("Installation conflicts; no changes made:\n" + "\n".join(conflicts))
    result = {
        "target": str(target),
        "dry_run": dry_run,
        "changed": [change[0] for change in changes],
        "unchanged": unchanged,
    }
    if dry_run or not changes:
        return result
    target.mkdir(parents=True, exist_ok=True)
    for name, source, destination, wanted in changes:
        # A staging folder has no discoverable SKILL.md at its root.
        with tempfile.TemporaryDirectory(prefix=".valor-skills-", dir=target.parent) as temporary:
            stage = Path(temporary) / name
            backup = Path(temporary) / "previous"
            shutil.copytree(source, stage, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            if files(stage) != wanted:
                raise ValueError(f"Source changed while copying {name}; rerun validation")
            if destination.exists():
                # Detect edits made since batch preflight before replacing anything.
                if files(destination) != state["skills"].get(name):
                    raise ValueError(f"Installed {name} changed during installation; stopped")
                destination.rename(backup)
            try:
                stage.rename(destination)
                state["skills"][name] = wanted
                save_state(target, state)
            except Exception:
                if destination.exists():
                    shutil.rmtree(destination)
                if backup.exists():
                    backup.rename(destination)
                raise
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    lint = sub.add_parser(
        "check", help="Check all source/target coverage, metadata, links, and drift"
    )
    lint.add_argument("--json", action="store_true", help="Emit a machine-readable report")
    deploy = sub.add_parser("install", help="Copy registered global skills into user discovery")
    deploy.add_argument("--target", type=Path, default=Path.home() / ".agents/skills")
    deploy.add_argument(
        "--dry-run", action="store_true", help="Validate and show changes without writing"
    )
    args = parser.parse_args()
    try:
        if args.command == "check":
            result = check(ROOT)
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print(
                    f"{result['skills']} skills: {result['global']} global, "
                    f"{result['project']} project"
                )
                for error in result["errors"]:
                    print(f"FAIL: {error}")
                print("PASS" if result["ok"] else "FAIL")
            return 0 if result["ok"] else 1
        print(json.dumps(install(ROOT, args.target, args.dry_run), indent=2))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
