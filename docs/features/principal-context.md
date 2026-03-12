# Principal Context

Injects the supervisor's strategic operating context (`config/PRINCIPAL.md`) into agent decision-making workflows, enabling autonomous prioritization, scoping, and escalation grounded in documented priorities rather than ad-hoc inference.

## How It Works

`config/PRINCIPAL.md` is a human-authored markdown file structured loosely on the TELOS framework: Mission, Goals, Beliefs, Strategies, Challenges, Narratives, Projects, and Learned lessons. The system reads this file at decision time and injects relevant sections into agent prompts.

### Loading

`load_principal_context()` in `agent/sdk_client.py` reads `config/PRINCIPAL.md` and supports two modes:

- **Condensed** (`condensed=True`, default): Extracts only Mission, Goals, and Projects sections. This keeps the worker system prompt lean (~300-500 tokens of principal context) while providing the strategic essentials.
- **Full** (`condensed=False`): Returns the entire file content. Used by the Observer Agent, which makes triage decisions that benefit from the complete strategic picture.

If the file is missing or empty, the function returns an empty string and logs a warning. No crash, no degraded behavior beyond the absence of strategic context.

### Injection Points

| Component | Mode | Purpose |
|-----------|------|---------|
| Worker system prompt (`load_system_prompt()`) | Condensed | Gives workers mission/goals/project priorities for scoping decisions |
| Observer system prompt (`_build_observer_system_prompt()`) | Full | Gives the Observer complete strategic context for STEER/DELIVER routing |
| do-plan skill (`.claude/skills/do-plan/SKILL.md`) | Reference | Points planners to PRINCIPAL.md when setting appetite and scope |
| Reflections (`scripts/reflections.py`) | Staleness check | Flags when PRINCIPAL.md hasn't been updated in 90+ days |

### Staleness Detection

The reflections maintenance system (step 16) checks the file modification time of `config/PRINCIPAL.md`. If it exceeds 90 days, a finding is recorded recommending the supervisor review and update their strategic context. This prevents the agent from making decisions based on outdated priorities.

## Key Files

| File | Role |
|------|------|
| `config/PRINCIPAL.md` | The principal context file (human-authored, read-only by system) |
| `agent/sdk_client.py` | `load_principal_context()` and `load_system_prompt()` |
| `bridge/observer.py` | `_build_observer_system_prompt()` with full principal context |
| `.claude/skills/do-plan/SKILL.md` | References PRINCIPAL.md for appetite/scoping |
| `scripts/reflections.py` | `step_principal_staleness()` for 90-day freshness check |
| `tests/unit/test_principal_context.py` | Unit tests for all injection points |

## Keeping PRINCIPAL.md Current

- Edit `config/PRINCIPAL.md` directly when priorities, goals, or project portfolio changes
- The reflections system will flag staleness after 90 days of no modification
- Resolve any `<!-- TOM: -->` placeholder comments with actual answers
- The file is committed to git and propagated via the normal update process

## Design Decisions

- **Condensed vs full**: Workers get a slim summary to preserve context window budget. The Observer gets everything because its sessions are short-lived and triage-focused.
- **Static file, not auto-updated**: This is explicitly a human-authored document. The system reads it but never writes to it. No learning loops, no auto-classification.
- **Section extraction via regex**: Simple regex extraction of markdown sections. No external parser needed, fails gracefully to a 500-char truncation if section headers change.
- **Graceful degradation**: Every injection point handles missing/empty PRINCIPAL.md without error. The system works exactly as before if the file doesn't exist.
