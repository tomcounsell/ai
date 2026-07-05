"""reflections/housekeeping/merged_branch_cleanup.py — Prune merged branches, audit plan files.

What it does: Deletes local git branches merged into main and audits docs/plans/
    for complete/orphaned/closed-issue plans (deletes branches; reads plan files
    and queries GitHub issue state via gh). Also acts as the path-independent
    backstop for the plan-migration invariant (issue #1900, Tier 0): a plan whose
    OWN ``tracking:`` frontmatter issue is closed is migrated into
    ``docs/plans/completed/`` via ``migrate_plan_to_completed()`` -- the same
    primitive the deterministic ``/do-merge --issue`` call uses. This catches
    merges that bypass ``/do-merge`` entirely (manual `gh pr merge`, forked
    `/do-sdlc`, cross-machine merges).
Cadence: 86400s (daily) (keeps the branch list and plans dir from accreting cruft)
Failure modes:
    - git/gh subprocess failure or timeout -> caught per step, logged, skipped
    - missing docs/plans dir -> returns early with branch-cleanup findings only
    - tracking-issue state lookup returns "unknown" -> plan is never migrated
      (the gate is non-vacuous: only a literal "closed" state qualifies)
Related reflections:
    - tech_debt_scan: complementary stale-artifact audit over project source
See also: config/reflections.yaml (declaration), docs/features/reflections.md,
    docs/features/plan-migration-invariant.md
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

from reflections.utilities import PROJECT_ROOT, load_local_projects
from scripts.migrate_completed_plan import extract_tracking_issue, migrate_plan_to_completed

logger = logging.getLogger("reflections.maintenance")

# Single toggle point for arming the mechanism (issue #1900, Tier 0). Armed
# (True) as of the arm-reflection task: the evidence-gate regression test
# (tests/unit/test_plan_migration_invariant.py) proves the gate is
# independent of is_complete and non-vacuous (requires a literal "closed"
# issue state), so run() now calls git mv for real, bounded by
# MIGRATION_PER_RUN_CAP below.
MIGRATION_APPLY_ENABLED = True

# Per-run cap on plans migrated (or reported as migratable) in a single
# invocation -- bounds the blast radius of the daily unattended sweep. The
# one-time backfill sweep (`migrate_completed_plan.py --sweep`) is uncapped;
# this cap only applies to this recurring reflection.
MIGRATION_PER_RUN_CAP = 10


async def run() -> dict:
    """Clean up stale git branches and audit plan files.

    - Deletes local branches merged into main
    - Migrates plans whose OWN tracking issue is closed into completed/
    - Audits docs/plans/ for complete/orphaned/stale-issue plans
    """
    findings: list[str] = []
    projects = load_local_projects()
    migrated_count = 0

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

    # Extract issue refs (broad prose scan -- feeds the existing closed_issue finding)
    # and each plan's OWN tracking-issue number (narrow frontmatter scan -- feeds
    # the migration gate below). These are deliberately separate sets: the
    # migration gate must not key on the broader `refs` scan, or a plan that only
    # mentions a closed sibling issue in prose could get migrated by mistake.
    plan_issue_refs: dict[Path, list[int]] = {}
    plan_tracking_issue: dict[Path, int | None] = {}
    for plan_file in plan_files:
        plan_text = plan_file.read_text(errors="replace")
        refs: set[int] = set()
        for m in re.finditer(r"#(\d+)", plan_text):
            refs.add(int(m.group(1)))
        for m in re.finditer(r"github\.com/[^/]+/[^/]+/issues/(\d+)", plan_text):
            refs.add(int(m.group(1)))
        plan_issue_refs[plan_file] = sorted(refs)

        tracking_url = extract_tracking_issue(plan_text)
        tracking_num: int | None = None
        if tracking_url:
            tm = re.search(r"/issues/(\d+)", tracking_url)
            if tm:
                tracking_num = int(tm.group(1))
        plan_tracking_issue[plan_file] = tracking_num

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
    for tracking_num in plan_tracking_issue.values():
        if tracking_num is not None:
            all_issue_nums.add(tracking_num)

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

    stats = {"complete": 0, "orphaned": 0, "closed_issue": 0, "active": 0, "migrated": 0}

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
            # Intentionally no `continue` here: the migration gate below must be
            # evaluated regardless of checkbox completeness. Previously an
            # all-checkboxes-complete plan short-circuited past the
            # tracking-issue-closed check entirely (issue #1900 Blocker 1), so
            # ~34 of 212 root plans could never migrate even with a closed issue.

        # --- Migration gate: keyed on the plan's OWN tracking-issue frontmatter
        # (plan_tracking_issue), NOT the broader prose `refs` set used by the
        # closed_issue finding below. Non-vacuous: requires a literal "closed"
        # state for that one issue; "unknown"/missing tracking issue defers and
        # never migrates (issue #1900 Blocker 2 -- the old `all(... if s !=
        # "unknown")` check was vacuously True when every ref state was
        # "unknown", which could migrate an ACTIVE plan on a gh outage).
        tracking_num = plan_tracking_issue.get(plan_file)
        if tracking_num is not None and issue_states.get(tracking_num) == "closed":
            if migrated_count < MIGRATION_PER_RUN_CAP:
                verdict = migrate_plan_to_completed(plan_file, apply=MIGRATION_APPLY_ENABLED)
                migrated_count += 1
                stats["migrated"] += 1
                action_word = "Migrated" if MIGRATION_APPLY_ENABLED else "Would migrate"
                findings.append(
                    f"{action_word} ({verdict}): {plan_file.name} -> completed/ "
                    f"(tracking issue #{tracking_num} closed)"
                )
            else:
                findings.append(
                    f"Deferred migration (per-run cap {MIGRATION_PER_RUN_CAP} reached): "
                    f"{plan_file.name} (tracking issue #{tracking_num} closed)"
                )
            continue

        if is_complete:
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

    migration_mode = "migrated" if MIGRATION_APPLY_ENABLED else "would-migrate"
    summary = (
        f"Branch/plan cleanup: {len(findings)} finding(s), "
        f"{stats['active']} active plans, {stats['complete']} complete, "
        f"{stats['orphaned']} orphaned, {stats['migrated']} {migration_mode}"
    )
    logger.info(summary)
    return {"status": "ok", "findings": findings, "summary": summary}
