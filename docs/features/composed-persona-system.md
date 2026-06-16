# Composed Persona System

> **Granite PTY path retired (issue #1692):** The `compose_system_prompt`
> composition model described here is retired for the granite PTY container path.
> All granite sessions (Eng, Teammate) now receive persona entirely via prime
> commands (`.claude/commands/granite/prime-*-role.md`) — no `--append-system-prompt`
> flag is set at spawn. The `compose_system_prompt` function and the
> `get_response_via_harness` non-granite path are preserved for backward compat
> but are no longer the production persona delivery mechanism.

Single composer (`compose_system_prompt`) that assembles the agent's system
prompt from three orthogonal axes — **persona**, **access level**, and
(reserved) **channel** — replacing the hand-coded picker ladders that used to
live in two parallel sites. Applies to non-granite (direct `claude -p`) sessions.

## Problem this solves

Before this feature, system-prompt assembly was scattered across:

- Two prompt-builder functions with hard-baked behavior — `load_system_prompt`
  (engineer + WORKER_RULES + principal + criteria) and
  `load_eng_system_prompt` (engineer + WORKER rails + work-vault `CLAUDE.md`).
- Two parallel pickers that branched on `SessionType` plus an inline
  `transport == "email"` override:
  `agent/sdk_client.py` (the `get_response_via_harness` path) and
  `agent/session_executor.py` (the harness-route persona resolution). The two
  had drifted independently.
- Telegram-specific format rules embedded in the working-agent persona
  segments (`tools.md`, the engineer overlay) — channel concerns leaking
  into the working agent where they don't belong.
- Drafter system prompt assembled separately from the working-agent prompt
  (`bridge/message_drafter.py:DRAFTER_SYSTEM_PROMPT`), with no shared
  composer.

Adding any new (persona × access × channel) combination meant another branch
in two files. The picker drift between the two sites was the most expensive
failure mode — a fix to one site silently bypassed the other.

## Design

### Three orthogonal axes

| Axis | Type | Defined in | Purpose |
|------|------|------------|---------|
| Persona | `PersonaType` enum (3 members) | [`config/enums.py`](../../config/enums.py) | Voice and identity — `engineer`, `teammate`, `customer-service`. |
| Access level | `AccessLevel` enum (3 members) | [`config/enums.py`](../../config/enums.py) | Prompt rails — `WORKER` (full + WORKER_RULES + principal + criteria + work-vault CLAUDE.md), `TEAMMATE` (conversational, no rails), `CUSTOMER_SERVICE` (action-oriented, no code writes, no rails). |
| Channel | `str \| None` | reserved | Output medium — currently no working-agent cell consumes this; channel-specific concerns live in the message drafter. |

`AccessLevel` is **prompt-only**. Runtime tool restrictions (Write/Edit
blocking, teammate write-path scoping) are enforced separately by
[`agent/hooks/pre_tool_use.py`](../../agent/hooks/pre_tool_use.py) keyed on
`SessionType`. The two stay decoupled by design.

### Composer signature

```python
from agent.sdk_client import compose_system_prompt
from config.enums import PersonaType, AccessLevel

prompt = compose_system_prompt(
    persona=PersonaType.ENGINEER,
    access_level=AccessLevel.WORKER,
    channel=None,                  # reserved; no current cell uses it
    project=None,                  # reserved for future project-level overlays
    working_directory=None,        # under WORKER, appends work-vault CLAUDE.md when set
)
```

### Composition order (strict additive layering)

1. `WORKER_RULES` — only when `access_level == WORKER`.
2. Persona prompt — identity + segments per
   [`config/personas/segments/manifest.json`](../../config/personas/segments/manifest.json) +
   persona overlay (preserves the loader-warning pattern in
   `load_persona_prompt` for CRITIQUE / workflow-announcement / dev-session
   drift on the engineer overlay).
3. Principal context — only when `access_level == WORKER`.
4. Completion criteria — only when `access_level == WORKER`.
5. Work-vault `CLAUDE.md` — only when `access_level == WORKER`,
   `working_directory` is provided, and the file exists at
   `Path(working_directory) / "CLAUDE.md"`.

No new segments were added to `manifest.json`. Voice consolidation (banned
phrases, "no empty promises", tone) is **deferred** to a follow-up plan to
keep the byte-stability mitigation clean (see "Byte stability" below).

### Single-source-of-truth resolver

```python
from agent.sdk_client import _resolve_compose_args

persona, access_level, channel = _resolve_compose_args(
    session_type=SessionType.ENG,
    project=project_dict,
    transport="email",
    chat_title=None,
    is_dm=False,
    project_mode="eng",           # forces engineer rails even for non-ENG sessions
)
```

Both call sites — `agent/sdk_client.py` and `agent/session_executor.py` — call
`_resolve_compose_args` instead of duplicating the branch ladder. The
`project.email.persona` per-project override lives **only** here.

Mapping rules (evaluated in this order):

- `SessionType.TEAMMATE` + `transport=="email"` + `project.email.persona` set
  (and not `"teammate"`) → `(<email persona>, <access level for that persona>, "email")`.
- `SessionType.ENG` → `(ENGINEER, WORKER, None)`.
- `project_mode == "eng"` → `(ENGINEER, WORKER, None)` even when session_type
  is not `ENG`.
- `SessionType.TEAMMATE` (default) → `(TEAMMATE, TEAMMATE, None)`.
- Unknown session type → resolved via `_resolve_persona(project, chat_title, is_dm)` →
  `(<persona>, <access level for that persona>, None)`.

Persona → access-level mapping (today's 1:1, in `_access_level_for_persona`;
the orthogonality is preserved in the type system so future per-project rails
don't need new SessionType members):

| Persona | Default access level |
|---------|---------------------|
| `TEAMMATE` | `TEAMMATE` |
| `CUSTOMER_SERVICE` | `CUSTOMER_SERVICE` |
| `ENGINEER` (and any other) | `WORKER` |

### Backward-compatible wrappers

`load_system_prompt()` and `load_eng_system_prompt(working_directory)` are
preserved as thin wrappers over `compose_system_prompt`. `load_system_prompt()`
delegates to `compose_system_prompt(PersonaType.ENGINEER, AccessLevel.WORKER)`;
`load_eng_system_prompt(working_directory)` adds the work-vault `CLAUDE.md`
appendix under WORKER access. All existing call sites continue to work without
change. New code is encouraged to call the composer directly.

## Drafter (medium-aware split)

> **Updated (drafter_passthrough_validation):** The Haiku LLM rewrite path was removed from the drafter. `_draft_with_haiku`, `_draft_with_openrouter`, `_compose_drafter_prompt`, `BASE_DRAFTER_PROMPT`, and `MEDIUM_RULES` are all deleted. The `medium` parameter on `draft_message` is still active — it now routes to deterministic validators (`_validate_for_medium`) rather than to an LLM system prompt composer.

The `medium` parameter on `draft_message` discriminates which wire-format validator runs:

- `"telegram"` → `validate_telegram(text)` — checks for Markdown table syntax (`| --- |`) which does not render in Telegram
- `"email"` → `validate_email(text)` — checks for any Markdown on the wire (plain prose only)

The naming convention is **`medium`** (not `channel`) on the drafter's public
surface because that's the existing parameter name and it ties through to
`_validate_for_medium(text, medium)` in `bridge/message_drafter.py`.

## Byte stability (issue #1227)

The Anthropic prompt cache is byte-keyed: a one-character drift in the
~74K-char engineer prompt prefix evicts the cached entry and pushes eng-session
TTFT from < 90s (warm) to 15-20min (cold). This refactor preserves the
byte-stable prefix invariant for the production WORKER cells.

### Per-machine fixtures

The composed prompt embeds machine-specific values:

- `working_directory` (WORKER cell) — embedded inside the work-vault `CLAUDE.md`
  content; varies per machine.
- `{{identity.*}}` substitutions — `~/Desktop/Valor/identity.json` shallow-
  merges per-machine overrides via `load_identity()`.
- Work-vault `CLAUDE.md` content — varies per machine.

A single shared fixture file would fail on bridge machines. Instead, each
machine commits its own fixture under
`tests/fixtures/{hostname-slug}/{dev,eng}_system_prompt_baseline.txt` (where
`hostname-slug` is `socket.gethostname()` with each `.` rewritten to `-`). The
`dev_*` baseline captures `load_system_prompt()` (no work-vault appendix); the
`eng_*` baseline captures `load_eng_system_prompt(work_dir)`.

### Capturing the baseline

```bash
python scripts/capture_persona_baseline.py
# Captures tests/fixtures/{hostname}/{dev,eng}_system_prompt_baseline.txt
# from the current load_system_prompt() / load_eng_system_prompt(work_dir) output.
```

`--work-dir PATH` overrides the default work-vault path
(`~/work-vault/AI Valor Engels System`).

### Test behavior

`tests/unit/test_compose_system_prompt.py` reads the local-machine fixture
only. On machines without a baseline, the test **SKIPs** (does not FAIL) with
a message pointing at the capture script. Cache stability for this plan's
purposes is about consecutive sessions on a single machine — a freshly-
introduced machine has no prior cache to break, so SKIP is the correct
behavior.

### Rejected strategies

- **Token normalization**: replacing machine-specific paths with sentinels
  before snapshot. Rejected because the same normalization would have to be
  applied at runtime to validate the cache prefix, defeating the byte-
  identical invariant.
- **Structural equality**: comparing segment-list ordering rather than
  bytes. Rejected because the prompt cache hits on byte equality, not
  structural equality.

## What did **not** change

- `manifest.json` — no new segments added (voice consolidation is deferred).
- `WORKER_RULES` constant — kept inline next to the composer.
- `_load_persona_overlay_with_log` — kept as a logging adapter for the
  TEAMMATE / CUSTOMER_SERVICE cells; it emits the canonical
  `Persona overlay loaded:` log line that test-cuttlefish-* skills grep on.
- The `--exclude-dynamic-system-prompt-sections` argv flag — passed
  unchanged through `get_response_via_harness`.
- Hook layer at `agent/hooks/pre_tool_use.py` — `AccessLevel` is prompt-only;
  runtime tool restrictions stay keyed on `SessionType`.

## Adding a new access level

1. Add a member to `AccessLevel` in
   [`config/enums.py`](../../config/enums.py).
2. Add a branch to `compose_system_prompt` for the new level's
   composition order (rails, appendices).
3. Update `_resolve_compose_args` and `_access_level_for_persona` to map
   the relevant inputs to the new level.
4. Add a test cell to
   `tests/unit/test_compose_system_prompt.py::test_compose_cell_returns_nonempty_string`
   and a startup-lint check if the new level has invariants worth asserting.

## Adding a new persona

1. Add a member to `PersonaType` in
   [`config/enums.py`](../../config/enums.py).
2. Place the overlay file at
   `~/Desktop/Valor/personas/{persona}.md` (private) or
   `config/personas/{persona}.md` (repo fallback).
3. If the persona maps to an access level other than `WORKER`, update
   `_access_level_for_persona`.

## Open follow-ups

- **Voice consolidation** — banned phrases, no-empty-promises, tone, and
  good/bad examples are still scattered across per-persona overlays and
  `DRAFTER_SYSTEM_PROMPT`. Consolidating them into a shared `voice.md`
  segment would change the assembled prompt bytes for the existing
  cells, breaking byte-stability. A follow-up plan will move voice content
  into a single source after this composer ships and stabilizes; that plan
  can negotiate the one-time cache bust on its own terms.
- **Channel parameter** — `compose_system_prompt(..., channel=None)` is
  reserved for forward-compat. No working-agent cell consumes it today; it
  stays in the signature with a TODO note. Drop or formalize once a concrete
  need surfaces.
- **Source label propagation** — the `_persona_source` label
  (`session_type=eng`, `project.email.persona`, `email-default`,
  `session_type=teammate`) used in
  `agent/session_executor.py` log lines is still derived locally rather than
  returned from `_resolve_compose_args`. If a third call site emerges, fold
  the source label into the resolver's return value.

## Where the code lives

| Symbol | Location |
|--------|----------|
| `AccessLevel` enum | [`config/enums.py`](../../config/enums.py) |
| `compose_system_prompt` | [`agent/sdk_client.py`](../../agent/sdk_client.py) (near `load_eng_system_prompt`) |
| `_resolve_compose_args` | [`agent/sdk_client.py`](../../agent/sdk_client.py) |
| `_access_level_for_persona` | [`agent/sdk_client.py`](../../agent/sdk_client.py) |
| `load_system_prompt` (wrapper) | [`agent/sdk_client.py`](../../agent/sdk_client.py) |
| `load_eng_system_prompt` (wrapper) | [`agent/sdk_client.py`](../../agent/sdk_client.py) |
| Picker call sites | `agent/sdk_client.py` and `agent/session_executor.py` |
| Drafter medium split | [`bridge/message_drafter.py`](../../bridge/message_drafter.py) (`BASE_DRAFTER_PROMPT`, `MEDIUM_RULES`, `_compose_drafter_prompt`) |
| Baseline capture script | [`scripts/capture_persona_baseline.py`](../../scripts/capture_persona_baseline.py) |
| Per-machine fixtures | `tests/fixtures/{hostname-slug}/{dev,eng}_system_prompt_baseline.txt` |
| Tests | `tests/unit/test_compose_system_prompt.py`, `tests/unit/test_resolve_compose_args.py` |

## See also

- Issue [#1268](https://github.com/tomcounsell/ai/issues/1268) — the
  composer ask.
- Issue [#1227](https://github.com/tomcounsell/ai/issues/1227) — the
  prompt-cache stability invariant this composer preserves.
- [`docs/features/eng-session-architecture.md`](eng-session-architecture.md)
  — the Eng session architecture that drives the access-level mapping.
- [`docs/plans/composed-persona-system.md`](../plans/composed-persona-system.md)
  — the plan, including the seven resolved architectural questions and the
  per-machine fixture mitigation rationale.
