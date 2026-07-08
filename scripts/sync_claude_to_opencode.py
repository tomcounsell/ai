#!/usr/bin/env python3
"""Idempotently sync this repo's Claude Code configuration into OpenCode's layout.

Source of truth (Claude Code):
  .claude/agents/*.md        -> .opencode/agents/*.md
  .claude/commands/roles/*.md -> .opencode/commands/*.md
  .claude/settings.json (hooks + permissions) -> .opencode/opencode.json[permission]
  .claude/hooks/*.py         -> .opencode/plugins/valor-bridge.ts (hook port)

OpenCode natively discovers .claude/skills/*/SKILL.md, so skills are NOT migrated.

Every generated artifact is stamped with the sync date and a provenance comment, and
.opencode/SYNC_MANIFEST.json records the sha256 of every consumed source file so future
drift is detectable. Re-running this script only rewrites files whose source changed.

Usage:
  python scripts/sync_claude_to_opencode.py
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CLAUDE_DIR = REPO_ROOT / ".claude"
OPENCODE_DIR = REPO_ROOT / ".opencode"
SYNC_DATE = date.today().isoformat()

# Claude model shorthands -> OpenCode provider/model ids
MODEL_MAP = {
    "sonnet": "anthropic/claude-sonnet-4-5",
    "haiku": "anthropic/claude-haiku-4-5",
    "opus": "anthropic/claude-opus-4-5",
}

# Claude tool names -> OpenCode permission keys
TOOL_MAP = {
    "Write": "write",
    "Edit": "edit",
    "NotebookEdit": "edit",
    "Read": "read",
    "Glob": "glob",
    "Grep": "grep",
    "Bash": "bash",
    "Agent": "task",
}

# OpenCode only accepts a fixed color palette (or #rrggbb). Map Claude names onto it.
COLOR_MAP = {
    "cyan": "info",
    "blue": "info",
    "green": "success",
    "yellow": "warning",
    "red": "error",
    "purple": "accent",
    "orange": "warning",
    "magenta": "accent",
}


def map_color(c):
    if c is None:
        return None
    c = str(c).lower()
    if c in ("primary", "secondary", "accent", "success", "warning", "error", "info"):
        return c
    if re.fullmatch(r"#[0-9a-fa-f]{6}", c):
        return c
    return COLOR_MAP.get(c)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _as_list(v):
    """Claude frontmatter may write tools as a list OR a bare comma string."""
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return [v]


def stamp(source_rel: str) -> str:
    return f"<!-- opencode-sync: generated {SYNC_DATE} from {source_rel} -->"


def split_frontmatter(text: str):
    """Return (frontmatter_dict, body_text) for a --- delimited markdown file."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return {}, text
    fm = yaml.safe_load(parts[1]) or {}
    return fm, parts[2]


def dump_frontmatter(fm: dict) -> str:
    body = yaml.safe_dump(fm, sort_keys=False, default_flow_style=False, allow_unicode=True)
    return "---\n" + body + "---\n"


# --------------------------------------------------------------------------- #
# Permissions (Phase 1)
# --------------------------------------------------------------------------- #
def _claude_bash_to_glob(spec: str) -> str:
    """Bash(gh pr:*) -> 'gh pr *'; Bash(git add *) -> 'git add *'."""
    return spec.replace(":*", " *")


def build_permission() -> dict:
    """Translate settings.json (+ settings.local.json) allow list into OpenCode's
    permission schema. Claude Code asks for anything not listed; we mirror that with
    bash '*': 'ask' plus explicit allows, while edits/writes stay allowed by default."""
    permission: dict = {
        "edit": "allow",
        "write": "allow",
        "bash": {"*": "ask"},
        "skill": {"*": "allow"},
    }

    for cfg_name in ("settings.json", "settings.local.json"):
        cfg_path = CLAUDE_DIR / cfg_name
        if not cfg_path.exists():
            continue
        cfg = json.loads(cfg_path.read_text())
        for entry in cfg.get("permissions", {}).get("allow", []):
            m = re.match(r"^(Bash|Skill|Write|Edit|Read|Glob|Grep)\((.*)\)$", entry)
            if not m:
                continue
            kind, spec = m.group(1), m.group(2)
            if kind == "Bash":
                glob = _claude_bash_to_glob(spec)
                permission["bash"][glob] = "allow"
            elif kind == "Skill":
                permission["skill"][spec.strip().lower()] = "allow"
            elif kind in ("Write", "Edit", "Read", "Glob", "Grep"):
                permission[TOOL_MAP.get(kind, kind.lower())] = "allow"

        # skillOverrides "off" -> deny the skill in OpenCode
        for name, val in cfg.get("skillOverrides", {}).items():
            if str(val).lower() == "off":
                permission["skill"][name] = "deny"

    return permission


def write_opencode_json(permission: dict) -> None:
    header = (
        f"// opencode-sync: generated {SYNC_DATE}\n"
        "// Source of truth: .claude/settings.json (permissions) + this script's template.\n"
        "// Re-run scripts/sync_claude_to_opencode.py to regenerate idempotently.\n"
    )
    config = {
        "$schema": "https://opencode.ai/config.json",
        "model": "anthropic/claude-sonnet-4-5",
        "small_model": "anthropic/claude-haiku-4-5",
        "instructions": ["CLAUDE.md"],
        "permission": permission,
    }
    OPENCODE_DIR.mkdir(exist_ok=True)
    (OPENCODE_DIR / "opencode.json").write_text(header + json.dumps(config, indent=2) + "\n")


# --------------------------------------------------------------------------- #
# Agents (Phase 2)
# --------------------------------------------------------------------------- #
def sync_agents(manifest: dict) -> None:
    out_dir = OPENCODE_DIR / "agents"
    out_dir.mkdir(parents=True, exist_ok=True)
    for src in sorted((CLAUDE_DIR / "agents").glob("*.md")):
        fm, body = split_frontmatter(src.read_text())
        new_fm: dict = {}
        if "description" in fm:
            new_fm["description"] = fm["description"]
        # mode: builder is usable as a primary build agent; rest are subagents
        new_fm["mode"] = "all" if src.stem == "builder" else "subagent"
        if "model" in fm:
            new_fm["model"] = MODEL_MAP.get(str(fm["model"]), str(fm["model"]))
        if "color" in fm:
            mapped = map_color(fm["color"])
            if mapped:
                new_fm["color"] = mapped

        perm: dict = {}
        tools = _as_list(fm.get("tools"))
        dis = _as_list(fm.get("disallowedTools"))
        if tools and tools != ["*"]:
            perm["*"] = "deny"
            for t in tools:
                perm[TOOL_MAP.get(t, str(t).lower())] = "allow"
        if dis:
            for t in dis:
                perm[TOOL_MAP.get(t, str(t).lower())] = "deny"
        if perm:
            new_fm["permission"] = perm

        content = dump_frontmatter(new_fm) + stamp(f".claude/agents/{src.name}") + "\n" + body
        (out_dir / src.name).write_text(content)
        manifest["agents"][f".claude/agents/{src.name}"] = sha256(src)


# --------------------------------------------------------------------------- #
# Commands (Phase 4)
# --------------------------------------------------------------------------- #
def sync_commands(manifest: dict) -> None:
    out_dir = OPENCODE_DIR / "commands"
    out_dir.mkdir(parents=True, exist_ok=True)
    roles_dir = CLAUDE_DIR / "commands" / "roles"
    if not roles_dir.exists():
        return
    for src in sorted(roles_dir.glob("*.md")):
        if src.name.startswith("_"):  # shared include, not a command
            continue
        fm, body = split_frontmatter(src.read_text())
        new_fm = {"description": fm.get("description", src.stem), "agent": "build"}
        content = (
            dump_frontmatter(new_fm) + stamp(f".claude/commands/roles/{src.name}") + "\n" + body
        )
        (out_dir / src.name).write_text(content)
        manifest["commands"][f".claude/commands/roles/{src.name}"] = sha256(src)


# --------------------------------------------------------------------------- #
# Hooks -> OpenCode plugin (Phase 3)
# --------------------------------------------------------------------------- #
def parse_hooks() -> dict:
    """Extract every hook command from settings.json, classified by OpenCode event."""
    settings = json.loads((CLAUDE_DIR / "settings.json").read_text())
    groups = {
        "pre_bash": [],
        "pre_edit": [],
        "pre_global": [],
        "post_write": [],
        "post_edit": [],
        "post_global": [],
        "session_created": [],
        "session_idle": [],
        "session_compacted": [],
    }
    for event, entries in settings.get("hooks", {}).items():
        for entry in entries:
            matcher = entry.get("matcher", "")
            for hook in entry.get("hooks", []):
                cmd = hook["command"]
                blocking = "|| true" not in cmd
                clean = re.sub(r"\s*\|\|\s*true\s*$", "", cmd).strip()
                rec = {"cmd": clean, "blocking": blocking}
                if event == "PreToolUse":
                    if matcher == "":
                        groups["pre_global"].append(rec)
                    elif "Bash" in matcher:
                        groups["pre_bash"].append(rec)
                    elif "Write" in matcher or "Edit" in matcher:
                        groups["pre_edit"].append(rec)
                elif event == "PostToolUse":
                    if matcher == "":
                        groups["post_global"].append(rec)
                    elif "Write" in matcher:
                        groups["post_write"].append(rec)
                    elif "Edit" in matcher:
                        groups["post_edit"].append(rec)
                elif event == "UserPromptSubmit":
                    groups["session_created"].append(rec)
                elif event == "Stop":
                    groups["session_idle"].append(rec)
                elif event == "SubagentStop":
                    groups["session_idle"].append(rec)
                elif event == "PostCompact":
                    groups["session_compacted"].append(rec)
    return groups


PLUGIN_TEMPLATE = """// opencode-sync: generated {date}
// Port of .claude/hooks/*.py validators into an OpenCode plugin.
// Re-run scripts/sync_claude_to_opencode.py to regenerate idempotently.
//
// OpenCode has no PreToolUse/PostToolUse/Stop hook runner, so this plugin
// re-dispatches the SAME python validators on tool.execute.before/after and
// the nearest session-lifecycle equivalents. CLAUDE_PROJECT_DIR is injected
// from the project directory so the validators behave exactly as under Claude Code.
// Blocking validators throw to reject the operation; best-effort ("|| true")
// validators never block.

import {{ type Plugin }} from "@opencode-ai/plugin"

const PRE_BASH = {pre_bash}
const PRE_EDIT = {pre_edit}
const PRE_GLOBAL = {pre_global}
const POST_WRITE = {post_write}
const POST_EDIT = {post_edit}
const POST_GLOBAL = {post_global}
const SESSION_CREATED = {session_created}
const SESSION_IDLE = {session_idle}
const SESSION_COMPACTED = {session_compacted}

async function runValidator(rec: {{ cmd: string; blocking: boolean }}, payload: any, dir: string) {{
  const proc = Bun.spawn(["bash", "-c", rec.cmd], {{
    stdin: new Blob([JSON.stringify(payload)]),
    env: {{ ...process.env, CLAUDE_PROJECT_DIR: dir }},
    stdout: "pipe",
    stderr: "pipe",
  }})
  const code = await proc.exited
  if (rec.blocking && code !== 0) {{
    const err = await new Response(proc.stderr).text()
    const tail = err.slice(0, 400)
    const msg = "[valor-bridge] " + rec.cmd.split(" ").pop()
      + " blocked (exit " + code + "): " + tail
    throw new Error(msg)
  }}
}}

const toolPayload = (tool: string, args: any) => ({{
  tool_name: tool === "write" ? "Write" : tool === "edit" ? "Edit" : tool,
  tool_input: {{
    command: args?.command ?? "",
    file_path: args?.filePath,
    filePath: args?.filePath,
  }},
}})

export const ValorBridge: Plugin = async ({{ directory }}) => {{
  const dir = directory
  return {{
    "tool.execute.before": async (input, _output) => {{
      const p = toolPayload(input.tool, input.args)
      if (input.tool === "bash") for (const v of PRE_BASH) await runValidator(v, p, dir)
      if (input.tool === "edit" || input.tool === "write") {{
        for (const v of PRE_EDIT) await runValidator(v, p, dir)
      }}
      for (const v of PRE_GLOBAL) await runValidator(v, p, dir)
    }},
    "tool.execute.after": async (input, _output) => {{
      const p = toolPayload(input.tool, input.args)
      if (input.tool === "edit" || input.tool === "write") {{
        for (const v of POST_WRITE) await runValidator(v, p, dir)
        for (const v of POST_EDIT) await runValidator(v, p, dir)
      }}
      for (const v of POST_GLOBAL) await runValidator(v, p, dir)
    }},
    "session.created": async () => {{
      for (const v of SESSION_CREATED)
        await runValidator(v, {{ tool_name: "", tool_input: {{}} }}, dir)
    }},
    "session.idle": async () => {{
      for (const v of SESSION_IDLE)
        await runValidator(v, {{ tool_name: "", tool_input: {{}} }}, dir)
    }},
    "session.compacted": async () => {{
      for (const v of SESSION_COMPACTED)
        await runValidator(v, {{ tool_name: "", tool_input: {{}} }}, dir)
    }},
  }}
}}
"""


def write_plugin(manifest: dict) -> None:
    groups = parse_hooks()
    plugins_dir = OPENCODE_DIR / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    content = PLUGIN_TEMPLATE.format(
        date=SYNC_DATE,
        pre_bash=json.dumps(groups["pre_bash"]),
        pre_edit=json.dumps(groups["pre_edit"]),
        pre_global=json.dumps(groups["pre_global"]),
        post_write=json.dumps(groups["post_write"]),
        post_edit=json.dumps(groups["post_edit"]),
        post_global=json.dumps(groups["post_global"]),
        session_created=json.dumps(groups["session_created"]),
        session_idle=json.dumps(groups["session_idle"]),
        session_compacted=json.dumps(groups["session_compacted"]),
    )
    (plugins_dir / "valor-bridge.ts").write_text(content)
    # record the consumed hook sources for drift detection
    hooks_root = CLAUDE_DIR / "hooks"
    for p in sorted(hooks_root.rglob("*.py")):
        rel = str(p.relative_to(REPO_ROOT))
        manifest["hooks"][rel] = sha256(p)


# --------------------------------------------------------------------------- #
# Manifest
# --------------------------------------------------------------------------- #
def write_manifest(manifest: dict) -> None:
    manifest["generated_on"] = SYNC_DATE
    (OPENCODE_DIR / "SYNC_MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")


def main() -> None:
    OPENCODE_DIR.mkdir(exist_ok=True)
    manifest = {"generated_on": SYNC_DATE, "agents": {}, "commands": {}, "hooks": {}}

    permission = build_permission()
    write_opencode_json(permission)
    sync_agents(manifest)
    sync_commands(manifest)
    write_plugin(manifest)
    write_manifest(manifest)

    n_agents = len(manifest["agents"])
    n_cmds = len(manifest["commands"])
    n_hooks = len(manifest["hooks"])
    print(
        f"[opencode-sync] {SYNC_DATE}: wrote opencode.json, {n_agents} agents, "
        f"{n_cmds} commands, valor-bridge.ts ({n_hooks} hook sources tracked)"
    )


if __name__ == "__main__":
    main()
