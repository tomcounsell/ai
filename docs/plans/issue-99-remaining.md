---
status: Ready
type: chore
appetite: Small
owner: Tom
created: 2026-02-22
updated: 2026-02-24
tracking: https://github.com/yudame/cuttlefish/issues/99
branch: feature/issue-99-remaining
---

# Issue #99 Remaining Work: Episode Descriptions + Podcast Covers

## Problem

Issue #99 ("Podcast pages: broken media, raw markdown, malformed timestamps") is CLOSED with all P0-P2 items shipped. Two backfill chores remain that were deferred:

1. **Auto-generate episode descriptions** -- 7/49 episodes still have blank `description` fields. These are unproduced episodes (no audio, no report_text). The 42 episodes that had old feed data were backfilled in commit `e1c3235`. Future episodes get descriptions via the `write_metadata` AI tool in the production pipeline. A management command provides a safety net for any episodes that slip through without descriptions.

2. **Podcast-level cover images** -- All 3 `Podcast` records have empty `cover_image_url`. The RSS feed template conditionally renders `<itunes:image>` only when present, so podcast apps currently show no channel artwork. Issue #101 covers the web UI for cover art upload, but a `--podcast-covers` flag on `upload_podcast_media` provides an immediate CLI path for the Yudame Research global cover.

**Current data (verified 2026-02-24):**

| Metric | Count |
|--------|-------|
| Total episodes | 49 |
| With description | 42 |
| Without description | 7 |
| With report_text | 41 |
| Missing description AND have report_text | 0 |
| Podcasts with cover_image_url | 0/3 |

The 7 episodes without descriptions also have no `report_text` -- they are draft/in-progress episodes that have not been produced yet. The command should still be built because: (a) it serves as a backfill tool when episodes are published outside the normal pipeline, and (b) future batch imports via `backfill_episodes` may leave descriptions blank.

## Appetite

**Size:** Small batch (half day)

**Branch:** `feature/issue-99-remaining`

## Solution

### Task 1: `generate_descriptions` Management Command

**File:** `apps/podcast/management/commands/generate_descriptions.py`

A management command that extracts a short description from `report_text` for episodes that have report content but no description. Designed for idempotent backfill runs.

**Algorithm:**
```
1. Query episodes where description is blank AND report_text is non-empty
2. For each episode:
   a. Split report_text into lines
   b. Skip leading markdown headers (lines starting with #) and blank lines
   c. Take first paragraph (text up to next blank line or end)
   d. Strip any remaining markdown formatting (bold, italic, links)
   e. Truncate to 250 chars at nearest sentence boundary (". " or end of text)
   f. Save with update_fields=["description", "modified_at"]
3. Print summary: N descriptions generated, M skipped (no report_text), K skipped (already has description)
4. Support --dry-run flag (preview without saving)
```

**Why a management command:**
- One-time/periodic backfill for existing episodes
- Future episodes use the production pipeline (`step_metadata` -> `write_episode_metadata` -> `write_metadata` AI tool)
- A `save()` hook would fire on every save, even during draft state when description should be blank

**Tests:** `apps/podcast/tests/test_generate_descriptions.py`

Test cases:
- Episode with report_text and no description -> description populated from first paragraph
- Episode with no report_text -> skipped
- Episode that already has a description -> skipped
- Truncation at sentence boundary when first paragraph exceeds 250 chars
- Markdown header stripping (lines starting with `#` are skipped)
- Inline markdown stripping (`**bold**`, `*italic*`, `[links](url)` cleaned)
- `--dry-run` flag prints preview without saving to database
- Summary output shows correct counts

### Task 2: `--podcast-covers` Flag on `upload_podcast_media`

**File:** `apps/podcast/management/commands/upload_podcast_media.py` (MODIFY)

Add a `--podcast-covers` flag that uploads podcast-level cover images to Supabase after the episode loop completes.

**Cover file lookup priority:**
1. `{source_dir}/../cover.png` -- global cover (used by yudame-research)
2. `{source_dir}/../{podcast_slug}/cover.png` -- per-podcast cover if organized by slug
3. Skip if not found (SATSOL/Soul World Bank covers handled by issue #101 edit page)

**Implementation:**
```python
# New argument
parser.add_argument(
    "--podcast-covers",
    action="store_true",
    help="Also upload podcast-level cover images",
)

# After episode loop, before summary:
if options.get("podcast_covers"):
    self._upload_podcast_covers(source_dir, dry_run, force, storage, stats)
```

The `_upload_podcast_covers` method:
- Iterates `Podcast.objects.all()`
- Skips podcasts that already have a valid `cover_image_url` (unless `--force`)
- Looks for cover file using the priority above
- Uploads to `podcast/{slug}/cover.png` in Supabase
- Updates `podcast.cover_image_url` with the public URL
- Adds `podcast_cover_uploaded` and `podcast_cover_skipped` to stats

**Tests:** Added to `apps/podcast/tests/test_generate_descriptions.py` or a separate test file. Since `upload_podcast_media` involves Supabase (external service), tests verify argument parsing and the cover file lookup logic, not actual uploads.

## Rabbit Holes

- **AI-generated descriptions for episodes without report_text**: The 7 episodes with no description also have no report_text. Generating descriptions from title alone would require an AI call or template-based approach. Not worth it -- these are unproduced drafts. Skip them.
- **Image resizing for podcast covers**: Apple requires 1400x1400 minimum, 3000x3000 recommended. The existing `cover.png` is 3.6MB which is fine. Don't add PIL/Pillow dependency for resizing -- accept as-is.
- **Private bucket routing for podcast covers**: SATSOL and Soul World Bank are restricted podcasts using the private bucket. The `upload_podcast_media` command currently only uses the public bucket. For restricted podcasts, covers would need to go to the private bucket with signed URL generation. Leave this for issue #101's edit page which already handles bucket routing via `podcast.uses_private_bucket`.

## Risks

### Risk 1: Pending Podcast table migration
The `Podcast` model inherits `Publishable` which adds `published_at`, `edited_at`, `unpublished_at` columns. The local database is missing these columns (verified: `column podcast_podcast.published_at does not exist`). This means `Podcast.objects.all()` will fail at query time.

**Mitigation:** The `upload_podcast_media` command already queries `Podcast.objects.all()` and works in production (where migrations are applied). For local testing, use `Episode`-only queries or run pending migrations first. Tests use Django's test database which runs all migrations from scratch, so this does not affect test execution.

### Risk 2: Sentence boundary truncation edge cases
Report text may start with a sentence longer than 250 characters, or may have no period at all.

**Mitigation:** If no sentence boundary is found within 250 chars, truncate at the nearest word boundary and append "..." as an ellipsis. Test this edge case explicitly.

## No-Gos (Out of Scope)

- AI-generated descriptions for episodes without report_text
- Image resizing or format conversion
- Private bucket uploads for podcast covers (handled by #101)
- Generating covers for SATSOL and Soul World Bank (handled by #101)
- Running migrations (wait for Tom)

## Files Changed

| File | Change |
|------|--------|
| `apps/podcast/management/commands/generate_descriptions.py` | NEW -- management command |
| `apps/podcast/tests/test_generate_descriptions.py` | NEW -- tests for both tasks |
| `apps/podcast/management/commands/upload_podcast_media.py` | MODIFY -- add `--podcast-covers` flag and `_upload_podcast_covers` method |

## Success Criteria

- [ ] `generate_descriptions` command exists and extracts first paragraph from report_text
- [ ] Descriptions are <=250 chars, truncated at sentence boundaries, no markdown headers or inline formatting
- [ ] `--dry-run` flag previews without saving
- [ ] Episodes without report_text are skipped with informative output
- [ ] Episodes that already have descriptions are skipped
- [ ] `upload_podcast_media --podcast-covers` flag exists and uploads cover files
- [ ] Cover file lookup finds `{source_dir}/../cover.png` for yudame-research
- [ ] All tests pass: `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_generate_descriptions.py -v`
- [ ] Pre-commit passes: `uv run pre-commit run --all-files`

## Team Members

- **Builder (descriptions-and-covers)**
  - Name: descriptions-builder
  - Role: Create generate_descriptions command, add --podcast-covers flag, write tests
  - Agent Type: builder
  - Resume: true

## Step by Step Tasks

### 1. Write failing tests for generate_descriptions
- **Task ID**: test-generate-descriptions
- **Depends On**: none
- **Assigned To**: descriptions-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `apps/podcast/tests/test_generate_descriptions.py`
- Write tests for: report_text extraction, skip-no-report, skip-has-description, truncation, markdown stripping, dry-run
- Confirm tests fail (RED phase)

### 2. Implement generate_descriptions command
- **Task ID**: build-generate-descriptions
- **Depends On**: test-generate-descriptions
- **Assigned To**: descriptions-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `apps/podcast/management/commands/generate_descriptions.py`
- Implement algorithm: strip headers, first paragraph, sentence truncation, inline markdown cleanup
- Confirm tests pass (GREEN phase)

### 3. Add --podcast-covers flag to upload_podcast_media
- **Task ID**: build-podcast-covers-flag
- **Depends On**: build-generate-descriptions
- **Assigned To**: descriptions-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `--podcast-covers` argument to `upload_podcast_media`
- Implement `_upload_podcast_covers` method with cover file lookup
- Add tests for argument parsing and cover lookup logic
- Confirm all tests pass

### 4. Final validation
- **Task ID**: validate-all
- **Depends On**: build-podcast-covers-flag
- **Assigned To**: descriptions-builder
- **Agent Type**: builder
- **Parallel**: false
- Run `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_generate_descriptions.py -v`
- Run `uv run pre-commit run --all-files`
- Commit to `feature/issue-99-remaining` branch

## Validation Commands

- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_generate_descriptions.py -v` -- all pass
- `uv run pre-commit run --all-files` -- all pass

## Open Questions

1. **Should the generate_descriptions command also handle episodes with only a title (no report_text)?** Current plan skips them entirely. Alternative: generate a minimal description like "{title} - Episode {number} of {podcast_title}". This would cover the 7 remaining episodes but produces generic text.

2. **Should we upload the Yudame Research cover immediately via Django shell?** The `--podcast-covers` flag requires a local source directory with cover files. If the goal is speed, a one-liner in Django shell (`Podcast.objects.filter(slug="yudame-research").update(cover_image_url="...")`) after manual Supabase upload might be faster than building the flag. The flag is more reusable long-term.

3. **Is issue #99 the right tracking issue?** It is CLOSED. Should a new issue be created for this remaining work, or should #99 be reopened? The current plan links to #99 since these tasks originated from that issue.
