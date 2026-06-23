"""reflections/housekeeping/merged_branch_cleanup.py — Prune merged branches, audit plan files.

What it does: Deletes local git branches merged into main and audits docs/plans/
    for complete/orphaned/closed-issue plans (deletes branches; reads plan files
    and queries GitHub issue state via gh).
Cadence: 86400s (daily) (keeps the branch list and plans dir from accreting cruft)
Failure modes:
    - git/gh subprocess failure or timeout -> caught per step, logged, skipped
    - missing docs/plans dir -> returns early with branch-cleanup findings only
Related reflections:
    - tech_debt_scan: complementary stale-artifact audit over project source
See also: config/reflections.yaml (declaration), docs/features/reflections.md
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

from reflections.utilities import PROJECT_ROOT, load_local_projects

logger = logging.getLogger("reflections.maintenance")


async def run() -> dict:
    """Clean up stale git branches and audit plan files.

    - Deletes local branches merged into main
    - Audits docs/plans/ for complete/orphaned/stale-issue plans
    """
    findings: list[str] = []
    projects = load_local_projects()

    # --- Stale branch cleanup ---
    try:
        result = await asyncio.create_subprocess_exec(
            "git",
            "branch",
            "--merged",
            "main",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(PROJECT_ROOT),
        )
        stdout, _ = await asyncio.wait_for(result.communicate(), timeout=15)
        if result.returncode == 0:
            for line in stdout.decode().splitlines():
                branch = line.strip().lstrip("* ")
                if branch and branch not in ("main", "master"):
                    del_proc = await asyncio.create_subprocess_exec(
                        "git",
                        "branch",
                        "-d",
                        branch,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        cwd=str(PROJECT_ROOT),
                    )
                    await asyncio.wait_for(del_proc.communicate(), timeout=10)
                    if del_proc.returncode == 0:
                        findings.append(f"Deleted merged branch: {branch}")
                        logger.info(f"Branch cleanup: deleted merged branch {branch}")
    except Exception as e:
        logger.warning(f"Branch cleanup failed (non-fatal): {e}")

    # --- Plan file cleanup ---
    plans_dir = PROJECT_ROOT / "docs" / "plans"
    if not plans_dir.exists():
        return {
            "status": "ok",
            "findings": findings,
            "summary": f"Branch cleanup: {len(findings)} finding(s)",
        }

    plan_files = sorted(plans_dir.glob("*.md"))

    project_wd = None
    for project in projects:
        if project.get("github"):
            project_wd = project["working_directory"]
            break

    # Detect duplicates
    normalized: dict[str, list[Path]] = {}
    for pf in plan_files:
        key = pf.stem.replace("-", "_").lower()
        normalized.setdefault(key, []).append(pf)
    for _key, dupes in normalized.items():
        if len(dupes) > 1:
            names = ", ".join(d.name for d in dupes)
            findings.append(f"Duplicate plans: {names}")

    # Extract issue refs
    plan_issue_refs: dict[Path, list[int]] = {}
    for plan_file in plan_files:
        plan_text = plan_file.read_text(errors="replace")
        refs: set[int] = set()
        for m in re.finditer(r"#(\d+)", plan_text):
            refs.add(int(m.group(1)))
        for m in re.finditer(r"github\.com/[^/]+/[^/]+/issues/(\d+)", plan_text):
            refs.add(int(m.group(1)))
        plan_issue_refs[plan_file] = sorted(refs)

    # Check issue states
    async def check_issue_state(issue_num: int) -> tuple[int, str]:
        if not project_wd:
            return issue_num, "unknown"
        try:
            proc = await asyncio.create_subprocess_exec(
                "gh",
                "issue",
                "view",
                str(issue_num),
                "--json",
                "state",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=project_wd,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                data = json.loads(stdout.decode())
                return issue_num, data.get("state", "unknown").lower()
        except Exception as e:
            logger.warning(f"Could not check issue #{issue_num}: {e}")
        return issue_num, "unknown"

    all_issue_nums: set[int] = set()
    for refs in plan_issue_refs.values():
        all_issue_nums.update(refs)

    issue_states: dict[int, str] = {}
    if all_issue_nums:
        issue_list = sorted(all_issue_nums)
        for i in range(0, len(issue_list), 10):
            batch = issue_list[i : i + 10]
            results = await asyncio.gather(
                *[check_issue_state(n) for n in batch], return_exceptions=True
            )
            for r in results:
                if isinstance(r, tuple):
                    issue_states[r[0]] = r[1]

    stats = {"complete": 0, "orphaned": 0, "closed_issue": 0, "active": 0}

    for plan_file in plan_files:
        plan_name = plan_file.stem
        plan_text = plan_file.read_text(errors="replace")
        refs = plan_issue_refs.get(plan_file, [])

        checkboxes = re.findall(r"- \[([ xX])\]", plan_text)
        checked = sum(1 for c in checkboxes if c.lower() == "x")
        is_complete = checkboxes and checked == len(checkboxes)

        if is_complete:
            stats["complete"] += 1
            findings.append(
                f"Plan complete: {plan_name} -- "
                f"run /do-docs then delete docs/plans/{plan_file.name}"
            )
            continue

        if refs:
            ref_states = [issue_states.get(r, "unknown") for r in refs]
            all_closed = all(s == "closed" for s in ref_states if s != "unknown")
            any_open = any(s == "open" for s in ref_states)

            if all_closed and not any_open:
                stats["closed_issue"] += 1
                closed_refs = ", ".join(f"#{r}" for r in refs)
                findings.append(f"Plan with closed issue(s): {plan_file.name} ({closed_refs})")
                continue

            if any_open:
                stats["active"] += 1
                continue

        if project_wd:
            try:
                gh_proc = await asyncio.create_subprocess_exec(
                    "gh",
                    "issue",
                    "list",
                    "--state",
                    "open",
                    "--search",
                    plan_name,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=project_wd,
                )
                gh_stdout, _ = await asyncio.wait_for(gh_proc.communicate(), timeout=15)
                if gh_proc.returncode == 0 and gh_stdout.strip():
                    stats["active"] += 1
                    continue
            except Exception as e:
                logger.warning(f"Could not search issues for plan {plan_name}: {e}")

        stats["orphaned"] += 1
        findings.append(f"Orphaned plan (no open issue): {plan_file.name}")

    summary = (
        f"Branch/plan cleanup: {len(findings)} finding(s), "
        f"{stats['active']} active plans, {stats['complete']} complete, "
        f"{stats['orphaned']} orphaned"
    )
    logger.info(summary)
    return {"status": "ok", "findings": findings, "summary": summary}
