---
status: Ready
type: feature
appetite: Medium
owner: Valor
created: 2026-03-31
tracking: https://github.com/tomcounsell/popoto/issues/319
last_comment_id:
---

# Partitioned Reads for Hash-Based Field Indexes

## Problem

Fields that store per-instance data in a single Redis hash (ConfidenceField's companion hash) require HGETALL to read all entries, then client-side filtering. As the entry count grows (currently around 1,200 in production, expected to reach 10,000+), this becomes increasingly wasteful.

**Current behavior:**
ConfidenceField stores all per-member confidence metadata in a single Redis hash at `$ConfidencF:{Model}:{field}:data`. When the query system needs confidence scores for sorting/filtering (via `_materialize_confidence_sortedset` in query.py), it calls `HGETALL` on this hash, loading every entry into memory regardless of how many are actually needed for the query.

**Desired outcome:**
Hash-based field indexes support a `partition_by` parameter (matching SortedField's existing API) that splits the single Redis hash into per-partition hashes. A query scoped to one partition reads only that partition's hash, avoiding the O(N) full scan.

## Prior Art

- **PR #138**: Rename sort_by to partition_by on SortedField -- established the `partition_by` API pattern and naming convention that this work extends to hash-based fields.
- **PR #159**: Fix SortedField ghost entries on partition key change -- demonstrates the complexity of handling partition key changes on save/delete; same patterns will apply here.
- **PR #188**: Document multi-tenancy pattern using KeyField -- documents the KeyField + partition_by pattern for sorted fields. This work extends that pattern to hash-based companion data.
- **Issue #75 / #78**: Performance audit and msgpack deserialization optimization -- prior performance work on the query path, but focused on deserialization rather than data volume.

## Data Flow

1. **Entry point**: Application calls `ConfidenceField.update_confidence(instance, "field", signal=0.9)` or model save triggers `on_save()`
2. **ConfidenceField.on_save()**: Writes to companion hash at `$ConfidencF:{Model}:{field}:data` via `HSETNX`/`HSET`, keyed by `member_key` (the model instance's redis key)
3. **Query path**: `_materialize_confidence_sortedset()` in query.py calls `HGETALL` on the companion hash, unpacks all entries, builds a temp ZADD sorted set for scoring
4. **Output**: Query returns filtered/sorted model instances

With partitioning, step 2 writes to `$ConfidencF:{Model}:{field}:data:{partition_value}` and step 3 reads only the partition-scoped hash.

## Architectural Impact

- **New dependencies**: None -- uses existing Redis hash commands
- **Interface changes**: `ConfidenceField.__init__()` gains optional `partition_by` parameter. `_get_data_hash_key()` gains a partition-aware overload. All changes are backward-compatible (no partition_by = current single-hash behavior).
- **Coupling**: Minimal increase -- ConfidenceField reads partition field values from model instances, same pattern SortedFieldMixin already uses.
- **Data ownership**: No change -- ConfidenceField still owns its companion hash data
- **Reversibility**: High -- partition_by is opt-in; removing it reverts to single-hash behavior. Migration helper can merge partitioned hashes back.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (scope alignment on which fields get partition support)
- Review rounds: 1 (code review)

## Prerequisites

No prerequisites -- this work has no external dependencies. Requires only a running Redis instance for tests.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `redis-cli ping` | Test execution |

## Solution

### Key Elements

- **ConfidenceField partition_by parameter**: Optional parameter matching SortedField's API that partitions the companion hash by one or more key field values
- **Partition-aware hash key generation**: `_get_data_hash_key()` appends partition field values to the hash key when partition_by is configured
- **Partition-aware query materialization**: `_materialize_confidence_sortedset()` reads only the partition-scoped hash when partition fields are present in the query
- **HSCAN fallback for unpartitioned reads**: Add an HSCAN-with-MATCH utility for cases where partitioning is not configured but filtered reads are still needed

### Flow

**Model definition** (add `partition_by='project'` to ConfidenceField) --> **on_save()** (writes to partitioned hash key) --> **update_confidence()** (updates partitioned hash) --> **query with partition filter** (reads only scoped hash) --> **results** (only relevant entries loaded)

### Technical Approach

1. **Add `partition_by` to ConfidenceField.__init__()**: Accept a string or tuple of field names, normalize to tuple, validate they reference existing fields (at model metaclass time). Mirror SortedFieldMixin's validation logic.

2. **Modify `_get_data_hash_key()`**: When partition_by is set and a model instance is provided, append the partition field values to the hash key. Pattern: `$ConfidencF:{Model}:{field}:data:{partition_val1}:{partition_val2}`.

3. **Add `_get_partitioned_data_hash_key(model_instance, field_name)`**: Reads partition field values from the instance (like `get_partitioned_sortedset_db_key` does for SortedField). Used by on_save, on_delete, update_confidence, get_confidence, get_confidence_data.

4. **Update `_materialize_confidence_sortedset()` in query.py**: When the ConfidenceField has partition_by and the query includes partition field values, build the partition-scoped hash key and HGETALL only that. When partition fields are missing from the query, either raise QueryException (matching SortedField behavior) or fall back to scanning all partition hashes.

5. **Update Lua script (BAYESIAN_UPDATE_LUA)**: No changes needed -- the Lua script receives the hash key as KEYS[1], so passing a partitioned key works transparently.

6. **Add HSCAN utility method**: For the unpartitioned case, add a `get_confidence_filtered(model_class, field_name, pattern)` classmethod that uses HSCAN with MATCH to iterate without loading everything into memory. This is a secondary optimization for users who cannot partition.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Test that querying a partitioned ConfidenceField without providing partition field values raises QueryException (not silent empty results)
- [ ] Test that on_save with a None partition field value raises or handles gracefully
- [ ] Existing `except Exception: pass` blocks in ConfidenceField.update_confidence's event stream logging are out of scope (pre-existing)

### Empty/Invalid Input Handling
- [ ] Test partition_by referencing a non-existent field name raises ModelException at class definition time
- [ ] Test get_confidence on a partition with no entries returns initial_confidence (not None or crash)
- [ ] Test HSCAN utility with no matching pattern returns empty dict

### Error State Rendering
- [ ] QueryException messages for missing partition fields include the field names needed (actionable error)

## Test Impact

- [ ] `tests/test_confidence_field.py` -- UPDATE: Add new test cases for partitioned behavior alongside existing unpartitioned tests. Existing tests should pass unchanged since partition_by is opt-in.
- [ ] `tests/test_agent_memory_e2e.py` -- No change expected; these tests use ConfidenceField without partition_by and should continue working.

Existing confidence field tests are not affected because the feature is purely additive (partition_by defaults to empty tuple, preserving current single-hash behavior).

## Rabbit Holes

- **Generic HashFieldMixin**: Tempting to create a general-purpose "partitioned hash mixin" that any field can use. ConfidenceField is the only current consumer; generalize later if/when a second use case appears.
- **Automatic partition detection**: Attempting to infer partition fields from query context or model structure. Explicit partition_by is clearer and matches the existing SortedField pattern.
- **Hash-to-hash migration tooling**: Building a general migration framework for resharding existing hashes into partitioned ones. A simple one-off script in the plan's tasks is sufficient.
- **HSCAN cursor management**: Building an async iterator over HSCAN results. The simple synchronous HSCAN-with-MATCH is enough for the stated use case.

## Risks

### Risk 1: Partition key change leaves orphaned hash entries
**Impact:** If a model instance changes its partition key field value (e.g., moves from project A to project B), the old partition hash retains the stale entry.
**Mitigation:** Mirror SortedFieldMixin.on_save() pattern: detect partition changes via `_saved_field_values`, remove from old partition hash, add to new. PR #159 solved this exact problem for sorted sets.

### Risk 2: Backward compatibility for existing unpartitioned data
**Impact:** Existing deployments have all confidence data in a single hash. Adding partition_by would cause new writes to go to partitioned keys while old data remains in the unpartitioned hash.
**Mitigation:** Provide a migration helper method `ConfidenceField.migrate_to_partitioned(model_class, field_name)` that reads the single hash and redistributes entries into partition-scoped hashes. Document this in the migration guide.

## Race Conditions

### Race 1: Concurrent update_confidence during partition key migration
**Location:** confidence_field.py, update_confidence and on_save
**Trigger:** Instance A's partition key changes while another process calls update_confidence on instance A using the old partition key
**Data prerequisite:** The on_save must complete (removing old entry, adding new entry) before update_confidence reads the hash
**State prerequisite:** model_instance must have current partition field values
**Mitigation:** update_confidence always reads partition values from the model instance at call time. The Lua EVAL is atomic within a single hash key. The only risk is a stale instance object in the caller's memory -- document that callers should reload the instance after partition key changes.

## No-Gos (Out of Scope)

- Generic partitioned hash mixin for all field types (only ConfidenceField for now)
- Automatic partition splitting/rebalancing
- Cross-partition aggregate queries (e.g., "confidence across all projects")
- Partitioning for CoOccurrenceField (already uses per-PK sorted sets, which is inherently partitioned)
- Async/streaming HSCAN iteration

## Update System

No update system changes required -- this is a library feature in the popoto package. Consumers update by bumping their popoto dependency version.

## Agent Integration

No agent integration required -- this is an internal library change to the popoto ORM. The ai system's memory layer (which uses ConfidenceField) will benefit by adding `partition_by='project'` to its model definitions, but that is a separate downstream change.

## Documentation

### Feature Documentation
- [ ] Update `docs/multi-tenancy.md` to add a section on hash-based field partitioning with ConfidenceField example
- [ ] Add inline code examples in ConfidenceField docstring showing partition_by usage

### External Documentation Site
- [ ] Update MkDocs pages for ConfidenceField API reference with partition_by parameter
- [ ] Verify docs build passes with `mkdocs build`

### Inline Documentation
- [ ] Docstrings on `_get_partitioned_data_hash_key()` explaining key structure
- [ ] Code comments on partition change detection logic in on_save/on_delete

## Success Criteria

- [ ] `ConfidenceField(partition_by='project')` creates per-project companion hashes
- [ ] `update_confidence()` writes to the correct partition hash
- [ ] `get_confidence()` and `get_confidence_data()` read from the correct partition hash
- [ ] `_materialize_confidence_sortedset()` reads only the partition-scoped hash when partition fields are in the query
- [ ] Partition key change correctly removes from old hash and adds to new
- [ ] Unpartitioned ConfidenceField behavior is unchanged (backward compatible)
- [ ] HSCAN utility method works for filtered reads on unpartitioned hashes
- [ ] Migration helper redistributes existing single-hash data into partitioned hashes
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (confidence-partition)**
  - Name: confidence-builder
  - Role: Implement partition_by support in ConfidenceField and query materialization
  - Agent Type: builder
  - Resume: true

- **Builder (tests)**
  - Name: test-builder
  - Role: Write tests for partitioned ConfidenceField behavior
  - Agent Type: test-engineer
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: Verify backward compatibility and partition correctness
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add partition_by to ConfidenceField
- **Task ID**: build-confidence-partition
- **Depends On**: none
- **Validates**: tests/test_confidence_field.py (update), tests/test_partitioned_confidence.py (create)
- **Assigned To**: confidence-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `partition_by` parameter to `ConfidenceField.__init__()` with validation
- Implement `_get_partitioned_data_hash_key(model_instance, field_name)` that appends partition values
- Update `_get_data_hash_key()` to delegate to partitioned version when partition_by is set
- Update `on_save()`, `on_delete()`, `update_confidence()`, `get_confidence()`, `get_confidence_data()` to use partitioned hash keys
- Add partition change detection in `on_save()` (mirror SortedFieldMixin pattern from PR #159)
- Update Lua script call site to pass partitioned hash key

### 2. Update query materialization
- **Task ID**: build-query-materialization
- **Depends On**: build-confidence-partition
- **Validates**: tests/test_confidence_field.py
- **Assigned To**: confidence-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `_materialize_confidence_sortedset()` in query.py to detect partition_by on the field
- When partition fields are in query_params, build partition-scoped hash key and HGETALL only that
- When partition fields are missing, raise QueryException (matching SortedField behavior)

### 3. Add HSCAN utility
- **Task ID**: build-hscan-utility
- **Depends On**: none
- **Validates**: tests/test_partitioned_confidence.py (create)
- **Assigned To**: confidence-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `get_confidence_filtered(model_class, field_name, pattern)` classmethod using HSCAN with MATCH
- Return dict of {member_key: confidence_data} for matching entries
- Document as alternative to partitioning for simpler use cases

### 4. Add migration helper
- **Task ID**: build-migration-helper
- **Depends On**: build-confidence-partition
- **Validates**: tests/test_partitioned_confidence.py (create)
- **Assigned To**: confidence-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `ConfidenceField.migrate_to_partitioned(model_class, field_name)` classmethod
- Read all entries from single hash, determine partition key for each member, write to partitioned hashes
- Delete old single hash after successful migration
- Add dry_run parameter that reports what would happen without modifying data

### 5. Write tests
- **Task ID**: build-tests
- **Depends On**: build-confidence-partition, build-query-materialization
- **Validates**: tests/test_partitioned_confidence.py (create)
- **Assigned To**: test-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Test partitioned on_save creates correct hash key structure
- Test partitioned update_confidence writes to correct partition
- Test partitioned get_confidence reads from correct partition
- Test partition key change removes from old, adds to new
- Test unpartitioned behavior unchanged (backward compat)
- Test query materialization with partition filter reads only scoped hash
- Test QueryException when partition fields missing from query
- Test HSCAN utility with various match patterns
- Test migration helper redistributes data correctly

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: confidence-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update docs/multi-tenancy.md with hash-based field partitioning section
- Update ConfidenceField docstring with partition_by examples
- Update MkDocs API reference

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met
- Verify backward compatibility with unpartitioned models

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `black --check src/ tests/` | exit code 0 |
| Confidence tests pass | `pytest tests/test_confidence_field.py tests/test_partitioned_confidence.py -v` | exit code 0 |
| Docs build | `mkdocs build` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

No open questions -- the approach directly mirrors the established SortedField partition_by pattern, and the issue clearly defines the problem and three candidate solutions. This plan implements option 1 (partitioned hash keys) as the primary solution and option 2 (HSCAN filtered read) as a secondary utility.
