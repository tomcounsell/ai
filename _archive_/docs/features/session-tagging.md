# Session Tagging

Automatic and manual tag management for AgentSession instances. Tags categorize sessions by activity type (e.g., "pr-created", "reflections", "sdlc") for querying and analytics.

## How It Works

Tags are stored in the `AgentSession.tags` ListField (Redis via Popoto). Auto-tagging runs at session completion time inside `finalize_session()` in `models/session_lifecycle.py`.

### Auto-Tag Rules

| Signal | Tag Applied |
|--------|-------------|
| `classification_type == "bug"` | `bug` |
| `classification_type == "feature"` | `feature` |
| `classification_type == "chore"` | `chore` |
| Branch name starts with `session/` | `sdlc` |
| Transcript contains `gh pr create` | `pr-created` |
| Transcript contains `pytest` or `Skill(do-test` | `tested` |
| Sender or session_id contains "reflections" | `reflections` |
| `slug` is set | `planned-work` |
| `turn_count >= 20` | `long-session` |

Auto-tagging reads only the last 50 lines of the transcript for pattern matching. It never removes existing tags — only adds new ones. Failures are caught and logged without breaking session completion.

## API

All functions are in `tools/session_tags.py`:

```python
from tools.session_tags import add_tags, remove_tags, get_tags, sessions_by_tag, auto_tag_session

# CRUD
add_tags("session-123", ["hotfix", "urgent"])
remove_tags("session-123", ["urgent"])
tags = get_tags("session-123")  # ["hotfix"]

# Query
bug_sessions = sessions_by_tag("bug")
bug_sessions_proj = sessions_by_tag("bug", project_key="valor")

# Auto-tag (called automatically at session completion)
auto_tag_session("session-123")
```

## Integration Points

- **`models/session_lifecycle.py`**: `auto_tag_session()` is called in `finalize_session()` before the AgentSession status update
- **`models/agent_session.py`**: Tags stored in `AgentSession.tags` ListField
- **`tools/session_tags.py`**: Public API module

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| Rule-based, not LLM | Simpler, faster, deterministic — sufficient for v1 |
| Last 50 lines only | Avoids reading large transcripts; most signals appear near session end |
| Python-side filtering for `sessions_by_tag` | Popoto ListField may not support native contains queries; dataset is small |
| Auto-tag in `finalize_session()` | Single entrypoint where every terminal session transition runs |
| try/except around auto-tagging | Tagging is non-critical — must never break session completion |
| Open tag vocabulary | Any string is valid; well-known tags are auto-applied but custom tags are welcome |

## Components

| Component | Path | Purpose |
|-----------|------|---------|
| Session tags module | `tools/session_tags.py` | CRUD and auto-tagging API |
| Lifecycle integration | `models/session_lifecycle.py` | Calls auto_tag_session at completion via `finalize_session()` |
| AgentSession model | `models/agent_session.py` | Tags stored in ListField |
| Unit tests | `tests/unit/test_session_tags.py` | 33 tests covering all rules and edge cases |

## Related

- [Session Transcripts](session-transcripts.md) — Transcript system that triggers auto-tagging
- [Classification](classification.md) — Message classification that feeds classification_type tags
- [Session Isolation](session-isolation.md) — Session scoping system
