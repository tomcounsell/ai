# Phase 3: Project Configuration — projects.json and Personas

Load this when configuring `~/Desktop/Valor/projects.json` (Step 6).

Project configuration lives in `~/Desktop/Valor/projects.json` (iCloud-synced, private). This directory is shared across machines via iCloud.

Check if `~/Desktop/Valor/projects.json` exists. If not, create from the repo example:

```bash
mkdir -p ~/Desktop/Valor
cp config/projects.example.json ~/Desktop/Valor/projects.json
```

Edit `~/Desktop/Valor/projects.json` for this machine's projects.

## Critical rules when editing projects.json

1. **Every project MUST have `working_directory`** -- absolute path to the repo on this machine
2. **Every project MUST have `machine`** -- the exact `ComputerName` of the single machine that owns it (`scutil --get ComputerName`). This is the source of truth for ownership; whitelists, groups, and email patterns all inherit from it. Two projects on different machines must never share a Telegram group, email contact, or DM whitelist contact id — see [Single-Machine Ownership](../../../../docs/features/single-machine-ownership.md).
3. **Always include the full `defaults` section** -- copy it from the example if missing
4. **DO NOT set `respond_to_all: false`** -- the default is `true`, which is correct. Omit the field entirely from project-level telegram config.
5. **Keep project telegram config minimal** -- usually just `"groups": {"Eng: ProjectName": {"persona": "engineer"}}` is sufficient
6. **Verify paths exist on disk** -- run `ls` on each `working_directory` to confirm

**No per-contact ownership edits.** When adding this machine, you do not edit `dms.whitelist`, individual `telegram.groups` entries, or `email.contacts/domains` to "exclude" other machines. Just set each project's `machine` field once. The validator (`bridge/config_validation.py`) and the update gate (`scripts/update/run.py` Step 4.6) will enforce that no contact is owned by two machines.

Example minimal project entry:

```json
{
  "projects": {
    "myproject": {
      "name": "My Project",
      "working_directory": "~/src/myproject",
      "telegram": {
        "groups": {
          "Eng: My Project": {"persona": "engineer"}
        }
      },
      "github": {
        "org": "orgname",
        "repo": "reponame"
      },
      "context": {
        "tech_stack": ["Python"],
        "description": "What the agent should focus on"
      }
    }
  },
  "defaults": {
    "working_directory": "~/src/ai",
    "telegram": {
      "respond_to_all": true,
      "respond_to_mentions": true,
      "respond_to_dms": true,
      "mention_triggers": ["@valor", "valor", "hey valor"]
    },
    "response": {
      "typing_indicator": true,
      "max_response_length": 4000,
      "timeout_seconds": 300
    }
  }
}
```

## Persona overlays

Persona overlay files live in `~/Desktop/Valor/personas/`. The loader (`agent.sdk_client.load_persona_prompt`) prefers the private overlay when present and falls back to the in-repo template (`config/personas/<persona>.md`) otherwise. Seeding the private overlays from the in-repo defaults at setup time gives the agent identical behavior on every fresh machine without waiting for iCloud propagation from another box.

The engineer and customer-service personas have in-repo templates that are version-controlled and PR-reviewable:
- `config/personas/engineer.md` — Engineer SDLC-owner playbook (CRITIQUE/REVIEW gates, Mode 3 parallel orchestrator, `merge_authorized` bypass)
- `config/personas/customer-service.md` — Customer-service overlay for `customer-service`-persona sessions

Seed them into the vault if not already present (do NOT overwrite — existing overlays may carry per-machine customizations):

```bash
mkdir -p ~/Desktop/Valor/personas

for persona in engineer customer-service; do
  src="config/personas/${persona}.md"
  dst="$HOME/Desktop/Valor/personas/${persona}.md"
  if [ ! -f "$dst" ]; then
    cp "$src" "$dst"
    echo "Seeded $dst from $src"
  else
    echo "$dst already exists — leaving in place (run \`diff\` to compare with $src)"
  fi
done
```

The `teammate` persona has no in-repo template — there is no `config/personas/teammate.md`. A teammate overlay is purely operator-authored under `~/Desktop/Valor/personas/teammate.md`; if absent, the loader has no fallback for that persona, so a teammate-using machine must author its own overlay.

If the machine is already running and you want to inspect drift between the in-repo template and the private overlay:

```bash
diff config/personas/engineer.md ~/Desktop/Valor/personas/engineer.md
diff config/personas/customer-service.md ~/Desktop/Valor/personas/customer-service.md
```

The persona loader emits a WARNING log line if a known load-bearing substring is missing from the private engineer overlay (e.g., `CRITIQUE` for the pipeline gate, `Mode 3` for the parallel orchestrator, `merge_authorized` for the stale-baseline bypass). The `/update` script also runs an engineer-overlay drift check (`scripts/update/persona_drift.py`, Step 4.10). Watch `logs/bridge.log` after the first session for these warnings — they signal that the private overlay has rolled back and should be re-synced.

## Cross-machine reuse

If the project is already defined on another machine's `~/Desktop/Valor/projects.json`, copy its entry rather than writing from scratch (iCloud syncs this file across machines).

After editing, verify all working directories exist:

```bash
# For each project's working_directory, confirm it exists
ls ~/src/<project_dir>
```
