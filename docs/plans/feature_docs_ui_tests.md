---
status: Planning
type: feature
appetite: Large
owner: Valor
created: 2026-03-07
tracking: https://github.com/yudame/cuttlefish/issues/125
---

# Comprehensive Feature Documentation with UI/UX Test Coverage

## Problem

The cuttlefish platform has grown to include podcast production, MCP servers, deep research, file storage, medication tracking, design system, background tasks, and AI integration patterns -- but only 4 of these features have documentation in `docs/features/`. Without comprehensive docs, we cannot systematically test. Without tests, we cannot identify gaps or regressions in user-facing flows.

**Current behavior:**
- Only 4 feature docs exist: `deep-research-orchestrator.md`, `file-storage-service.md`, `local-audio-worker.md`, `podcast-services.md`
- No `docs/features/README.md` index exists
- Existing E2E tests are mostly stubs or mock examples (`test_e2e_basic.py` has placeholder tests)
- HTMX interaction tests exist but depend on `browser-use` which may not be installed
- No systematic coverage of all public routes
- No UX flow validation tests

**Desired outcome:**
- Every major feature has a `docs/features/*.md` entry
- A `docs/features/README.md` index links all feature docs
- Django `TestCase`-based UI tests cover all public routes (no browser dependency)
- Playwright-based E2E tests cover critical user flows
- HTMX interaction patterns are tested via Django test client (header-based)
- A gap analysis identifies missing features or broken flows

## Appetite

**Size:** Large

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1-2 (scope alignment on which features to document)
- Review rounds: 1 (code review of tests and docs)

This is primarily a documentation and test-writing task. The code changes are additive (new files), not modifications to existing functionality. The bottleneck is thoroughness, not complexity.

## Prerequisites

No prerequisites -- this work has no external dependencies. It uses Django's built-in test client for UI tests and optionally Playwright for E2E tests. The existing test infrastructure in `apps/public/tests/` provides patterns to follow.

## Solution

### Key Elements

- **Feature Documentation Suite**: Create `docs/features/*.md` entries for all 9 major features, plus a README index
- **Route Coverage Tests**: Django `TestCase` tests that hit every public URL and verify status codes, template rendering, and basic content
- **HTMX Interaction Tests**: Tests that verify HTMX partial responses work correctly using Django test client with `HTTP_HX_REQUEST` headers
- **Playwright E2E Tests**: Browser-based tests for critical user flows (podcast creation, medication tracking, authentication)
- **Gap Analysis**: Test failures that identify concrete missing features or broken flows

### Flow

**Documentation** → Write feature docs from codebase analysis → Create README index → Cross-reference with URL routes

**UI Tests** → Enumerate all URL patterns → Write status code tests → Add template content assertions → Test HTMX partials

**UX Tests** → Map critical user flows → Write Playwright tests → Validate draft vs published visibility → Test error states

### Technical Approach

- Feature docs are written by reading existing code, services, views, and templates -- no new functionality needed
- UI tests use Django's `TestCase` and `Client` (zero external dependencies) for route coverage
- HTMX tests use `self.client.get(url, HTTP_HX_REQUEST="true")` to simulate HTMX requests
- Playwright E2E tests extend the existing `apps/public/tests/test_e2e_production_pages.py` pattern
- All tests use `DJANGO_SETTINGS_MODULE=settings pytest` as specified in CLAUDE.md
- Tests that require authentication use Django's `self.client.login()` or factory-created users

### Routes to Cover

**Public (unauthenticated):**
- `/` (home)
- `/pricing/`
- `/privacy/`
- `/terms/`
- `/health/`
- `/health/deep/`
- `/podcast/` (list)
- `/podcast/<slug>/` (detail)
- `/podcast/<slug>/feed.xml` (RSS)
- `/podcast/<slug>/<episode>/` (episode detail)
- `/mcp/creative-juices/` (landing)
- `/mcp/cto-tools/` (landing)
- `/docs/` (index)

**Authenticated:**
- `/dashboard/`
- `/account/settings`
- `/account/login`
- `/drugs/` (medication dashboard)
- `/drugs/medication/add/`
- `/drugs/schedule/`
- `/podcast/<slug>/new/` (create episode)
- `/podcast/<slug>/edit/`
- `/podcast/<slug>/<episode>/edit/<step>/` (workflow)
- `/admin/`

**Teams:**
- `/team/`
- `/team/create/`
- `/team/<slug>/`
- `/team/<slug>/edit/`
- `/team/<slug>/delete/`

## Rabbit Holes

- **Testing external API calls in service layer**: The service layer tests already exist (`test_ai_tools/`). This plan covers UI/UX tests, NOT service-level integration tests. Do not re-test service internals.
- **Browser-use framework**: The existing `browser-use` integration is complex and may not be installed. Use Playwright directly for E2E tests, not browser-use.
- **Testing MCP server protocol**: MCP SSE/HTTP endpoints have their own test suite (`test_mcp_*`). Document MCP servers but don't write new protocol-level tests.
- **Testing background task execution**: Background tasks run via Django 6.0's task framework. Document the system but test only the UI triggers, not the task execution itself.
- **Exhaustive accessibility audit**: Basic accessibility checks are in scope. A full WCAG 2.1 audit is a separate project.

## Risks

### Risk 1: Some routes may require specific database state
**Impact:** Tests may fail with 404/500 if podcasts, episodes, or medications don't exist in the test database
**Mitigation:** Use factory functions from `apps/common/tests/factories.py` and `apps/podcast/tests/` to create required database records in test setUp

### Risk 2: Playwright tests may be flaky in CI
**Impact:** E2E tests that depend on a running server may fail intermittently
**Mitigation:** Use Django's `LiveServerTestCase` for Playwright tests. Mark pure Playwright tests with `@pytest.mark.e2e` so they can be run separately

### Risk 3: Large number of new test files may slow test suite
**Impact:** Full test suite takes longer to run
**Mitigation:** Organize tests into focused modules. Use `pytest -m "not e2e"` for fast runs

## Race Conditions

No race conditions identified -- all operations are documentation writes and test assertions, which are synchronous and single-threaded.

## No-Gos (Out of Scope)

- **Service-level integration tests**: Already covered by existing test suites in `apps/podcast/tests/test_ai_tools/`
- **MCP protocol-level tests**: Already covered by `test_mcp_*.py` files
- **Full WCAG 2.1 accessibility audit**: Separate project
- **Performance/load testing**: Separate concern
- **Writing new features to fill gaps**: This plan identifies gaps; fixing them is separate work
- **Testing external third-party APIs**: No mocking of Anthropic, OpenAI, etc.
- **Mobile app testing**: Web-only

## Update System

No update system changes required -- this work is purely documentation and test files within the repository. No new dependencies, config files, or migration steps needed.

## Agent Integration

No agent integration required -- this is a documentation and testing task. No new MCP tools, bridge changes, or `.mcp.json` modifications needed. The feature docs will be useful context for the agent's future interactions but require no code integration.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/README.md` index table linking all feature docs
- [ ] Create `docs/features/podcast-production-pipeline.md` -- Full 12-phase workflow
- [ ] Create `docs/features/mcp-servers.md` -- Creative Juices and CTO Tools
- [ ] Update `docs/features/deep-research-orchestrator.md` -- Verify current accuracy
- [ ] Update `docs/features/file-storage-service.md` -- Verify current accuracy
- [ ] Create `docs/features/frontend-design-system.md` -- HTMX, Tailwind v4, brand.css
- [ ] Create `docs/features/background-tasks.md` -- Django 6.0 task framework
- [ ] Create `docs/features/medication-tracker.md` -- Drugs app features
- [ ] Create `docs/features/authentication.md` -- Login, permissions, roles
- [ ] Create `docs/features/ai-integration-patterns.md` -- PydanticAI, named tools, adapters

### Inline Documentation
- [ ] Code comments on non-obvious test patterns (HTMX header simulation, factory usage)

## Success Criteria

- [ ] Every major feature has a `docs/features/*.md` entry (9+ docs)
- [ ] `docs/features/README.md` index exists and links all feature docs
- [ ] UI tests cover all public-facing routes (13+ public, 10+ authenticated)
- [ ] HTMX interaction tests verify partial response patterns
- [ ] Playwright E2E tests cover podcast list/detail, medication dashboard, and auth flows
- [ ] All new tests pass with `DJANGO_SETTINGS_MODULE=settings pytest`
- [ ] Gap analysis section in PR description lists concrete missing features or broken flows
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (feature-docs)**
  - Name: docs-writer
  - Role: Write all feature documentation files and README index
  - Agent Type: documentarian
  - Resume: true

- **Builder (ui-tests)**
  - Name: ui-test-writer
  - Role: Write Django TestCase-based route coverage and HTMX interaction tests
  - Agent Type: test-engineer
  - Resume: true

- **Builder (e2e-tests)**
  - Name: e2e-test-writer
  - Role: Write Playwright-based E2E tests for critical user flows
  - Agent Type: frontend-tester
  - Resume: true

- **Validator (all)**
  - Name: test-validator
  - Role: Run all tests and verify documentation completeness
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Write Feature Documentation
- **Task ID**: build-feature-docs
- **Depends On**: none
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: true
- Create `docs/features/README.md` with index table
- Create `docs/features/podcast-production-pipeline.md` from `apps/podcast/services/`, `tasks.py`, and workflow views
- Create `docs/features/mcp-servers.md` from `apps/ai/` views and MCP server code
- Create `docs/features/frontend-design-system.md` from templates, `static/css/`, and component views
- Create `docs/features/background-tasks.md` from `apps/podcast/tasks.py` and Django task config
- Create `docs/features/medication-tracker.md` from `apps/drugs/` models, views, templates
- Create `docs/features/authentication.md` from `apps/public/urls.py` auth routes and views
- Create `docs/features/ai-integration-patterns.md` from `apps/podcast/services/` AI tool patterns
- Review and update existing docs for accuracy

### 2. Write UI Route Coverage Tests
- **Task ID**: build-ui-tests
- **Depends On**: none
- **Assigned To**: ui-test-writer
- **Agent Type**: test-engineer
- **Parallel**: true
- Create `apps/public/tests/test_route_coverage.py` with tests for all public routes
- Create `apps/podcast/tests/test_route_coverage.py` with tests for podcast routes
- Create `apps/drugs/tests/test_route_coverage.py` with tests for medication routes
- Create `apps/ai/tests/test_route_coverage.py` with tests for MCP and AI routes
- Test status codes (200, 302 for auth-required), template usage, and basic content
- Use factories to create required database records (Podcast, Episode, Medication, User)

### 3. Write HTMX Interaction Tests
- **Task ID**: build-htmx-tests
- **Depends On**: none
- **Assigned To**: ui-test-writer
- **Agent Type**: test-engineer
- **Parallel**: true
- Create `apps/public/tests/test_htmx_route_tests.py` for HTMX partial responses
- Test that HTMX requests return partial HTML (no full page wrapper)
- Test OOB swap patterns (toasts, alerts, modals)
- Test medication add/edit/delete HTMX flows
- Verify `HX-Trigger` headers in responses where expected

### 4. Write Playwright E2E Tests
- **Task ID**: build-e2e-tests
- **Depends On**: none
- **Assigned To**: e2e-test-writer
- **Agent Type**: frontend-tester
- **Parallel**: true
- Create `apps/public/tests/test_e2e_user_flows.py` with critical flow tests
- Test: homepage loads and navigation works
- Test: login flow (form submission, redirect to dashboard)
- Test: podcast list and episode detail pages render correctly
- Test: medication dashboard loads for authenticated users
- Test: design elements / style guide page renders
- Test: draft episode visibility (owner/staff only)
- Mark all E2E tests with `@pytest.mark.e2e`

### 5. Validate All Tests Pass
- **Task ID**: validate-tests
- **Depends On**: build-ui-tests, build-htmx-tests, build-e2e-tests
- **Assigned To**: test-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `DJANGO_SETTINGS_MODULE=settings pytest apps/public/tests/test_route_coverage.py apps/podcast/tests/test_route_coverage.py apps/drugs/tests/test_route_coverage.py apps/ai/tests/test_route_coverage.py -v`
- Run `DJANGO_SETTINGS_MODULE=settings pytest apps/public/tests/test_htmx_route_tests.py -v`
- Verify all tests pass
- Report any failures as gap analysis findings

### 6. Validate Documentation Completeness
- **Task ID**: validate-docs
- **Depends On**: build-feature-docs
- **Assigned To**: test-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all 9+ feature docs exist in `docs/features/`
- Verify `docs/features/README.md` index links all docs
- Verify each doc has: title, architecture/overview, usage, and key details
- Check cross-references between docs are accurate

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: validate-tests, validate-docs
- **Assigned To**: test-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `DJANGO_SETTINGS_MODULE=settings pytest -v`
- Verify all success criteria met
- Generate gap analysis report listing missing features or broken flows found during testing
- Verify code quality: `uv run pre-commit run --all-files`

## Validation Commands

- `DJANGO_SETTINGS_MODULE=settings pytest apps/public/tests/test_route_coverage.py -v` -- Validates public route coverage
- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_route_coverage.py -v` -- Validates podcast route coverage
- `DJANGO_SETTINGS_MODULE=settings pytest apps/drugs/tests/test_route_coverage.py -v` -- Validates medication route coverage
- `DJANGO_SETTINGS_MODULE=settings pytest apps/ai/tests/test_route_coverage.py -v` -- Validates AI/MCP route coverage
- `ls docs/features/*.md | wc -l` -- Should be 12+ (4 existing + 8 new)
- `test -f docs/features/README.md && echo "OK"` -- Validates index exists
- `uv run pre-commit run --all-files` -- Code quality check

---

## Open Questions

1. **Podcast fixture data**: Should tests create podcast/episode records via factories, or is there a fixture set already available? I see `apps/drugs/fixtures/` exists -- are there podcast fixtures too?
2. **E2E test environment**: Should Playwright E2E tests target `localhost` (via `LiveServerTestCase`) or the production URL (`https://ai.yuda.me`)? The existing `test_e2e_production_pages.py` targets production.
3. **Draft visibility rules**: What are the exact rules for draft episode visibility? Is it owner-only, staff-only, or both? This affects the authentication test assertions.
4. **Design elements page**: The issue mentions `/design-elements/` but the URL config shows `/ui/examples/` -- which is the correct path to test?
