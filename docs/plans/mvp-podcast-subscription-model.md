---
status: Ready
type: feature
appetite: Small
owner: valorengels
created: 2026-04-10
tracking: https://github.com/yudame/cuttlefish/issues/237
last_comment_id: ""
---

# MVP: PodcastSubscription Model

## Problem

**Current behavior:**
`apps/podcast/models/` contains `Podcast`, `PodcastConfig`, `PodcastAccessToken`, `Episode`, `EpisodeWorkflow`, and `EpisodeArtifact` — all production-pipeline concerns. `apps/common/models/subscription.py` has a generic `Subscription` model wired to Stripe. There is no model linking a paying subscriber to a specific podcast, and no place to store podcast-specific subscription preferences (topic focus, delivery cadence, episode length, next drop time).

**Desired outcome:**
A `PodcastSubscription` model that OneToOne-links `common.Subscription` (billing) to `podcast.Podcast` (product), stores podcast-specific fields, and is registered in Django admin for operator use.

## Freshness Check

**Baseline commit:** `97b2143fec299457b34661dfa36c12c364c3875e`
**Issue filed at:** 2026-04-10T08:48:02Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `apps/podcast/models/podcast.py` — Podcast model with TextChoices and Timestampable mixin — confirmed at lines 1-79, still holds
- `apps/podcast/models/podcast_config.py` — PodcastConfig conventions (TextChoices, OneToOne, Timestampable, no explicit `models.Model`) — confirmed at lines 1-84, still holds
- `apps/common/models/subscription.py` — generic Subscription with Stripe fields, `user` null=True — confirmed at lines 1-217, still holds
- `apps/podcast/admin.py` — admin registrations using `unfold.admin.ModelAdmin` — confirmed at lines 1-120, still holds
- `apps/common/tests/factories.py` — ModelFactory base class pattern — confirmed at lines 1-378, still holds

**Cited sibling issues/PRs re-checked:**
- No sibling issues cited.

**Commits on main since issue was filed (touching referenced files):**
- None — no commits touched the referenced files since the issue was filed.

**Active plans in `docs/plans/` overlapping this area:** `private-podcast-feeds.md` — covers feed auth/access tokens, not subscription management. No overlap.

**Notes:** The issue was filed the same day as plan creation with no intervening changes. All claims hold.

## Prior Art

No prior issues or PRs found related to `PodcastSubscription` model. This is greenfield work.

## Architectural Impact

- **New dependencies**: None — uses existing `Timestampable` mixin, `common.Subscription`, and `podcast.Podcast` which are already installed.
- **Interface changes**: `apps/podcast/models/__init__.py` gains a new export. `apps/podcast/admin.py` gains a new `@admin.register` block.
- **Coupling**: Adds a thin bridge between `apps/common` (billing) and `apps/podcast` (product). This coupling is intentional — the model exists precisely to link these two domains.
- **Data ownership**: `PodcastSubscription` owns podcast-specific subscription preferences. `common.Subscription` continues to own billing data. No existing ownership changes.
- **Reversibility**: Easy to reverse — no existing models or tables are modified. The new migration can be reverted without side effects.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. All required models (`common.Subscription`, `podcast.Podcast`) already exist.

## Solution

### Key Elements

- **`PodcastSubscription` model**: Thin wrapper linking `common.Subscription` to `podcast.Podcast` with podcast-specific fields (cadence, length, topic, scheduling, email suppression flag).
- **Django admin registration**: `PodcastSubscriptionAdmin` with `list_display` covering the fields operators need at a glance.
- **Factory class**: `PodcastSubscriptionFactory` in `apps/common/tests/factories.py` for use in all downstream tests.
- **Migration file**: Created via `makemigrations` but NOT run (per project policy).

### Flow

No user-facing flow for this issue — it is a data model. Operator flow:

Django admin → Podcast Subscriptions list → Filter by status/podcast → View/edit subscriber record

### Technical Approach

Follow the conventions established in `apps/podcast/models/podcast_config.py`:

- Class inherits from `Timestampable` (no explicit `models.Model` second parent needed — `Timestampable` already inherits from it)
- Inner classes for `Status` and `Cadence` using `models.TextChoices`
- `CharField` with `choices` and `default` for enum fields
- `db_index=True` on fields that will be queried (subscriber_email)
- `OneToOneField` for both `subscription` and `podcast` links
- `null=True, blank=True` on optional datetime fields (`next_drop_at`)
- `__str__` returns a human-readable label combining subscriber email and podcast title

The admin follows the pattern in `apps/podcast/admin.py`:
- Uses `unfold.admin.ModelAdmin` (not raw `admin.ModelAdmin`)
- `@admin.register(PodcastSubscription)` decorator
- `list_display`, `list_filter`, `search_fields` matching operator needs

**OneToOne note:** Both `subscription` and `podcast` are OneToOne in MVP. One billing record serves one podcast. One podcast has one active subscription in MVP scope. This is a deliberate MVP constraint — the parent PRD describes multi-subscriber podcasts as a future consideration.

**Denormalized fields note:** `subscriber_email` and `subscriber_name` are denormalized from Stripe data onto `PodcastSubscription`. This avoids requiring a Django User account for MVP subscribers (email-only product philosophy). The canonical billing link is still `common.Subscription`.

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers are introduced by this work — the model is a straightforward Django model with no try/except blocks.

### Empty/Invalid Input Handling
- `topic_focus = TextField()` accepts empty string — tests should cover the create path with a blank topic_focus to confirm it doesn't raise.
- `next_drop_at` is `null=True, blank=True` — tests verify None is accepted.

### Error State Rendering
- No user-visible output in this issue. Admin rendering is handled by Django/Unfold.

## Test Impact

No existing tests affected — this is a greenfield model with no prior test coverage. No existing test files reference `PodcastSubscription`.

## Rabbit Holes

- **Multi-subscriber model**: OneToOne on Podcast is an MVP simplification. Do not expand to ForeignKey or many-to-many now.
- **SuppressionEntry model**: `do_not_email` is a placeholder boolean for MVP. A proper suppression-list model is out of scope.
- **Stripe webhook handlers**: Wiring `PodcastSubscription` status to Stripe events is a follow-up issue. Do not add webhook handling here.
- **User account creation**: MVP subscribers are email-only. Do not add `user` FK or account-creation logic to this model.
- **DRIFTING/DISENGAGED/PAUSED statuses**: Deferred per the parent PRD. Only ACTIVE and CHURNED for MVP.

## Risks

### Risk 1: OneToOne on Podcast constrains future multi-subscriber use
**Impact:** If MVP validates and the product scales to multiple subscribers per podcast, the OneToOne constraint will require a migration to ForeignKey.
**Mitigation:** This is a documented, intentional MVP constraint. The parent PRD explicitly defers multi-subscriber support. The migration is straightforward when needed.

### Risk 2: Migration not run introduces drift between model and DB
**Impact:** Any code that tries to use `PodcastSubscription` before the migration is applied will fail with `django.db.ProgrammingError`.
**Mitigation:** Per project policy, migrations are created but not run until Tom approves. No code outside tests should reference this model until the migration is applied.

## Race Conditions

No race conditions identified — this is a synchronous, single-threaded model definition with no async operations or shared mutable state.

## No-Gos (Out of Scope)

- Stripe webhook integration (follow-up issue)
- Email delivery logic (follow-up issue)
- User account creation for subscribers (not in MVP product philosophy)
- Multi-subscriber podcasts (future PRD phase)
- `DRIFTING`, `DISENGAGED`, `PAUSED` status values (deferred per parent PRD)
- Modifying `common.Subscription` in any way
- Running the generated migration

## Update System

No update system changes required — this feature adds a new Django model and migration file; no new environment variables, binaries, or config files are needed.

## Agent Integration

No agent integration required — this is a database model and admin registration with no MCP tool exposure needed at this stage.

## Documentation

The model will be documented inline via docstrings. No separate feature doc is needed beyond what is already in `docs/features/private-podcast-mvp.md`. The plan document itself serves as the implementation reference.

- [ ] Docstring on `PodcastSubscription` explaining the OneToOne MVP constraint and denormalization rationale

## Success Criteria

- [ ] `apps/podcast/models/podcast_subscription.py` exists with the model as specified
- [ ] Migration file created (but NOT run)
- [ ] `PodcastSubscription` importable from `apps.podcast.models`
- [ ] Registered in Django admin with `list_display`: `subscriber_email`, `podcast`, `status`, `cadence`, `next_drop_at`
- [ ] `__str__` returns a useful label (e.g., `"alice@example.com — My Podcast"`)
- [ ] `PodcastSubscriptionFactory` added to `apps/common/tests/factories.py`
- [ ] Tests in `apps/podcast/tests/test_models/test_podcast_subscription.py` cover: create, ACTIVE→CHURNED transition, cadence field, `subscription` link, `podcast` link
- [ ] All existing tests still pass

## Team Orchestration

### Team Members

- **Builder (model)**
  - Name: model-builder
  - Role: Create the `PodcastSubscription` model file, update `__init__.py`, register in admin, add factory, create migration, write tests
  - Agent Type: builder
  - Resume: true

- **Validator (model)**
  - Name: model-validator
  - Role: Verify all acceptance criteria are met, run test suite, confirm importability
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create PodcastSubscription model
- **Task ID**: build-model
- **Depends On**: none
- **Validates**: `apps/podcast/tests/test_models/test_podcast_subscription.py` (create)
- **Assigned To**: model-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `apps/podcast/models/podcast_subscription.py` with `PodcastSubscription(Timestampable)` class
  - `subscription = OneToOneField("common.Subscription", on_delete=CASCADE, related_name="podcast_subscription")`
  - `podcast = OneToOneField("podcast.Podcast", on_delete=CASCADE, related_name="podcast_subscription")`
  - `subscriber_email = EmailField(db_index=True)`
  - `subscriber_name = CharField(max_length=200)`
  - `Status(TextChoices)`: ACTIVE="active", CHURNED="churned"; `status = CharField(choices, default=ACTIVE)`
  - `Cadence(TextChoices)`: WEEKLY="weekly", BIWEEKLY="biweekly"; `cadence = CharField(choices, default=WEEKLY)`
  - `length_minutes = PositiveIntegerField(default=15)`
  - `topic_focus = TextField(blank=True)`
  - `next_drop_at = DateTimeField(null=True, blank=True)`
  - `do_not_email = BooleanField(default=False)`
  - `class Meta`: `ordering = ["-created_at"]`, `verbose_name = "Podcast Subscription"`, `verbose_name_plural = "Podcast Subscriptions"`
  - `__str__`: returns `f"{self.subscriber_email} — {self.podcast}"`
- Export `PodcastSubscription` from `apps/podcast/models/__init__.py` (add import line and `__all__` entry)
- Register `PodcastSubscriptionAdmin(ModelAdmin)` in `apps/podcast/admin.py` using `unfold.admin.ModelAdmin`
  - `list_display = ["subscriber_email", "podcast", "status", "cadence", "next_drop_at"]`
  - `list_filter = ["status", "cadence", "podcast"]`
  - `search_fields = ["subscriber_email", "subscriber_name"]`
  - `raw_id_fields = ["subscription", "podcast"]`
  - Update import at top of `admin.py` to include `PodcastSubscription`
- Add `PodcastSubscriptionFactory` to `apps/common/tests/factories.py`
  - Import `Subscription` from `apps.common.models` and `Podcast` from `apps.podcast.models` (inside create method or at top of file)
  - `default_data`: `status="active"`, `cadence="weekly"`, `length_minutes=15`, `topic_focus="AI and technology"`, `subscriber_email=lambda: f"subscriber_{random.randint(1000,9999)}@example.com"`, `subscriber_name="Test Subscriber"`
  - `create()` method: create a `common.Subscription` if `subscription` not provided; create a `Podcast` if `podcast` not provided
- Run `DJANGO_SETTINGS_MODULE=settings python manage.py makemigrations podcast --name podcast_subscription` to generate migration file (do NOT run `migrate`)
- Create `apps/podcast/tests/test_models/test_podcast_subscription.py`:
  - Import `PodcastSubscriptionFactory` and models
  - `test_create_podcast_subscription`: create via factory, assert fields saved correctly
  - `test_status_transition_active_to_churned`: set status to CHURNED, save, reload from DB, assert `status == "churned"`
  - `test_cadence_field`: assert default is "weekly", set to "biweekly", save, reload, assert
  - `test_subscription_link`: assert `podcast_subscription.subscription` is a `common.Subscription` instance
  - `test_podcast_link`: assert `podcast_subscription.podcast` is a `Podcast` instance
  - `test_str_representation`: assert `str(instance)` contains subscriber email and podcast title

### 2. Validate model
- **Task ID**: validate-model
- **Depends On**: build-model
- **Assigned To**: model-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_models/test_podcast_subscription.py -v` — all tests must pass
- Run `DJANGO_SETTINGS_MODULE=settings pytest` — full suite must pass
- Verify `from apps.podcast.models import PodcastSubscription` works in `python manage.py shell`
- Verify migration file exists in `apps/podcast/migrations/` with correct name
- Verify `PodcastSubscription` appears in Django admin at `/admin/podcast/podcastsubscription/`
- Report pass/fail status

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| New tests pass | `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_models/test_podcast_subscription.py -v` | exit code 0 |
| Full suite passes | `DJANGO_SETTINGS_MODULE=settings pytest` | exit code 0 |
| Model importable | `DJANGO_SETTINGS_MODULE=settings python -c "from apps.podcast.models import PodcastSubscription; print(PodcastSubscription)"` | exit code 0 |
| Migration file exists | `ls apps/podcast/migrations/ \| grep podcast_subscription` | output contains `podcast_subscription` |
| Admin registered | `DJANGO_SETTINGS_MODULE=settings python -c "from apps.podcast.admin import PodcastSubscriptionAdmin"` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None — the issue contains a complete solution sketch with all design decisions documented and rationale explained.
