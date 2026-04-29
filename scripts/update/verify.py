"""Environment verification for update system."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from scripts.update.service import is_bridge_running

# Ensure PATH includes common tool locations (launchd has minimal PATH)
_EXTRA_PATHS = [
    str(Path.home() / ".pyenv" / "shims"),
    str(Path.home() / ".local" / "bin"),
    str(Path.home() / "Library" / "Python" / "3.12" / "bin"),
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
    "/usr/local/bin",
]
_current_path = os.environ.get("PATH", "")
_missing = [p for p in _EXTRA_PATHS if p not in _current_path.split(":")]
if _missing:
    os.environ["PATH"] = ":".join(_missing) + ":" + _current_path


@dataclass
class ToolCheck:
    """Result of a single tool check."""

    name: str
    available: bool
    version: str | None = None
    error: str | None = None


@dataclass
class GitignoreIssue:
    """A file that should be gitignored but isn't."""

    repo: str
    file_path: str
    size_mb: float


@dataclass
class VerificationResult:
    """Result of environment verification."""

    system_tools: list[ToolCheck] = field(default_factory=list)
    python_deps: list[ToolCheck] = field(default_factory=list)
    dev_tools: list[ToolCheck] = field(default_factory=list)
    valor_tools: list[ToolCheck] = field(default_factory=list)
    ollama: ToolCheck | None = None
    sdk_auth: dict[str, bool] = field(default_factory=dict)
    mcp_servers: list[str] = field(default_factory=list)
    gitignore_issues: list[GitignoreIssue] = field(default_factory=list)


def run_cmd(
    cmd: list[str],
    cwd: Path | None = None,
    check: bool = False,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    """Run a command and return result."""
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )


def check_command(name: str, version_flag: str = "--version") -> ToolCheck:
    """Check if a command is available and get its version."""
    if not shutil.which(name):
        return ToolCheck(name=name, available=False, error="Not found in PATH")

    try:
        result = run_cmd([name, version_flag], timeout=10)
        version = result.stdout.strip() or result.stderr.strip()
        # Take first line only
        version = version.split("\n")[0] if version else None
        return ToolCheck(name=name, available=True, version=version)
    except subprocess.TimeoutExpired:
        return ToolCheck(name=name, available=True, error="Timeout getting version")
    except Exception as e:
        return ToolCheck(name=name, available=True, error=str(e))


def check_python_import(
    project_dir: Path, module: str, display_name: str | None = None
) -> ToolCheck:
    """Check if a Python module can be imported."""
    python_path = project_dir / ".venv" / "bin" / "python"
    name = display_name or module

    if not python_path.exists():
        return ToolCheck(name=name, available=False, error="No .venv/bin/python")

    try:
        result = run_cmd(
            [str(python_path), "-c", f"import {module}"],
            cwd=project_dir,
            timeout=10,
        )
        return ToolCheck(name=name, available=result.returncode == 0)
    except Exception as e:
        return ToolCheck(name=name, available=False, error=str(e))


def check_venv_tool(project_dir: Path, tool: str) -> ToolCheck:
    """Check if a tool exists in .venv/bin and get version."""
    tool_path = project_dir / ".venv" / "bin" / tool

    if not tool_path.exists():
        return ToolCheck(name=tool, available=False, error="Not in .venv/bin")

    try:
        result = run_cmd([str(tool_path), "--version"], timeout=10)
        version = result.stdout.strip() or result.stderr.strip()
        version = version.split("\n")[0] if version else None
        return ToolCheck(name=tool, available=True, version=version)
    except Exception as e:
        return ToolCheck(name=tool, available=True, error=str(e))


def check_python_alias() -> ToolCheck:
    """Check that 'python' resolves to python3 3.12+.

    Claude Code hooks invoke bare 'python' under /bin/sh, which does not
    honor zsh aliases. If 'python' is not on PATH, every hook that uses it
    silently fails with 'command not found' and surfaces errors in the UI.
    Require a real 'python' binary on PATH that is Python 3.12+.

    Fix on macOS:
      ln -sf "$(command -v python3)" /opt/homebrew/bin/python
    """
    python_path = shutil.which("python")
    python3_path = shutil.which("python3")

    if not python3_path:
        return ToolCheck(name="python", available=False, error="python3 not found")

    if not python_path:
        return ToolCheck(
            name="python",
            available=False,
            error=(
                "bare 'python' not on PATH — Claude Code hooks fail under /bin/sh. "
                'Fix: ln -sf "$(command -v python3)" /opt/homebrew/bin/python'
            ),
        )

    try:
        result = run_cmd([python_path, "--version"], timeout=5)
        version = result.stdout.strip()
        import re as _re

        m = _re.search(r"(\d+)\.(\d+)", version)
        ok = m and (int(m.group(1)), int(m.group(2))) >= (3, 12)
        if ok:
            return ToolCheck(name="python", available=True, version=version)
        return ToolCheck(
            name="python",
            available=False,
            version=version,
            error=(
                f"python is {version}, expected 3.12+. "
                f"Fix: brew install python@3.12 && "
                f'ln -sf "$(command -v python3.12)" /opt/homebrew/bin/python'
            ),
        )
    except Exception as e:
        return ToolCheck(name="python", available=False, error=f"python --version failed: {e}")


def check_system_tools() -> list[ToolCheck]:
    """Check system-level tools."""
    tools = [
        ("claude", "--version"),
        ("gh", "--version"),
        ("git", "--version"),
        ("sentry-cli", "--version"),
        ("uv", "--version"),
    ]
    results = [check_command(name, flag) for name, flag in tools]
    results.append(check_python_alias())
    return results


def check_python_deps(project_dir: Path) -> list[ToolCheck]:
    """Check core Python dependencies."""
    deps = [
        "telethon",
        "httpx",
        "dotenv",
        "anthropic",
        "ollama",
        "google_auth_oauthlib",
    ]
    return [check_python_import(project_dir, dep) for dep in deps]


def check_dev_tools(project_dir: Path) -> list[ToolCheck]:
    """Check development tools."""
    tools = ["pytest", "ruff", "mypy"]
    return [check_venv_tool(project_dir, tool) for tool in tools]


def check_valor_tools(project_dir: Path) -> list[ToolCheck]:
    """Check Valor-specific CLI tools."""
    results = []

    # SMS reader
    python_path = project_dir / ".venv" / "bin" / "python"
    if python_path.exists():
        try:
            result = run_cmd(
                [
                    str(python_path),
                    "-m",
                    "tools.sms_reader.cli",
                    "recent",
                    "--limit",
                    "1",
                ],
                cwd=project_dir,
                timeout=10,
            )
            results.append(
                ToolCheck(
                    name="sms_reader",
                    available=result.returncode == 0,
                    error=result.stderr.strip() if result.returncode != 0 else None,
                )
            )
        except Exception as e:
            results.append(ToolCheck(name="sms_reader", available=False, error=str(e)))

    # valor-calendar - check multiple locations
    calendar_found = False
    calendar_version = None

    # Check venv first
    venv_calendar = project_dir / ".venv" / "bin" / "valor-calendar"
    if venv_calendar.exists():
        try:
            result = run_cmd([str(venv_calendar), "--version"], timeout=10)
            if result.returncode == 0:
                calendar_found = True
                calendar_version = result.stdout.strip()
        except Exception:
            pass

    # Check user bin
    if not calendar_found:
        user_calendar = Path.home() / "Library" / "Python" / "3.12" / "bin" / "valor-calendar"
        if user_calendar.exists():
            try:
                result = run_cmd([str(user_calendar), "--version"], timeout=10)
                if result.returncode == 0:
                    calendar_found = True
                    calendar_version = result.stdout.strip()
            except Exception:
                pass

    results.append(
        ToolCheck(
            name="valor-calendar",
            available=calendar_found,
            version=calendar_version,
        )
    )

    # valor-email — verify CLI is on PATH after `pip install -e .`.
    # Mirrors the verification line required by docs/plans/valor-email-cli.md.
    venv_email = project_dir / ".venv" / "bin" / "valor-email"
    email_found = False
    email_err = None
    if venv_email.exists():
        try:
            result = run_cmd([str(venv_email), "--help"], timeout=10)
            email_found = result.returncode == 0
            if not email_found:
                email_err = result.stderr.strip() or None
        except Exception as e:
            email_err = str(e)
    else:
        email_err = "Not in .venv/bin (run `uv sync` or `pip install -e .`)"

    results.append(
        ToolCheck(
            name="valor-email",
            available=email_found,
            error=email_err if not email_found else None,
        )
    )

    return results


def check_ollama(model: str = "gemma4:e2b") -> ToolCheck:
    """Check if Ollama is available and has the required model."""
    if not shutil.which("ollama"):
        return ToolCheck(name="ollama", available=False, error="Not installed")

    try:
        result = run_cmd(["ollama", "list"], timeout=30)
        if result.returncode != 0:
            return ToolCheck(name="ollama", available=False, error="Failed to list models")

        has_model = model in result.stdout
        return ToolCheck(
            name=f"ollama ({model})",
            available=has_model,
            version=model if has_model else None,
            error=f"Model {model} not found" if not has_model else None,
        )
    except subprocess.TimeoutExpired:
        return ToolCheck(name="ollama", available=False, error="Timeout")
    except Exception as e:
        return ToolCheck(name="ollama", available=False, error=str(e))


def pull_ollama_model(model: str = "gemma4:e2b") -> bool:
    """Pull an Ollama model. Returns True if successful."""
    if not shutil.which("ollama"):
        return False

    try:
        result = run_cmd(["ollama", "pull", model], timeout=600)
        return result.returncode == 0
    except Exception:
        return False


def check_sdk_auth(project_dir: Path) -> dict[str, bool]:
    """Check SDK authentication status."""
    result = {
        "claude_desktop_running": False,
        "api_key_configured": False,
        "use_api_billing": False,
    }

    # Check Claude Desktop
    try:
        ps_result = run_cmd(["pgrep", "-f", "Claude.app"], timeout=5)
        result["claude_desktop_running"] = ps_result.returncode == 0
    except Exception:
        pass

    # Check .env for API key and billing preference. The .env symlinks to
    # ~/Desktop/Valor/.env (iCloud + TCC-protected); read can raise PermissionError
    # (TCC revoked) or OSError (iCloud eviction). Neither should crash the orchestrator.
    env_file = project_dir / ".env"
    if env_file.exists():
        try:
            content = env_file.read_text()
            result["api_key_configured"] = "ANTHROPIC_API_KEY=sk-ant-" in content
            result["use_api_billing"] = "USE_API_BILLING=true" in content
        except OSError as exc:
            result["env_read_error"] = str(exc)

    return result


def sync_claude_oauth(project_dir: Path) -> dict[str, str | bool]:
    """Sync Claude OAuth token from Desktop Valor dir to Claude Desktop config.

    The source of truth for OAuth credentials is:
        ~/Desktop/Valor/claude_oauth_config.json
    The target (where Claude CLI reads auth) is:
        ~/Library/Application Support/Claude/config.json

    This copies the oauth:tokenCache key from source to target, keeping
    all other Claude Desktop settings intact.

    Returns dict with: synced (bool), reason (str), refreshed_from_live (bool)
    """
    import json

    source = Path.home() / "Desktop" / "Valor" / "claude_oauth_config.json"
    target = Path.home() / "Library" / "Application Support" / "Claude" / "config.json"

    result: dict[str, str | bool] = {
        "synced": False,
        "reason": "",
        "refreshed_from_live": False,
    }

    if not source.exists():
        result["reason"] = "No source credentials at ~/Desktop/Valor/claude_oauth_config.json"
        return result

    try:
        source_config = json.loads(source.read_text())
    except (json.JSONDecodeError, OSError) as e:
        result["reason"] = f"Failed to read source config: {e}"
        return result

    source_token = source_config.get("oauth:tokenCache")
    if not source_token:
        result["reason"] = "Source config has no oauth:tokenCache"
        return result

    # First check if CLI auth is already working
    claude_bin = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")
    try:
        auth_result = run_cmd([claude_bin, "auth", "status"], timeout=10)
        if auth_result.returncode == 0 and "loggedIn" in auth_result.stdout:
            # Auth works — refresh the source from the live config (it may be newer)
            if target.exists():
                try:
                    live_config = json.loads(target.read_text())
                    live_token = live_config.get("oauth:tokenCache")
                    if live_token and live_token != source_token:
                        # Live token is different (refreshed) — update source
                        source_config["oauth:tokenCache"] = live_token
                        source.write_text(json.dumps(source_config, indent=2) + "\n")
                        result["refreshed_from_live"] = True
                except (json.JSONDecodeError, OSError):
                    pass
            result["synced"] = True
            result["reason"] = "Auth already working"
            return result
    except Exception:
        pass

    # Auth not working — sync source token to target
    target.parent.mkdir(parents=True, exist_ok=True)

    target_config: dict = {}
    if target.exists():
        try:
            target_config = json.loads(target.read_text())
        except (json.JSONDecodeError, OSError):
            target_config = {}

    target_config["oauth:tokenCache"] = source_token
    try:
        target.write_text(json.dumps(target_config, indent=2) + "\n")
        result["synced"] = True
        result["reason"] = "Copied oauth:tokenCache to Claude Desktop config"
    except OSError as e:
        result["reason"] = f"Failed to write target config: {e}"

    return result


def migrate_settings_json_paths() -> dict[str, str | bool]:
    """Migrate legacy Desktop/claude_code paths in ~/.claude/settings.json to Desktop/Valor.

    Reads the global Claude settings file and replaces any occurrence of
    'Desktop/claude_code' with 'Desktop/Valor' in the serialized JSON.
    This handles the statusline command path and any other references.

    Returns dict with: migrated (bool), reason (str)
    """
    import json

    settings_path = Path.home() / ".claude" / "settings.json"
    result: dict[str, str | bool] = {"migrated": False, "reason": ""}

    if not settings_path.exists():
        result["reason"] = "No ~/.claude/settings.json found"
        return result

    try:
        content = settings_path.read_text()
    except OSError as e:
        result["reason"] = f"Failed to read settings.json: {e}"
        return result

    if "Desktop/claude_code" not in content:
        result["reason"] = "No legacy paths found — already migrated or never present"
        return result

    updated = content.replace("Desktop/claude_code", "Desktop/Valor")

    # Validate JSON before writing
    try:
        json.loads(updated)
    except json.JSONDecodeError as e:
        result["reason"] = f"Updated content is not valid JSON: {e}"
        return result

    try:
        settings_path.write_text(updated)
        result["migrated"] = True
        result["reason"] = "Replaced Desktop/claude_code with Desktop/Valor in settings.json"
    except OSError as e:
        result["reason"] = f"Failed to write settings.json: {e}"

    return result


def check_mcp_servers() -> list[str]:
    """Get list of configured MCP servers by reading config files directly.

    Reads ~/.claude/mcp_settings.json instead of `claude mcp list` which
    can hang when Claude Desktop is running.
    """
    import json

    servers = []

    # Read from ~/.claude/mcp_settings.json (global config, key: "servers")
    mcp_settings = Path.home() / ".claude" / "mcp_settings.json"
    if mcp_settings.exists():
        try:
            data = json.loads(mcp_settings.read_text())
            for key in ("servers", "mcpServers"):
                if key in data:
                    servers.extend(data[key].keys())
                    break
        except (json.JSONDecodeError, OSError):
            pass

    # Read from project .mcp.json if it exists (key: "mcpServers")
    project_mcp = Path.cwd() / ".mcp.json"
    if project_mcp.exists():
        try:
            data = json.loads(project_mcp.read_text())
            for key in ("mcpServers", "servers"):
                if key in data:
                    for name in data[key]:
                        if name not in servers:
                            servers.append(name)
                    break
        except (json.JSONDecodeError, OSError):
            pass

    return servers


def check_gitignore_issues() -> list[GitignoreIssue]:
    """Check all repos under ~/src/ for files that should be gitignored."""
    issues = []
    src_dir = Path.home() / "src"
    if not src_dir.is_dir():
        return issues

    # Patterns that should never be committed (large generated artifacts)
    bad_patterns = ["*embedding*.json"]

    skip_dirs = {".venv", ".mypy_cache", "node_modules", "__pycache__", ".git"}

    for repo_dir in sorted(src_dir.iterdir()):
        if not (repo_dir / ".git").is_dir():
            continue

        # Find matching files
        for pattern in bad_patterns:
            for match in repo_dir.rglob(pattern):
                # Skip vendored/generated directories
                if any(part in skip_dirs for part in match.parts):
                    continue

                rel_path = str(match.relative_to(repo_dir))

                # Check if gitignored
                result = run_cmd(
                    ["git", "check-ignore", "-q", rel_path],
                    cwd=repo_dir,
                    timeout=5,
                )
                if result.returncode != 0:
                    # Not gitignored
                    try:
                        size_mb = match.stat().st_size / (1024 * 1024)
                    except OSError:
                        size_mb = 0.0
                    issues.append(
                        GitignoreIssue(
                            repo=repo_dir.name,
                            file_path=rel_path,
                            size_mb=round(size_mb, 1),
                        )
                    )

    return issues


def _load_api_key(project_dir: Path) -> str:
    """Load ANTHROPIC_API_KEY from env or .env file."""
    import os

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        env_file = project_dir / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                    break
    return api_key


def _check_model_valid(model_id: str, api_key: str) -> str | None:
    """Ping the Anthropic API with a model ID. Returns error string or None."""
    import json

    try:
        result = run_cmd(
            [
                "curl",
                "-s",
                "--max-time",
                "5",
                "https://api.anthropic.com/v1/messages",
                "-H",
                f"x-api-key: {api_key}",
                "-H",
                "anthropic-version: 2023-06-01",
                "-H",
                "content-type: application/json",
                "-d",
                json.dumps(
                    {
                        "model": model_id,
                        "max_tokens": 1,
                        "messages": [{"role": "user", "content": "hi"}],
                    }
                ),
            ],
            timeout=10,
        )
        response = json.loads(result.stdout)
        if response.get("type") == "error":
            err = response["error"]["message"]
            # Only flag model-not-found errors, not rate limits etc.
            if "model" in err.lower() or "not_found" in response["error"].get("type", ""):
                return f"Model '{model_id}' is invalid: {err}"
    except Exception:
        pass  # Network issues aren't model problems
    return None


def verify_models(project_dir: Path) -> list[str]:
    """Verify all Anthropic models in config/models.py are still valid.

    Returns a list of error strings (empty if all OK).
    """
    import re

    errors: list[str] = []

    api_key = _load_api_key(project_dir)
    if not api_key:
        return []  # Can't check without key

    # Load model IDs from config/models.py
    models_file = project_dir / "config" / "models.py"
    if not models_file.exists():
        return []

    content = models_file.read_text()
    # Match lines like: HAIKU = "claude-haiku-4-5-20251001"
    # Skip OPENROUTER_ models (different API)
    seen = set()
    for match in re.finditer(
        r'^(?!OPENROUTER_)([A-Z_]+)\s*=\s*"(claude-[^"]+)"', content, re.MULTILINE
    ):
        name, model_id = match.group(1), match.group(2)
        if model_id in seen:
            continue
        seen.add(model_id)

        error = _check_model_valid(model_id, api_key)
        if error:
            errors.append(f"config/models.py {name}: {error}")

    return errors


def _check_bridge_heartbeat(project_dir: Path, max_age_seconds: int = 300) -> str | None:
    """Check bridge log for a recent heartbeat, proving Telegram is connected.

    Returns a short status string (e.g. "uptime=5m") if a recent heartbeat
    is found, or None if no evidence of a healthy bridge.
    """
    log_path = project_dir / "logs" / "bridge.log"
    if not log_path.exists():
        return None

    # Read last ~50 lines efficiently
    try:
        tail = subprocess.run(
            ["tail", "-50", str(log_path)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        lines = tail.stdout.strip().splitlines()
    except Exception:
        return None

    # Look for the most recent heartbeat line
    # Format: {"timestamp": "...", ... "message": "[heartbeat] Bridge alive (uptime=2m, workers=0)"}
    heartbeat_re = re.compile(r"\[heartbeat\] Bridge alive \(uptime=(\d+m), workers=(\d+)\)")
    for line in reversed(lines):
        match = heartbeat_re.search(line)
        if not match:
            continue
        # Verify timestamp is recent
        try:
            ts_match = re.search(r'"timestamp":\s*"([^"]+)"', line)
            if ts_match:
                ts = datetime.fromisoformat(ts_match.group(1).replace("Z", "+00:00"))
                age = (datetime.now(UTC) - ts).total_seconds()
                if age <= max_age_seconds:
                    return f"uptime={match.group(1)}"
        except (ValueError, TypeError):
            continue
    return None


def check_telegram_session(project_dir: Path) -> ToolCheck:
    """Check if the Telegram session file exists and is authorized.

    Uses Telethon to connect (without sending auth codes) and verify
    the session is valid. If not, instructs the user to run telegram_login.py.
    """
    python_path = project_dir / ".venv" / "bin" / "python"
    if not python_path.exists():
        return ToolCheck(name="telegram_session", available=False, error="No .venv/bin/python")

    # Find session file
    data_dir = project_dir / "data"
    session_files = list(data_dir.glob("*.session"))
    if not session_files:
        return ToolCheck(
            name="telegram_session",
            available=False,
            error="No session file in data/. Run: python scripts/telegram_login.py",
        )

    # If the bridge is already running, it holds a lock on the session file.
    # Opening a second Telethon client causes SQLite lock contention and
    # produces false "unauthorized" results. Trust the running bridge instead.
    if is_bridge_running():
        return ToolCheck(
            name="telegram_session", available=True, version="authorized (bridge running)"
        )

    # Bridge is NOT running — safe to check authorization directly.
    # Outputs one of: authorized, unauthorized, flood:NNN, error:MESSAGE
    check_script = (
        "import asyncio, os, sys; "
        "sys.path.insert(0, '.'); "
        "from dotenv import load_dotenv; load_dotenv(); "
        "from telethon import TelegramClient; "
        "from telethon.errors import FloodWaitError; "
        "session = list(__import__('pathlib').Path('data').glob('*.session'))[0]; "
        "client = TelegramClient(str(session).replace('.session',''), "
        "int(os.getenv('TELEGRAM_API_ID',0)), os.getenv('TELEGRAM_API_HASH','')); "
        "async def check(): "
        "  try: "
        "    await client.connect(); "
        "    ok = await client.is_user_authorized(); "
        "    await client.disconnect(); "
        "    print('authorized' if ok else 'unauthorized'); "
        "  except FloodWaitError as e: "
        "    print(f'flood:{e.seconds}'); "
        "  except Exception as e: "
        "    print(f'error:{type(e).__name__}: {e}'[:200]); "
        "asyncio.run(check())"
    )

    def _check_auth(cwd: Path) -> str:
        """Run the auth check script. Returns status string."""
        try:
            r = run_cmd([str(python_path), "-c", check_script], cwd=cwd, timeout=15)
            return r.stdout.strip() or "error:no output"
        except subprocess.TimeoutExpired:
            return "error:timeout"
        except Exception as e:
            return f"error:{e}"

    auth_status = _check_auth(project_dir)

    if auth_status == "authorized":
        return ToolCheck(name="telegram_session", available=True, version="authorized")
    elif auth_status.startswith("flood:"):
        seconds = auth_status.split(":", 1)[1]
        return ToolCheck(
            name="telegram_session",
            available=True,
            version="flood-wait",
            error=f"Telegram rate-limited ({seconds}s wait). Bridge will retry.",
        )
    elif auth_status == "unauthorized":
        return ToolCheck(
            name="telegram_session",
            available=False,
            error="Session expired/invalid. Run: python scripts/telegram_login.py",
        )
    else:
        # Direct auth check failed (often because bridge holds the session lock).
        # Fall back to checking bridge logs for recent heartbeats.
        bridge_status = _check_bridge_heartbeat(project_dir)
        if bridge_status:
            return ToolCheck(
                name="telegram_session",
                available=True,
                version=f"connected (via bridge heartbeat, {bridge_status})",
            )
        # No heartbeat either — genuinely unknown
        detail = auth_status.replace("error:", "", 1)
        return ToolCheck(
            name="telegram_session",
            available=True,
            version="unknown",
            error=f"Could not verify session ({detail}). Bridge will retry on startup.",
        )


def check_google_token(project_dir: Path) -> ToolCheck:
    """Verify per-machine Google OAuth token exists, migrating from shared if needed."""
    try:
        # Import triggers migration automatically
        from tools.google_workspace.auth import TOKEN_PATH

        if TOKEN_PATH.exists():
            return ToolCheck(
                name="google-token",
                available=True,
                version=TOKEN_PATH.name,
            )
        return ToolCheck(
            name="google-token",
            available=False,
            error=f"No token at {TOKEN_PATH}. Run OAuth flow to create.",
        )
    except Exception as e:
        return ToolCheck(
            name="google-token",
            available=False,
            error=f"Token check failed: {e}",
        )


_KEY_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=")
_SECTION_RE = re.compile(r"^#\s*={10,}")  # section separator lines (# ===...)


def _parse_env_example(path: Path) -> list[tuple[str, str]]:
    """Return list of (key, description) pairs from a .env.example file.

    Description is the last non-blank, non-separator comment line immediately
    above the key declaration. Blank lines reset the comment accumulator.
    """
    lines = path.read_text().splitlines()
    result = []
    comment_block: list[str] = []
    for line in lines:
        stripped = line.strip()
        if _SECTION_RE.match(stripped):
            # Section separator — reset accumulator without contributing to description
            comment_block = []
        elif stripped.startswith("#"):
            comment_block.append(stripped.lstrip("#").strip())
        elif m := _KEY_RE.match(stripped):
            key = m.group(1)
            # Use last non-empty comment line as the description
            description = next((c for c in reversed(comment_block) if c), "")
            result.append((key, description))
            comment_block = []
        else:
            comment_block = []  # blank line resets comment accumulation
    return result


def _parse_env_keys(path: Path) -> set[str]:
    """Return set of all keys present in .env (blank values are present)."""
    keys = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
            if _KEY_RE.match(key + "="):
                keys.add(key)
    return keys


def check_env_completeness(project_dir: Path) -> ToolCheck:
    """Check that .env contains all keys declared in .env.example.

    Returns a single ToolCheck:
    - available=True, version="all N vars present" when .env has all declared keys
    - available=False, error="N missing: KEY1 (desc); KEY2 (desc)" when gaps exist
    - available=True, version="skipped (.env not found)" when .env doesn't exist
    - available=True, version="skipped (read error)" on OSError
    """
    try:
        env_example = project_dir / ".env.example"
        env_file = project_dir / ".env"

        if not env_example.exists():
            return ToolCheck(
                name="env-completeness",
                available=True,
                version="skipped (.env.example not found)",
            )

        declared = _parse_env_example(env_example)

        if not env_file.exists():
            return ToolCheck(
                name="env-completeness",
                available=True,
                version="skipped (.env not found)",
            )

        present = _parse_env_keys(env_file)
        declared_keys = {k for k, _ in declared}
        missing_keys = declared_keys - present

        if not missing_keys:
            return ToolCheck(
                name="env-completeness",
                available=True,
                version=f"all {len(declared_keys)} vars present",
            )

        desc_map = dict(declared)
        parts = [
            f"{k} ({desc_map.get(k, 'no description')})" if desc_map.get(k) else k
            for k in sorted(missing_keys)
        ]
        error = f"{len(missing_keys)} missing: {'; '.join(parts)}"
        return ToolCheck(name="env-completeness", available=False, error=error)

    except OSError:
        return ToolCheck(
            name="env-completeness",
            available=True,
            version="skipped (read error)",
        )


def verify_environment(project_dir: Path, check_ollama_model: bool = True) -> VerificationResult:
    """Run all environment verification checks."""
    result = VerificationResult()

    result.system_tools = check_system_tools()
    result.python_deps = check_python_deps(project_dir)
    result.dev_tools = check_dev_tools(project_dir)
    result.valor_tools = check_valor_tools(project_dir)
    result.valor_tools.append(check_telegram_session(project_dir))
    result.valor_tools.append(check_google_token(project_dir))
    result.valor_tools.append(check_env_completeness(project_dir))

    if check_ollama_model:
        ollama_model = os.getenv("OLLAMA_SUMMARIZER_MODEL", "gemma4:e2b")
        result.ollama = check_ollama(ollama_model)

    result.sdk_auth = check_sdk_auth(project_dir)
    result.mcp_servers = check_mcp_servers()
    result.gitignore_issues = check_gitignore_issues()

    return result


def check_projects_json(project_dir: Path) -> ToolCheck:
    """Validate ~/Desktop/Valor/projects.json before allowing a service restart.

    Acts as the green-light gate for bridge startup: if the iCloud-synced
    config is malformed (e.g. one Telegram contact mapped to multiple
    machines), the update script must not bounce the bridge — the existing
    process keeps running on the old (validated) config until the operator
    fixes the file.

    Returns ToolCheck.available=True on pass; available=False with an
    error string on failure. Also returns True with version="skipped" when
    the file is missing (fresh machine; bridge install gates separately).
    """
    import json
    import sys

    config_path = Path.home() / "Desktop" / "Valor" / "projects.json"
    if not config_path.exists():
        return ToolCheck(
            name="projects.json",
            available=True,
            version="skipped (file not present)",
        )

    try:
        cfg = json.loads(config_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        return ToolCheck(
            name="projects.json",
            available=False,
            error=f"Failed to read/parse: {e}",
        )

    # Import the validator from the bridge package. Add project root to
    # sys.path defensively in case verify.py is invoked outside the venv.
    sys.path.insert(0, str(project_dir))
    try:
        from bridge.config_validation import (
            ConfigValidationError,
            validate_projects_config,
        )
    except ImportError as e:
        return ToolCheck(
            name="projects.json",
            available=False,
            error=f"Could not import validator: {e}",
        )

    try:
        validate_projects_config(cfg)
    except ConfigValidationError as e:
        return ToolCheck(name="projects.json", available=False, error=str(e))

    projects = cfg.get("projects", {})
    whitelist_count = len(cfg.get("dms", {}).get("whitelist", []))
    group_count = sum(
        len((p.get("telegram") or {}).get("groups") or {})
        for p in projects.values()
        if isinstance(p, dict)
    )
    email_count = sum(
        len((p.get("email") or {}).get("contacts") or [])
        + len((p.get("email") or {}).get("domains") or [])
        for p in projects.values()
        if isinstance(p, dict)
    )
    return ToolCheck(
        name="projects.json",
        available=True,
        version=(
            f"valid ({whitelist_count} DM contacts, "
            f"{group_count} groups, {email_count} email patterns)"
        ),
    )


def check_sdlc_tool(project_dir: Path) -> ToolCheck:
    """Verify `sdlc-tool` is installed and dispatches into the repo's tools.

    Acts as a green-light gate for the bridge restart, mirroring
    ``check_projects_json``. If the wrapper is missing or its dispatch
    fails, the bridge keeps running on the previous (working) build and
    the operator is told what to fix.

    Three failure modes:
    - wrapper not on PATH (hardlink step failed or PATH is wrong)
    - wrapper resolves but dispatch raises (typically a venv / uv mismatch)
    - wrapper hardlink shares a different inode from ``scripts/sdlc-tool``
      (means an old copy is still live; ran an /update that didn't sync)
    """
    wrapper_path = shutil.which("sdlc-tool")
    if not wrapper_path:
        return ToolCheck(
            name="sdlc-tool",
            available=False,
            error=(
                "sdlc-tool not on PATH. Re-run `/update` to hardlink it into "
                "~/.local/bin, and ensure ~/.local/bin is on PATH."
            ),
        )

    # Confirm the on-PATH wrapper points at the repo copy (same inode).
    src_path = project_dir / "scripts" / "sdlc-tool"
    if src_path.exists():
        try:
            if os.stat(wrapper_path).st_ino != os.stat(src_path).st_ino:
                return ToolCheck(
                    name="sdlc-tool",
                    available=False,
                    error=(
                        f"sdlc-tool at {wrapper_path} is not hardlinked to "
                        f"{src_path}. Re-run `/update` to refresh the link."
                    ),
                )
        except OSError as e:
            return ToolCheck(
                name="sdlc-tool",
                available=False,
                error=f"Could not stat sdlc-tool source/dest: {e}",
            )

    # Smoke test: dispatch into stage-query against a non-existent issue.
    # `stage-query` returns a graceful "no session" payload on miss, so a
    # zero exit + parseable JSON proves the dispatch works end-to-end.
    try:
        result = run_cmd(
            [wrapper_path, "stage-query", "--issue-number", "0"],
            cwd=Path("/tmp"),  # foreign cwd to exercise the resolver
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return ToolCheck(
            name="sdlc-tool",
            available=False,
            error="sdlc-tool stage-query --issue-number 0 timed out (>30s)",
        )
    except Exception as e:
        return ToolCheck(name="sdlc-tool", available=False, error=str(e))

    if result.returncode != 0:
        return ToolCheck(
            name="sdlc-tool",
            available=False,
            error=(
                f"sdlc-tool stage-query --issue-number 0 exited "
                f"{result.returncode}: {result.stderr.strip() or result.stdout.strip()}"
            ),
        )

    # Confirm stdout is parseable JSON.
    import json as _json

    try:
        _json.loads(result.stdout.strip() or "{}")
    except _json.JSONDecodeError as e:
        return ToolCheck(
            name="sdlc-tool",
            available=False,
            error=f"sdlc-tool stage-query stdout is not valid JSON: {e}",
        )

    return ToolCheck(
        name="sdlc-tool",
        available=True,
        version=f"resolved at {wrapper_path}",
    )


def check_machine_identity(project_dir: Path) -> dict:
    """Verify this machine's identity against projects.json config.

    Reads ComputerName via scutil, matches against the 'machine' field
    in ~/Desktop/Valor/projects.json, and returns the matched projects.
    """
    import json

    # Get this machine's name
    try:
        hostname = subprocess.check_output(["scutil", "--get", "ComputerName"], text=True).strip()
    except Exception as e:
        return {"error": f"Could not read ComputerName: {e}"}

    # Find projects.json
    config_path = Path.home() / "Desktop" / "Valor" / "projects.json"
    if not config_path.exists():
        config_path = project_dir / "config" / "projects.json"
    if not config_path.exists():
        return {"error": "projects.json not found", "hostname": hostname}

    try:
        config = json.loads(config_path.read_text())
    except Exception as e:
        return {"error": f"Failed to read projects.json: {e}", "hostname": hostname}

    # Match projects by machine field
    hostname_lower = hostname.lower()
    matched = []
    for key, project in config.get("projects", {}).items():
        machine = project.get("machine", "")
        if machine.lower() == hostname_lower:
            matched.append(key)

    return {
        "hostname": hostname,
        "projects": matched,
        "config_path": str(config_path),
    }
