# Perplexity Manual Paste Fallback

## Overview

When the automated Perplexity Deep Research step (Phase 2) is skipped or fails, the podcast production workflow previously had no recovery path except re-triggering the same API call. This feature adds a manual paste panel to Phase 3 (Question Discovery) so users can run the Perplexity prompt themselves and paste the result back in.

## User Flow

1. Phase 2 (Perplexity Research) is skipped or fails — the `p2-perplexity` artifact contains a `[SKIPPED: ...]` or `[FAILED: ...]` sentinel.
2. User navigates to Phase 3 (Question Discovery). The manual paste panel appears automatically alongside the standard "Retry" button.
3. The panel shows the `prompt-perplexity` artifact content in a read-only block with a "Copy prompt" button.
4. User opens [Perplexity Deep Research](https://www.perplexity.ai/), pastes the prompt, and copies the result.
5. User pastes the result into the textarea and clicks "Save research".
6. The system validates the content, writes it to `p2-perplexity`, and re-enqueues Question Discovery.
7. Phase 3 reloads — the `p2-perplexity` substep now shows as complete/running, unblocking the pipeline.

## Technical Details

### New View: `PastePerplexityResearchView`

Located in `apps/podcast/workflow.py`. POST-only view with the following behavior:

- **Validation**: rejects empty content, whitespace-only content, and content beginning with `[SKIPPED:` or `[FAILED:` (these are error sentinels, not real research).
- **Artifact update**: writes the pasted content to the `p2-perplexity` `EpisodeArtifact`. Sets `metadata["manually_pasted"] = True` and `metadata["manually_pasted_at"]` for auditability.
- **Workflow resume**: if the workflow is in `paused_for_human`, `failed`, or `paused_at_gate` state, calls `resume_workflow()` before re-enqueuing.
- **Re-enqueue**: calls `step_question_discovery.enqueue(episode_id=episode.pk)`.
- **Redirect**: redirects to Phase 3 (HTMX-aware, same pattern as `RetryResearchSourceView`).
- **Missing artifact guard**: if `p2-perplexity` does not exist yet, logs a warning and redirects without enqueuing.

### URL

```
POST /podcast/<slug>/<episode_slug>/perplexity-paste/
```

Named `podcast:paste_perplexity_research`.

### Template: Phase 3 Manual Paste Panel

In `apps/public/templates/podcast/_workflow_step_content.html`, a conditional block renders when `current_step == 3` and `perplexity_status` is `"skipped"` or `"failed"`.

The panel includes:
- Status description (skipped vs. failed)
- `prompt-perplexity` content in a `<pre>` block with a "Copy prompt" button
- A `<textarea>` for pasting the Perplexity result
- A "Save research" submit button

If `prompt-perplexity` does not exist, a note is shown in place of the prompt display.

### Context Variables (Phase 3)

The following context variables are injected by `EpisodeWorkflowView._load_context()` and `WorkflowPollView.get()` when `step == 3`:

| Variable | Type | Description |
|----------|------|-------------|
| `perplexity_status` | `str` | Status of `p2-perplexity`: `"skipped"`, `"failed"`, `"complete"`, `"pending"`, etc. |
| `perplexity_prompt_content` | `str` | Content of `prompt-perplexity` artifact (empty string if not found) |
| `perplexity_paste_url` | `str` | URL for the paste endpoint |

## Coexistence with Auto-Retry

The existing "Retry" button (driven by `button_state`) is preserved. The manual paste panel renders alongside it — they are independent recovery paths. The auto-retry will re-run the Perplexity API call; the manual paste bypasses the API entirely.

## Validation Rules

| Input | Outcome |
|-------|---------|
| Empty string | Rejected — no write, no enqueue |
| Whitespace only | Rejected — no write, no enqueue |
| Starts with `[SKIPPED:` | Rejected — sentinel content |
| Starts with `[FAILED:` | Rejected — sentinel content |
| Any other non-empty string | Accepted — written to `p2-perplexity` |

## Test Coverage

Tests are in `apps/podcast/tests/test_perplexity_paste.py`:

- `test_anonymous_redirects_to_login` — auth guard
- `test_non_staff_gets_403` — permission guard
- `test_valid_paste_writes_artifact_and_redirects` — happy path write + redirect
- `test_valid_paste_enqueues_question_discovery` — task enqueue on valid paste
- `test_valid_paste_resumes_paused_workflow` — workflow resume on valid paste
- `test_empty_content_does_not_write` — empty rejection
- `test_whitespace_only_content_does_not_write` — whitespace rejection
- `test_skipped_sentinel_content_is_rejected` — sentinel rejection
- `test_failed_sentinel_content_is_rejected` — sentinel rejection
- `test_missing_artifact_does_not_crash` — graceful handling of missing artifact
