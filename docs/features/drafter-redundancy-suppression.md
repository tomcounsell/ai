# Drafter Redundancy Suppression

**Status:** Shipped (issue #1205)

Deterministic bigram-Jaccard pre-send guard for SDLC sessions that suppresses
near-verbatim repeated status messages from PM sessions. When a draft is
substantially the same as a recently-sent draft and carries no new artifact,
the text send is suppressed and a 👀 reaction is queued on the human's anchor
message instead.

## What it does

When a PM session in `waiting_for_children` status drafts and sends the same
status paragraph multiple times (e.g., "I'll confirm merge-readiness next
turn"), the redundancy filter intercepts duplicate sends before they reach the
Telegram outbox. The human sees one text message plus 👀 eye-emoji reactions
for subsequent near-duplicates — the signal "still working" without the noise.

## Where it lives

| File | Role |
|------|------|
| `bridge/redundancy_filter.py` | Pure functions: `should_suppress()`, `SuppressionVerdict` |
| `agent/output_handler.py` | Call site: wired into `TelegramRelayOutputHandler.send` |
| `models/agent_session.py` | `recent_sent_drafts` field + `record_recent_sent_draft()` helper |
| `agent/agent_session_queue.py` | `recent_sent_drafts` in `_AGENT_SESSION_FIELDS` allow-list |

## Verdicts

`should_suppress()` returns one of two verdicts:

| Verdict | Meaning |
|---------|---------|
| `"send"` | Deliver the draft normally. |
| `"suppress"` | Skip the text outbox write; queue a 👀 reaction instead. |

There is intentionally no `"trim"` verdict — rewriting drafts is out of scope
for a deterministic filter. Trimming is RTR's job for non-SDLC sessions.

## Termination conditions (force `"send"`)

Evaluated in order; the first matching condition returns `"send"` immediately:

1. **Empty draft** — `draft_text` is empty or whitespace-only.
2. **No baseline** — `recent_sent_drafts` is `None` or empty (cannot be redundant).
3. **Has expectations** — `MessageDraft.expectations` is non-empty (drafter detected
   a question for the human; this send is intentional).
4. **Terminal status** — `session.status` is `"completed"`, `"failed"`, or `"blocked"`.
5. **New artifact** — The new draft contains an artifact (PR URL, commit hash, error
   string, etc.) not present in any within-window prior draft. Artifact sets are
   compared as the union of all values across all keys in the dict returned by
   `extract_artifacts()` (`{commits, urls, files_changed, test_results, errors}`).

## Similarity metric

**Bigram Jaccard:** `J = |bigrams(A) ∩ bigrams(B)| / |bigrams(A) ∪ bigrams(B)|`

- Computed once for the new draft; once per in-window prior draft.
- Default threshold: `J ≥ 0.65` (tunable via `DRAFTER_REDUNDANCY_THRESHOLD`).
- Bigrams include unigrams (single words ≥ 4 chars) via `_extract_bigrams()` from
  `agent/memory_extraction.py` — single import, no local copy.

## Observability

Two `session_events` entries are emitted:

| Event type | When |
|------------|------|
| `drafter.suppressed_redundant` | Suppression fired; reaction queued. |
| `drafter.suppress_fallthrough` | Suppression would have fired but no `reply_to_msg_id` anchor existed; text sent. |

Both entries carry `{type, ts, chat_id, reason, draft_preview}` — compatible with
the RTR event schema. Dashboard tooling that reads `session.session_events` can
count `drafter.suppressed_redundant` events to surface PM sessions that are
looping.

## Bypass conditions

The filter is bypassed entirely (falls through to the existing RTR + outbox path)
when any of the following are true:

- `DRAFTER_REDUNDANCY_SUPPRESSION_ENABLED` is `false` / `0` / `no` / `off`.
- `session` is `None`.
- `session.is_sdlc` is `False` (non-SDLC sessions defer to RTR).
- An unhandled exception occurs anywhere inside the filter — errors always return
  `SuppressionVerdict("send", reason="filter_error")`.

## Failure modes

| Failure | Behaviour |
|---------|-----------|
| `_extract_bigrams` raises | `filter_error` → deliver text. |
| `extract_artifacts` raises | `_draft_artifacts = {}` → may suppress if Jaccard alone qualifies, but no false-positive on artifact-diff. |
| `session.record_recent_sent_draft` save fails | Logged as `WARNING`; the `rpush` already completed, so delivery is not reversed. |
| No `reply_to_msg_id` anchor | `suppress_fallthrough` event; text delivered (mirrors RTR's no-anchor contract). |

## Configuration

All knobs are environment variables with sensible defaults. Set them in
`~/Desktop/Valor/.env`; no code change required.

| Variable | Default | Description |
|----------|---------|-------------|
| `DRAFTER_REDUNDANCY_SUPPRESSION_ENABLED` | `true` | Kill switch. Set to `false` to restore pre-fix behavior. |
| `DRAFTER_REDUNDANCY_THRESHOLD` | `0.65` | Bigram Jaccard threshold. Higher = stricter (fewer suppressions). |
| `DRAFTER_RECENT_DRAFTS_N` | `3` | Number of recent sent drafts retained per session for comparison. |
| `DRAFTER_REDUNDANCY_WINDOW_SECONDS` | `600` | Time window (seconds) for comparison. Older entries are skipped. |

## Relationship to RTR

Read-the-Room (issue #1193, PR #1204) is an opt-in Haiku-based guard for
non-SDLC sessions. The redundancy filter is a deterministic, always-on guard
for SDLC sessions. They compose without conflict:

```
TelegramRelayOutputHandler.send
  │
  ├─ Drafter runs (draft_message)
  │
  ├─ [SDLC sessions] Redundancy filter (bridge/redundancy_filter.py)
  │   → suppress? queue 👀 reaction and return
  │   → send?    fall through ↓
  │
  ├─ [All sessions] RTR (bridge/read_the_room.py) — opt-in
  │   → suppress? queue 👀 reaction and return
  │   → trim?     swap delivery_text and fall through ↓
  │   → send?     fall through ↓
  │
  └─ Telegram outbox rpush → record_recent_sent_draft
```

RTR's SDLC-session bypass (`bridge/read_the_room.py:400`) is structurally
present but currently a no-op (the `sdlc_slug` attribute it reads does not
exist on `AgentSession`). The redundancy filter intentionally does not rely on
that bypass — it scopes itself via `session.is_sdlc`, the real property at
`models/agent_session.py:1612`.

## `recent_sent_drafts` field

`AgentSession.recent_sent_drafts` is a `ListField` holding the last N
successfully-sent drafts as dicts:

```python
{
    "ts": 1714500000.0,      # Unix timestamp of the send
    "text": "...",           # Draft text preview (capped at 500 chars)
    "artifacts": {"urls": [...], "commits": [...]}  # From extract_artifacts()
}
```

- Written by `record_recent_sent_draft()` AFTER `rpush` succeeds.
- Capped at `DRAFTER_RECENT_DRAFTS_N` entries (FIFO — oldest dropped).
- Persisted via `save(update_fields=["recent_sent_drafts", "updated_at"])` to
  avoid clobbering concurrent writes to other fields (see #898).
- Included in `_AGENT_SESSION_FIELDS` so it survives the queue→worker session-job hop.

## Testing

| Test file | Coverage |
|-----------|---------|
| `tests/unit/test_redundancy_filter.py` | All 5 termination conditions, Jaccard threshold edges, error fallback, stale priors |
| `tests/unit/test_output_handler.py::TestRedundancyFilterWiring` | SDLC suppress → reaction, non-SDLC bypass, draft recording, filter exception fallthrough |
| `tests/unit/test_agent_session.py::TestRecentSentDraftsField` | Field presence, FIFO cap, scoped save contract |
| `tests/integration/test_message_drafter_integration.py` | 3-send regression scenario, artifact-termination integration |
