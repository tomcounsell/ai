#!/usr/bin/env python3
"""
Update orchestrator - main entry point for update system.

Usage:
    python scripts/update/run.py --full      # Full update (from /update skill)
    python scripts/update/run.py --cron      # Minimal update (from cron)
    python scripts/update/run.py --verify    # Just verify environment
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.update import cal_integration, deps, git, service, verify  # noqa: E402


@dataclass
class UpdateConfig:
    """Configuration for update run."""
    # What to run
    do_git_pull: bool = True
    do_dep_sync: bool = True
    do_service_restart: bool = True
    do_verify: bool = True
    do_calendar: bool = False  # Only in full mode
    do_ollama: bool = False  # Only in full mode
    do_mcp: bool = False  # Only in full mode

    # Options
    verbose: bool = False
    json_output: bool = False
    force_dep_sync: bool = False  # Sync even if no dep files changed

    @classmethod
    def full(cls) -> UpdateConfig:
        """Config for full update (from /update skill)."""
        return cls(
            do_git_pull=True,
            do_dep_sync=True,
            do_service_restart=True,
            do_verify=True,
            do_calendar=True,
            do_ollama=True,
            do_mcp=True,
            verbose=True,
        )

    @classmethod
    def cron(cls) -> UpdateConfig:
        """Config for cron update (minimal, unattended)."""
        return cls(
            do_git_pull=True,
            do_dep_sync=True,
            do_service_restart=False,  # Use restart flag instead
            do_verify=False,
            do_calendar=False,
            do_ollama=False,
            do_mcp=False,
            verbose=False,
        )

    @classmethod
    def verify_only(cls) -> UpdateConfig:
        """Config for verification only."""
        return cls(
            do_git_pull=False,
            do_dep_sync=False,
            do_service_restart=False,
            do_verify=True,
            do_calendar=True,
            do_ollama=True,
            do_mcp=True,
            verbose=True,
        )


@dataclass
class UpdateResult:
    """Result of update run."""
    success: bool = True
    git_result: git.GitPullResult | None = None
    dep_result: deps.DepSyncResult | None = None
    version_info: list[deps.VersionInfo] | None = None
    verification: verify.VerificationResult | None = None
    calendar_hook: cal_integration.CalendarHookResult | None = None
    calendar_config: cal_integration.CalendarConfigResult | None = None
    service_status: service.ServiceStatus | None = None
    caffeinate_status: service.CaffeinateStatus | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def log(msg: str, verbose: bool = True, always: bool = False) -> None:
    """Print log message. Use always=True for key status messages."""
    if verbose or always:
        print(f"[update] {msg}")


def run_update(project_dir: Path, config: UpdateConfig) -> UpdateResult:
    """Run update with given configuration."""
    result = UpdateResult()
    v = config.verbose

    # Step 1: Git pull
    if config.do_git_pull:
        log("Pulling latest changes...", v)
        result.git_result = git.git_pull(project_dir)

        if not result.git_result.success:
            log(f"FAIL: {result.git_result.error}", v)
            result.success = False
            result.errors.append(f"Git pull failed: {result.git_result.error}")
            return result

        if result.git_result.commit_count == 0:
            log(f"Already up to date ({git.get_short_sha(project_dir)})", v, always=True)
        else:
            log(f"Pulled {result.git_result.commit_count} commit(s):", v, always=True)
            for commit in result.git_result.commits[:5]:
                log(f"  {commit}", v, always=True)

        if result.git_result.stashed:
            if result.git_result.stash_restored:
                log("Stashed and restored local changes", v)
            else:
                result.warnings.append("Local changes stashed but failed to restore")

    # Step 2: Check for pending critical upgrades
    pending = git.check_upgrade_pending(project_dir)
    if pending.pending:
        log(f"WARNING: Critical dependency upgrade pending since {pending.timestamp}", v)
        result.warnings.append(f"Critical upgrade pending: {pending.reason}")

    # Step 3: Dependency sync
    if config.do_dep_sync:
        should_sync = config.force_dep_sync

        # Check if dep files changed
        if result.git_result and result.git_result.commit_count > 0:
            changed_files = git.get_changed_files(
                project_dir,
                result.git_result.before_sha,
                result.git_result.after_sha,
            )

            if deps.check_dep_files_changed(changed_files):
                # Check for critical dep changes
                critical_changes = git.check_critical_dep_changes(
                    project_dir,
                    result.git_result.before_sha,
                    result.git_result.after_sha,
                )

                if critical_changes:
                    log("CRITICAL dependency changes detected:", v, always=True)
                    for change in critical_changes:
                        log(f"  {change}", v, always=True)
                    log("Skipping auto-sync. Run /update manually to apply.", v, always=True)
                    git.set_upgrade_pending(project_dir, "critical-dep-upgrade")
                else:
                    should_sync = True

        if should_sync:
            log("Syncing dependencies...", v, always=True)
            result.dep_result = deps.sync_dependencies(project_dir)

            if result.dep_result.success:
                log(f"Dependencies synced via {result.dep_result.method}", v, always=True)
            else:
                log(f"WARN: Dep sync failed: {result.dep_result.error}", v, always=True)
                result.warnings.append(f"Dep sync failed: {result.dep_result.error}")

            # Verify critical versions
            result.version_info = deps.verify_critical_versions(project_dir)
            mismatches = [vi for vi in result.version_info if not vi.matches]
            if mismatches:
                for vi in mismatches:
                    log(f"WARN: {vi.package} version mismatch: {vi.version} != {vi.expected}", v)
                    result.warnings.append(f"{vi.package} version mismatch")
        else:
            log("No dependency changes, skipping sync", v)

    # Step 4: Ollama model (full mode only)
    if config.do_ollama:
        log("Checking Ollama model...", v)
        ollama_model = os.getenv("OLLAMA_SUMMARIZER_MODEL", "qwen3:4b")
        ollama_check = verify.check_ollama(ollama_model)

        if not ollama_check.available:
            if ollama_check.error and "Not installed" not in ollama_check.error:
                log(f"Pulling Ollama model {ollama_model}...", v)
                if verify.pull_ollama_model(ollama_model):
                    log(f"Ollama model {ollama_model} pulled", v)
                else:
                    result.warnings.append(f"Failed to pull Ollama model {ollama_model}")
            else:
                log("Ollama not installed, skipping", v)

    # Step 5: Service management
    if config.do_service_restart:
        log("Installing/restarting services...", v)

        # Install caffeinate first
        caff = service.get_caffeinate_status()
        if not caff.installed:
            log("Installing caffeinate service...", v)
            if service.install_caffeinate():
                log("Caffeinate installed", v)
            else:
                result.warnings.append("Failed to install caffeinate")

        # Install main service (handles both bridge and update cron)
        if service.install_service(project_dir):
            log("Services installed/restarted", v)
        else:
            result.warnings.append("Service install may have failed")

        # Wait and check status
        import time
        time.sleep(2)
        result.service_status = service.get_service_status(project_dir)
        result.caffeinate_status = service.get_caffeinate_status()

        if result.service_status.running:
            log(f"Bridge running (PID: {result.service_status.pid})", v)
        else:
            log("WARN: Bridge not running after restart", v)
            result.warnings.append("Bridge not running after restart")

        # Check update cron
        if service.is_update_cron_installed():
            log("Update cron installed", v)
        else:
            result.warnings.append("Update cron not installed")

    elif result.git_result and result.git_result.commit_count > 0:
        # Cron mode: set restart flag instead of restarting
        log("Setting restart flag for graceful restart...", v, always=True)
        git.set_restart_requested(project_dir, result.git_result.commit_count)

    # Step 6: Environment verification
    if config.do_verify:
        log("Verifying environment...", v)
        result.verification = verify.verify_environment(
            project_dir,
            check_ollama_model=config.do_ollama,
        )

        # Report system tools
        for tool in result.verification.system_tools:
            status = "OK" if tool.available else "MISSING"
            log(f"  {tool.name}: {status}", v)

        # Report SDK auth
        auth = result.verification.sdk_auth
        if auth.get("claude_desktop_running"):
            log("  SDK auth: Claude Desktop (subscription)", v)
        elif auth.get("api_key_configured"):
            log("  SDK auth: API key", v)
        else:
            log("  SDK auth: NOT CONFIGURED", v)
            result.warnings.append("SDK auth not configured")

    # Step 7: Calendar integration
    if config.do_calendar:
        log("Checking calendar integration...", v)

        # Global hook
        result.calendar_hook = cal_integration.ensure_global_hook(project_dir)
        if result.calendar_hook.configured:
            if result.calendar_hook.created:
                log("Calendar hook installed", v)
            else:
                log("Calendar hook OK", v)
        else:
            log(f"WARN: Calendar hook issue: {result.calendar_hook.error}", v)
            result.warnings.append(f"Calendar hook: {result.calendar_hook.error}")

        # Calendar config
        result.calendar_config = cal_integration.generate_calendar_config(project_dir)
        if result.calendar_config.success:
            log(f"Calendar config: {len(result.calendar_config.mappings)} mappings", v)
            for mapping in result.calendar_config.mappings:
                status = "OK" if mapping.accessible else "INACCESSIBLE"
                cal_name = mapping.calendar_name or mapping.calendar_id
                log(f"  {mapping.slug} -> {cal_name} ({status})", v)
        else:
            log(f"WARN: Calendar config: {result.calendar_config.error}", v)
            result.warnings.append(f"Calendar config: {result.calendar_config.error}")

    # Step 8: MCP servers
    if config.do_mcp:
        log("Checking MCP servers...", v)
        mcp_servers = verify.check_mcp_servers()
        if mcp_servers:
            log(f"MCP servers: {len(mcp_servers)}", v)
            for server in mcp_servers[:5]:
                log(f"  {server}", v)
        else:
            log("No MCP servers configured", v)

    # Final summary
    if v:
        log("", v)
        log("=" * 50, v)
        if result.errors:
            log(f"FAILED with {len(result.errors)} error(s)", v)
            result.success = False
        elif result.warnings:
            log(f"COMPLETED with {len(result.warnings)} warning(s)", v)
        else:
            log("COMPLETED successfully", v)

        if result.git_result:
            sha = git.get_short_sha(project_dir)
            log(f"HEAD: {sha}", v)

    return result


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Valor update system")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--full", action="store_true", help="Full update (all checks)")
    mode.add_argument("--cron", action="store_true", help="Cron update (minimal)")
    mode.add_argument("--verify", action="store_true", help="Verify only (no changes)")

    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--quiet", action="store_true", help="Suppress output")
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=PROJECT_ROOT,
        help="Project directory",
    )

    args = parser.parse_args()

    # Select config
    if args.full:
        config = UpdateConfig.full()
    elif args.cron:
        config = UpdateConfig.cron()
    else:
        config = UpdateConfig.verify_only()

    if args.quiet:
        config.verbose = False
    if args.json:
        config.json_output = True
        config.verbose = False

    # Run update
    result = run_update(args.project_dir, config)

    # Output
    if args.json:
        # Convert to JSON-serializable dict
        output = {
            "success": result.success,
            "errors": result.errors,
            "warnings": result.warnings,
        }

        if result.git_result:
            output["git"] = {
                "success": result.git_result.success,
                "commits": result.git_result.commit_count,
                "before": result.git_result.before_sha[:8],
                "after": result.git_result.after_sha[:8],
            }

        if result.service_status:
            output["service"] = {
                "running": result.service_status.running,
                "pid": result.service_status.pid,
            }

        print(json.dumps(output, indent=2))

    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
