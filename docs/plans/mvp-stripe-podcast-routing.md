---
status: Ready
type: feature
appetite: Small
owner: valorengels
created: 2026-04-10
tracking: https://github.com/yudame/cuttlefish/issues/238
last_comment_id:
---

# MVP Stripe Podcast Routing

## Problem

Stripe webhook infrastructure exists in this codebase but is entirely disconnected from the web server. `apps/integration/stripe/webhook.py` has complete, working handler functions for all relevant Stripe events — but `apps/api/views/stripe.py` is a stub (three comment lines), and no URL route points to any Stripe endpoint. Even if a webhook arrived, the existing `handle_subscription_created` handler creates `common.Subscription` records but has no awareness of podcast-specific metadata (which `Podcast` the subscription is for, topic focus, or delivery cadence).

**Desired outcome:**

- A live CSRF-exempt endpoint at `/webhooks/stripe/` that verifies Stripe signatures and dispatches to the existing handler
- `handle_subscription_created` extended to create a `PodcastSubscription` when `metadata["podcast_id"]` is present, linking it to the new `common.Subscription` in the same transaction
- `handle_subscription_deleted` extended to transition the linked `PodcastSubscription.status` to CHURNED
- Both extensions are idempotent — replayed events produce no duplicate records
- A Django admin action on `Podcast` to generate a Stripe Checkout session URL with the required metadata, so operators can manually onboard subscribers during MVP

## Freshness Check

**Baseline commit:** `af7cd83` (feat(podcast): add PodcastSubscription model linking subscribers to podcasts)

**Files verified as of baseline:**

| File | Issue claim | Current state | Disposition |
|------|------------|---------------|-------------|
| `apps/api/views/stripe.py` | Stub with only comments | Confirmed — three comment lines, no implementation | Unchanged |
| `apps/integration/stripe/webhook.py` | Complete handlers for all events | Confirmed — all six handlers present | Unchanged |
| `apps/api/urls.py` | No webhook URL wired | Confirmed — only `pending-audio` and `audio-callback` routes | Unchanged |
| `apps/api/tests/test_stripe_webhook.py` | Tests exist | Confirmed stub only: "All tests have been removed" | Unchanged |
| `apps/podcast/models/podcast_subscription.py` | Must exist (from #237) | `PodcastSubscription` model exists with `Status.CHURNED`, `OneToOneField(common.Subscription)`, `subscriber_email`, `topic_focus` | Unchanged |
| `apps/podcast/admin.py` | Has `PodcastAdmin` to extend | Confirmed — `PodcastAdmin(ModelAdmin)` registered with `@admin.register(Podcast)` | Unchanged |

**Sibling issue #237** (PodcastSubscription model): merged as `af7cd83`. Pre-requisite satisfied.

**Key API routing finding:** The api app is mounted at `api/` in `settings/urls.py` (line 135: `path("api/", include("apps.api.urls"))`). The webhook URL must therefore go into `apps/api/urls.py` and will be reachable at `/api/webhooks/stripe/` — **OR** the webhook route can be added to `settings/urls.py` at the top level as `/webhooks/stripe/`. Stripe's dashboard webhook URL is configured by the operator, so either path works; the plan uses top-level `/webhooks/stripe/` for clarity and to avoid the `/api/` prefix on an endpoint that is not an API consumer endpoint.

## Architectural Impact

- **New dependencies:** None — uses existing `stripe` library, existing `handle_stripe_webhook` function, existing `PodcastSubscription` model, existing `create_checkout_session` shortcut
- **Interface changes:** One new URL at `/webhooks/stripe/` (additive), two handler extensions (non-breaking)
- **Coupling:** `apps/integration/stripe/webhook.py` gains an import from `apps.podcast.models` — this is an acceptable cross-app dependency since podcast is already imported by other integration-layer modules
- **Data ownership:** `PodcastSubscription` is owned by `apps/podcast`; the webhook handler creates records on its behalf
- **Reversibility:** Remove the URL entry, revert the two handler extensions — no migrations needed (PodcastSubscription already migrated in #237)
- **Non-podcast behavior:** `handle_checkout_session_completed`, `handle_subscription_updated`, `handle_payment_intent_succeeded/failed` are untouched. Only `handle_subscription_created` and `handle_subscription_deleted` are extended, with changes guarded by `if podcast_id:` checks

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

- #237 merged (satisfied — `PodcastSubscription` exists at `af7cd83`)
- `STRIPE_WEBHOOK_SECRET` set in Render production environment (ops task, not code)
- Stripe dashboard: operator must configure webhook endpoint URL after deploy (ops task)

## Solution

### Data Flow

```
Stripe → POST /webhooks/stripe/
  → stripe_webhook_view (new, CSRF-exempt)
    → handle_stripe_webhook(payload, sig)  [existing, verifies signature]
      → route by event_type
        → customer.subscription.created
          → handle_subscription_created(event)  [existing + extended]
            → Subscription.objects.create(...)  [existing]
            → if podcast_id in metadata:
                → PodcastSubscription.objects.get_or_create(subscription=sub, podcast=podcast)  [new]
        → customer.subscription.deleted
          → handle_subscription_deleted(event)  [existing + extended]
            → Subscription.objects.get(stripe_id=...)  [existing]
            → subscription.status = CANCELED; subscription.save()  [existing]
            → if hasattr(subscription, 'podcast_subscription'):  [new]
                → ps.status = CHURNED; ps.save()

Operator → Django admin → Podcast change list
  → "Generate Stripe Checkout URL" action (new)
    → form: price_id, topic_focus, success_url, cancel_url
    → create_checkout_session(price_id, mode='subscription', metadata={podcast_id, topic_focus})
    → display URL in admin message
```

### Key Elements

**1. Webhook view** (`apps/api/views/stripe.py`): Replace stub with a `@csrf_exempt @require_POST` view that reads `request.body` and `HTTP_STRIPE_SIGNATURE`, calls `handle_stripe_webhook`, and returns `JsonResponse` with 200 on success or 400 on failure.

**2. URL route** (`settings/urls.py`): Add `path("webhooks/stripe/", stripe_webhook_view, name="stripe-webhook")` at the top level (not under `/api/`). CSRF exemption is already applied at the view via decorator.

**3. `handle_subscription_created` extension** (`apps/integration/stripe/webhook.py`): After creating `subscription`, check `metadata.get("podcast_id")`. If present, attempt `Podcast.objects.get(id=podcast_id)`. If found, call `PodcastSubscription.objects.get_or_create(subscription=subscription, podcast=podcast, defaults={...})`. Extract `subscriber_email` from `metadata.get("subscriber_email", "")` or the subscription's user email. Extract `topic_focus` from `metadata.get("topic_focus", "")` and `cadence` from `metadata.get("cadence", "weekly")`. Log a warning (do not raise) if `podcast_id` is present but the `Podcast` does not exist.

**4. `handle_subscription_deleted` extension** (`apps/integration/stripe/webhook.py`): After marking `subscription.status = CANCELED`, check `hasattr(subscription, 'podcast_subscription')`. If the reverse relation exists, set `subscription.podcast_subscription.status = PodcastSubscription.Status.CHURNED` and save. This is idempotent — setting CHURNED on an already-CHURNED record is a no-op.

**5. Admin action** (`apps/podcast/admin.py`): Add a method `generate_checkout_url` to `PodcastAdmin`. It uses `ModelAdmin.message_user` to display the Stripe Checkout URL. The action takes the selected `Podcast` queryset. For each podcast, call `create_subscription_checkout` from `apps.integration.stripe.shortcuts` with `metadata={"podcast_id": str(podcast.id), "topic_focus": ""}`. Display the URL in a success admin message. Since this is an MVP operator tool, `price_id` is taken from `settings.STRIPE_PODCAST_PRICE_ID` (add to settings with a fallback of empty string; operator must configure). The action is registered as `actions = ["generate_checkout_url"]` on `PodcastAdmin`.

### Technical Approach

**Idempotency pattern:** `handle_subscription_created` uses `Subscription.objects.create()` — this will raise `IntegrityError` on duplicate `stripe_id` due to the unique constraint. The existing code does not guard against this, but it is acceptable: Stripe's `customer.subscription.created` event fires once per subscription. The `PodcastSubscription` creation uses `get_or_create` to be explicitly idempotent even if the handler runs twice.

**Import guard:** `PodcastSubscription` and `Podcast` are imported at the top of `handle_subscription_created` (not module-level in `webhook.py`) using a local import to avoid circular dependency risk. Alternatively, a module-level import is fine since `apps.podcast` does not import from `apps.integration.stripe`.

**Admin action — single vs. multiple selection:** The action is designed for single-podcast selection. If multiple podcasts are selected, it generates one URL per podcast and shows multiple messages. This is acceptable for MVP.

**`subscriber_email` sourcing:** The `customer.subscription.created` event does not directly contain the subscriber's email. It can be sourced from the linked `User.email` (already looked up via `customer_id` → `User.stripe_customer_id`) or from metadata passed through the checkout session. The admin action must therefore also pass `subscriber_email` in checkout metadata. The handler reads `metadata.get("subscriber_email", "")` and falls back to `subscription.user.email` if available.

## Failure Path Test Strategy

### Signature verification failure
`handle_stripe_webhook` already returns `{"success": False, "error": "...", "status": "invalid_signature"}`. The view returns this dict as `JsonResponse(result, status=400)`. Test: send a request with an invalid `Stripe-Signature` header, assert HTTP 400.

### `podcast_id` in metadata but Podcast not found
Log a warning, skip `PodcastSubscription` creation, return success (do not fail the webhook). Stripe must receive 2xx or it retries. Failing the webhook for a missing podcast would cause infinite retries. Test: `handle_subscription_created` with `metadata={"podcast_id": "999999"}` produces one `Subscription`, zero `PodcastSubscription`, logs a warning.

### `customer.subscription.deleted` with no linked `PodcastSubscription`
Already handled — `hasattr(subscription, 'podcast_subscription')` is False. No exception. Return existing success response. Test: delete handler with a non-podcast subscription produces no error.

### Empty `price_id` in admin action
`create_subscription_checkout` returns `{"success": False}` if `STRIPE_ENABLED=False` or `STRIPE_API_KEY` is unset. The action must check this and display an error message to the operator rather than raising. Test is manual (admin interaction).

## Rabbit Holes

- **`BillingEvent` audit log** — explicitly out of scope for MVP. The `stripe_id` uniqueness constraint on `common.Subscription` is sufficient idempotency for this stage
- **Webhook signature timestamp tolerance** — Stripe's SDK handles the 5-minute tolerance window internally in `client.verify_webhook_signature`. Do not add custom timestamp logic
- **Stripe customer lookup at subscription creation** — `handle_subscription_created` already finds the user via `stripe_customer_id`. Do not add a secondary email-based lookup
- **`PodcastSubscription` deduplication across podcasts** — the `OneToOneField` on both `subscription` and `podcast` enforces uniqueness at the DB level. Do not add application-layer uniqueness checks
- **Webhook retry rate limiting** — out of scope. Stripe controls retry rate. Standard Django request handling is sufficient
- **Topic focus form in checkout** — MVP admin action passes `topic_focus=""` in metadata. Subscriber-entered topic focus is a future enhancement. Do not add a custom Stripe Checkout form for topic focus

## Risks

### Risk 1: `/webhooks/stripe/` vs `/api/webhooks/stripe/`
**Impact:** If the route is added under `apps/api/urls.py` (mounted at `/api/`), the endpoint is at `/api/webhooks/stripe/`. If added to `settings/urls.py`, it is at `/webhooks/stripe/`. The operator must configure Stripe's dashboard with the correct URL.
**Mitigation:** Plan specifies top-level `/webhooks/stripe/` in `settings/urls.py`. This is the conventional Stripe webhook path. Document in the operator runbook (not code).

### Risk 2: `apps.podcast` import in `apps.integration.stripe.webhook`
**Impact:** Creates a cross-layer dependency (integration layer importing from application layer).
**Mitigation:** Use a local import inside the `if podcast_id:` block rather than a module-level import. This prevents circular import at startup and makes the dependency explicit and optional.

### Risk 3: `STRIPE_PODCAST_PRICE_ID` not configured
**Impact:** Admin action generates a Checkout URL with an empty `price_id`, which Stripe rejects.
**Mitigation:** Add a guard in the admin action: if `settings.STRIPE_PODCAST_PRICE_ID` is empty, show an error `message_user` and return early. Add the setting to `.env.example` with a placeholder comment.

## No-Gos (Out of Scope)

- `BillingEvent` / audit log model (deferred to parent PRD)
- Custom rate limiting on the webhook endpoint
- Topic focus intake form for subscribers during Stripe Checkout
- Modification of `handle_checkout_session_completed`, `handle_subscription_updated`, `handle_payment_intent_succeeded`, or `handle_payment_intent_failed`
- Any changes to non-podcast subscription flows
- Email notification to subscriber on creation (separate issue #240)
- Multi-podcast subscriber support (one-to-one enforced by OneToOneField)
- Webhook endpoint authentication beyond Stripe signature verification

## Success Criteria

- [ ] `GET`/`POST /webhooks/stripe/` — POST with valid payload and signature returns HTTP 200 with JSON
- [ ] `POST /webhooks/stripe/` with invalid signature returns HTTP 400 with JSON error body
- [ ] `handle_subscription_created` with `metadata={"podcast_id": "<id>", "topic_focus": "AI", "subscriber_email": "test@example.com"}` creates both a `Subscription` and a linked `PodcastSubscription`
- [ ] Replaying the same `customer.subscription.created` event produces exactly one `PodcastSubscription` (idempotency via `get_or_create`)
- [ ] `handle_subscription_created` with no `podcast_id` in metadata creates only a `Subscription` (non-podcast path unchanged)
- [ ] `handle_subscription_deleted` on a subscription with a linked `PodcastSubscription` transitions `PodcastSubscription.status` to `CHURNED`
- [ ] `handle_subscription_deleted` on a subscription with no linked `PodcastSubscription` returns success without error
- [ ] Unit tests in `apps/api/tests/test_stripe_webhook.py` cover: webhook view 200, webhook view 400 (bad sig), subscription-created with podcast metadata (creates PodcastSubscription), subscription-created without podcast metadata (no PodcastSubscription), subscription-created replay (idempotent), subscription-deleted with podcast link (CHURNED), subscription-deleted without podcast link (no error)
- [ ] `PodcastAdmin` has a `generate_checkout_url` action that calls `create_subscription_checkout` and displays the URL in the admin message
- [ ] All existing tests pass: `DJANGO_SETTINGS_MODULE=settings pytest apps/api/ apps/podcast/ apps/integration/ -v`

## Step by Step Tasks

### 1. Restore stripe_webhook_view in apps/api/views/stripe.py
- **Task ID**: restore-view
- **Depends On**: none
- **File**: `apps/api/views/stripe.py`
- Replace the three-line stub with a proper view:
  - Import `csrf_exempt` from `django.views.decorators.csrf`
  - Import `require_POST` from `django.views.decorators.http`
  - Import `JsonResponse` from `django.http`
  - Import `handle_stripe_webhook` from `apps.integration.stripe.webhook`
  - Decorate `stripe_webhook_view` with `@csrf_exempt` and `@require_POST`
  - Read `payload = request.body` and `signature = request.META.get("HTTP_STRIPE_SIGNATURE", "")`
  - Call `result = handle_stripe_webhook(payload, signature)`
  - Return `JsonResponse(result, status=400)` if `not result.get("success")`, else `JsonResponse(result, status=200)`

### 2. Wire URL in settings/urls.py
- **Task ID**: wire-url
- **Depends On**: restore-view
- **File**: `settings/urls.py`
- Import `stripe_webhook_view` from `apps.api.views.stripe`
- Add `path("webhooks/stripe/", stripe_webhook_view, name="stripe-webhook")` to `urlpatterns` above the `api/` include

### 3. Extend handle_subscription_created for podcast metadata
- **Task ID**: extend-created
- **Depends On**: none (can run in parallel with tasks 1-2)
- **File**: `apps/integration/stripe/webhook.py`
- After the `subscription = Subscription.objects.create(...)` call:
  ```python
  podcast_id = metadata.get("podcast_id")
  if podcast_id:
      try:
          from apps.podcast.models import Podcast, PodcastSubscription
          podcast = Podcast.objects.get(id=podcast_id)
          subscriber_email = metadata.get("subscriber_email", "") or (user.email if user else "")
          ps, created = PodcastSubscription.objects.get_or_create(
              subscription=subscription,
              podcast=podcast,
              defaults={
                  "subscriber_email": subscriber_email,
                  "subscriber_name": metadata.get("subscriber_name", ""),
                  "topic_focus": metadata.get("topic_focus", ""),
                  "cadence": metadata.get("cadence", PodcastSubscription.Cadence.WEEKLY),
              },
          )
          if created:
              logger.info(f"Created PodcastSubscription {ps.id} for podcast {podcast_id}")
          else:
              logger.info(f"PodcastSubscription already exists for subscription {subscription_id}")
      except Podcast.DoesNotExist:
          logger.warning(f"Podcast {podcast_id} not found — skipping PodcastSubscription creation")
  ```
- Return dict from function must also include `"podcast_subscription_created": created` when podcast path runs

### 4. Extend handle_subscription_deleted for CHURNED transition
- **Task ID**: extend-deleted
- **Depends On**: none (can run in parallel with tasks 1-2)
- **File**: `apps/integration/stripe/webhook.py`
- After `subscription.save()` in the success path of `handle_subscription_deleted`:
  ```python
  if hasattr(subscription, 'podcast_subscription'):
      from apps.podcast.models import PodcastSubscription
      ps = subscription.podcast_subscription
      ps.status = PodcastSubscription.Status.CHURNED
      ps.save(update_fields=["status", "updated_at"])
      logger.info(f"Transitioned PodcastSubscription {ps.id} to CHURNED")
  ```

### 5. Add generate_checkout_url admin action to PodcastAdmin
- **Task ID**: admin-action
- **Depends On**: none (can run in parallel)
- **File**: `apps/podcast/admin.py`
- Add import: `from django.conf import settings`
- Add import: `from apps.integration.stripe.shortcuts import create_subscription_checkout`
- Add method to `PodcastAdmin`:
  ```python
  @admin.action(description="Generate Stripe Checkout URL")
  def generate_checkout_url(self, request, queryset):
      price_id = getattr(settings, "STRIPE_PODCAST_PRICE_ID", "")
      if not price_id:
          self.message_user(request, "STRIPE_PODCAST_PRICE_ID is not configured.", level="error")
          return
      for podcast in queryset:
          result = create_subscription_checkout(
              price_id=price_id,
              metadata={
                  "podcast_id": str(podcast.id),
                  "topic_focus": "",
              },
          )
          if result.get("success") and result.get("url"):
              self.message_user(request, f"{podcast.title}: {result['url']}")
          else:
              self.message_user(request, f"{podcast.title}: Failed — {result.get('error', 'unknown')}", level="error")
  ```
- Register action: add `"generate_checkout_url"` to `PodcastAdmin.actions`

### 6. Write unit tests
- **Task ID**: write-tests
- **Depends On**: extend-created, extend-deleted, restore-view, wire-url
- **File**: `apps/api/tests/test_stripe_webhook.py`
- Replace stub with full test module. Use `SubscriptionFactory` and `PodcastFactory` from `apps.common.tests.factories`
- Patch `apps.integration.stripe.shortcuts.handle_webhook_event` to avoid real Stripe API calls
- Test cases:
  1. `test_webhook_view_returns_200_on_success` — mock valid event, POST to `/webhooks/stripe/`, assert 200
  2. `test_webhook_view_returns_400_on_bad_signature` — mock `handle_stripe_webhook` to return `{"success": False, "error": "bad sig"}`, assert 400
  3. `test_handle_subscription_created_creates_podcast_subscription` — call `handle_subscription_created` with `metadata={"podcast_id": str(podcast.id), "subscriber_email": "a@b.com", "topic_focus": "AI"}`, assert `PodcastSubscription` created and linked
  4. `test_handle_subscription_created_without_podcast_id` — call without `podcast_id`, assert no `PodcastSubscription` created
  5. `test_handle_subscription_created_idempotent` — call twice with same `stripe_id` (first creates, second gets existing), assert one `PodcastSubscription`
  6. `test_handle_subscription_deleted_transitions_to_churned` — create `PodcastSubscription`, call `handle_subscription_deleted`, assert `status == CHURNED`
  7. `test_handle_subscription_deleted_without_podcast_subscription` — call on non-podcast subscription, assert no error and success response

### 7. Add STRIPE_PODCAST_PRICE_ID to settings
- **Task ID**: settings-update
- **Depends On**: none
- **File**: `settings/base.py` (or `settings/third_party.py`)
- Add: `STRIPE_PODCAST_PRICE_ID = env("STRIPE_PODCAST_PRICE_ID", default="")`
- **File**: `.env.example`
- Add: `STRIPE_PODCAST_PRICE_ID=price_xxx  # Stripe Price ID for private podcast subscription`

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| All API tests | `DJANGO_SETTINGS_MODULE=settings pytest apps/api/ -v` | exit code 0 |
| Integration tests | `DJANGO_SETTINGS_MODULE=settings pytest apps/integration/ -v` | exit code 0 |
| Podcast tests | `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/ -v` | exit code 0 |
| Webhook URL resolves | `DJANGO_SETTINGS_MODULE=settings python -c "from django.urls import reverse; print(reverse('stripe-webhook'))"` | `/webhooks/stripe/` |
| Manual smoke test | `stripe listen --forward-to localhost:8000/webhooks/stripe/` then `stripe trigger customer.subscription.created` | See log: "Created PodcastSubscription" |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
