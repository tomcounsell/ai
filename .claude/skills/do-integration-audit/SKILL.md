---
name: do-integration-audit
description: "Audit how well a named feature is integrated into its host project. Checks for orphan code, dead wiring, missing tests, undocumented entry points, config gaps, and partial connections. Use when checking feature health, validating a feature shipped correctly, reviewing integration quality, or asking 'is this feature actually wired up?'. Also triggered by 'check feature integration', 'is X connected', 'validate feature', 'scan for dead wiring', or 'what's broken about this feature'."
allowed-tools: Read, Grep, Glob, Bash, Agent
argument-hint: "<feature-topic>"
---

# Feature Integration Audit

Audits how thoroughly a named feature is wired into its host project. A feature can exist in a codebase without being truly integrated — code is present but unreachable, tests exist but don't exercise real paths, documentation mentions it but entry points are missing. This audit finds the gaps between "code exists" and "feature works end-to-end."

Takes a feature topic as its argument and maps every integration surface: entry points, imports, tests, docs, config, and error handling. Produces a severity-grouped findings report and pauses for human review.

## What this skill does

1. Discovers all code, config, docs, and tests related to the given feature topic
2. Maps the feature's integration surfaces: how it's entered, imported, configured, tested, and documented
3. Runs 12 semantic checks against each integration surface
4. Produces a structured findings report organized by severity (CRITICAL, WARNING, INFO)
5. Pauses for discussion — no auto-fix, findings only

## Invocation

```
/do-integration-audit <feature-topic> [--path <dir>] [--severity critical|warning|info]
```

- `feature-topic`: The feature to audit (e.g., "authentication", "search", "notifications", "billing"). Required.
- `--path`: Directory to scope the audit. Defaults to project root.
- `--severity`: Minimum severity to include in the report. Default: show all.

## Quick start

1. **Discover**: Search for all files related to the feature topic — source code, tests, docs, config, migrations, routes, CLI commands. Use the feature name, synonyms, and related terms. Cast a wide net.
2. **Map surfaces**: For each discovered file, classify it as one of: implementation, entry point, test, documentation, configuration, or migration.
3. **Check**: Run each of the 12 audit checks against the feature's integration map.
4. **Filter**: If `--severity` is set, exclude findings below the threshold.
5. **Report**: Present findings grouped by severity.
6. **Pause**: Wait for human review — never modify source files.

### Step 1: Discovery strategy

The feature topic is a semantic label, not a file path. Discovery requires creative search:

1. **Direct search**: Grep for the feature name and common synonyms in file names, class names, function names, comments, and docstrings.
2. **Import tracing**: From discovered implementation files, trace what imports them — this reveals whether the feature is actually reachable.
3. **Route/endpoint scan**: Search for URL routes, CLI commands, event handlers, or menu items that expose the feature to users.
4. **Config scan**: Search for environment variables, settings keys, feature flags, or config files that control the feature.
5. **Test scan**: Search for test files that reference the feature — distinguish unit tests (isolated) from integration tests (exercising real wiring).
6. **Doc scan**: Search docs, READMEs, and changelogs for mentions of the feature.

If discovery finds fewer than 3 files, the feature may not exist or the topic name may be wrong. Confirm with the user before proceeding.

### Step 2: Surface classification

For each discovered file, assign one or more surface types:

| Surface | What it means | Examples |
|---------|--------------|---------|
| **Implementation** | Core feature logic | Models, services, utilities, handlers |
| **Entry point** | How users/systems reach the feature | Routes, CLI commands, event listeners, cron jobs, API endpoints |
| **Test** | Verification of feature behavior | Unit tests, integration tests, e2e tests |
| **Documentation** | User-facing explanation | README sections, doc pages, API docs, inline help text |
| **Configuration** | Settings that control the feature | Env vars, config files, feature flags, defaults |
| **Migration** | Schema or data changes the feature depends on | DB migrations, data scripts, config migrations |

---

## Audit Checks

### 1. orphan-code
Feature implementation exists but nothing in the main application imports or references it. The code is present but unreachable — it ships but never runs. This often happens when a feature is built in isolation and the wiring step is forgotten, or when the entry point that used to call it was removed.
**Severity**: CRITICAL
**Verification**: Trace imports from discovered implementation files. If no entry point or application code imports the feature module, it's orphaned.

### 2. dead-entrypoint
An entry point (route, CLI command, menu item, event handler) is defined for the feature but it's disconnected from the application's routing/dispatch. The endpoint exists in code but users can't reach it — it's not registered in the router, not listed in the CLI group, or not subscribed to the event bus.
**Severity**: CRITICAL
**Verification**: For each entry point, verify it's registered in the application's wiring (URL conf, CLI group, event bus, cron scheduler). Check that the path from app startup to the entry point is unbroken.

### 3. partial-wiring
The feature is connected but incompletely — some code paths work while others are stubbed, TODO'd, or raise `NotImplementedError`. This creates a feature that appears functional but fails on specific inputs or paths. Look for `TODO`, `FIXME`, `NotImplementedError`, `pass` in non-trivial methods, or commented-out blocks within feature code.
**Severity**: WARNING
**Verification**: Scan feature implementation files for stub indicators. Each stub found is a partial-wiring finding. Ignore stubs in test files.

### 4. missing-integration-test
The feature has implementation and possibly unit tests, but no test exercises the actual integration path — entry point through to side effect. Unit tests with mocked dependencies don't count here because they can't catch wiring failures. The question is: does any test actually call the feature the way a real user or system would?
**Severity**: WARNING
**Verification**: Check test files for the feature. Classify each as unit (mocked dependencies, isolated) or integration (real wiring, real dependencies or fixtures). If zero integration tests exist, flag it.

### 5. undocumented-entry
The feature works but has no user-facing documentation explaining how to access or use it. Users can't discover what they don't know exists. Check for: README mentions, doc pages, API docs, help text in CLI commands, or docstrings on public entry points.
**Severity**: WARNING
**Verification**: Cross-reference entry points against documentation surfaces. Each entry point that has no corresponding documentation is a finding.

### 6. config-gap
The feature reads configuration (env vars, settings, flags) that isn't documented, has no defaults, or isn't present in example/template config files. This means the feature works on the developer's machine but fails on fresh deploys because the config was never propagated.
**Severity**: WARNING
**Verification**: Scan feature code for config reads (env var lookups, settings access, flag checks). For each, verify: (a) it has a sensible default or is documented as required, and (b) it appears in example config files or setup docs.

### 7. stale-reference
Other parts of the codebase reference the feature by old names, removed APIs, deprecated patterns, or incorrect paths. This happens when a feature is refactored but callers aren't updated — the references compile or import but produce wrong results at runtime.
**Severity**: WARNING
**Verification**: Search for references to the feature outside its own directory. Check whether those references use current API surfaces (function names, class names, import paths). Flag references to names that no longer exist in the feature's implementation.

### 8. inconsistent-interface
The feature exposes its functionality through multiple integration points (API, CLI, SDK, internal calls) but each uses different naming, argument patterns, or return types. Callers must learn a different dialect for each surface, which increases cognitive load and produces bugs when developers assume consistency that isn't there. A well-integrated feature presents a uniform vocabulary regardless of how it's accessed.
**Severity**: WARNING
**Verification**: Compare the feature's public interfaces across surfaces. Check that: (a) the same operation uses the same name everywhere (e.g., not `create_user` in the API but `add_user` in the CLI), (b) argument names and ordering are consistent, (c) return types follow the same shape. Flag divergences.

### 9. non-reusable-interface
The feature's public interface is tightly coupled to a specific caller or integration context, making it hard to use from new integration points. Signs include: hardcoded assumptions about the caller (e.g., request objects in a function that should be framework-agnostic), mixed business logic and transport concerns in the same function, or missing a clean service/core layer that multiple entry points could share.
**Severity**: WARNING
**Verification**: Check whether the feature has a clean internal API (service layer, core module, or similar) that entry points delegate to. If every entry point reimplements the logic or the core logic imports transport/framework types, the interface isn't reusable.

### 10. internal-naming-drift
Names within the feature (functions, classes, variables, config keys, database columns) use inconsistent terms for the same concept. Example: the model says `subscription`, the service says `plan`, and the template says `membership` — all meaning the same thing. This makes the feature harder to search, debug, and extend because the same concept has multiple identifiers.
**Severity**: WARNING
**Verification**: Catalog the key domain concepts in the feature. For each, check that the same term is used in models, services, entry points, tests, and docs. Flag cases where synonyms are used for the same concept within the feature boundary.

### 11. external-naming-drift
Other systems that reference this feature use inconsistent or ambiguous names for it. Foreign keys, config prefixes, import aliases, log messages, and API paths should all use the same term. When a billing system calls it `subscription_id` but the user system calls it `plan_id` and the admin panel calls it `membership_ref`, developers can't tell these all point to the same feature.
**Severity**: WARNING
**Verification**: Search outside the feature's directory for references to it — foreign keys, import aliases, config prefixes, log messages, URL path segments. Check that these external references use a consistent name derived from the feature's own terminology. Flag divergences, especially in database columns and foreign keys where inconsistency becomes permanent.

### 12. missing-error-boundary
The feature integrates with the application but has no error handling at the boundary. Exceptions propagate from feature internals to the caller, potentially crashing the parent context (a web request, a CLI command, a background job). Well-integrated features catch their own errors at the boundary and return structured errors or degrade gracefully.
**Severity**: INFO
**Verification**: Examine each entry point for try/except, error handlers, or framework-level error boundaries. If the entry point can raise unhandled exceptions from feature internals, flag it. Ignore cases where the framework provides a global error handler.

---

## Output Format

Present findings using this structure. Adapt the content to actual findings.

```
## Integration Audit Report: {feature-topic}

### Discovery
Found N files across M surfaces:
- Implementation: N files
- Entry points: N (routes: N, CLI: N, events: N)
- Tests: N (unit: N, integration: N)
- Documentation: N files
- Configuration: N keys
- Migrations: N files

### Integration Map
| Surface | File | Status |
|---------|------|--------|
| entry point | path/to/route.py:45 | connected |
| entry point | path/to/cli.py:12 | dead |
| implementation | path/to/service.py | orphaned |

### Findings

#### CRITICAL
- [check-name] Item: specific finding with evidence

#### WARNING
- [check-name] Item: specific finding with evidence

#### INFO
- [check-name] Item: specific finding with evidence

### Summary
PASS: N  WARN: N  FAIL: N
```

### Example: Authentication feature in a Django project

```
## Integration Audit Report: authentication

### Discovery
Found 14 files across 5 surfaces:
- Implementation: 4 files (models.py, backends.py, middleware.py, utils.py)
- Entry points: 3 (routes: login, logout, password-reset)
- Tests: 5 (unit: 4, integration: 1)
- Documentation: 1 file (docs/auth.md)
- Configuration: 3 keys (AUTH_BACKEND, SESSION_TTL, PASSWORD_MIN_LENGTH)

### Integration Map
| Surface | File | Status |
|---------|------|--------|
| entry point | urls.py:12 → views.login | connected |
| entry point | urls.py:13 → views.logout | connected |
| entry point | urls.py:14 → views.password_reset | dead — view exists but url not in root urlconf |
| implementation | auth/middleware.py | connected via settings.MIDDLEWARE |
| implementation | auth/backends.py | orphaned — AUTH_BACKEND default points to django.contrib.auth, not this |
| config | AUTH_BACKEND | not in .env.example, no default in settings.py |

### Findings

#### CRITICAL
- [orphan-code] auth/backends.py: Custom auth backend is never used — AUTH_BACKEND defaults to django.contrib.auth.backends.ModelBackend, not auth.backends.TokenBackend. The custom backend ships but never runs.
- [dead-entrypoint] password-reset: View function exists at auth/views.py:89 but the URL pattern at urls.py:14 is not included in the root urlconf (myproject/urls.py only includes login and logout paths).

#### WARNING
- [config-gap] AUTH_BACKEND: Read in settings.py:34 via os.environ.get() with no default. Missing from .env.example and deployment docs. Feature silently falls back to Django default, masking the missing config.
- [missing-integration-test] Only 1 of 5 tests exercises real auth flow (test_login_flow). No integration test for logout or password-reset paths. The 4 unit tests mock the backend, so they can't catch wiring issues like the orphaned backend above.
- [undocumented-entry] password-reset: No mention in docs/auth.md. Users don't know this endpoint exists.

#### WARNING (continued)
- [inconsistent-interface] authenticate: API endpoint accepts `email` + `password`, but CLI login command accepts `username` + `pass`. Same operation, different argument names.
- [internal-naming-drift] auth/backends.py calls it `token`, auth/middleware.py calls it `session_key`, auth/utils.py calls it `credential` — all refer to the same session identifier.
- [external-naming-drift] User model FK is `auth_backend_id` in users table, but `login_provider_id` in the audit_log table — both reference auth/backends.

#### INFO
- [missing-error-boundary] views.login:23: AuthenticationError from backends.py propagates as unhandled 500. Should return 401 with error message.

### Summary
PASS: 28  WARN: 6  FAIL: 2
```

### Example: Search feature in a FastAPI project

```
## Integration Audit Report: search

### Discovery
Found 8 files across 4 surfaces:
- Implementation: 3 files (search/engine.py, search/indexer.py, search/models.py)
- Entry points: 2 (routes: /api/search, CLI: reindex command)
- Tests: 2 (unit: 2, integration: 0)
- Documentation: 0 files
- Configuration: 4 keys (SEARCH_ENGINE_URL, SEARCH_INDEX_NAME, SEARCH_BATCH_SIZE, SEARCH_TIMEOUT)

### Findings

#### CRITICAL
(none)

#### WARNING
- [missing-integration-test] Zero integration tests. Both test files mock the search engine client. No test verifies that the /api/search endpoint returns results from a real (or fixture) index. Wiring between the route handler and search.engine module is untested.
- [undocumented-entry] /api/search: No API docs, no README mention, no docstring on the route handler. Endpoint is discoverable only by reading the code.
- [undocumented-entry] CLI reindex: Command registered in cli.py:45 but not mentioned in README or --help description is empty string.
- [config-gap] SEARCH_TIMEOUT: Used in engine.py:12 but missing from .env.example. Other 3 search config keys are documented.
- [partial-wiring] search/indexer.py:67: `async def reindex_incremental` raises NotImplementedError. CLI reindex command only calls full reindex, but the incremental path is referenced in 2 comments as the intended production path.
- [stale-reference] app/recommendations.py:34: imports `from search.engine import FullTextSearch` — class was renamed to `SearchEngine` in search/engine.py 3 months ago. Import succeeds because old name exists as a deprecated alias, but logs a deprecation warning on every call.

- [non-reusable-interface] search/engine.py:SearchEngine.__init__ takes a FastAPI `Request` object to extract auth headers. Any non-HTTP caller (CLI reindex, background job) must fabricate a fake request. Should accept a credentials/config object instead.
- [external-naming-drift] recommendations.py calls it `FullTextSearch`, analytics.py calls it `SearchClient`, both import from search/engine.py — only `SearchEngine` is the current class name.

#### INFO
- [missing-error-boundary] /api/search route handler: ConnectionError from search engine propagates as 500. Should return 503 with retry-after header.

### Summary
PASS: 22  WARN: 8  FAIL: 0
```

---

## After the Audit

Findings only. The skill never modifies source files. Next steps are decided by the human:

- Fix critical findings first (orphan code, dead entry points) — these mean the feature isn't actually working
- Address warning-level findings in a follow-up PR (missing tests, config gaps, stale references)
- Track info-level findings as improvement opportunities
- Re-run the audit after fixes to verify resolution

## Version history

- v1.0.0 (2026-04-03): Initial — 8 checks, semantic discovery, high-autonomy prompt-only approach
