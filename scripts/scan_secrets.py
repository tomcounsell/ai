#!/usr/bin/env python3
"""Scan repository for leaked secrets in files and git history.

Usage:
    python scripts/scan_secrets.py              # Scan current files only
    python scripts/scan_secrets.py --history    # Also scan git history (slower)
    python scripts/scan_secrets.py --staged     # Scan only staged files (for pre-commit)
"""

import argparse
import os
import re
import subprocess
import sys

# Patterns that indicate real secrets (not placeholders)
SECRET_PATTERNS = [
    # Anthropic
    (r"sk-ant-api\d{2}-[A-Za-z0-9_-]{20,}", "Anthropic API Key"),
    # OpenAI
    (r"sk-[A-Za-z0-9]{32,}", "OpenAI API Key"),
    # Notion
    (r"ntn_[A-Za-z0-9]{30,}", "Notion API Key"),
    (r"secret_[A-Za-z0-9]{30,}", "Notion Integration Secret"),
    # GitHub
    (r"ghp_[A-Za-z0-9]{36,}", "GitHub Personal Access Token"),
    (r"gho_[A-Za-z0-9]{36,}", "GitHub OAuth Token"),
    (r"ghs_[A-Za-z0-9]{36,}", "GitHub Server Token"),
    (r"ghr_[A-Za-z0-9]{36,}", "GitHub Refresh Token"),
    # Slack
    (r"xoxb-[A-Za-z0-9-]{20,}", "Slack Bot Token"),
    (r"xoxp-[A-Za-z0-9-]{20,}", "Slack User Token"),
    (r"xoxa-[A-Za-z0-9-]{20,}", "Slack App Token"),
    # Telegram
    (r"\d{8,10}:[A-Za-z0-9_-]{35}", "Telegram Bot Token"),
    # Stripe
    (r"sk_live_[A-Za-z0-9]{20,}", "Stripe Live Secret Key"),
    (r"rk_live_[A-Za-z0-9]{20,}", "Stripe Live Restricted Key"),
    # AWS
    (r"AKIA[0-9A-Z]{16}", "AWS Access Key ID"),
    # Perplexity
    (r"pplx-[A-Za-z0-9]{40,}", "Perplexity API Key"),
    # Linear
    (r"lin_api_[A-Za-z0-9]{30,}", "Linear API Key"),
    # Render
    (r"rnd_[A-Za-z0-9]{30,}", "Render API Key"),
    # Sentry
    (r"sntrys_[A-Za-z0-9]{30,}", "Sentry Auth Token"),
    # Superface
    (r"sfs_[A-Za-z0-9]{30,}", "Superface SDK Token"),
    # Generic long Bearer tokens (hardcoded, not from env vars)
    (r'["\']Bearer\s+[A-Za-z0-9_-]{40,}["\']', "Hardcoded Bearer Token"),
    # Generic private keys
    (r"-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----", "Private Key"),
]

# Files/patterns to skip
SKIP_PATTERNS = [
    r"\.env\.example$",
    r"\.env\.template$",
    r"scan_secrets\.py$",
    r"\.git/",
    r"__pycache__/",
    r"\.pyc$",
    r"node_modules/",
    r"security-reviewer\.md$",
]

# Content patterns that indicate placeholders (not real secrets)
PLACEHOLDER_PATTERNS = [
    r"\*\*\*\*",
    r"your[_-]",
    r"_here",
    r"example",
    r"EXAMPLE",
    r"placeholder",
    r"<your",
    r"TODO",
    r"\.\.\.",
]


def should_skip_file(filepath: str) -> bool:
    for pattern in SKIP_PATTERNS:
        if re.search(pattern, filepath):
            return True
    return False


def is_placeholder(line: str) -> bool:
    for pattern in PLACEHOLDER_PATTERNS:
        if re.search(pattern, line, re.IGNORECASE):
            return True
    return False


def scan_content(content: str, filepath: str = "<unknown>") -> list[dict]:
    findings = []
    for line_num, line in enumerate(content.split("\n"), 1):
        if is_placeholder(line):
            continue
        # Skip comment-only lines that are documenting bad practices
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("//"):
            continue
        for pattern, secret_type in SECRET_PATTERNS:
            matches = re.findall(pattern, line)
            if matches:
                for match in matches:
                    # Skip if inside an f-string variable reference
                    if f"{{{match}" in line or f"${match}" in line:
                        continue
                    findings.append(
                        {
                            "file": filepath,
                            "line": line_num,
                            "type": secret_type,
                            "match": match[:20] + "..." if len(match) > 20 else match,
                            "context": line.strip()[:100],
                        }
                    )
    return findings


def is_git_tracked(filepath: str, repo_root: str) -> bool:
    """Check if a file is tracked by git (not gitignored)."""
    result = subprocess.run(
        ["git", "check-ignore", "-q", filepath],
        capture_output=True,
        cwd=repo_root,
    )
    # Exit 0 means file IS ignored, exit 1 means it's tracked
    return result.returncode != 0


def scan_current_files(repo_root: str) -> list[dict]:
    all_findings = []
    for root, dirs, files in os.walk(repo_root):
        # Skip hidden dirs and common non-code dirs
        dirs[:] = [
            d
            for d in dirs
            if not d.startswith(".")
            and d not in ("node_modules", "__pycache__", "venv", ".venv")
        ]
        for fname in files:
            filepath = os.path.join(root, fname)
            relpath = os.path.relpath(filepath, repo_root)
            if should_skip_file(relpath):
                continue
            # Only scan git-tracked files (skip gitignored files like .env)
            if not is_git_tracked(relpath, repo_root):
                continue
            try:
                with open(filepath, errors="ignore") as f:
                    content = f.read()
                findings = scan_content(content, relpath)
                all_findings.extend(findings)
            except (OSError, UnicodeDecodeError):
                continue
    return all_findings


def scan_staged_files(repo_root: str) -> list[dict]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    staged_files = result.stdout.strip().split("\n")
    all_findings = []
    for relpath in staged_files:
        if not relpath or should_skip_file(relpath):
            continue
        # Get staged content (not working tree content)
        result = subprocess.run(
            ["git", "show", f":{relpath}"],
            capture_output=True,
            text=True,
            cwd=repo_root,
        )
        if result.returncode == 0:
            findings = scan_content(result.stdout, relpath)
            all_findings.extend(findings)
    return all_findings


def scan_git_history(repo_root: str) -> list[dict]:
    all_findings = []
    # Extract only the added lines from full git history
    for pattern, secret_type in SECRET_PATTERNS:
        result = subprocess.run(
            ["git", "log", "--all", "-p", f"-G{pattern}"],
            capture_output=True,
            text=True,
            cwd=repo_root,
        )
        if result.returncode != 0:
            continue
        current_commit = ""
        current_file = ""
        for line in result.stdout.split("\n"):
            if line.startswith("commit "):
                current_commit = line.split()[1][:8]
            elif line.startswith("diff --git"):
                parts = line.split(" b/")
                current_file = parts[-1] if len(parts) > 1 else ""
            elif line.startswith("+") and not line.startswith("+++"):
                content = line[1:]
                if is_placeholder(content):
                    continue
                if re.search(pattern, content):
                    all_findings.append(
                        {
                            "file": f"{current_file} (commit {current_commit})",
                            "line": 0,
                            "type": secret_type,
                            "match": "***REDACTED***",
                            "context": content.strip()[:80] + "...",
                        }
                    )
    # Deduplicate
    seen = set()
    unique = []
    for f in all_findings:
        key = (f["file"], f["type"])
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def main():
    parser = argparse.ArgumentParser(description="Scan for leaked secrets")
    parser.add_argument("--history", action="store_true", help="Also scan git history")
    parser.add_argument(
        "--staged", action="store_true", help="Scan only staged files (pre-commit)"
    )
    args = parser.parse_args()

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    if args.staged:
        findings = scan_staged_files(repo_root)
        label = "staged files"
    else:
        findings = scan_current_files(repo_root)
        label = "current files"

    if findings:
        print(f"\nSECRETS FOUND in {label}:")
        print("=" * 60)
        for f in findings:
            print(f"  [{f['type']}] {f['file']}:{f['line']}")
            print(f"    Match: {f['match']}")
            print(f"    Context: {f['context']}")
            print()
    else:
        print(f"No secrets found in {label}.")

    history_findings = []
    if args.history:
        print("\nScanning git history (this may take a moment)...")
        history_findings = scan_git_history(repo_root)
        if history_findings:
            print("\nSECRETS FOUND in git history:")
            print("=" * 60)
            for f in history_findings:
                print(f"  [{f['type']}] {f['file']}")
                print(f"    Context: {f['context']}")
                print()
            print("WARNING: Secrets in git history require key rotation.")
            print(
                "Consider using 'git filter-repo' or BFG Repo-Cleaner to remove them."
            )
        else:
            print("No secrets found in git history.")

    total = len(findings) + len(history_findings)
    if total > 0:
        print(f"\nTotal findings: {total}")
        sys.exit(1)
    else:
        print("\nAll clear - no secrets detected.")
        sys.exit(0)


if __name__ == "__main__":
    main()
