#!/usr/bin/env python3
"""Scanner for documentation potentially affected by code changes.

Scans all markdown files in docs/ for references to changed files and
returns a confidence-scored list of potentially affected documentation.

Confidence levels:
    HIGH: Direct file path reference (exact match)
    MED-HIGH: Direct function/class name reference
    MED: Directory or module reference
    LOW: Keyword match (filename without extension)

Usage:
    python scripts/scan_related_docs.py file1.py file2.py
    python scripts/scan_related_docs.py --json file1.py file2.py

Examples:
    # Scan for docs referencing bridge code
    python scripts/scan_related_docs.py bridge/telegram_bridge.py

    # Check multiple files with JSON output
    python scripts/scan_related_docs.py --json tools/search.py mcp_servers/social.py

Exit codes:
    0 - Success
    1 - Error (invalid arguments, missing docs directory, etc.)
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


def extract_code_identifiers(file_path: str) -> dict[str, list[str]]:
    """Extract function and class names from a Python file.

    Args:
        file_path: Path to Python file to analyze

    Returns:
        Dict with 'functions' and 'classes' lists of identifier names.
        Returns empty lists if file doesn't exist or isn't Python.
    """
    path = Path(file_path)

    if not path.exists() or path.suffix != ".py":
        return {"functions": [], "classes": []}

    try:
        content = path.read_text()
    except Exception:
        return {"functions": [], "classes": []}

    # Extract function definitions (def function_name)
    functions = re.findall(
        r"^\s*def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", content, re.MULTILINE
    )

    # Extract class definitions (class ClassName)
    classes = re.findall(
        r"^\s*class\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*[\(:]", content, re.MULTILINE
    )

    return {
        "functions": list(set(functions)),
        "classes": list(set(classes)),
    }


def normalize_path(file_path: str, repo_root: Path) -> str:
    """Normalize a file path to be relative to repo root.

    Args:
        file_path: File path (absolute or relative)
        repo_root: Repository root directory

    Returns:
        Path relative to repo root with forward slashes
    """
    path = Path(file_path)

    # Convert to absolute if relative
    if not path.is_absolute():
        path = repo_root / path

    # Make relative to repo root
    try:
        rel_path = path.relative_to(repo_root)
    except ValueError:
        # Path is outside repo, use as-is
        rel_path = path

    return str(rel_path).replace("\\", "/")


def scan_doc_for_references(
    doc_path: Path,
    changed_files: list[str],
    identifiers_map: dict[str, dict[str, list[str]]],
) -> dict[str, Any]:
    """Scan a single doc file for references to changed files.

    Args:
        doc_path: Path to markdown file
        changed_files: List of normalized changed file paths
        identifiers_map: Map of file paths to their extracted identifiers

    Returns:
        Dict with confidence level and matched patterns, or None if no matches
    """
    try:
        content = doc_path.read_text()
    except Exception:
        return None

    matches = {
        "HIGH": [],
        "MED-HIGH": [],
        "MED": [],
        "LOW": [],
    }

    for changed_file in changed_files:
        changed_path = Path(changed_file)
        filename = changed_path.name
        filename_no_ext = changed_path.stem
        parent_dir = str(changed_path.parent)

        # HIGH: Direct file path reference (with or without backticks)
        # Match patterns: path/to/file.py, `path/to/file.py`, (path/to/file.py)
        if re.search(
            rf"[`\(]?{re.escape(changed_file)}[`\)]?",
            content,
            re.IGNORECASE,
        ):
            matches["HIGH"].append(f"File path: {changed_file}")

        # HIGH: File referenced in markdown link or code block
        if re.search(
            rf"\[.*?\]\(.*?{re.escape(filename)}.*?\)",
            content,
            re.IGNORECASE,
        ):
            matches["HIGH"].append(f"Markdown link: {filename}")

        # MED-HIGH: Function or class name reference
        identifiers = identifiers_map.get(changed_file, {})
        for func in identifiers.get("functions", []):
            # Match function calls: func() or func( or `func()`
            if re.search(
                rf"`?{re.escape(func)}\s*\(`?",
                content,
            ):
                matches["MED-HIGH"].append(f"Function: {func}()")

        for cls in identifiers.get("classes", []):
            # Match class references: ClassName or `ClassName`
            if re.search(
                rf"\b`?{re.escape(cls)}`?\b",
                content,
            ):
                matches["MED-HIGH"].append(f"Class: {cls}")

        # MED: Directory or module reference
        if parent_dir and parent_dir != ".":
            # Match directory in paths or standalone
            if re.search(
                rf"[`\(]?{re.escape(parent_dir)}[/`\)]",
                content,
                re.IGNORECASE,
            ):
                matches["MED"].append(f"Directory: {parent_dir}/")

        # LOW: Filename without extension (keyword match)
        # Only match as word boundary to avoid partial matches
        if re.search(
            rf"\b{re.escape(filename_no_ext)}\b",
            content,
            re.IGNORECASE,
        ):
            # Don't count if already matched at higher confidence
            already_matched = any(
                matches["HIGH"] or matches["MED-HIGH"] or matches["MED"]
            )
            if not already_matched:
                matches["LOW"].append(f"Keyword: {filename_no_ext}")

    # Determine highest confidence level with matches
    for level in ["HIGH", "MED-HIGH", "MED", "LOW"]:
        if matches[level]:
            return {
                "confidence": level,
                "matches": matches[level],
            }

    return None


def scan_docs_directory(
    changed_files: list[str],
    docs_dir: Path,
    repo_root: Path,
) -> list[dict[str, Any]]:
    """Scan all markdown files in docs directory for references.

    Args:
        changed_files: List of changed file paths (normalized)
        docs_dir: Path to docs directory
        repo_root: Repository root directory

    Returns:
        List of dicts with doc_path, confidence, and matches
    """
    if not docs_dir.exists():
        return []

    # Extract identifiers from changed Python files
    identifiers_map = {}
    for file_path in changed_files:
        full_path = str(repo_root / file_path)
        identifiers_map[file_path] = extract_code_identifiers(full_path)

    results = []

    # Find all markdown files
    for doc_path in docs_dir.rglob("*.md"):
        match_info = scan_doc_for_references(doc_path, changed_files, identifiers_map)

        if match_info:
            results.append(
                {
                    "doc_path": str(doc_path.relative_to(repo_root)),
                    "confidence": match_info["confidence"],
                    "matches": match_info["matches"],
                }
            )

    # Sort by confidence level (HIGH > MED-HIGH > MED > LOW)
    confidence_order = {"HIGH": 0, "MED-HIGH": 1, "MED": 2, "LOW": 3}
    results.sort(key=lambda x: confidence_order[x["confidence"]])

    return results


def format_results_text(results: list[dict[str, Any]]) -> str:
    """Format results as human-readable text.

    Args:
        results: List of scan results

    Returns:
        Formatted text output
    """
    if not results:
        return "No documentation references found."

    output = []
    output.append(f"Found {len(results)} document(s) with references:\n")

    # Group by confidence level
    by_confidence = defaultdict(list)
    for result in results:
        by_confidence[result["confidence"]].append(result)

    for level in ["HIGH", "MED-HIGH", "MED", "LOW"]:
        docs = by_confidence.get(level, [])
        if not docs:
            continue

        output.append(f"\n{level} Confidence ({len(docs)} doc(s)):")
        output.append("-" * 50)

        for doc in docs:
            output.append(f"  {doc['doc_path']}")
            for match in doc["matches"][:3]:  # Show first 3 matches
                output.append(f"    - {match}")
            if len(doc["matches"]) > 3:
                output.append(f"    ... and {len(doc['matches']) - 3} more")

    return "\n".join(output)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scan documentation for references to changed files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage:")[1] if "Usage:" in __doc__ else "",
    )
    parser.add_argument(
        "files",
        nargs="+",
        help="Changed file paths to scan for",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--docs-dir",
        type=Path,
        help="Documentation directory (default: docs/)",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        help="Repository root directory (default: current directory)",
    )

    args = parser.parse_args()

    # Determine repo root
    if args.repo_root:
        repo_root = args.repo_root.resolve()
    else:
        # Try to find git root
        current = Path.cwd()
        while current != current.parent:
            if (current / ".git").exists():
                repo_root = current
                break
            current = current.parent
        else:
            repo_root = Path.cwd()

    # Determine docs directory
    docs_dir = args.docs_dir if args.docs_dir else repo_root / "docs"

    if not docs_dir.exists():
        print(f"Error: Documentation directory not found: {docs_dir}", file=sys.stderr)
        return 1

    # Normalize changed file paths
    changed_files = [normalize_path(f, repo_root) for f in args.files]

    # Scan documentation
    results = scan_docs_directory(changed_files, docs_dir, repo_root)

    # Output results
    if args.json:
        output = {
            "changed_files": changed_files,
            "docs_directory": str(docs_dir.relative_to(repo_root)),
            "total_matches": len(results),
            "results": results,
        }
        print(json.dumps(output, indent=2))
    else:
        print(format_results_text(results))

    return 0


if __name__ == "__main__":
    sys.exit(main())
