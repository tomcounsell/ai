---
status: docs_complete
type: bug
appetite: Small
owner: Valor Engels
created: 2026-08-13
tracking: https://github.com/tomcounsell/ai/issues/2719
last_comment_id: 5245622050
revision_applied: true
revision_applied_at: 2026-08-13T12:39:23Z
---

# Single Jinja Filter Registrar for the Dashboard

## Problem

The dashboard's production Jinja filters (`format_timestamp`, `format_duration`,
`format_interval_filter`, `format_relative`, `freshness_age`, `usd`) are registered
imperatively inside `ui/app.py::create_app` (lines 149-154). The render tests do not
boot FastAPI — they build bare `jinja2.Environment` objects in fixtures and **hand-copy**
a subset of those registrations, with simplified stand-in implementations.

There are three independent lists of filters today:

| Site | Filters registered | Semantics |
|---|---|---|
| `ui/app.py::create_app:149-154` | all 6 | production |
| `tests/unit/test_session_modal_liveness_render.py:28-54` | 3 (`format_timestamp`, `format_duration`, `freshness_age`) | hand-rolled stand-ins |
| `tests/unit/test_per_project_modal.py:29-39` | 4 (`format_timestamp`, `format_duration`, `format_interval_filter`, `format_relative`) | one-line lambdas |

Nothing keeps them in sync. The only coupling is a comment (`# Mirror the filters
registered in ui/app.py::create_app`), and comments do not fail builds.

Hotfix `96b0f65dd` ("Dashboard: round USD costs up to the nearest cent", landed on `main`
as a `No-issue:` hotfix) added `_filter_usd`, registered it in `create_app`, and switched
four cost display sites to `{{ ... | usd }}`. The liveness fixture was never updated, so
Jinja raises `TemplateRuntimeError: No filter named 'usd' found.` at render time.

**Current behavior:**

```
tests/unit/test_session_modal_liveness_render.py::TestJobRowLivenessSignals::test_cost_still_renders_alongside_the_freshness_chip
tests/unit/test_session_modal_liveness_render.py::TestModalMetadataSections::test_token_cost_strip_renders_when_present
E   jinja2.exceptions.TemplateRuntimeError: No filter named 'usd' found.
```

Both error before reaching any assertion, so the cost-rendering behavior they exist to
protect is currently unverified. `ui/templates/_partials/analytics_stats.html` also uses
`| usd` (lines 30, 34) and has zero render coverage anywhere under `tests/`.

**Desired outcome:** one source of truth for filter registration, shared by production and
every test env; a guard that fails when any template references a filter the production
environment does not provide; and both named node IDs green.

## Freshness Check

**Baseline commit:** `b4a2284261de8e357dd6340361242c5310285f54`
**Issue filed at:** 2026-08-10T20:17:26Z
**Disposition:** Minor drift

**File:line references re-verified:**

- `ui/app.py:154` — issue claims `templates.env.filters["usd"] = _filter_usd`. **Still holds**, exact line.
- `ui/app.py:118` — `_filter_usd` definition. **Still holds.** `_filter_usd(1.2345) == "$1.24"`, `_filter_usd(3.75) == "$3.75"` (verified by execution).
- `tests/unit/test_session_modal_liveness_render.py:28-55` — hand-copied fixture. **Still holds**, now precisely lines 28-54.
- `ui/templates/_partials/jobs_table.html:133` — `{{ job.total_cost_usd | usd }}`. **Drifted to line 146** (commit `13d058848` restructured the Jobs table from 7 columns to 5). The `| usd` site survived the restructure intact; only its line number moved.
- `ui/templates/_partials/session_modal_content.html:189` — `{{ pipeline.total_cost_usd | usd }}`. **Still holds**, exact line.
- `ui/templates/_partials/analytics_stats.html:30,34` — two `| usd` sites, zero test coverage. **Still holds**, exact lines.
- `tests/unit/test_per_project_modal.py:35-38` — second hand-rolled env (from the issue's first comment). **Still holds**, lines 29-39 for the whole fixture.

**Cited sibling issues/PRs re-checked:**

- **#2720** — filed 2 minutes after #2719 by a concurrent triage agent, same defect.
  **Closed 2026-08-11T07:59:46Z as a duplicate of #2719**, consolidating here. Its unique
  content (per-template filter-demand table, `analytics_stats.html` coverage gap, and the
  stale-assertion correction) is carried into this plan.
- **#2334** — nightly regression detector that surfaced the failure. Informational only.

**Commits on main since issue was filed (touching referenced files):**

- `13d058848` fix(dashboard): collapse Jobs table from 7 columns to 5 — **partially relevant.**
  Restructured `jobs_table.html` and `session_row.html`. Moved the `| usd` site from line 133
  to 146 but did not change filter demand. Does not change the root cause.
- `bb2edf3ce` fix(dashboard): expose Rooms and other Job/Session fields in dashboard.json —
  **irrelevant.** Touched `ui/app.py` JSON serializers only, not the filter block.
- `e48607be3` fix(dashboard): cap dashboard session age even for active-status sessions —
  **irrelevant.** No template or filter change.

**Active plans in `docs/plans/` overlapping this area:** none. No open plan touches `ui/` or
the dashboard render tests.

**Bug still reproduces:** yes. Confirmed by direct execution against baseline — rendering
`_partials/jobs_table.html` and `_partials/session_modal_content.html` through the fixture's
filter set raises `No filter named 'usd' found.`

**Notes:** The only drift is the `jobs_table.html:133 → :146` line move. Use line 146 in any
build-time reference. Critically, the issue's *second* comment already flagged that
registering `usd` alone does **not** turn both tests green — that correction is confirmed
and expanded in Spike Results below, where a **third** affected assertion was found that
neither issue caught.

## Prior Art

- **Issue #2720** — "Dashboard render tests fail: `usd` Jinja filter added to templates but
  not to the hand-rolled test render envs" — duplicate of #2719, closed 2026-08-11. Same root
  cause, same rejection of the narrow one-line fix. Its investigation found the stale
  `$1.2345` assertion; carried forward here.
- No merged PRs found touching Jinja filter registration or template render-test wiring.
  Searches: `gh issue list --state closed --search "jinja filter register dashboard template"`,
  `gh pr list --state merged --search "jinja filter template render test"`.

There have been **no prior fix attempts** — this is the first pass at the defect, so there is
no "Why Previous Fixes Failed" section.

## Research

**Queries used:**

- `jinja2 detect unregistered filters in templates static analysis env.parse nodes.Filter`

**Key findings:**

- An unregistered filter raises `TemplateRuntimeError` only at **render** time, never at
  import or collection time — which is exactly why this class of bug reaches `main`. Static
  detection requires parsing.
  Source: [Filters | pallets/jinja | DeepWiki](https://deepwiki.com/pallets/jinja/4-filters)
- `Environment.parse(source)` returns an AST whose nodes support `find_all()`; each node
  carries `lineno`, so a lint can report the offending template and line.
  Source: [Extensions — Jinja Documentation (3.1.x)](https://jinja.palletsprojects.com/en/stable/extensions/)
- Custom filters live in the `Environment.filters` dict, so the guard is a set-difference of
  parsed filter names against `env.filters` on an environment that has the production
  registrar applied.
  Source: [Custom Filters | pallets/jinja | DeepWiki](https://deepwiki.com/pallets/jinja/4.2-custom-filters)
- **`nodes.FilterBlock` must be included alongside `nodes.Filter`** — the `{% filter %}` tag
  applies a filter to a whole block and is a distinct node type. Scanning only `nodes.Filter`
  leaves a hole. This directly shaped the guard implementation below; the in-repo spike had
  originally scanned `nodes.Filter` alone.
  Source: [jinja/src/jinja2/filters.py](https://github.com/pallets/jinja/blob/main/src/jinja2/filters.py)
- **Known blind spot:** dynamic filter application via `|attr(...)` or `map('name')` passes
  the filter name as a runtime string and is invisible to AST scanning. Confirmed by
  inspection that no dashboard template uses either idiom, so the guard is complete for the
  current template set; documented as a limitation rather than solved.

Sources: [Filters | pallets/jinja](https://deepwiki.com/pallets/jinja/4-filters) ·
[Custom Filters | pallets/jinja](https://deepwiki.com/pallets/jinja/4.2-custom-filters) ·
[Jinja Extensions docs](https://jinja.palletsprojects.com/en/stable/extensions/) ·
[jinja2 filters.py source](https://github.com/pallets/jinja/blob/main/src/jinja2/filters.py)

## Spike Results

All four spikes ran against baseline `b4a22842` by direct execution in the repo venv.

### spike-1: Does a template AST scan cleanly detect filter demand?

- **Assumption**: "Walking `env.parse()` output can enumerate every filter each template
  demands, so a guard test is cheap."
- **Method**: prototype (in-repo script, `./.venv/bin/python`)
- **Finding**: Confirmed. Parsing all `ui/templates/**/*.html` and collecting
  `nodes.Filter` names yields exactly 14 distinct names; subtracting Jinja built-ins leaves
  precisely the 6 production filters and **zero unknowns**. Per-template demand:

  | Template | Production filters demanded |
  |---|---|
  | `_partials/jobs_table.html` | `format_duration`, `format_timestamp`, `freshness_age`, `usd` |
  | `_partials/session_modal_content.html` | `format_duration`, `format_timestamp`, `usd` |
  | `_partials/session_row.html` | `format_duration`, `format_timestamp`, `freshness_age` |
  | `_partials/analytics_stats.html` | `usd` |
  | `reflections/_macros.html` | `format_duration`, `format_interval_filter`, `format_relative`, `format_timestamp` |

  Non-production names seen are all Jinja built-ins: `dictsort`, `format`, `int`, `join`,
  `length`, `max`, `select`, `tojson`.
- **Confidence**: high
- **Impact on plan**: The guard test (open question 2) is confirmed cheap and viable — it is
  ~15 lines and runs in well under a second. It goes in scope. Web research added
  `nodes.FilterBlock` to the node types scanned.

### spike-2: Does swapping in the production registrar break existing assertions?

- **Assumption**: "The fixture's simplified stand-ins are not load-bearing, so the real
  filters drop in cleanly." (Open question 1.)
- **Method**: prototype — rebuilt the liveness fixture env with the six production filters
  and re-rendered every scenario the test file covers.
- **Finding**: **The assumption is false. Three assertions shift, not one.**

  1. `TestModalMetadataSections::test_token_cost_strip_renders_when_present:240` asserts
     `"$1.2345" in html`. That encodes the pre-`96b0f65dd` `"%.4f"` format. Production
     `_filter_usd(1.2345)` returns `"$1.24"` (ceil-to-cent, deliberate). **Stale independent
     of the registration defect** — registering `usd` alone leaves this test red.
  2. `TestModalLivenessSection::test_renders_timestamps_via_format_filter:199` computes
     `expected = datetime.fromtimestamp(ts, tz=UTC).strftime("%H:%M")` for `ts = now - 60`
     and asserts it appears in the HTML. That mirrors the *fixture's* stand-in. Production
     `_filter_format_timestamp` humanizes: for `now - 60` it returns `"1m ago"`. Verified:
     old expectation absent, `"1m ago"` present. **This assertion was found by this plan's
     spike and appears in neither #2719 nor #2720.**
  3. `TestJobRowLivenessSignals::test_cost_still_renders_alongside_the_freshness_chip:475`
     asserts `"$3.75"`. **Survives** — `_filter_usd(3.75) == "$3.75"` is byte-identical to
     the retired `"%.2f"` output. Passes by coincidence, not design.

  `format_duration` output differs between stand-in and production (`f"{int(seconds)}s"` vs.
  `10s`/`2m`/`20m` bucketing), but no assertion in either file inspects a duration string —
  the freshness tests assert only CSS tier classes. So the duration change is inert.
- **Confidence**: high
- **Impact on plan**: Open question 1 is **resolved: the stand-ins are load-bearing for
  exactly two assertions**, both of which are stale relative to shipped production behavior
  and must be re-baselined as part of this change. The builder must not read the resulting
  red as a refactor regression and "fix" it by weakening `_filter_usd` or the registrar.

### spike-3: Is `tests/unit/test_per_project_modal.py` safe under the production registrar?

- **Assumption**: "The second hand-rolled env can adopt the shared registrar without
  assertion churn."
- **Method**: prototype — rebuilt its env (`autoescape=True`) with the production filters and
  re-rendered `reflections/_partials/modal_content.html` with a two-project run.
- **Finding**: Confirmed safe. Render succeeds, sub-row count is 2, `[ai]` / `[popoto]` /
  `badge-disabled` all present. Its assertions are structural (row counts, CSS classes), never
  formatted output, so the richer production filters change nothing observable.
- **Confidence**: high
- **Impact on plan**: This fixture is in scope for the sweep with zero assertion changes.
  Note it constructs its env with `autoescape=True` while the liveness fixture uses the
  default `autoescape=False`; the registrar must take an `Environment` and register filters
  only, never own autoescape or loader configuration.

### spike-4: Does `analytics_stats.html` render coverage cost anything?

- **Assumption**: "Adding a render test for the third `| usd` site is cheap." (Open question 3.)
- **Method**: code-read — the template is 83 lines, takes a single flat `analytics` mapping,
  and has no includes or macros.
- **Finding**: A render test needs one dict of scalar stats and asserts the two cost cards.
  There is no fixture scaffolding to build beyond what the shared registrar already provides.
- **Confidence**: high
- **Impact on plan**: Open question 3 is **resolved: in scope.** Leaving the only remaining
  uncovered `| usd` site uncovered in the very change that exists to make `| usd` safe would
  be self-defeating, and the cost is one small test.

## Data Flow

1. **Entry point (production)**: `create_app()` builds `Jinja2Templates(directory=TEMPLATES_DIR)`.
2. **Registration**: `register_template_filters(templates.env)` populates `env.filters` with
   the six named filters.
3. **Render**: a route handler calls `templates.TemplateResponse(...)`; Jinja resolves each
   `| name` token against `env.filters` at render time. An unresolved name raises
   `TemplateRuntimeError` — a 500, not a startup failure.
4. **Entry point (tests)**: a fixture builds a bare `Environment(loader=FileSystemLoader(...))`
   with its own autoescape choice, then calls the **same** `register_template_filters(env)`.
5. **Output**: identical filter semantics on both paths, so a rendered substring asserted in a
   test is the substring production emits.

The guard test is a fourth consumer: it builds a production-registered environment, parses
every template under `ui/templates/`, and asserts the set of demanded filter names minus
`env.filters` is empty.

## Architectural Impact

- **New dependencies**: none. `jinja2` is already a direct dependency; `jinja2.nodes` is part of it.
- **Interface changes**: one new public function `ui.app.register_template_filters(env)`. The
  six `_filter_*` functions stay private and unchanged. `create_app`'s behavior is identical.
- **Coupling**: net decrease. Three parallel definitions collapse to one. Tests gain an
  import-level dependency on `ui.app`, which is correct — the whole point is that the test env
  is derived from production rather than guessed at.
- **Data ownership**: unchanged. The registrar owns filter names only; loader, autoescape, and
  template directory stay with each caller.
- **Reversibility**: trivial. The change is one extracted function plus test-side call sites.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (the three open questions are resolved by spikes, not judgment calls)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. It touches only `ui/app.py` and
files under `tests/unit/`, and needs no secrets, services, or network access.

## Solution

### Key Elements

- **`ui.app.register_template_filters(env)`**: the single source of truth. Takes any
  `jinja2.Environment` and registers all six dashboard filters on it. Registers filters and
  nothing else — no loader, no autoescape, no globals.
- **`create_app` delegation**: the six imperative assignments at `ui/app.py:149-154` are
  replaced by one call against `templates.env`. Production behavior is byte-identical.
- **Test fixtures as consumers**: both `tests/unit/test_session_modal_liveness_render.py::env`
  and `tests/unit/test_per_project_modal.py::env` delete their hand-rolled filters and call the
  registrar. Each keeps its own loader and autoescape setting.
- **Re-baselined assertions**: the two assertions that encoded stand-in output shapes are
  updated to the production shapes shipped by `96b0f65dd`.
- **Filter-demand guard test**: a new test parses every template under `ui/templates/` and
  asserts every `nodes.Filter` / `nodes.FilterBlock` name resolves against a
  production-registered environment. This is the guard that makes the *class* of bug
  impossible, not just this instance.
- **No-hand-copy guard test**: the symmetric half of the same defect. A test walks
  `tests/**/*.py` and fails on any file that assigns into a Jinja `.filters[...]` dict, so a
  future fixture cannot re-open the hand-copy hole that caused #2719. Enforcement moves out of
  prose and into CI.
- **Production-shape equality assertion**: the guard env is a bare `Environment`, but
  production renders through `Jinja2Templates`. The guard asserts the two have identical
  filter-key sets, so a production-only filter added by some other mechanism trips the guard
  instead of silently re-creating the divergence one level up.
- **`analytics_stats.html` render coverage**: a small render test for the last uncovered
  `| usd` surface.

### Flow

Developer adds a new filter → registers it in `register_template_filters` → every test env
picks it up automatically → templates may use it.

Developer uses a filter in a template but forgets to register it → **the guard test fails at
CI time**, naming the template and line → never reaches `main`.

### Technical Approach

This section states **contracts**, not implementations. Placement within `ui/app.py`, helper
decomposition, and docstring phrasing are the builder's to choose; only the behavior below is
binding.

- **Registrar contract**: `register_template_filters(env: Environment) -> None` in `ui/app.py`
  takes any `jinja2.Environment` and registers exactly the six dashboard filters on it. It
  registers filters and nothing else — it must not touch loader, autoescape, globals, or
  extensions, because the two consuming fixtures differ in autoescape (spike-3). The six
  `_filter_*` functions are unchanged; `_filter_usd` semantics stay exactly as shipped by
  `96b0f65dd`.
- `create_app` calls the registrar in place of the six imperative assignments at
  `ui/app.py:149-154`. Production behavior is byte-identical.
- **Filter-demand guard contract**: a new `tests/unit/test_template_filter_registry.py`, marked
  `pytest.mark.unit` and `pytest.mark.webui` to match the existing render tests, parses every
  `ui/templates/**/*.html` against a registrar-configured environment and fails if any
  `nodes.Filter` **or** `nodes.FilterBlock` name is absent from `env.filters`. Scanning both
  node types is deliberate — `{% filter %}` is a distinct node (see Research). The failure
  message names template path, filter name, and line number. The scan must also assert it
  visited a non-zero template count and collected a non-empty filter set, so an empty sweep
  cannot pass vacuously (Risk 2).
- **Production-shape equality**: the guard env is a bare `Environment`, but production renders
  through `Jinja2Templates` (`ui/app.py:146`; imported from `fastapi.templating` at
  `ui/app.py:21`, not starlette). Assert filter-**key** equality between the two:

  ```
  prod = Jinja2Templates(directory=str(TEMPLATES_DIR))
  register_template_filters(prod.env)
  assert set(prod.env.filters) == set(guard_env.filters)
  ```

  Do **not** call `create_app()` for this — it mounts `StaticFiles` and builds every router.
  Compare keys only: verified by execution that `Jinja2Templates` differs from a bare
  `Environment` in exactly two non-filter ways (`autoescape` is a `select_autoescape` callable,
  and it injects a `url_for` global), and its extra-filter set is empty today. So the assertion
  passes now and acts as a tripwire if a future `filters.update(...)`, `Jinja2Templates(env=...)`,
  or Jinja extension introduces a production-only filter the guard would otherwise not see.
- **No-hand-copy guard contract**: the same file walks `tests/**/*.py`, skips its own filename,
  and fails on any file matching the regex `\.filters\s*\[`. Anchor on the attribute form
  (`\.filters\s*[`), **not** a bare `filters[` — a local variable named `filters` would
  false-positive. The self-exemption is required because the guard file legitimately reads
  `env.filters`. Verified against the live repo at plan time: exactly **7** matches exist today,
  all inside the two fixtures Task 2 rewires
  (`test_session_modal_liveness_render.py:51-53`, `test_per_project_modal.py:35-38`), so the
  test goes green the moment Task 2 lands and needs **no grandfather list**. This is what makes
  "never hand-copy filters" an enforced rule rather than a README note — the plan's own root
  cause was that comments do not fail builds.
- **Demonstrated-red requirement**: the two guards are validators, and a passing suite never
  proves a validator blocks. Before landing, for each guard: introduce the violation, capture
  the failing output, remove it, capture the passing output. For the filter-demand guard,
  temporarily add `{{ 1 | notafilter }}` to a template and confirm the failure names that
  template and line. For the no-hand-copy guard, temporarily add an `env.filters["x"] = ...`
  line to a scratch test file and confirm the failure names that file. Paste all four outputs
  into the PR description.
- Re-baseline the two stale assertions against production semantics:
  - `test_token_cost_strip_renders_when_present` → assert `"$1.24"` for a `1.2345` input
    (ceil-to-cent, per `96b0f65dd`), not `"$1.2345"`.
  - `test_renders_timestamps_via_format_filter` → compute the expectation by calling
    `_filter_format_timestamp(ts)` rather than hardcoding, so the test asserts *agreement with
    production* instead of re-encoding a format that can drift again. Do **not** hardcode
    `"1m ago"`: the filter calls `.astimezone()` and several branches return
    `dt.strftime("%H:%M")`, so a hardcoded expectation is local-timezone dependent. Because that
    computed expectation is self-referential (the template applies the same function, so it can
    only fail if the filter token is dropped entirely, and `"1m ago"` is a 7-char substring
    cheap to satisfy incidentally in a large HTML blob), add discriminating checks alongside it:
    assert the **raw unfiltered value is absent** (`str(int(ts)) not in html`) and keep the
    `"Last active"` structural assertion. The `now - 60` input is safe from the
    `< 60 → "just now"` boundary because the filter samples `utc_now()` strictly after `ts` is
    captured, so `diff` is always `>= 60`.
- Add `tests/unit/test_analytics_stats_render.py` that renders `_partials/analytics_stats.html`
  through a registrar-configured env with a minimal `analytics` mapping and asserts both cost
  cards render ceil-to-cent values.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No exception handlers in scope. `register_template_filters` is six dict assignments with
      no branching; the six `_filter_*` functions already handle `None` by returning `"-"` or
      `"$0.00"` and contain no `try`/`except`. No `except Exception: pass` blocks exist in
      `ui/app.py`'s filter region.

### Empty/Invalid Input Handling
- [ ] Assert `register_template_filters` registers every one of the six names, by comparing
      `set(env.filters) - set(Environment().filters)` against the expected name set. This is
      what makes "adding a new production filter without touching test code does not break
      these tests" mechanically true rather than aspirational. **This completeness test is the
      one registrar-behavior test worth writing.**
- [ ] **DROPPED — no idempotency test.** An earlier draft prescribed asserting that calling the
      registrar twice leaves `env.filters` equivalent. That is a property of
      `dict.__setitem__`, a Python language guarantee, not of this code. Testing it adds a row
      to the suite and zero information. Overwrite-on-repeat remains the intended behavior and
      is exactly what `create_app` does today; it simply needs no test.
- [ ] **RELOCATED — filter `None`-paths do not belong in the registry test.**
      `_filter_usd(None) == "$0.00"` and `_filter_format_timestamp(None) == "-"` are properties
      of the filters, not the registrar, and are unrelated to the #2719 defect. If kept, they go
      in a filters-focused test module so `test_template_filter_registry.py` retains exactly one
      job: proving the registration seam is complete and unbypassed. They are optional; the plan
      does not require them.

### Error State Rendering
- [ ] The two guard tests *are* the error-state tests for this feature. The filter-demand guard
      asserts an unregistered filter is detected statically instead of surfacing as a
      render-time 500. The no-hand-copy guard asserts a re-opened hand-copy hole is detected at
      CI time instead of surfacing as a stale fixture months later. The demonstrated-red steps
      prove both fire.
- [ ] Filter-demand guard failure output must name the offending template path, filter name, and
      line number; no-hand-copy guard failure output must name the offending test file — both
      asserted by inspecting the assertion messages in the red-state demonstrations.

## Test Impact

- [ ] `tests/unit/test_session_modal_liveness_render.py::env` (fixture, lines 28-54) — REPLACE:
      delete the three hand-rolled stand-ins, call `register_template_filters(env)`.
- [ ] `tests/unit/test_session_modal_liveness_render.py::TestModalMetadataSections::test_token_cost_strip_renders_when_present`
      — UPDATE: assert `"$1.24"` (ceil-to-cent) instead of the retired 4-decimal `"$1.2345"`.
      Stale since `96b0f65dd`; fails even after the registrar lands unless updated.
- [ ] `tests/unit/test_session_modal_liveness_render.py::TestModalLivenessSection::test_renders_timestamps_via_format_filter`
      — UPDATE: expectation currently mirrors the fixture stand-in (`strftime("%H:%M")` UTC).
      Re-baseline against `_filter_format_timestamp` output (`"1m ago"` for `now - 60`),
      computed by calling the production filter rather than hardcoded.
- [ ] `tests/unit/test_session_modal_liveness_render.py::TestJobRowLivenessSignals::test_cost_still_renders_alongside_the_freshness_chip`
      — UPDATE (no assertion change): unblocked by the registrar; `"$3.75"` already matches
      production output. Verify green, do not edit.
- [ ] `tests/unit/test_per_project_modal.py::env` (fixture, lines 29-39) — REPLACE: delete the
      four lambdas, call `register_template_filters(e)`. Keep `autoescape=True`. Spike-3
      confirms no assertion in that file changes.
- [ ] `tests/unit/test_template_filter_registry.py` — CREATE: filter-demand guard,
      production-shape (`Jinja2Templates`) filter-key equality assertion, registrar-completeness
      test, and the no-hand-copy guard over `tests/**/*.py`. No idempotency test (see Failure
      Path Test Strategy).
- [ ] `tests/unit/test_analytics_stats_render.py` — CREATE: render coverage for the third
      `| usd` surface.
- [ ] No other test constructs a Jinja `Environment` — verified by
      `grep -rn "Environment(" tests/`, which returns exactly the two fixtures above. The
      no-hand-copy guard makes this a standing invariant rather than a one-time check: verified
      at plan time that `\.filters\s*\[` matches exactly 7 lines across `tests/`, all inside
      those two fixtures, so the guard is green immediately after Task 2 with no exemption list.

## Rabbit Holes

- **Rewriting the render tests to boot the FastAPI app via `TestClient`.** Tempting ("then
  they'd use the real env by construction"), but it drags in Redis, Popoto, and route handlers
  for what are template smoke tests, and would make a fast unit suite slow and flaky. The
  registrar gives the same fidelity for the filter surface at zero cost.
- **Aligning the two fixtures' `autoescape` settings.** They differ (`False` in liveness,
  `True` in per-project). Harmonizing means re-baselining escaping-sensitive assertions across
  both files for no benefit to this defect. Leave each fixture's autoescape alone.
- **Generalizing the guard into a whole-template linter** (undefined variables, missing
  includes, unregistered tests via `env.tests`). `jinja2.meta.find_undeclared_variables` makes
  this look easy, but templates legitimately receive context at render time, so a variable
  lint would be almost entirely false positives. Scope the guard to filters, where the
  registry is closed and the answer is unambiguous.
- **Solving dynamic filter application** (`|attr('usd')`, `map('usd')`). No dashboard template
  uses either idiom. Building AST machinery for a case that does not exist is speculative;
  document the limitation in the guard's docstring instead.
- **"Fixing" `_filter_usd` when the cost assertion goes red.** The ceil-to-cent behavior is
  deliberate and shipped. The assertion is wrong, not the filter.

## Risks

### Risk 1: The builder reads a re-baselined assertion as a regression it caused
**Impact:** The builder "fixes" the red by weakening `_filter_usd` back to 4 decimals or by
special-casing the fixture, silently reverting a shipped production decision and defeating the
plan's whole point.
**Mitigation:** Spike-2 records both stale assertions with their exact old and new expected
values, and the No-Gos forbid touching `_filter_usd` semantics. A Verification anti-criterion
asserts `"$1.2345"` no longer appears anywhere in the test file, and `math.ceil` remains in
`_filter_usd`.

### Risk 2: The guard test passes vacuously
**Impact:** A guard that never fires is worse than no guard — it manufactures false confidence
about exactly the class of bug it exists to catch. A typo in the glob, a swallowed parse error,
or an empty template list would all still show green.
**Mitigation:** Demonstrated-red is a required build step: inject `{{ 1 | notafilter }}`, show
the failure naming template and line, remove, show green, paste both into the PR. Additionally
assert the scan visited a non-zero number of templates and that the demanded-filter set is
non-empty, so an empty sweep fails loudly.

### Risk 3: A production filter is removed but templates still use it
**Impact:** The registrar makes tests track production exactly — so deleting a filter from the
registrar silently propagates the deletion into every test env, and a template still using that
filter would fail only at render time in production.
**Mitigation:** This is precisely what the guard catches: it resolves template demand against
the *registrar's* output, so removing a still-used filter turns the guard red immediately.

### Risk 5: The guard env and the production env re-diverge
**Impact:** The guard builds a bare `Environment` while production renders through
`Jinja2Templates`. If a future change adds a production-only filter by some mechanism the
`grep -c 'templates.env.filters\['` anti-criterion cannot see — `filters.update(...)`,
`Jinja2Templates(env=...)`, or a Jinja extension registered in `create_app` — the guard would
green-light templates against a filter set production does not actually have. That is the
original three-lists divergence reproduced one level up.
**Mitigation:** The guard asserts filter-**key** equality between its own env and a
`Jinja2Templates`-shaped env with the registrar applied. Verified at plan time that the two
differ only in `autoescape` (a `select_autoescape` callable) and a `url_for` global, with
identical filter sets. This is a **partial** mitigation, and the boundary matters: the equality
test constructs its own `Jinja2Templates` and never calls `create_app` (which would mount
StaticFiles and build every router), so it catches a filter contributed by `Jinja2Templates`
itself — a FastAPI/Starlette upgrade shipping a new default — but *not* a filter registered by
one of the three mechanisms above from inside `create_app`. That residual case remains covered
only by the `ui/app.py` anti-criterion grep, which sees subscript assignment and nothing else.

### Risk 4: Importing `ui.app` in unit tests pulls in a heavy dependency chain
**Impact:** `ui/app.py` imports `agent.constants`, which imports the `agent` package and
transitively `popoto`. If that chain is slow or requires Redis at import time, the render tests
get slower or gain a service dependency.
**Mitigation:** Verified at plan time — `./.venv/bin/python -c "import ui.app"` succeeds with no
Redis connection, and the existing render tests already run in the standard unit lane. If import
cost ever becomes a problem, the six filters and the registrar can move to a leaf module
(`ui/filters.py`) that `ui/app.py` re-exports. Not needed now; noted as the escape hatch.

## Race Conditions

No race conditions identified. Filter registration is synchronous, single-threaded, and happens
once during `create_app()` before the app serves any request; the test fixtures register on
function-scoped environments that no other test can observe. There is no shared mutable state
across processes and no ordering dependency beyond "register before render", which is
structurally guaranteed by both call sites.

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan. The three open questions the
issue raised are resolved by spikes and all three answers landed *in* scope: the fixtures adopt
the real production filters (Q1), the template-scanning guard test is built (Q2), and
`analytics_stats.html` gains render coverage (Q3).

Two things this plan deliberately does not *change* (as opposed to defers) — both are asserted
as anti-criteria in Verification:

- **`_filter_usd` semantics stay exactly as shipped by `96b0f65dd`.** Ceil-to-cent is a
  deliberate production decision; this is a test-infrastructure fix and must not alter rendered
  production output.
- **No template markup changes.** The `| usd` call sites, the Jobs table structure from
  `13d058848`, and every other template stay byte-identical.

## Update System

No update system changes required — this is a test-infrastructure and internal-refactor change.
No new dependencies (`jinja2` and `jinja2.nodes` are already present), no new config files, no
new secrets, no migration steps for existing installations. The dashboard is served from the
repo checkout, so a normal `/update` picks the change up with no extra step.

No Popoto model changes, so no entry in `scripts/update/migrations.py` is needed.

## Agent Integration

No agent integration required — this is a dashboard-internal change with no new capability for
the agent to reach. No new CLI entry point in `pyproject.toml [project.scripts]`, and
`bridge/telegram_bridge.py` does not import `ui.app`. `register_template_filters` is consumed
only by `create_app` and by test fixtures.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/dashboard.md` with a short "Template filters" subsection: the six
      filters, `register_template_filters` as the single registration seam, and the rule that a
      new filter is added there and nowhere else. `docs/features/dashboard.md:139` already
      documents the `freshness_age` filter, so this extends existing content rather than
      creating a new page.
- [ ] Update `tests/README.md`: add `test_template_filter_registry.py` and
      `test_analytics_stats_render.py` to the suite index, and note that dashboard render
      fixtures must obtain filters from `ui.app.register_template_filters`, never hand-copy
      them — pointing at the no-hand-copy guard as the *enforcement*. The README note is
      documentation of a rule CI enforces, not the rule's only home; the plan's own root cause
      was that comments do not fail builds.
- [ ] No new `docs/features/README.md` index entry — no new feature page is created.

### External Documentation Site
- [ ] Not applicable — this repo has no Sphinx/MkDocs site.

### Inline Documentation
- [ ] `register_template_filters` gets a docstring stating it is the single source of truth for
      dashboard template filters, that callers own loader/autoescape, and that new filters go
      here so tests inherit them.
- [ ] The guard test gets a docstring explaining what it catches, and explicitly recording the
      dynamic-filter (`|attr`, `map('name')`) blind spot.

## Success Criteria

- [x] `tests/unit/test_session_modal_liveness_render.py::TestJobRowLivenessSignals::test_cost_still_renders_alongside_the_freshness_chip` passes.
- [x] `tests/unit/test_session_modal_liveness_render.py::TestModalMetadataSections::test_token_cost_strip_renders_when_present` passes, asserting `$1.24`.
- [x] `tests/unit/test_session_modal_liveness_render.py::TestModalLivenessSection::test_renders_timestamps_via_format_filter` passes against production `_filter_format_timestamp` output.
- [x] `tests/unit/test_per_project_modal.py` passes unchanged in its assertions.
- [x] No test file hand-copies filter registrations — enforced by the no-hand-copy guard test in `tests/unit/test_template_filter_registry.py` (a CI test, not a human-run grep), anchored on `\.filters\s*\[` with the guard file self-exempted.
- [x] The guard env's filter keys equal a `Jinja2Templates`-shaped production env's filter keys.
- [x] `ui/app.py` registers filters in exactly one place (`register_template_filters`); `create_app` contains no `templates.env.filters[` assignment.
- [x] The filter-demand guard fails when a template references an unregistered filter, and the no-hand-copy guard fails when a test file assigns into `.filters[...]` — both proven by red-state demonstrations pasted into the PR description.
- [x] `_partials/analytics_stats.html` has render coverage asserting both cost cards.
- [x] No production render output changes: `_filter_usd` still uses `math.ceil`, and no file under `ui/templates/` is modified by this PR.
- [x] Tests pass (`/do-test`)
- [x] Documentation updated (`/do-docs`)
- [x] No xfail markers exist in the touched test files (verified: none present today).

## Team Orchestration

### Team Members

- **Builder (registrar + fixtures)**
  - Name: `filter-registrar-builder`
  - Role: Extract `register_template_filters`, rewire `create_app` and both test fixtures, re-baseline the two stale assertions.
  - Agent Type: builder
  - Resume: true

- **Builder (guard + coverage)**
  - Name: `filter-guard-builder`
  - Role: Write the template filter-demand guard test with its demonstrated-red proof, plus `analytics_stats.html` render coverage.
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `filter-validator`
  - Role: Verify all success criteria, run the named node IDs, confirm the red-state proof is present in the PR description.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `filter-documentarian`
  - Role: Update `docs/features/dashboard.md` and `tests/README.md`.
  - Agent Type: documentarian
  - Resume: true

### File Ownership (prevents the two builders colliding)

The split is kept rather than merged, so the boundary must be explicit:

| Agent | Owns | Must not touch |
|---|---|---|
| `filter-registrar-builder` | `ui/app.py`, `tests/unit/test_session_modal_liveness_render.py`, `tests/unit/test_per_project_modal.py` (Tasks 1-2, serial) | the two new test files |
| `filter-guard-builder` | `tests/unit/test_template_filter_registry.py`, `tests/unit/test_analytics_stats_render.py` (Tasks 3-4, serial) | `ui/app.py` — its only production dependency is `from ui.app import TEMPLATES_DIR, register_template_filters` |

Each agent runs its own tasks serially; the two agents run in parallel with each other. No task
is `Parallel: true` while sharing an assignee with another concurrently-flagged task.

### Available Agent Types

Standard Tier 1 pool. No domain framing needed — this is ordinary Python/Jinja test-infrastructure work.

## Step by Step Tasks

### 1. Extract the registrar and rewire production

- **Task ID**: build-registrar
- **Depends On**: none
- **Validates**: `./.venv/bin/python -c "from ui.app import register_template_filters"` and `./scripts/pytest-clean.sh tests/unit/test_per_project_modal.py`
- **Informed By**: spike-3 (registrar must own filters only, never autoescape — the two fixtures differ)
- **Assigned To**: `filter-registrar-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add `register_template_filters(env: Environment) -> None` to `ui/app.py`, moving the six assignments from lines 149-154 verbatim (retargeted to the `env` parameter). Placement within the module is the builder's call.
- Replace those six lines in `create_app` with `register_template_filters(templates.env)`.
- Docstring states the contract: single source of truth; callers own loader/autoescape; new filters go here. Wording is the builder's.
- Confirm `./.venv/bin/python -c "import ui.app"` still imports without a Redis connection.
- **Why `test_per_project_modal.py` is the gate here:** Task 1 changes no test file, so that suite passes both before and after. A red there means the `create_app` rewiring broke import or registration — a meaningful Task-1 signal. `test_template_filter_registry.py` is **not** a Task 1 validator; it does not exist until Task 3 creates it (the earlier draft's circular dependency).

### 2. Rewire both test fixtures and re-baseline stale assertions

- **Task ID**: build-fixtures
- **Depends On**: build-registrar
- **Validates**: `tests/unit/test_session_modal_liveness_render.py`, `tests/unit/test_per_project_modal.py`
- **Informed By**: spike-2 (exactly two assertions shift: `$1.2345` → `$1.24`, and the `strftime("%H:%M")` expectation → production humanized output; `$3.75` survives unchanged), spike-3 (per-project file needs no assertion changes)
- **Assigned To**: `filter-registrar-builder`
- **Agent Type**: builder
- **Parallel**: false
- Delete the three stand-ins in `test_session_modal_liveness_render.py:28-54`; call `register_template_filters(env)`. Drop the now-false `# Mirror the filters...` comment.
- Delete the four lambdas in `test_per_project_modal.py:29-39`; call `register_template_filters(e)`, keeping `autoescape=True`.
- `test_token_cost_strip_renders_when_present`: assert `"$1.24"`, not `"$1.2345"`. Add a comment naming `96b0f65dd` as the reason.
- `test_renders_timestamps_via_format_filter`: compute the expectation by calling `_filter_format_timestamp(ts)` rather than hardcoding, so the test asserts agreement with production.
- Do NOT touch `_filter_usd` or any file under `ui/templates/`.

### 3. Build the filter-demand guard with a demonstrated-red proof

- **Task ID**: build-guard
- **Depends On**: build-registrar
- **Validates**: `tests/unit/test_template_filter_registry.py` (create)
- **Informed By**: spike-1 (AST scan finds 6 production filters, 8 built-ins, 0 unknowns across 5 templates), research (`nodes.FilterBlock` must be scanned alongside `nodes.Filter`; `|attr`/`map('name')` are a documented blind spot)
- **Assigned To**: `filter-guard-builder`
- **Agent Type**: test-engineer
- **Parallel**: true (with Tasks 1-2 only — Task 4 now runs after this one on the same agent)
- **Must not touch `ui/app.py`.** Its only production dependency is `from ui.app import TEMPLATES_DIR, register_template_filters`. Tasks 1-2 own that file serially on the other agent.
- Create `tests/unit/test_template_filter_registry.py`, marked `pytest.mark.unit` and `pytest.mark.webui`.
- Filter-demand guard: parse every `ui/templates/**/*.html` on a registrar-configured env; assert no `nodes.Filter`/`nodes.FilterBlock` name is missing from `env.filters`. Failure message names template, filter, and line.
- Assert the sweep visited a non-zero template count and collected a non-empty filter set, so an empty scan cannot pass vacuously.
- **Production-shape equality**: build `Jinja2Templates(directory=str(TEMPLATES_DIR))`, apply the registrar to its `.env`, and assert its filter **keys** equal the guard env's. Do NOT call `create_app()` (it mounts StaticFiles and builds every router). Keys only — the two envs legitimately differ in `autoescape` and a `url_for` global.
- **No-hand-copy guard**: walk `tests/**/*.py`, skip `Path(__file__).name`, and fail on any file matching `re.search(r"\.filters\s*\[", ...)`. Anchor on the attribute form, not a bare `filters[`. Verified at plan time: 7 matches today, all in the two fixtures Task 2 rewires — no grandfather list needed. The self-exemption is required because this file legitimately reads `env.filters`.
- Add a registrar-completeness test: `set(env.filters) - set(Environment().filters)` equals the six expected names.
- **No idempotency test** — `dict.__setitem__` idempotence is a Python language guarantee, not behavior of this code.
- **No filter `None`-path assertions here** — `_filter_usd(None)` / `_filter_format_timestamp(None)` are filter properties, not registrar properties, and are unrelated to #2719. This file keeps exactly one job. Put them in a filters-focused module if you want them at all (optional).
- **Demonstrated red, both guards**: (a) temporarily add `{{ 1 | notafilter }}` to a template, capture the failing output, remove it, capture the passing output; (b) temporarily add an `env.filters["x"] = ...` line to a scratch test file, capture the failing output naming that file, remove it, capture the passing output. All four outputs go in the PR description.
- Docstring records the dynamic-filter (`|attr`, `map('name')`) blind spot.

### 4. Add analytics_stats render coverage

- **Task ID**: build-analytics-coverage
- **Depends On**: build-guard
- **Validates**: `tests/unit/test_analytics_stats_render.py` (create)
- **Informed By**: spike-4 (83-line template, flat `analytics` mapping, no includes or macros)
- **Assigned To**: `filter-guard-builder`
- **Agent Type**: test-engineer
- **Parallel**: false
- **Sequenced deliberately.** An earlier draft marked Tasks 3 and 4 both `Parallel: true` while assigning both to `filter-guard-builder` — one agent cannot execute two tasks concurrently, so a supervisor honoring the flags would either double-dispatch the agent (rival incarnations racing on one worktree) or silently serialize against the stated plan. This is one small render test; parallelism buys nothing, and sequencing after Task 3 lets it reuse the registrar-configured env helper Task 3 just wrote. Do NOT reassign it to `filter-registrar-builder`, which owns the serial Tasks 1-2 on `ui/app.py`.
- Render `_partials/analytics_stats.html` through a registrar-configured env with a minimal `analytics` mapping.
- Assert both cost cards (`cost_today_usd` at line 30, `cost_7d_usd` at line 34) render ceil-to-cent values.
- Assert the sub-cent case renders `$0.01`, not `$0.00` — the behavior `96b0f65dd` exists to protect.

### 5. Validation

- **Task ID**: validate-all-tests
- **Depends On**: build-fixtures, build-guard, build-analytics-coverage
- **Assigned To**: `filter-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run `./scripts/pytest-clean.sh tests/unit/test_session_modal_liveness_render.py tests/unit/test_per_project_modal.py tests/unit/test_template_filter_registry.py tests/unit/test_analytics_stats_render.py`.
- Run every Verification-table command and record actual output.
- Confirm the red-state proof for the guard is present in the PR description.
- Confirm `git diff --name-only` includes no path under `ui/templates/`.

### 6. Documentation

- **Task ID**: document-feature
- **Depends On**: validate-all-tests
- **Assigned To**: `filter-documentarian`
- **Agent Type**: documentarian
- **Parallel**: false
- Add the "Template filters" subsection to `docs/features/dashboard.md`.
- Update `tests/README.md` with the new test file and the no-hand-copying rule.

### 7. Final Validation

- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: `filter-validator`
- **Agent Type**: validator
- **Parallel**: false
- Re-run the full Verification table.
- Confirm every Success Criteria checkbox.
- Generate the final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Target render tests pass | `./scripts/pytest-clean.sh tests/unit/test_session_modal_liveness_render.py tests/unit/test_per_project_modal.py` | exit code 0 |
| New guard + coverage tests pass | `./scripts/pytest-clean.sh tests/unit/test_template_filter_registry.py tests/unit/test_analytics_stats_render.py` | exit code 0 |
| Registrar exists and is importable | `./.venv/bin/python -c "from ui.app import register_template_filters; print('ok')"` | output contains ok |
| Registrar covers all six filters | `./.venv/bin/python -c "from jinja2 import Environment; from ui.app import register_template_filters as r; e=Environment(); b=set(Environment().filters); r(e); print(sorted(set(e.filters)-b))"` | output contains format_duration |
| Every template filter resolves | `./.venv/bin/python -c "import pathlib,jinja2;from jinja2 import nodes;from ui.app import register_template_filters as r;td=pathlib.Path('ui/templates');e=jinja2.Environment(loader=jinja2.FileSystemLoader(str(td)));r(e);m=[(str(p),n.name) for p in td.rglob('*.html') for n in e.parse(p.read_text()).find_all((nodes.Filter,nodes.FilterBlock)) if n.name not in e.filters];print(len(m))"` | output contains 0 |
| No hand-copied filters in tests | `! grep -rnE '\.filters\s*\[' tests/ --include='*.py' \| grep -v test_template_filter_registry.py \| grep -q .` | exit code 0 |
| create_app has no inline registrations | `! grep -q 'templates.env.filters\[' ui/app.py` | exit code 0 |
| Anti-criterion: usd semantics unchanged | `grep -c 'math.ceil' ui/app.py` | output > 0 |
| Anti-criterion: stale 4-decimal assertion gone | `! grep -q '1\.2345"' tests/unit/test_session_modal_liveness_render.py` | exit code 0 |
| Anti-criterion: no template markup changed | `test -z "$(git diff --name-only origin/main... -- ui/templates/)"` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

**Exit-code convention:** the four anti-criterion rows above are written as `! grep -q ...` /
`test -z ...` rather than `grep -c ... == 0`. `grep` exits **1** when it finds nothing, so a
runner judging by exit code would read the *success* state (no matches) as a failure. The one
positive row (`grep -c 'math.ceil' ui/app.py`, expected `> 0`) is judged by output, not exit
code, and is already correct as written.

## Critique Results

War room depth: **FULL** (3 critics). Findings: 8 total — 0 blockers, 6 concerns, 2 nits.
Verdict: **READY TO BUILD (with concerns)**, recorded in commit `6d2ad5999` on `main`.

**Revision status: all 8 findings applied.** Every row below is addressed in the plan body; the
Implementation Note column is retained because the build agents consume it directly.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness | The guard builds a bare `Environment`, but production renders through `Jinja2Templates` (`ui/app.py:146`). The only defense against the two re-diverging is the `grep -c 'templates.env.filters\['` anti-criterion, which catches subscript assignment only — a future `filters.update(...)`, `Jinja2Templates(env=...)`, or a Jinja extension in `create_app` adds a production-only filter the guard cannot see. That is the three-lists divergence one level up. | Technical Approach → Production-shape equality; Risk 5; Task 3; Success Criteria | Assert filter-key equality between the guard env and a production-shaped env: `from fastapi.templating import Jinja2Templates` (ui/app.py:21 imports it from fastapi, not starlette); `prod = Jinja2Templates(directory=str(TEMPLATES_DIR)); register_template_filters(prod.env); assert set(prod.env.filters) == set(guard_env.filters)`. Do NOT call `create_app()` — it mounts StaticFiles and builds every router. Compare `.filters` KEYS only: verified by execution that `Jinja2Templates` differs from a bare `Environment` in exactly two non-filter ways (`autoescape` is a `select_autoescape` callable, and it injects a `url_for` global); its extra-filter set is empty today, which is why the assertion passes now and acts as a tripwire later. |
| CONCERN | Risk & Robustness | Re-baselining `test_renders_timestamps_via_format_filter` by computing `expected = _filter_format_timestamp(ts)` makes the assertion self-referential — the template applies the same function, so it can only fail if the filter token is dropped entirely, and it matches the 7-char substring `1m ago`, cheap to satisfy incidentally in a large HTML blob. | Technical Approach → re-baseline bullet 2 (discriminating checks added); Task 2 | Keep the production-agreement computation but add a discriminating check: alongside `assert expected in html`, assert the raw unfiltered value is absent (`assert str(int(ts)) not in html`) and keep `assert "Last active" in html`. Do NOT hardcode `"1m ago"`: `_filter_format_timestamp` calls `.astimezone()` and several branches return `dt.strftime("%H:%M")`, so a hardcoded expectation is local-timezone dependent. The `now - 60` input is safe from the `< 60 → "just now"` boundary because the filter samples `utc_now()` strictly after `ts` is captured, so `diff` is always `>= 60`. |
| CONCERN | Scope & Value | Failure Path Test Strategy states the registrar "is six dict assignments with no branching," then prescribes tests for that non-branching code: an idempotency test (a property of Python dict assignment, not of this code) and `None`-path assertions that belong to the filters, not the registrar, and are unrelated to the defect. | Failure Path Test Strategy → Empty/Invalid Input Handling (idempotency DROPPED, None-paths RELOCATED); Task 3 | KEEP the completeness test — `set(env.filters) - set(Environment().filters) == {the six names}` — it is what makes "add a filter, tests inherit it" mechanically true rather than aspirational. DROP the idempotency test outright: `dict.__setitem__` idempotence is a language guarantee. If the `None`-path assertions are kept, move them to a filters-focused test so `test_template_filter_registry.py` retains exactly one job. |
| CONCERN | History & Consistency | The plan names its root cause ("comments do not fail builds") but lands the anti-recurrence rule as a `tests/README.md` note plus a Success Criterion checked by a human running `grep -rn 'filters\['` once. That is a comment with extra steps: the plan guards *templates demanding an unregistered filter* but leaves the symmetric failure — *a new fixture hand-rolling filters again* — enforced by prose. A third fixture added later reproduces #2719 with nothing going red. | Solution → No-hand-copy guard test; Technical Approach → No-hand-copy guard contract; Task 3; Verification; Success Criteria | Promote the grep into a CI test in `tests/unit/test_template_filter_registry.py`: walk `tests/**/*.py`, skip `Path(__file__).name`, and flag any file matching `re.search(r"\.filters\s*\[", p.read_text())`. Anchor on `\.filters\s*\[` (attribute access), NOT the plan's bare `filters[`, so a local variable named `filters` cannot false-positive. Verified against the live repo: exactly 7 matches exist today, all in the two fixtures Task 2 rewires (`test_session_modal_liveness_render.py:51-53`, `test_per_project_modal.py:35-38`), so the test goes green the moment Task 2 lands and needs no grandfather list. The filename self-exemption is required because the guard file legitimately reads `env.filters`. |
| CONCERN | History & Consistency | Task 1 declares `Validates: tests/unit/test_template_filter_registry.py (create)`, but that file is created by Task 3, which itself declares `Depends On: build-registrar`. The dependency graph and the validation field point in opposite directions: a builder following Task 1 literally blocks on a file that cannot exist yet, or writes the guard early and collides with Task 3's assignee on the same new file. | Task 1 `Validates:` rewritten; guard file left on Task 3 only | Set Task 1 `Validates:` to `./.venv/bin/python -c "from ui.app import register_template_filters"` plus `./scripts/pytest-clean.sh tests/unit/test_per_project_modal.py`. The second command is a meaningful Task-1 gate precisely because Task 1 changes no test file — that suite passes both before and after, so a red there means the `create_app` rewiring broke import or registration. Leave `test_template_filter_registry.py (create)` on Task 3 only. |
| CONCERN | History & Consistency + Scope & Value | Tasks 3 and 4 are both `Parallel: true` while both are `Assigned To: filter-guard-builder`. One agent cannot execute two tasks concurrently, so a supervisor honoring the flags either double-dispatches the same agent (two incarnations racing on one worktree) or silently serializes, contradicting the Team Orchestration table. Relatedly, four named agents for a Small change touching one production file and four test files is handoff overhead exceeding the work. | Task 4 set `Parallel: false`, `Depends On: build-guard`; Team Orchestration → File Ownership table | Set Task 4 `Parallel: false` and run it after Task 3 on the same agent — it is one small render test, the parallelism buys nothing, and sequencing lets it reuse the registrar-configured env helper Task 3 just wrote. Do NOT reassign Task 4 to `filter-registrar-builder`, which owns the serial Tasks 1-2 on `ui/app.py`. If the two builders are instead merged (the simpler option), the conflict dissolves; a kept split requires the guard builder never touch `ui/app.py` — its only production dependency is `from ui.app import TEMPLATES_DIR, register_template_filters`. |
| NIT | Risk & Robustness | Three anti-criterion Verification rows use `grep -c` expecting `match count == 0`, but `grep` exits 1 when it finds nothing, so a runner judging by exit code reads the success state as a failure. | Verification table rewritten to `! grep -q` / `test -z` form + exit-code convention note | Use `! grep -q 'templates.env.filters\[' ui/app.py` (exit 0 when absent) or `grep -c ... ; test $? -eq 1`. The positive row (`grep -c 'math.ceil' ui/app.py`, expected `> 0`) is already correct. |
| NIT | Scope & Value | The plan pins builder-owned detail: the exact ~15-line guard body, the exact insertion point ("immediately after `_filter_usd` and before `create_app`"), and two docstrings' content. None change the outcome; over-specification invites review churn when the builder's equally-correct arrangement differs. | Technical Approach reframed as contracts; guard body, insertion point, and docstring wording de-pinned in Tasks 1 and 3 | State the contract (guard scans `nodes.Filter` and `nodes.FilterBlock` across every template and names template/filter/line on failure; registrar takes an `Environment` and registers filters only) and let the builder own placement and phrasing. |

---

## Open Questions

None. The issue's three open questions were all resolved empirically at plan time:

1. **Are the simplified stand-ins load-bearing?** Yes, for exactly two assertions — and both
   are stale relative to shipped production behavior, so they get re-baselined rather than
   preserved (spike-2). One of the two was found by this plan and appears in neither #2719 nor
   #2720.
2. **Is a template-scanning guard cheap?** Yes — ~15 lines via `env.parse()` AST walk,
   sub-second, zero false positives against the current template set (spike-1).
3. **Should `analytics_stats.html` gain coverage here?** Yes, in scope — it is the last
   uncovered `| usd` site and costs one small test (spike-4).
