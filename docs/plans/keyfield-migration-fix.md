# Plan: KeyField Migration Fix for filter()-Loaded Instances

**Issue:** https://github.com/tomcounsell/popoto/issues/298
**Slug:** keyfield-migration-fix

## Problem

When a model instance is loaded via `query.filter()` and a KeyField value is changed, calling `.save()` creates a new Redis entry without removing the old one, resulting in duplicate index entries.

### Root Cause

Instances returned by `filter()` go through `Query.get_many_objects()` which calls `decode_popoto_model_hashmap()` in `encoding.py`. There are two code paths, and both have bugs:

1. **Lazy path** (`_create_lazy_model`, encoding.py L383-415): This is the default path used by `get_many_objects(lazy=True)`. It bypasses `__init__` via `object.__new__()` and explicitly sets `_redis_key = None` and `_saved_field_values = dict()` without computing either value. This means `save()` cannot determine the old key to delete.

2. **Non-lazy path** (encoding.py L327-349): Creates the instance via `model_class(**model_attrs)` which calls `__init__`. The `__init__` at base.py L604-608 does compute `_redis_key` if all KeyField values are present. Then `_saved_field_values` is populated at L343-346. This path works correctly for the non-lazy case.

The `save()` method (base.py L1224) checks `self._redis_key != new_db_key.redis_key` to detect key migration. When `_redis_key` is `None` (lazy instances), this condition is always `True`, but `obsolete_redis_key` gets set to `None` which makes the cleanup at L1253-1274 a no-op (condition `self.obsolete_redis_key and ...` fails). The old hash and index entries are never deleted.

Additionally, `_saved_field_values` being empty means even if cleanup ran, it would use current (new) field values instead of original (old) values, cleaning up the wrong index entries.

### Impact

- Every KeyField change on a filter()-loaded instance creates a duplicate
- The old Redis hash key persists as an orphan
- The old index entries persist, causing the same logical record to appear multiple times in future queries
- This affects both the full save path and the partial save (update_fields) path

## Solution

### Fix 1: Populate `_redis_key` in `_create_lazy_model` (encoding.py)

After creating the lazy instance, compute its `_redis_key` from the KeyField values embedded in the `_lazy_fields` data. Since KeyField values are part of the Redis key structure, we can compute `db_key.redis_key` without fully deserializing all fields.

The fix: after setting up the lazy instance, decode only the KeyField values from `_lazy_fields`, set them on the instance, and compute `_redis_key = instance.db_key.redis_key`. This mirrors what `__init__` does at base.py L604-608.

### Fix 2: Populate `_saved_field_values` in `_create_lazy_model` (encoding.py)

The lazy model also needs `_saved_field_values` populated so that when a KeyField value changes, `save()` can use the original values for `on_delete` cleanup of old index entries.

For lazy instances, `_saved_field_values` should be populated lazily as fields are accessed (piggybacking on the existing `__getattribute__` lazy decode mechanism), or eagerly for KeyField values only since those are critical for key migration.

The recommended approach: eagerly decode and store KeyField values in `_saved_field_values` at lazy model creation time. KeyFields are lightweight (strings/ints) and there are typically few of them per model, so the performance cost is negligible.

### Fix 3: Verify `get()` path (query.py L1362-1368)

The `get()` method with direct key lookup calls `decode_popoto_model_hashmap()` without `lazy=True`, so it uses the non-lazy path which already works. Verify this with a test but no code change expected.

## Tasks

- [ ] In `_create_lazy_model` (encoding.py), after instance creation:
  - Decode KeyField values from `_lazy_fields`
  - Set decoded KeyField values as attributes on the instance
  - Compute and set `instance._redis_key = instance.db_key.redis_key`
  - Store KeyField values in `_saved_field_values`
  - Move decoded KeyField entries from `_lazy_fields` to `_decoded_fields`
- [ ] Add regression test: create instance, load via `filter()`, change KeyField, save, verify no duplicates
- [ ] Add regression test: create instance, load via `filter()`, change KeyField, save, verify old Redis hash key is deleted
- [ ] Add regression test: lazy-loaded instance has correct `_redis_key` and `_saved_field_values` after deserialization
- [ ] Add edge case test: change KeyField on filter()-loaded instance then delete -- verify clean removal
- [ ] Add edge case test: bulk filter, change KeyField on multiple instances, save all -- no duplicates
- [ ] Add edge case test: chained filter().filter() with KeyField change and save
- [ ] Add edge case test: `update_fields` partial save path with KeyField change on filter()-loaded instance
- [ ] Verify `get()` direct-lookup path already works (test KeyField change after `get()`)

## Success Criteria

- Filter()-loaded instances (both lazy and non-lazy) have `_redis_key` correctly set to their current Redis key after deserialization
- Filter()-loaded instances have `_saved_field_values` populated with at least all KeyField values after deserialization
- Changing a KeyField value on a filter()-loaded instance and calling `save()` removes the old Redis hash key and old index entries
- No duplicate records appear in query results after modifying and saving a KeyField on a filter()-loaded instance
- The partial save path (`update_fields`) also correctly migrates keys for filter()-loaded instances
- Lazy loading performance is not degraded -- only KeyField values are eagerly decoded, not all fields
- Existing tests continue to pass with no modifications

## No-Gos

- Do not change the lazy loading architecture -- the fix should work within the existing `_lazy_fields` / `_decoded_fields` / `__getattribute__` pattern
- Do not eagerly decode all fields on lazy instances -- only decode KeyField values needed for `_redis_key` and `_saved_field_values`
- Do not change the `save()` key migration logic in base.py -- the bug is in deserialization, not in save
- Do not add a "fixup" in save() that recomputes `_redis_key` if it is None -- that masks the real problem and would use current (possibly changed) field values

## Update System

No update system changes required -- this is a bug fix in the popoto library itself, not in the ai repo's deployment or update scripts.

## Agent Integration

No agent integration required -- this is a fix in popoto's core ORM layer. No MCP servers, bridge changes, or tool wrappers are needed.

## Failure Path Test Strategy

- Test that saving a filter()-loaded instance with an unchanged KeyField does NOT trigger unnecessary key migration (no false positives)
- Test that `_redis_key` is `None` for instances created via constructor (new, unsaved) -- the fix should only affect deserialized instances
- Test that deleting a filter()-loaded instance after KeyField change properly cleans up both old and new index entries
- Test concurrent saves of the same logical record with different KeyField values (last-write-wins, no orphans)

## Test Impact

- [ ] `tests/test_keyfield_stale_reads.py` -- No changes needed, these tests cover create-then-filter scenarios, not filter-then-modify
- [ ] `tests/test_key_fields.py` -- No changes needed, these tests cover basic KeyField querying and don't test the modify-after-filter path

No existing tests are affected -- this is a previously untested code path (modifying KeyField values on filter()-loaded instances). All new tests will be additive.

## Documentation

- [ ] Add inline code comments in `_create_lazy_model` explaining why KeyField values are eagerly decoded
- [ ] Update docstring of `_create_lazy_model` to document the `_redis_key` and `_saved_field_values` initialization

No external documentation file needed -- this is an internal bug fix with no API changes.

## Rabbit Holes

- **Full eager decode of `_saved_field_values` for lazy instances**: Tempting but defeats the purpose of lazy loading. Only KeyField values matter for key migration.
- **Refactoring save() to handle `_redis_key = None`**: This would add complexity to save() and mask deserialization bugs. The right fix is at the source.
- **Adding a `_from_redis` flag**: Overengineering. The existing `_is_persisted` flag plus a properly-set `_redis_key` is sufficient to distinguish new vs loaded instances.
