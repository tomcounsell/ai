#!/usr/bin/env python3
"""Idempotently sync this repo's Claude Code configuration into OpenCode's layout.

Source of truth (Claude Code):
  .claude/agents/*.md        -> .opencode/agents/*.md
  .claude/commands/roles/*.md -> .opencode/commands/*.md
  .claude/settings.json (hooks + permissions) -> .opencode/opencode.json[permission]
  .claude/hooks/*.py         -> .opencode/plugins/valor-bridge.ts (hook port)

OpenCode natively discovers .claude/skills/*/SKILL.md, so skills are NOT migrated.

Only the committed .claude/settings.json is consumed — the gitignored, machine-local
.claude/settings.local.json is deliberately excluded so committed artifacts stay
reproducible on every machine.

Selective rewrite: .opencode/SYNC_MANIFEST.json records the sha256 of every consumed
source file. Before writing each artifact the source hash is compared against the
manifest entry, and unchanged sources are skipped. Generated headers carry a stable
provenance comment (no date stamp), so re-running the script against unchanged
sources produces zero churn — on any day.

Usage:
  python scripts/sync_claude_to_opencode.py
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CLAUDE_DIR = REPO_ROOT / ".claude"
OPENCODE_DIR = REPO_ROOT / ".opencode"

# Bump whenever this script's templates or output format change. A version
# mismatch invalidates the manifest's source-hash skip, forcing a full
# content-compare pass so stale artifacts from an older generator are refreshed.
GENERATOR_VERSION = 2

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
    if re.fullmatch(r"#[0-9a-f]{6}", c):
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
    """Stable provenance header — deliberately date-free so unchanged sources
    regenerate byte-identically (zero churn across days)."""
    return f"<!-- opencode-sync: generated from {source_rel} -->"


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


def write_if_changed(path: Path, content: str) -> bool:
    """Write content only when it differs from what's on disk. Returns True if written."""
    if path.exists() and path.read_text() == content:
        return False
    path.write_text(content)
    return True


def load_manifest(opencode_dir: Path) -> dict:
    path = opencode_dir / "SYNC_MANIFEST.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def _skippable(old_manifest: dict) -> bool:
    """Source-hash skips are only honored for manifests written by THIS generator
    version — otherwise artifacts produced by older templates would never refresh."""
    return old_manifest.get("generator_version") == GENERATOR_VERSION


# --------------------------------------------------------------------------- #
# Permissions (Phase 1)
# --------------------------------------------------------------------------- #
def _claude_bash_to_globs(spec: str) -> list[str]:
    """Translate a Claude Bash() spec into OpenCode glob keys.

    Claude's prefix form `gh pr:*` matches both the bare `gh pr` and `gh pr <args>`,
    while the OpenCode glob `gh pr *` requires a trailing argument — so prefix specs
    emit BOTH keys. Explicit globs like `git add *` pass through unchanged.
    """
    if spec.endswith(":*"):
        base = spec[:-2]
        return [base, f"{base} *"]
    return [spec.replace(":*", " *")]


def build_permission(claude_dir: Path = CLAUDE_DIR) -> dict:
    """Translate the committed .claude/settings.json allow list into OpenCode's
    permission schema. Claude Code asks for anything not listed; we mirror that with
    bash '*': 'ask' plus explicit allows, while edits/writes stay allowed by default.

    Only the committed settings.json is read. The gitignored settings.local.json is
    machine-local; folding it in would leak one-off local grants (including
    destructive rm allowances) into a committed artifact and make regeneration
    non-reproducible across machines.
    """
    permission: dict = {
        "edit": "allow",
        "write": "allow",
        "bash": {"*": "ask"},
        "skill": {"*": "allow"},
    }

    cfg_path = claude_dir / "settings.json"
    if not cfg_path.exists():
        return permission
    cfg = json.loads(cfg_path.read_text())
    for entry in cfg.get("permissions", {}).get("allow", []):
        m = re.match(r"^(Bash|Skill|Write|Edit|Read|Glob|Grep)\((.*)\)$", entry)
        if not m:
            continue
        kind, spec = m.group(1), m.group(2)
        if kind == "Bash":
            for glob in _claude_bash_to_globs(spec):
                permission["bash"][glob] = "allow"
        elif kind == "Skill":
            permission["skill"][spec.strip().lower()] = "allow"
        # NOTE: path-scoped grants like Write(.claude/hooks/**) are intentionally
        # NOT translated. OpenCode's edit/write permissions have no per-path form
        # in this template, and collapsing the scope to a blanket "write": "allow"
        # would silently over-grant the day the template default tightens. The
        # template already allows edit/write, so dropping the scoped entry is a
        # no-op today and safe tomorrow.

    # skillOverrides "off" -> deny the skill in OpenCode
    for name, val in cfg.get("skillOverrides", {}).items():
        if str(val).lower() == "off":
            permission["skill"][name] = "deny"

    return permission


def write_opencode_json(permission: dict, opencode_dir: Path = OPENCODE_DIR) -> bool:
    header = (
        "// opencode-sync: generated from .claude/settings.json\n"
        "// Source of truth: committed .claude/settings.json (permissions) + this script's"
        " template.\n"
        "// settings.local.json is machine-local and deliberately excluded.\n"
        "// Re-run scripts/sync_claude_to_opencode.py to regenerate idempotently.\n"
    )
    config = {
        "$schema": "https://opencode.ai/config.json",
        "model": "anthropic/claude-sonnet-4-5",
        "small_model": "anthropic/claude-haiku-4-5",
        "instructions": ["CLAUDE.md"],
        "permission": permission,
    }
    opencode_dir.mkdir(exist_ok=True)
    return write_if_changed(
        opencode_dir / "opencode.json", header + json.dumps(config, indent=2) + "\n"
    )


# --------------------------------------------------------------------------- #
# Agents (Phase 2)
# --------------------------------------------------------------------------- #
def sync_agents(
    manifest: dict,
    old_manifest: dict,
    claude_dir: Path = CLAUDE_DIR,
    opencode_dir: Path = OPENCODE_DIR,
) -> tuple[int, int]:
    """Returns (written, skipped) counts."""
    out_dir = opencode_dir / "agents"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = skipped = 0
    for src in sorted((claude_dir / "agents").glob("*.md")):
        rel = f".claude/agents/{src.name}"
        src_hash = sha256(src)
        manifest["agents"][rel] = src_hash
        dest = out_dir / src.name
        # Selective rewrite: unchanged source (per manifest) + existing artifact -> skip.
        if (
            _skippable(old_manifest)
            and old_manifest.get("agents", {}).get(rel) == src_hash
            and dest.exists()
        ):
            skipped += 1
            continue

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

        content = dump_frontmatter(new_fm) + stamp(rel) + "\n" + body
        if write_if_changed(dest, content):
            written += 1
        else:
            skipped += 1
    return written, skipped


# --------------------------------------------------------------------------- #
# Commands (Phase 4)
# --------------------------------------------------------------------------- #
def sync_commands(
    manifest: dict,
    old_manifest: dict,
    claude_dir: Path = CLAUDE_DIR,
    opencode_dir: Path = OPENCODE_DIR,
) -> tuple[int, int]:
    """Returns (written, skipped) counts."""
    out_dir = opencode_dir / "commands"
    out_dir.mkdir(parents=True, exist_ok=True)
    roles_dir = claude_dir / "commands" / "roles"
    written = skipped = 0
    if not roles_dir.exists():
        return written, skipped
    for src in sorted(roles_dir.glob("*.md")):
        if src.name.startswith("_"):  # shared include, not a command
            continue
        rel = f".claude/commands/roles/{src.name}"
        src_hash = sha256(src)
        manifest["commands"][rel] = src_hash
        dest = out_dir / src.name
        if (
            _skippable(old_manifest)
            and old_manifest.get("commands", {}).get(rel) == src_hash
            and dest.exists()
        ):
            skipped += 1
            continue
        fm, body = split_frontmatter(src.read_text())
        new_fm = {"description": fm.get("description", src.stem), "agent": "build"}
        content = dump_frontmatter(new_fm) + stamp(rel) + "\n" + body
        if write_if_changed(dest, content):
            written += 1
        else:
            skipped += 1
    return written, skipped


# --------------------------------------------------------------------------- #
# Hooks -> OpenCode plugin (Phase 3)
# --------------------------------------------------------------------------- #
def parse_hooks(claude_dir: Path = CLAUDE_DIR) -> dict:
    """Extract every hook command from settings.json, classified by OpenCode event.

    Combined matchers (e.g. "Bash|Write") register in every matching group. Hooks
    that match no group are never dropped silently — a loud warning listing each
    dropped command is printed to stderr.
    """
    settings = json.loads((claude_dir / "settings.json").read_text())
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
    dropped: list[str] = []
    for event, entries in settings.get("hooks", {}).items():
        for entry in entries:
            matcher = entry.get("matcher", "")
            for hook in entry.get("hooks", []):
                cmd = hook["command"]
                blocking = "|| true" not in cmd
                clean = re.sub(r"\s*\|\|\s*true\s*$", "", cmd).strip()
                rec = {"cmd": clean, "blocking": blocking}
                matched = False
                if event == "PreToolUse":
                    if matcher == "":
                        groups["pre_global"].append(rec)
                        matched = True
                    else:
                        if "Bash" in matcher:
                            groups["pre_bash"].append(rec)
                            matched = True
                        if "Write" in matcher or "Edit" in matcher:
                            groups["pre_edit"].append(rec)
                            matched = True
                elif event == "PostToolUse":
                    if matcher == "":
                        groups["post_global"].append(rec)
                        matched = True
                    else:
                        if "Write" in matcher:
                            groups["post_write"].append(rec)
                            matched = True
                        if "Edit" in matcher:
                            groups["post_edit"].append(rec)
                            matched = True
                elif event == "UserPromptSubmit":
                    groups["session_created"].append(rec)
                    matched = True
                elif event in ("Stop", "SubagentStop"):
                    groups["session_idle"].append(rec)
                    matched = True
                elif event == "PostCompact":
                    groups["session_compacted"].append(rec)
                    matched = True
                if not matched:
                    dropped.append(f"{event}[matcher={matcher!r}]: {clean}")
    if dropped:
        print(
            "[opencode-sync] WARNING: the following hooks matched no OpenCode event "
            "group and were NOT ported into valor-bridge.ts:\n  " + "\n  ".join(dropped),
            file=sys.stderr,
        )
    return groups


PLUGIN_TEMPLATE = """// opencode-sync: generated from .claude/settings.json hooks
// Port of .claude/hooks/*.py validators into an OpenCode plugin.
// Re-run scripts/sync_claude_to_opencode.py to regenerate idempotently.
//
// OpenCode has no PreToolUse/PostToolUse/Stop hook runner, so this plugin
// re-dispatches the SAME python validators on tool.execute.before/after and
// the nearest session-lifecycle equivalents. CLAUDE_PROJECT_DIR is injected
// from the project directory so the validators behave exactly as under Claude Code.
//
// Blocking follows Claude Code's dual hook protocol:
//   1. stdout JSON {{"decision": "block", "reason": "..."}} with exit 0
//      (how every PreToolUse validator in this repo blocks), and
//   2. a non-zero exit code (how the PostToolUse plan validators block,
//      via sys.exit(2)).
// Best-effort ("|| true") validators never block.

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
  if (!rec.blocking) return
  const label = "[valor-bridge] " + (rec.cmd.split(" ").pop() ?? rec.cmd)
  // Protocol 1: stdout JSON block decision (validator exits 0).
  const out = (await new Response(proc.stdout).text()).trim()
  if (out.startsWith("{{")) {{
    let decision: any = null
    try {{
      decision = JSON.parse(out)
    }} catch {{
      decision = null // non-JSON stdout: fall through to the exit-code protocol
    }}
    if (decision?.decision === "block") {{
      throw new Error(label + " blocked: " + (decision.reason ?? "(no reason given)"))
    }}
  }}
  // Protocol 2: non-zero exit code (PostToolUse plan validators use sys.exit(2)).
  if (code !== 0) {{
    const err = await new Response(proc.stderr).text()
    throw new Error(label + " blocked (exit " + code + "): " + err.slice(0, 400))
  }}
}}

// OpenCode reports lowercase tool ids; the python validators fast-path on
// Claude Code's canonical casing (e.g. "Bash"), so map back before dispatch.
const TOOL_NAME_MAP: Record<string, string> = {{
  bash: "Bash",
  edit: "Edit",
  glob: "Glob",
  grep: "Grep",
  read: "Read",
  task: "Task",
  webfetch: "WebFetch",
  write: "Write",
}}

const toolPayload = (tool: string, args: any) => ({{
  tool_name: TOOL_NAME_MAP[tool] ?? tool,
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
      if (input.tool === "write") for (const v of POST_WRITE) await runValidator(v, p, dir)
      if (input.tool === "edit") for (const v of POST_EDIT) await runValidator(v, p, dir)
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


def write_plugin(
    manifest: dict, claude_dir: Path = CLAUDE_DIR, opencode_dir: Path = OPENCODE_DIR
) -> bool:
    groups = parse_hooks(claude_dir)
    plugins_dir = opencode_dir / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    content = PLUGIN_TEMPLATE.format(
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
    # record the consumed hook sources for drift detection
    hooks_root = claude_dir / "hooks"
    if hooks_root.exists():
        for p in sorted(hooks_root.rglob("*.py")):
            rel = f".claude/hooks/{p.relative_to(hooks_root)}"
            manifest["hooks"][rel] = sha256(p)
    return write_if_changed(plugins_dir / "valor-bridge.ts", content)


# --------------------------------------------------------------------------- #
# Orphan cleanup
# --------------------------------------------------------------------------- #
def remove_orphans(claude_dir: Path = CLAUDE_DIR, opencode_dir: Path = OPENCODE_DIR) -> int:
    """Delete generated artifacts whose source file no longer exists.

    Scans .opencode/agents/ and .opencode/commands/ only. A file is removed solely
    when BOTH hold: its corresponding .claude/ source is gone, AND it carries this
    generator's provenance stamp — hand-written files are never touched. Each
    deletion is logged loudly. Returns the number of files removed.
    """
    removed = 0
    for out_sub, src_sub in (("agents", "agents"), ("commands", "commands/roles")):
        out_dir = opencode_dir / out_sub
        if not out_dir.exists():
            continue
        for artifact in sorted(out_dir.glob("*.md")):
            src = claude_dir / src_sub / artifact.name
            if src.exists():
                continue
            if "opencode-sync: generated from" not in artifact.read_text():
                continue  # not one of ours — leave hand-written files alone
            print(
                f"[opencode-sync] REMOVED orphaned artifact {out_sub}/{artifact.name} "
                f"(source .claude/{src_sub}/{artifact.name} no longer exists)"
            )
            artifact.unlink()
            removed += 1
    return removed


# --------------------------------------------------------------------------- #
# Manifest
# --------------------------------------------------------------------------- #
def write_manifest(manifest: dict, opencode_dir: Path = OPENCODE_DIR) -> bool:
    """Write the manifest, preserving generated_on when nothing changed.

    generated_on only advances when some source hash changed, so a no-op sync
    leaves the manifest file (and its mtime-visible content) untouched.
    """
    path = opencode_dir / "SYNC_MANIFEST.json"
    old = load_manifest(opencode_dir)
    new = {"generator_version": GENERATOR_VERSION, **manifest}
    if {k: v for k, v in old.items() if k != "generated_on"} == new:
        return False
    out = {"generated_on": date.today().isoformat(), **new}
    path.write_text(json.dumps(out, indent=2) + "\n")
    return True


def main(claude_dir: Path = CLAUDE_DIR, opencode_dir: Path = OPENCODE_DIR) -> None:
    opencode_dir.mkdir(exist_ok=True)
    old_manifest = load_manifest(opencode_dir)
    manifest: dict = {"settings": {}, "agents": {}, "commands": {}, "hooks": {}}
    settings_path = claude_dir / "settings.json"
    if settings_path.exists():
        manifest["settings"][".claude/settings.json"] = sha256(settings_path)

    wrote_cfg = write_opencode_json(build_permission(claude_dir), opencode_dir)
    agents_written, agents_skipped = sync_agents(manifest, old_manifest, claude_dir, opencode_dir)
    cmds_written, cmds_skipped = sync_commands(manifest, old_manifest, claude_dir, opencode_dir)
    wrote_plugin = write_plugin(manifest, claude_dir, opencode_dir)
    removed = remove_orphans(claude_dir, opencode_dir)
    # The manifest is rebuilt from existing sources every run, so a deleted source's
    # entry drops out here — write_manifest sees the diff and persists the removal.
    wrote_manifest = write_manifest(manifest, opencode_dir)

    written = agents_written + cmds_written + sum([wrote_cfg, wrote_plugin, wrote_manifest])
    skipped = agents_skipped + cmds_skipped + sum([not wrote_cfg, not wrote_plugin])
    print(
        f"[opencode-sync] wrote {written} files "
        f"({agents_written} agents, {cmds_written} commands, "
        f"opencode.json={'rewritten' if wrote_cfg else 'unchanged'}, "
        f"valor-bridge.ts={'rewritten' if wrote_plugin else 'unchanged'}, "
        f"manifest={'advanced' if wrote_manifest else 'unchanged'}); "
        f"skipped {skipped} unchanged; removed {removed} orphans"
    )


if __name__ == "__main__":
    main()
