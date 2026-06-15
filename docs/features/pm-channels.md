# Eng-Mode Channels

> **Naming note:** This doc was originally titled "PM (Project Manager) Channels." The PM and Dev session roles have since merged into a single `eng` session (`SessionType.ENG`, engineer persona, `AccessLevel.WORKER`) that both orchestrates and executes SDLC work. The project `"mode"` field that this doc describes was renamed `"pm"` → `"eng"` in the same merge. The filename is retained for inbound-link stability.

Eng mode lets a Telegram group route to a work-vault project folder instead of a source-code repository. The eng session still runs the engineer persona with full WORKER rails; eng mode only changes the working directory and skips SDLC classification so the session takes its instructions from the work-vault `CLAUDE.md` rather than the in-repo SDLC pipeline.

## How It Works

### Project Mode Field

Each project in `~/Desktop/Valor/projects.json` supports an optional `"mode"` field:

- *unset* (default): Standard routing with SDLC classification, WORKER_RULES, and branch safety rails. The bridge classifies each message (`sdlc` vs `question`) and routes accordingly.
- `"eng"`: Skips SDLC classification (forces `"question"`) and runs in the project's working directory using its work-vault `CLAUDE.md` for instructions.

Unknown mode values are logged and treated as unset (`agent/sdk_client.py` around line 3159).

### Routing Flow

**Default mode (no `mode` field):**
1. Message arrives in a Telegram group
2. `find_project_for_chat()` matches the group to a project config
3. The bridge's `classify_work_request()` determines `sdlc` vs `question` routing
4. SDLC requests go to the ai/ repo orchestrator; questions go to the project working directory
5. The session spawns with WORKER_RULES + persona segments system prompt

**Eng mode (`"mode": "eng"`):**
1. Message arrives in a Telegram group
2. `find_project_for_chat()` matches the group to the eng-mode project config
3. SDLC classification is **skipped** — classification is forced to `ClassificationType.QUESTION` (`agent/sdk_client.py:3163`)
4. The session spawns with `cwd` set to the project working directory
5. The system prompt is composed via `load_eng_system_prompt(working_dir)` — engineer persona + WORKER_RULES + the work-vault `CLAUDE.md`

### System Prompt Composition

Eng sessions compose their system prompt via `load_eng_system_prompt(working_directory)`, which delegates to `compose_system_prompt(PersonaType.ENGINEER, AccessLevel.WORKER, working_directory=...)`. The composed string is:

```
[WORKER_RULES]
---
[Persona prompt — base + engineer overlay]
---
[Principal context]
---
[Completion criteria]
---
[Work-vault CLAUDE.md — project-specific instructions, if present]
```

Specifically `compose_system_prompt` under the `WORKER` branch (`agent/sdk_client.py:1090–1112`):
- Loads the engineer persona (`load_persona_prompt("engineer")` — base segments + engineer overlay)
- Puts WORKER_RULES first (safety rails take precedence over persona)
- Appends the project-specific `CLAUDE.md` from `working_directory` if it exists; logs and proceeds with the worker prompt only if absent
- Re-raises `FileNotFoundError` if the persona overlay or required identity/segment files are missing (no silent fallback)

The composed prompt is wired into `claude -p` via `--append-system-prompt` (issue #1148). `agent/session_executor.py:1685` calls `load_eng_system_prompt(working_dir)` on the WORKER-access branch and passes the result through `get_response_via_harness(system_prompt=...)`. The harness appends it to Claude Code's default system prompt so the engineer persona is additive guidance rather than a full replacement — see `docs/features/harness-abstraction.md#engineer-persona-injection-append-system-prompt-issue-1148` for the argv-level details.

Persona/access resolution is centralized in `_resolve_compose_args()` (`agent/sdk_client.py:1118`): `SessionType.ENG → (PersonaType.ENGINEER, AccessLevel.WORKER, None)`. A project with `"mode": "eng"` forces the same engineer rails even for sessions that aren't typed `ENG` (`project_mode == "eng"` branch, `agent/sdk_client.py:1166`). There is no separate "PM mode" persona or `PM_READONLY` access level — both were removed when the roles merged.

### What Eng Mode Skips

- SDLC classification (`classify_work_request()` is bypassed; classification forced to `"question"`)
- The bridge-level `sdlc` routing branch (no `/sdlc` orchestration is auto-injected by the bridge)
- Target-repo context injection (the session works in the work-vault directory)

WORKER_RULES (branch safety rails) are **not** skipped — eng sessions keep full WORKER access.

## Configuration

### projects.json Entry

```json
{
  "cuttlefish": {
    "name": "Cuttlefish",
    "description": "Project work for Cuttlefish",
    "mode": "eng",
    "working_directory": "~/work-vault/Cuttlefish/",
    "telegram": {
      "groups": ["Cuttlefish"],
      "respond_to_unaddressed": true,
      "respond_to_dms": false
    },
    "context": {
      "description": "Project work for Cuttlefish."
    }
  }
}
```

### Work-Vault CLAUDE.md

Each work-vault project folder can contain a `CLAUDE.md` with project-specific instructions. This file tells the session how to operate for that specific project.

Template structure:
- Project overview and goals
- Read-only source repo reference for status checks
- Project-specific do's and don'ts
- Linked resources (issue trackers, documents, etc.)

## Key Files

- `agent/sdk_client.py`: eng-mode detection (`project.get("mode") == "eng"`), classification bypass, `load_eng_system_prompt()`, `compose_system_prompt()`, `_resolve_compose_args()`
- `agent/session_executor.py:1685`: WORKER-access branch calls `load_eng_system_prompt(working_dir)`
- `~/Desktop/Valor/projects.json`: project entries with `"mode": "eng"`
- `tests/unit/test_pm_channels.py`: unit tests for eng-mode behavior

## Cold-Start TTFT Mitigation (issue #1227)

Eng sessions historically experienced a **15–20 minute time-to-first-token (TTFT)** on fresh spawns. The root cause: the full persona + project CLAUDE.md (~74,769 chars / ~18,750 tokens) is passed as `--append-system-prompt` and Anthropic's server-side prompt cache cannot be reused when the prefix contains per-machine dynamic sections (cwd, env info, memory paths, git status).

**Direction A fix (shipped):** The worker now injects `--exclude-dynamic-system-prompt-sections` into the harness argv for every session that carries a system prompt. This flag moves the dynamic per-machine sections into the first user message rather than the system prompt. The stable system-prompt prefix is then an exact match across consecutive eng sessions sharing the same `working_directory`, enabling Anthropic's server-side prompt cache to serve the prefix from cache (TTL ~5 minutes) rather than re-processing 18K tokens cold.

**Observable wins:**
- First eng session after a > 5-minute gap: still cold (~15–20 min) — cache is not yet populated.
- Second eng session within 5 minutes: TTFT drops to < 90 seconds (cache hit on the stable prefix).
- Repeat sessions in a busy SDLC pipeline: consistently sub-90s TTFT.

**TTFT instrumentation:** Every first-turn harness invocation writes a JSON line to `logs/cold_start_metrics.jsonl`:
```json
{"timestamp":"...", "session_id":"...", "session_type":"eng", "prompt_chars":74769, "model":"opus", "ttft_seconds":12.3, "cache_read_input_tokens":0}
```
Cache hits are confirmed by `cache_read_input_tokens > 0` in the log.

To check median TTFT for recent eng sessions:
```bash
python -c "
import json, statistics
ts = [json.loads(l)['ttft_seconds']
      for l in open('logs/cold_start_metrics.jsonl')
      if json.loads(l).get('session_type') == 'eng'][-10:]
print(f'median: {statistics.median(ts):.1f}s') if ts else print('no data yet')
"
```

## Design Decisions

1. **Engineer persona everywhere**: After the PM/Dev merge there is a single engineer persona. Eng-mode channels use the same persona and WORKER rails as repo-backed work; only the working directory and SDLC-classification bypass differ.
2. **Classification bypass, not a new classification**: Eng mode forces `"question"` rather than adding a third classification type, keeping the routing logic simple.
3. **No new tools**: Eng-mode behavior is driven entirely by routing and instructions, not new MCP tools or servers.
4. **Work-vault CLAUDE.md is optional**: If a project folder lacks CLAUDE.md, the session still works with just the persona segments and WORKER_RULES.
5. **Cold-start via cache stabilization, not prompt shrinkage**: CLAUDE.md is the developer source of truth and should not be mutilated for prompt-size optimization. The fix stabilises the prompt prefix so Anthropic's server-side cache covers it, rather than shrinking or splitting the prompt (Direction B would split; it's documented as available if Direction A alone is insufficient).
