"""Environment verification for update system."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ToolCheck:
    """Result of a single tool check."""
    name: str
    available: bool
    version: str | None = None
    error: str | None = None


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


def check_python_import(project_dir: Path, module: str, display_name: str | None = None) -> ToolCheck:
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


def check_system_tools() -> list[ToolCheck]:
    """Check system-level tools."""
    tools = [
        ("claude", "--version"),
        ("gh", "--version"),
        ("git", "--version"),
        ("uv", "--version"),
    ]
    return [check_command(name, flag) for name, flag in tools]


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
                [str(python_path), "-m", "tools.sms_reader.cli", "recent", "--limit", "1"],
                cwd=project_dir,
                timeout=10,
            )
            results.append(ToolCheck(
                name="sms_reader",
                available=result.returncode == 0,
                error=result.stderr.strip() if result.returncode != 0 else None,
            ))
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

    results.append(ToolCheck(
        name="valor-calendar",
        available=calendar_found,
        version=calendar_version,
    ))

    return results


def check_ollama(model: str = "qwen3:4b") -> ToolCheck:
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


def pull_ollama_model(model: str = "qwen3:4b") -> bool:
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

    # Check .env for API key and billing preference
    env_file = project_dir / ".env"
    if env_file.exists():
        content = env_file.read_text()
        result["api_key_configured"] = "ANTHROPIC_API_KEY=sk-ant-" in content
        result["use_api_billing"] = "USE_API_BILLING=true" in content

    return result


def check_mcp_servers() -> list[str]:
    """Get list of configured MCP servers."""
    try:
        result = run_cmd(["claude", "mcp", "list"], timeout=30)
        if result.returncode != 0:
            return []

        # Parse output - each line is a server
        servers = []
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if line and not line.startswith("No "):
                servers.append(line)
        return servers
    except Exception:
        return []


def verify_environment(project_dir: Path, check_ollama_model: bool = True) -> VerificationResult:
    """Run all environment verification checks."""
    result = VerificationResult()

    result.system_tools = check_system_tools()
    result.python_deps = check_python_deps(project_dir)
    result.dev_tools = check_dev_tools(project_dir)
    result.valor_tools = check_valor_tools(project_dir)

    if check_ollama_model:
        ollama_model = os.getenv("OLLAMA_SUMMARIZER_MODEL", "qwen3:4b")
        result.ollama = check_ollama(ollama_model)

    result.sdk_auth = check_sdk_auth(project_dir)
    result.mcp_servers = check_mcp_servers()

    return result
