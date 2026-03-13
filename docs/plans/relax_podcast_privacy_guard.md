---
status: Planning
type: feature
appetite: Small
owner: Tom
created: 2026-03-13
tracking: https://github.com/yudame/cuttlefish/issues/112
last_comment_id:
---

# Relax Podcast Privacy Guard

## Problem

The `Podcast.save()` method prevents **any** privacy change after creation, raising `ValueError("Podcast privacy cannot be changed after creation")`. This was designed to prevent audio files ending up in the wrong Supabase bucket.

However, only the `restricted` privacy level uses the private bucket. Switching between `public` and `unlisted` has zero bucket implications -- both use the public bucket with permanent URLs.

**Current behavior:**
Any privacy change after creation raises `ValueError`, even harmless ones like `public -> unlisted` or `unlisted -> public`.

**Desired outcome:**
Only block privacy changes that cross the `restricted` boundary (i.e., to or from `restricted`). Allow free switching between `public` and `unlisted`.

## Prior Art

- **Issue #96**: Podcast privacy setting -- Implemented the three-tier privacy model (public/unlisted/restricted) with the current blanket immutability guard. Status: closed, fully shipped.
- **Issue #122**: Fix 4 pre-existing test failures -- One failure was caused by `import_podcast_feed` passing `privacy` in `update_or_create` defaults, hitting the immutability guard. The fix avoided changing privacy on existing podcasts.

## Data Flow

1. **Entry point**: User or code changes `podcast.privacy` field value
2. **`Podcast.save()`**: Loads existing record, compares old vs new privacy value
3. **Guard logic**: Currently rejects ALL changes; proposed change rejects only restricted-boundary crossings
4. **Supabase bucket**: `uses_private_bucket` property determines storage -- only `restricted` uses private bucket
5. **Output**: Save succeeds (non-restricted change) or raises `ValueError` (restricted boundary crossing)

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: `Podcast.save()` guard logic becomes more permissive -- same interface, relaxed constraint
- **Coupling**: No change -- the guard still protects the bucket-routing invariant
- **Data ownership**: No change
- **Reversibility**: Trivial -- revert the guard condition to reject all changes

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

This is a ~10 line logic change in the model plus updated tests. No new files, no new dependencies, no migration.

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Relaxed guard in `Podcast.save()`**: Only raise `ValueError` when the change involves `restricted` (either from or to)
- **Updated tests**: Reflect the new allowed transitions and still-blocked transitions

### Flow

Save podcast with changed privacy -> Guard checks if `restricted` is involved -> If yes, raise error -> If no, allow save

### Technical Approach

Replace the blanket inequality check in `Podcast.save()` with a check that only blocks transitions involving `restricted`:

```python
def save(self, *args, **kwargs):
    if self.pk:
        try:
            existing = Podcast.objects.only("privacy").get(pk=self.pk)
            if existing.privacy != self.privacy:
                old_restricted = existing.privacy == self.Privacy.RESTRICTED
                new_restricted = self.privacy == self.Privacy.RESTRICTED
                if old_restricted or new_restricted:
                    raise ValueError(
                        "Podcast privacy cannot be changed to or from "
                        "'restricted' after creation because it would "
                        "leave audio files in the wrong storage bucket."
                    )
        except Podcast.DoesNotExist:
            pass
    super().save(*args, **kwargs)
```

Key decision: Block changes **to** restricted AND **from** restricted. Both directions would create a bucket mismatch for existing audio files.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `Podcast.DoesNotExist` catch in `save()` is already tested (existing test coverage)
- [ ] The `ValueError` raise path is tested for all restricted-boundary crossings

### Empty/Invalid Input Handling
- [ ] Not applicable -- privacy field has `choices` constraint, no empty/None inputs possible

### Error State Rendering
- [ ] Not applicable -- this is a model-level guard, not a user-facing UI change

## Rabbit Holes

- **Migrating existing audio between buckets**: Do not attempt to move files when privacy changes -- the guard exists precisely to avoid this complexity
- **Adding a UI for privacy changes**: This plan only relaxes the model guard; admin/UI changes for selecting privacy are separate work
- **`import_podcast_feed` changes**: The import command may benefit from this relaxation, but do not expand scope to refactor the import logic

## Risks

### Risk 1: Test assertions assume blanket rejection
**Impact:** Existing tests (`test_cannot_change_privacy_after_creation`, `test_cannot_change_restricted_to_public`) test specific transitions. Some may need updating if the tested transition is now allowed.
**Mitigation:** Review each existing test case and update assertions to match the new rules.

## Race Conditions

No race conditions identified. The `save()` guard is synchronous and single-threaded -- it reads the existing row and compares before writing. The existing `Podcast.DoesNotExist` guard handles the edge case of a deleted record.

## No-Gos (Out of Scope)

- Bucket migration tooling (moving audio files between public/private buckets)
- UI for changing podcast privacy after creation
- Changes to `import_podcast_feed` command
- Changes to feed views, web views, or admin

## Update System

No update system changes required -- this is a model-level logic change with no new dependencies or config.

## Agent Integration

No agent integration required -- this is a Django model change with no MCP or agent surface.

## Documentation

### Inline Documentation
- [ ] Updated error message in `save()` to explain the restricted-boundary constraint
- [ ] Updated docstring/comment above the guard to reflect the relaxed rule

No feature documentation changes needed -- the existing privacy settings plan (`docs/plans/podcast-privacy-settings.md`) documents the privacy model.

## Success Criteria

- [ ] `public -> unlisted` privacy change succeeds
- [ ] `unlisted -> public` privacy change succeeds
- [ ] `public -> restricted` raises `ValueError`
- [ ] `restricted -> public` raises `ValueError`
- [ ] `unlisted -> restricted` raises `ValueError`
- [ ] `restricted -> unlisted` raises `ValueError`
- [ ] Same-privacy saves still work (no regression)
- [ ] Tests pass (`/do-test`)

## Team Orchestration

### Team Members

- **Builder (model-guard)**
  - Name: guard-builder
  - Role: Update `Podcast.save()` guard logic and tests
  - Agent Type: builder
  - Resume: true

- **Validator (model-guard)**
  - Name: guard-validator
  - Role: Verify all privacy transitions behave correctly
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Update Privacy Guard Logic
- **Task ID**: build-guard
- **Depends On**: none
- **Assigned To**: guard-builder
- **Agent Type**: builder
- **Parallel**: true
- Modify `Podcast.save()` in `apps/podcast/models/podcast.py` to only block restricted-boundary changes
- Update error message to be specific about the restricted constraint

### 2. Update Tests
- **Task ID**: build-tests
- **Depends On**: none
- **Assigned To**: guard-builder
- **Agent Type**: builder
- **Parallel**: true
- Update `test_cannot_change_privacy_after_creation` -- this tests `public -> restricted`, should still raise
- Update `test_cannot_change_restricted_to_public` -- should still raise
- Add new test: `test_can_change_public_to_unlisted`
- Add new test: `test_can_change_unlisted_to_public`
- Add new test: `test_cannot_change_unlisted_to_restricted`
- Add new test: `test_cannot_change_restricted_to_unlisted`

### 3. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-guard, build-tests
- **Assigned To**: guard-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_models.py -v -k privacy`
- Verify all 6 transition scenarios pass
- Run full test suite to check for regressions

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Privacy tests pass | `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_models.py -v -k privacy` | exit code 0 |
| Full test suite | `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/ -x -q` | exit code 0 |
| Lint clean | `uv run pre-commit run --all-files` | exit code 0 |

---

## Open Questions

1. Should we log a warning when a non-restricted privacy change occurs (for audit purposes), or is the relaxed guard sufficient?
2. The `import_podcast_feed` command previously hit this guard -- should we verify it benefits from this relaxation, or leave that as separate work?
