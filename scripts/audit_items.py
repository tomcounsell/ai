#!/usr/bin/env python3
"""
Phase 2: Per-item SDLC audit.
Checks each plan's deliverables against HEAD, producing structured JSON findings.
Read-only: does not modify any code.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

WORKTREE = Path(__file__).parent.parent
DATA_DIR = WORKTREE / "data"
FINDINGS_DIR = DATA_DIR / "retroactive-audit-findings"
AUDIT_SET_FILE = DATA_DIR / "retroactive-audit-set.json"


def run(cmd, cwd=None):
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True,
        cwd=cwd or str(WORKTREE)
    )
    return result.stdout.strip(), result.returncode


def file_exists_at_head(filepath):
    """Check if a file exists in the current HEAD."""
    path = WORKTREE / filepath
    return path.exists()


def extract_doc_tasks(plan_content):
    """Extract documentation tasks from ## Documentation section."""
    if not plan_content:
        return []

    section_match = re.search(r'## Documentation\n(.*?)(?=\n## |\Z)', plan_content, re.DOTALL)
    if not section_match:
        return []

    section = section_match.group(1)
    tasks = []

    # Find checkbox items with file paths
    for line in section.split('\n'):
        if '- [ ]' in line or '- [x]' in line or '- [X]' in line:
            # Extract file paths from the line
            paths = re.findall(r'`(docs/[^`]+\.md)`|`(docs/[^`]+)`', line)
            for p1, p2 in paths:
                fp = p1 or p2
                if fp:
                    tasks.append({
                        'task': line.strip(),
                        'expected_file': fp,
                    })

    return tasks


def extract_test_impact(plan_content):
    """Extract test impact items from ## Test Impact section."""
    if not plan_content:
        return []

    section_match = re.search(r'## Test Impact\n(.*?)(?=\n## |\Z)', plan_content, re.DOTALL)
    if not section_match:
        return []

    section = section_match.group(1)
    tasks = []

    # Skip if explicitly says "no existing tests affected"
    if 'no existing tests affected' in section.lower():
        return []

    for line in section.split('\n'):
        if '- [ ]' in line or '- [x]' in line or '- [X]' in line:
            # Extract test file paths
            paths = re.findall(r'`(tests/[^`]+\.py(?:::[^`]+)?)`', line)
            for p in paths:
                # Strip test method/function (after ::)
                filepath = p.split('::')[0]
                tasks.append({
                    'task': line.strip(),
                    'expected_file': filepath,
                })

    return tasks


def extract_file_assertions(plan_content):
    """Extract file existence assertions from success criteria and other sections."""
    if not plan_content:
        return []

    assertions = []

    # Look for file paths mentioned in success criteria
    criteria_match = re.search(r'## Success Criteria\n(.*?)(?=\n## |\Z)', plan_content, re.DOTALL)
    if criteria_match:
        section = criteria_match.group(1)
        for line in section.split('\n'):
            if '- [ ]' in line or '- [x]' in line:
                # Extract file paths
                paths = re.findall(r'`([a-zA-Z_/][a-zA-Z0-9_/.-]+\.(py|md|json|sh|yaml|yml|txt))`', line)
                for p, ext in paths:
                    if not p.startswith('/') and '/' in p:
                        assertions.append({
                            'context': line.strip(),
                            'expected_file': p,
                            'section': 'Success Criteria',
                        })

    return assertions


def check_item(item):
    """Audit a single item against current HEAD state."""
    slug = item.get('plan_slug', f"issue-{item['issue_number']}")
    plan_content = item.get('plan_content', '')
    issue_number = item['issue_number']
    recoverable = item.get('recoverable', False)

    findings = []

    if not recoverable or not plan_content:
        return {
            'issue_number': issue_number,
            'plan_slug': slug,
            'issue_title': item.get('issue_title', ''),
            'merged_pr_number': item.get('merged_pr_number'),
            'auditable': False,
            'reason': 'Plan content not recoverable — using issue body only; skipping file assertions',
            'findings': [],
        }

    # Check frontmatter for proper structure
    has_frontmatter = plan_content.startswith('---') and '---' in plan_content[3:]
    if not has_frontmatter:
        return {
            'issue_number': issue_number,
            'plan_slug': slug,
            'issue_title': item.get('issue_title', ''),
            'merged_pr_number': item.get('merged_pr_number'),
            'auditable': False,
            'reason': 'No structured frontmatter — pre-SDLC format, cannot meaningfully audit',
            'findings': [],
        }

    # 1. Check Documentation section tasks
    doc_tasks = extract_doc_tasks(plan_content)
    for task in doc_tasks:
        fp = task['expected_file']
        exists = file_exists_at_head(fp)
        if not exists:
            findings.append({
                'type': 'missing_doc',
                'severity': 'high',
                'still_relevant': True,
                'confidence': 'high',
                'expected_file': fp,
                'evidence': task['task'],
                'description': f"Documentation task says '{fp}' should exist but it does not at HEAD",
            })

    # 2. Check Test Impact section
    test_tasks = extract_test_impact(plan_content)
    for task in test_tasks:
        fp = task['expected_file']
        exists = file_exists_at_head(fp)
        if not exists:
            # Check if test was supposed to be deleted
            task_text = task['task'].lower()
            if 'delete' in task_text or 'remove' in task_text:
                # Expected to be deleted — check if it still exists
                findings.append({
                    'type': 'stale_test_file',
                    'severity': 'low',
                    'still_relevant': True,
                    'confidence': 'medium',
                    'expected_file': fp,
                    'evidence': task['task'],
                    'description': f"Test file was supposed to be deleted but checking state unclear",
                })
            else:
                findings.append({
                    'type': 'missing_test',
                    'severity': 'medium',
                    'still_relevant': True,
                    'confidence': 'high',
                    'expected_file': fp,
                    'evidence': task['task'],
                    'description': f"Test Impact says '{fp}' should exist but it does not at HEAD",
                })

    # 3. Check Success Criteria file assertions
    assertions = extract_file_assertions(plan_content)
    for assertion in assertions:
        fp = assertion['expected_file']
        exists = file_exists_at_head(fp)
        if not exists:
            # Check if the path pattern matches something real
            # (Some paths may be data files or config files that are gitignored)
            findings.append({
                'type': 'missing_artifact',
                'severity': 'medium',
                'still_relevant': True,
                'confidence': 'medium',
                'expected_file': fp,
                'evidence': assertion['context'],
                'section': assertion['section'],
                'description': f"Success Criteria references '{fp}' which does not exist at HEAD",
            })

    # 4. Quick relevance check: if feature appears to be removed/replaced
    # Check for key module references in plan content
    # If plan mentions a specific module that no longer exists, some findings may not be relevant
    # Simple heuristic: check if the main source files mentioned in the PR diff still exist
    for finding in findings:
        fp = finding.get('expected_file', '')
        if fp:
            # Check if the file exists anywhere under a different path (rename)
            basename = Path(fp).name
            alt_search, _ = run(f"find . -name '{basename}' -not -path './.git/*' -not -path './.worktrees/*' 2>/dev/null | head -3")
            if alt_search and basename.endswith('.md'):
                finding['note'] = f"A file named '{basename}' exists at: {alt_search.split(chr(10))[0]}"

    return {
        'issue_number': issue_number,
        'plan_slug': slug,
        'issue_title': item.get('issue_title', ''),
        'merged_pr_number': item.get('merged_pr_number'),
        'auditable': True,
        'findings': findings,
        'doc_tasks_checked': len(doc_tasks),
        'test_tasks_checked': len(test_tasks),
        'file_assertions_checked': len(assertions),
    }


def main():
    FINDINGS_DIR.mkdir(parents=True, exist_ok=True)

    if not AUDIT_SET_FILE.exists():
        print("ERROR: audit set not found. Run build_audit_set.py first.")
        sys.exit(1)

    audit_set = json.loads(AUDIT_SET_FILE.read_text())
    print(f"=== Phase 2: Per-Item Audit ===\n")
    print(f"Processing {len(audit_set)} items...\n")

    total_findings = 0
    auditable_count = 0

    for i, item in enumerate(audit_set):
        slug = item.get('plan_slug', f"issue-{item['issue_number']}")
        print(f"[{i+1}/{len(audit_set)}] Auditing #{item['issue_number']}: {slug}")

        result = check_item(item)

        if result['auditable']:
            auditable_count += 1
            finding_count = len(result['findings'])
            total_findings += finding_count
            print(f"  -> {finding_count} findings (docs:{result.get('doc_tasks_checked',0)}, tests:{result.get('test_tasks_checked',0)}, artifacts:{result.get('file_assertions_checked',0)})")
        else:
            print(f"  -> NOT AUDITABLE: {result.get('reason', '')[:80]}")

        # Save individual findings file
        out_file = FINDINGS_DIR / f"{slug}.json"
        out_file.write_text(json.dumps(result, indent=2))

    print(f"\n=== Phase 2 Complete ===")
    print(f"Auditable items: {auditable_count}/{len(audit_set)}")
    print(f"Total findings: {total_findings}")
    print(f"Findings saved to: {FINDINGS_DIR}")


if __name__ == '__main__':
    main()
