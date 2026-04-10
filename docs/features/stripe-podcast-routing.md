# Stripe Podcast Routing

Connects the Stripe webhook infrastructure to podcast subscriptions. Stripe sends webhook events to `/webhooks/stripe/`, which are verified and dispatched to handler functions that create or update `PodcastSubscription` records.

## Webhook Endpoint

**URL:** `POST /webhooks/stripe/`
**CSRF-exempt:** Yes (Stripe sends from outside the browser session)
**Signature verification:** Handled via `STRIPE_WEBHOOK_SECRET` environment variable

The endpoint is registered at the top level of `settings/urls.py` to avoid the `/api/` prefix, which is appropriate since this endpoint is called by Stripe, not by API consumers.

## Metadata Contract

When Stripe Checkout creates a subscription with podcast-specific metadata, the following fields are read by `handle_subscription_created`:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `podcast_id` | str (integer ID) | Yes | ID of the `Podcast` to subscribe to. If present, triggers `PodcastSubscription` creation. If absent, only a `Subscription` is created (non-podcast path unchanged). |
| `subscriber_email` | str | No | Subscriber's email address. Falls back to `user.email` if the `customer_id` maps to a Django `User`. |
| `subscriber_name` | str | No | Subscriber's full name. |
| `topic_focus` | str | No | Subscriber-specified topic focus for episode curation. |
| `cadence` | str | No | Delivery cadence: `"weekly"` (default) or `"biweekly"`. |

## Handler Extensions

### `handle_subscription_created` (extended)

Located in `apps/integration/stripe/webhook.py`. After creating the `common.Subscription` record, it checks `metadata.get("podcast_id")`:

- **If `podcast_id` present and `Podcast` exists:** calls `PodcastSubscription.objects.get_or_create(subscription=sub, podcast=podcast, defaults={...})` — idempotent by design.
- **If `podcast_id` present but `Podcast` not found:** logs a warning, skips `PodcastSubscription` creation, returns success. Stripe must receive 2xx or it retries indefinitely — failing for a missing podcast would cause infinite retries.
- **If `podcast_id` absent:** no-op for podcast routing, non-podcast subscription flow unchanged.

### `handle_subscription_deleted` (extended)

After marking `subscription.status = CANCELED`, checks `hasattr(subscription, 'podcast_subscription')`. If the reverse relation exists, sets `podcast_subscription.status = CHURNED` and saves. This is idempotent — setting CHURNED on an already-CHURNED record is a no-op.

## Idempotency

| Scenario | Guarantee |
|----------|-----------|
| `customer.subscription.created` replayed | `PodcastSubscription.objects.get_or_create` — second call returns existing record, `created=False` |
| `customer.subscription.deleted` replayed | Setting `CHURNED` on already-CHURNED is idempotent |

## Admin Action: Generate Stripe Checkout URL

`PodcastAdmin` exposes a `generate_checkout_url` action. Operators select one or more podcasts in the Django admin and trigger this action to generate a Stripe Checkout URL for manual subscriber onboarding.

**Prerequisites:**
- `STRIPE_PODCAST_PRICE_ID` environment variable must be set to the Stripe Price ID for the podcast subscription product.
- `STRIPE_WEBHOOK_SECRET` must be set so that incoming webhooks can be verified.
- `STRIPE_API_KEY` must be set (test key for development, live key for production).

**Behaviour:**
1. Reads `settings.STRIPE_PODCAST_PRICE_ID`
2. Calls `create_subscription_checkout(price_id=..., metadata={"podcast_id": str(podcast.id), "topic_focus": ""})`
3. Displays the resulting URL in an admin success message — operator copies and sends to the subscriber

**Empty `price_id` guard:** If `STRIPE_PODCAST_PRICE_ID` is not configured, the action shows an error message and returns early (no Stripe API call is made).

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `STRIPE_API_KEY` | Yes | Stripe secret key (`sk_test_xxx` for dev, `sk_live_xxx` for production) |
| `STRIPE_WEBHOOK_SECRET` | Yes | Webhook signing secret from Stripe dashboard (`whsec_xxx`) |
| `STRIPE_PODCAST_PRICE_ID` | Yes (for admin action) | Stripe Price ID for the podcast subscription product (`price_xxx`) |

All three are in `.env.example` with placeholder values.

## Failure Paths

| Failure | Behaviour |
|---------|-----------|
| Invalid webhook signature | `handle_stripe_webhook` returns `{"success": False, "status": "invalid_signature"}`, view returns HTTP 400 |
| `podcast_id` in metadata but Podcast not found | Warning logged, `Subscription` created, `PodcastSubscription` skipped, HTTP 200 returned to Stripe |
| `customer.subscription.deleted` with no `PodcastSubscription` | `hasattr` returns False, no error, HTTP 200 returned |
| `STRIPE_PODCAST_PRICE_ID` not configured | Admin action shows error message, no Stripe API call |

## Data Flow

```
Stripe → POST /webhooks/stripe/
  → stripe_webhook_view (CSRF-exempt, require_POST)
    → handle_stripe_webhook(payload, sig)
      → handle_webhook_event(payload, sig)  [verifies signature]
        → customer.subscription.created
          → handle_subscription_created(event)
            → Subscription.objects.create(...)
            → if podcast_id in metadata:
                → PodcastSubscription.objects.get_or_create(...)
        → customer.subscription.deleted
          → handle_subscription_deleted(event)
            → subscription.status = CANCELED; save()
            → if hasattr(subscription, 'podcast_subscription'):
                → ps.status = CHURNED; save()

Operator → Django admin → Podcast change list
  → "Generate Stripe Checkout URL" action
    → create_subscription_checkout(price_id, metadata={podcast_id, topic_focus})
    → display URL in admin success message
```

## Operator Runbook

1. Set `STRIPE_WEBHOOK_SECRET` in the Render production environment
2. Set `STRIPE_API_KEY` in the Render production environment
3. Set `STRIPE_PODCAST_PRICE_ID` to the Stripe Price ID for the podcast subscription
4. Configure the Stripe dashboard webhook endpoint to point to `https://yourdomain.com/webhooks/stripe/` with events: `customer.subscription.created`, `customer.subscription.deleted`
5. To manually onboard a subscriber: navigate to Admin → Podcasts, select the podcast, run "Generate Stripe Checkout URL", copy the URL, send to the subscriber
