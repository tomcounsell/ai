# Phase 3: Project Configuration — projects.json and Personas

Load this when configuring `~/Desktop/Valor/projects.json` (Step 6).

`~/Desktop/Valor/projects.json` is iCloud-synced and shared by every machine; if the project is already defined there, reuse its entry.

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

The loader (`agent.sdk_client.load_persona_prompt`) reads `~/Desktop/Valor/personas/<persona>.md` when present and falls back to the in-repo `config/personas/<persona>.md`. Seed the vault from the in-repo templates, never overwriting an existing overlay (it may carry per-machine customizations):

```bash
mkdir -p ~/Desktop/Valor/personas
for persona in engineer customer-service teammate; do
  dst="$HOME/Desktop/Valor/personas/${persona}.md"
  [ -f "$dst" ] && echo "$dst exists, left in place" || cp "config/personas/${persona}.md" "$dst"
done
```

The loader logs a WARNING when a load-bearing substring (`CRITIQUE`, `Mode 3`, `merge_authorized`) is missing from the private engineer overlay, and `/update` runs an engineer-overlay drift check (`scripts/update/persona_drift.py`). Such a warning in `logs/bridge.log` after the first session means the overlay rolled back and should be re-synced (`diff config/personas/engineer.md ~/Desktop/Valor/personas/engineer.md`).

## Verify

`ls` each project's `working_directory` to confirm it exists on disk.
