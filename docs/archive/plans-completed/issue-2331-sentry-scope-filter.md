# Plan: Scope Sentry triage to this repo + expand synthetic-noise coverage

- **Issue:** #2331 (`bug`, `reflections`)
- **Slug:** `issue-2331-sentry-scope-filter`
- **File touched:** `reflections/sentry_triage.py` (+ one test module, one doc)
- **Size:** one new helper + one filter call + pattern-list additions. Not an epic.
- **Sibling:** complements PR #2330 / #2300 (dedup). This closes the *classification/scoping* half. **Do not touch dedup.**

## Goal

Stop `sentry-issue-triage` from filing GitHub issues against this repo for errors that are not actionable bugs in this repo:

1. **Cross-project errors** (podcast-episode workflow, Stripe billing, audio/ffmpeg pipeline) that share the `yudame` org must never reach classification/filing here.
2. **Synthetic/test-fixture titles** (`RuntimeError: boom`, `ValueError: corrupt`, `RuntimeError: provider down`, and similar single-token sentinels) emitted into *our own* Sentry project must classify as Class A (noise), not fall through to the `event_count >= 10` → Class C heuristic.

## Root cause (exact locations)

- `_fetch_unresolved_issues` (`reflections/sentry_triage.py:225`) queries `GET /organizations/{org}/issues/?query=is:unresolved` — the **entire org**, no project filter (line 231-233). Every project's issues come back in one flat list.
- `run_sentry_triage` (line 475) fetches (line 498), groups by `issue["project"]["slug"]` for reporting (line 505-508), then classifies **every** issue regardless of origin (line 518-520). There is no ownership gate anywhere between fetch and classify.
- `_classify_issue` (line 263) short-circuits to Class A / B only via the substring lists `_CLASS_A_PATTERNS` (line 114) and `_CLASS_B_PATTERNS` (line 151). Anything missing both lists hits the default: `event_count >= 10` → Class C, else Class D (line 294-296). Synthetic sentinels like `boom`, `corrupt`, `provider down` are absent from `_CLASS_A_PATTERNS`, so a fixture error with ≥10 events files as an actionable bug.
- The `[COWORK]` guard (line 582-592) already *acknowledges* the cross-project misfiling risk in its comment ("defaulting unmatched slugs to PROJECT_ROOT … cross-project misfiling risk") but only defaults the working directory — it does not drop the issue.

The originating project **is reliably available** on every issue: `issue["project"]["slug"]` (already read at line 507 and 569). That is the correct, least-brittle seam — `server_name`/`release`/tags are per-event and not present on the issue-list payload; a title-prefix allowlist is brittle and defeated by any untagged title.

## Design decisions

### 1. Scoping seam — project-slug allowlist, filtered in `run_sentry_triage` before classification

**Recommendation:** a config-driven **allowlist of owned Sentry project slugs**, applied as a filter in `run_sentry_triage` immediately after `_fetch_unresolved_issues` (line 498), *before* grouping and classification.

- New helper `_owned_project_slugs() -> set[str]` (mirroring `_get_org_slug` at line 211): reads env var `SENTRY_TRIAGE_PROJECT_SLUGS` (comma-separated), falling back to `.env`. Returns an empty set when unset.
- New helper `_is_owned_issue(issue: dict, allow: set[str]) -> bool`: returns `issue.get("project", {}).get("slug", "")` `in allow`. When `allow` is empty the filter is **disabled** (returns `True` for all) — see fail-safe below.
- In `run_sentry_triage`, after the fetch: if the allowlist is non-empty, drop non-owned issues and record how many were scoped out for the summary line.

**Why the allowlist, not `server_name`/`release`/title-prefix:** the slug is already parsed and is the canonical per-issue project identifier in the Sentry issues payload; `server_name`/`release` live on individual events (not the aggregated issue object) and would require extra API calls; title-prefix is brittle. Slug is the one field guaranteed present and stable.

**Why filter before classification, not as a new Class letter:** returning e.g. Class A for a foreign issue would trigger the tier-A Sentry auto-action (`status=ignored`, line 557) against *another project's* Sentry state — not our call to make. Dropping the issue entirely keeps `_classify_issue` pure and leaves foreign projects' Sentry state untouched.

**Config, not hardcode.** The CMA deployment environment already sets `SENTRY_ORG_SLUG`, `COWORK_ROUTINE=1`, and `GH_REPO=tomcounsell/ai` (see `docs/infra/cowork-sentry-triage.md`). `SENTRY_TRIAGE_PROJECT_SLUGS=<ai-project-slug>` joins that same set — one line, same pattern. **The exact slug string is not yet known from static inspection**: confirm it before build by running `/sentry` (dry-run) once and reading the `{short_id} [{proj}]` project tags in the findings, or `GET /organizations/yudame/projects/`. Set that confirmed slug as the deployment env value. Add a named module constant `_DEFAULT_OWNED_SLUGS: tuple[str, ...] = ()` with a provisional/tunable comment so the mechanism is self-documenting without baking a guessed slug into source.

### 2. Synthetic-noise coverage — targeted patterns + one precise single-token heuristic

The ownership allowlist is the primary defense and already scopes out the podcast/Stripe/ffmpeg examples (they live in other projects — including `OSError: SECONDARY: disk full while writing meta`, whose `SECONDARY:`/`writing meta` shape is the audio pipeline, not this repo). The pattern list only needs to catch sentinels emitted into *our own* project by test/verification runs.

**Recommendation:** extend `_CLASS_A_PATTERNS` (line 114) with anchored, high-specificity sentinels, plus one narrow regex heuristic — keep it precise, avoid over-broad substrings.

- Add anchored patterns (colon-space anchored to avoid matching real words): `": boom"`, `": corrupt"`, `"provider down"`. These map to the live examples #2246, #2241, #2231.
- Do **not** add a bare `"disk full"` pattern — a genuine `OSError: disk full` from this repo's own disk is a real bug we want filed. That example is cross-project and is handled by decision 1. If a synthetic `disk full` ever appears in our own project, add the full specific phrase (`"disk full while writing meta"`), never the bare two words.
- Add one precise regex heuristic in `_classify_issue`, evaluated with the Class-A patterns: title matching `^[A-Za-z]*(Error|Exception): [a-z]+$` — an exception type followed by a **single all-lowercase word** (e.g. `RuntimeError: boom`, `ValueError: corrupt`). Real production messages carry context (paths, IDs, capitalization, multiple words); a bare one-word lowercase message is a fixture tell. This backstops future sentinels without enumerating them.

Rationale for the regex over more substrings: a curated substring list rots and risks over-broad matches (`"corrupt"` alone would suppress a real `corrupt database` bug). The single-token regex is structurally narrow — it only fires on the exact shape synthetic fixtures produce.

### 3. Fail-safe direction — scope out (skip) on ambiguity, filter disabled when unconfigured

This issue is about **noise**: a false file is visible garbage; a false skip self-heals on the next daily run (and the error is still visible in Sentry). So lean toward **not filing** on ambiguity.

- **Within an active allowlist:** an issue whose `project.slug` is missing/`"unknown"`/not in the allowlist is **dropped** (scoped out). The allowlist model fail-safes toward skip by construction — only explicitly-owned slugs pass.
- **When `SENTRY_TRIAGE_PROJECT_SLUGS` is unset/empty:** the filter is **disabled** (all issues pass). This preserves the local, on-demand `/sentry` path, which is legitimately multi-project — it resolves a per-slug `working_directory` from `load_local_projects()` and files each project's issues into its own repo (line 577-600). Forcing the allowlist there would break correct local behavior. The bug is specifically the **CMA cloud run**, which defaults every unmatched slug to `PROJECT_ROOT`/`GH_REPO=tomcounsell/ai` (line 582-592) — that is exactly the mode the allowlist must gate. Consistent with the existing "the routine's environment MUST set X" contract (`COWORK_ROUTINE`, `GH_REPO`), the deployment MUST set `SENTRY_TRIAGE_PROJECT_SLUGS`.

## Implementation steps

**`reflections/sentry_triage.py`**

1. After `_CLASS_B_PATTERNS` (or near the classification constants), add `_DEFAULT_OWNED_SLUGS: tuple[str, ...] = ()` with a provisional/tunable comment, and a `_SYNTHETIC_TITLE_RE = re.compile(r"^[A-Za-z]*(?:Error|Exception): [a-z]+$")` (add `import re`).
2. Extend `_CLASS_A_PATTERNS` (line 114) with `": boom"`, `": corrupt"`, `"provider down"` (each with a `# test sentinel (#NNNN)` comment matching existing style).
3. In `_classify_issue` (line 263), after the Class-E block and before the substring loop (or alongside it), add: `if _SYNTHETIC_TITLE_RE.match(title): return "A", "noise pattern: single-token synthetic title"`.
4. Add `_owned_project_slugs() -> set[str]` (model on `_get_org_slug`, line 211): env `SENTRY_TRIAGE_PROJECT_SLUGS` → `.env` fallback → `set(_DEFAULT_OWNED_SLUGS)`; split on comma, strip, drop empties.
5. Add `_is_owned_issue(issue: dict, allow: set[str]) -> bool`: `not allow` → `True`; else `issue.get("project", {}).get("slug", "") in allow`.
6. In `run_sentry_triage`, right after `issues = _fetch_unresolved_issues(...)` (line 498) and the empty check: compute `allow = _owned_project_slugs()`; if `allow`, `scoped_out = [i for i in issues if not _is_owned_issue(i, allow)]` and `issues = [i for i in issues if _is_owned_issue(i, allow)]`; log/append a `scoped out N cross-project issue(s)` line and include the count in the summary. Re-check the `if not issues` empty branch after filtering.

**Order of guards:** ownership filter (run-level, drops the issue) → Class E stale → Class A (patterns + regex) → Class B → event-count default. Foreign issues never reach `_classify_issue`.

## Test plan (`tests/unit/test_sentry_triage_apply.py`, matching existing direct-unit + monkeypatch style)

Map to acceptance criteria:

- **AC: synthetic titles classify A / non-C.**
  - `test_classify_synthetic_boom` — `{"title": "RuntimeError: boom", "count": 50}` → `"A"`.
  - `test_classify_synthetic_corrupt` — `ValueError: corrupt`, count 50 → `"A"`.
  - `test_classify_synthetic_provider_down` — `RuntimeError: provider down`, count 50 → `"A"`.
  - `test_synthetic_regex_single_token` — parametrized true (`OSError: boom`) vs false (`ValueError: invalid literal for int`, `RuntimeError: provider down but recovered`) so the regex is proven narrow.
- **AC: a genuine in-repo actionable error still classifies C.**
  - `test_classify_real_actionable_still_c` — `{"title": "KeyError: 'session_id' in output_router.resolve", "count": 42, "lastSeen": <recent ISO>}` → `"C"`. Guards against over-broad patterns/regex.
- **AC: cross-project issues scoped out, never filed.**
  - `test_is_owned_issue_allowlist` — allow=`{"ai"}`: owned `{"project": {"slug": "ai"}}` → True; foreign `{"project": {"slug": "podcast"}}`, `{"project": {"slug": "stripe-billing"}}`, and missing-project `{}` → False.
  - `test_is_owned_issue_disabled_when_empty` — allow=`set()` → True for any slug (local multi-project path preserved).
  - `test_owned_project_slugs_env` — `monkeypatch.setenv("SENTRY_TRIAGE_PROJECT_SLUGS", "ai, other")` → `{"ai", "other"}`; unset → `set(_DEFAULT_OWNED_SLUGS)`.
  - `test_run_scopes_out_cross_project` — patch `_fetch_unresolved_issues` to return one `ai` issue + one `podcast` issue, set the env allowlist to `ai`, run in dry-run, assert only the `ai` issue reaches `classified` (e.g. via the returned `summary`/`findings` scoped-out count, or patch `_classify_issue` to record calls).

## Docs to update

- `docs/features/sentry-triage.md` — add an **Ownership scoping** section (the `SENTRY_TRIAGE_PROJECT_SLUGS` allowlist, the filter seam, fail-safe = drop-on-ambiguity, disabled-when-unset preserving local multi-project) and note the expanded Class-A synthetic-title coverage (anchored sentinels + single-token regex).
- `docs/infra/cowork-sentry-triage.md` — add `SENTRY_TRIAGE_PROJECT_SLUGS=<ai-slug>` to the deployment's required env set (alongside `COWORK_ROUTINE=1`, `GH_REPO`, `SENTRY_ORG_SLUG`) with the same "MUST set or it misfiles" emphasis.

## Success Criteria

- [ ] Cross-project Sentry issues (podcast-episode workflow, Stripe, audio/ffmpeg pipeline) are scoped out and never filed against this repo when `SENTRY_TRIAGE_PROJECT_SLUGS` is set.
- [ ] Synthetic/test titles (`RuntimeError: boom`, `ValueError: corrupt`, `RuntimeError: provider down`, single-token `<Error>: <word>`) classify as A, never C.
- [ ] A genuine in-repo actionable error (recent, ≥10 events) still classifies C.
- [ ] Local `/sentry` on-demand multi-project path is unchanged when the env var is unset.
- [ ] Unit tests in `tests/unit/test_sentry_triage_apply.py` cover all of the above and pass.
- [ ] `docs/features/sentry-triage.md` and `docs/infra/cowork-sentry-triage.md` updated.

## Test Impact

No existing tests are broken — the changes are additive:

- Ownership filtering is inert unless `SENTRY_TRIAGE_PROJECT_SLUGS` is set, and no existing test sets it, so `tests/unit/test_sentry_triage_apply.py` and the dry-run/apply paths are unaffected.
- New Class-A patterns and the single-token regex only add A-classifications for titles no existing test asserts on; existing `_classify_issue` assertions keep their outcomes.
- New tests are appended to `tests/unit/test_sentry_triage_apply.py` (see Test plan). No `UPDATE`/`DELETE`/`REPLACE` of existing cases required.

## Update System

No changes to the `/update` sync system, launchd plists, hardlinks, or reflection registry. The daily trigger is the existing CMA deployment (`depl_019ymjsGn1fwdLzGA8m8yrZt`); this work only adds one env var (`SENTRY_TRIAGE_PROJECT_SLUGS`) to that deployment's environment — an operator/deploy action documented in `docs/infra/cowork-sentry-triage.md`, not a code-wired update step.

## Agent Integration

No new agents, subagents, tools, or MCP servers. The affected surface is the `sentry-issue-triage` reflection callable, invoked by the CMA deployment and the `/sentry` skill (`.claude/skills/sentry/SKILL.md`). The skill needs no changes — it delegates to `run_sentry_triage()` unchanged; behavior shifts entirely through the new config-gated filter and classification patterns.

## Documentation

Use the `documentarian` agent for these.

### Feature Documentation
- [ ] Update `docs/features/sentry-triage.md` — add an **Ownership scoping** section (the `SENTRY_TRIAGE_PROJECT_SLUGS` allowlist, filter seam, fail-safe = drop-on-ambiguity, disabled-when-unset) and note the expanded Class-A synthetic-title coverage (anchored sentinels + single-token regex).
- [ ] `docs/features/README.md` index already lists sentry-triage; no new entry needed (verify link intact).

### External Documentation Site
- [ ] N/A — this repo has no Sphinx/MkDocs/RTD site for this area.

### Inline Documentation
- [ ] Provisional/tunable comment on `_DEFAULT_OWNED_SLUGS`, docstrings on `_owned_project_slugs` and `_is_owned_issue`, and a comment on the single-token regex explaining the fixture-shape rationale.
- [ ] Update `docs/infra/cowork-sentry-triage.md` to add `SENTRY_TRIAGE_PROJECT_SLUGS=<ai-slug>` to the deployment's required env set with "MUST set or it misfiles" emphasis.

## No-Gos (Out of Scope)

- **Dedup.** Already fixed by PR #2330 / #2300 — not touched here.
- **Sentry query-level project filtering** (`?project=<id>` in `_fetch_unresolved_issues`). Deferred: the post-fetch slug allowlist is simpler, keeps the org-wide grouping/reporting intact, and needs no numeric project-id lookup. Revisit only if fetch volume becomes a cost problem.
- **Per-project `GH_REPO` resolution** for a multi-repo cloud routine — out of scope; the single-repo pilot sets it statically.
- **`environment:production` tag filtering** as an alternative synthetic-noise gate — not pursued; the allowlist + patterns cover the observed cases without a query change.

## Risks

- **Wrong/omitted slug value.** If the deployment sets an incorrect `SENTRY_TRIAGE_PROJECT_SLUGS`, the allowlist scopes out this repo's own real errors (false skip). Mitigation: confirm the slug from a live `/sentry` dry-run (`[proj]` tags) or the Sentry projects API before setting; false skips self-heal next run and remain visible in Sentry. Lower-cost failure than the current false-file noise.
- **Over-broad synthetic patterns.** `": corrupt"` could in principle match a real message. Mitigation: anchored with colon-space; the `test_classify_real_actionable_still_c` regression case and the deliberately-narrow single-token regex (all-lowercase, single word) bound the blast radius. Never add bare `disk full`.
- **Local vs cloud behavior split.** The filter is disabled when the env var is unset — the local `/sentry` path is unchanged, but that means the noise fix only takes effect once the CMA deployment env is updated. Acceptable and intended (local multi-project filing must stay correct); flagged so the deploy step is not forgotten.
- **Env-only config.** The slug lives in the deployment/vault env, not tracked in-repo (like `GH_REPO`). Auditable via the infra doc; `_DEFAULT_OWNED_SLUGS` stays `()` in source to avoid a stale guessed value.
```
