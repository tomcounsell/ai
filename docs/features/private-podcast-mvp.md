# Private Podcast — MVP PRD

Status: **Draft** · Scope: **Minimum viable product** · Parent doc: [Private Podcast PRD](./private-podcast-prd.md)

The parent PRD is the full product vision. This document is a tight cut of what must exist to validate the core bet with 5-10 paying subscribers. Anything not in this doc is deferred to the parent PRD's phased rollout.

---

## 1. Goal

Validate: **will 5-10 humans pay a monthly fee for a weekly AI-generated podcast on their chosen topic, delivered to a private feed?**

Out of scope for validation:
- Cold outreach conversion
- Agentic reply handling
- Preview/approval loops
- Any self-serve subscriber UI

Those live in the [parent PRD](./private-podcast-prd.md) and get built after this MVP proves the core economic loop.

---

## 2. The Journey, Compressed

The full 4-stage journey collapses to **one automated flow** with manual bookends:

```
(manual acquisition)
  → Stripe Checkout
    → webhook creates subscription
      → [ACCESS READY] email
        → (manual topic_focus setup by operator)
          → scheduled task enqueues produce_episode on cadence
            → episode publishes
              → [NEW EPISODE] email
                → (repeat weekly)
```

**Manual operations** (acceptable at < 20 subscribers):
- Acquisition via warm intro / personal network
- First-episode topic setup in Django admin
- Reply handling via shared mailbox
- Churn handling (subscriber emails → operator cancels in Stripe + admin)
- Pause, resume, topic pivots — all done in admin on subscriber's behalf

**Automated operations**:
- Stripe webhook → subscription creation
- Post-payment access delivery email
- Scheduled episode enqueuing
- New episode delivery email

---

## 3. In Scope

### 3.1 Data Models (2 new)

```python
class PodcastSubscription(Timestampable):
    podcast = OneToOneField("podcast.Podcast", on_delete=CASCADE)
    subscriber_email = EmailField(db_index=True)
    subscriber_name = CharField(max_length=200)
    stripe_customer_id = CharField(max_length=100, unique=True)
    stripe_subscription_id = CharField(max_length=100, unique=True)

    class Status(TextChoices):
        ACTIVE = "active"
        CHURNED = "churned"
    status = CharField(choices=Status.choices, default=ACTIVE)

    class Cadence(TextChoices):
        WEEKLY = "weekly"
        BIWEEKLY = "biweekly"
    cadence = CharField(choices=Cadence.choices, default=WEEKLY)

    length_minutes = PositiveIntegerField(default=15)
    topic_focus = TextField()
    next_drop_at = DateTimeField(null=True)
    do_not_email = BooleanField(default=False)


class BillingEvent(Timestampable):
    subscription = ForeignKey("podcast.PodcastSubscription", null=True, on_delete=SET_NULL)
    stripe_event_id = CharField(max_length=100, unique=True)
    event_type = CharField(max_length=100)
    payload = JSONField()
    processed_at = DateTimeField(null=True)
```

All other models in the parent PRD (`PitchTarget`, `PodcastPitch`, `EpisodePreview`, `SubscriberReply`, `SuppressionEntry`) are deferred.

### 3.2 Stripe Integration

- Stripe Checkout hosted session — operator creates per subscriber manually
- Webhook endpoint at `/webhooks/stripe/` handling:
  - `checkout.session.completed` → create `PodcastSubscription`, write `BillingEvent`, trigger access email
  - `customer.subscription.deleted` → mark subscription CHURNED
- Signature verification via `STRIPE_WEBHOOK_SECRET`
- **Idempotent by `stripe_event_id`** — no duplicate subscriptions on retry

Customer Portal (self-serve cancel) enabled via Stripe dashboard config — no custom UI needed.

### 3.3 Email

Provider: **Postmark** (outbound only for MVP; inbound is a shared mailbox, not a webhook).

Required setup:
- Dedicated sending subdomain (e.g., `mail.ai.yuda.me`)
- SPF, DKIM, DMARC records
- Template-based send via Django template engine (not vendor templates)
- Both HTML and plain-text versions for every template

Templates in scope (2):
1. `[ACCESS READY]` — post-payment access delivery
2. `[NEW EPISODE]` — on episode publish

Both follow the [Email UX spec](./private-podcast-email-ux.md) exactly. All other templates in the parent PRD are deferred.

Inbound handling: a shared mailbox `podcast@ai.yuda.me` that the operator reads manually. No classifier, no `SubscriberReply` model, no webhook.

### 3.4 New Tasks (4)

| Task | Trigger | Purpose |
|---|---|---|
| `handle_stripe_webhook(event_id)` | Webhook POST | Create subscription + BillingEvent atomically |
| `send_access_ready_email(subscription_id)` | After subscription create | Deliver feed URL |
| `send_new_episode_email(episode_id)` | `post_save` on Episode when `published_at` transitions from null | Notify subscriber |
| `enqueue_due_episodes` | Scheduled (daily via cron) | Find subscriptions where `next_drop_at <= now`, enqueue `produce_episode`, advance `next_drop_at` by cadence |

All four reuse the existing Django `@task` framework and production pipeline. No changes to the existing 12-phase workflow.

### 3.5 Views

- `POST /webhooks/stripe/` — webhook handler (only new view)
- Existing feed URL — unchanged
- Existing `/briefing/` landing — unchanged (serves as pricing / value-prop page)

No adjust page. No operator console. No subscriber dashboard.

### 3.6 Admin

Use Django admin for all operator work:
- Create draft `Podcast` records with `privacy=restricted`
- Generate `PodcastAccessToken` via existing admin
- Set `PodcastSubscription.topic_focus` and `next_drop_at` after checkout
- Set `do_not_email=True` on unsubscribe requests
- Cancel Stripe subscription via Stripe dashboard (not our UI)

Register the 2 new models in admin with reasonable list displays and search fields.

---

## 4. Out of Scope (Deferred to Parent PRD)

Everything below is in the [parent PRD](./private-podcast-prd.md) and picked up in later phases:

- Cold outreach pipeline (`PitchTarget`, `PodcastPitch`, sample episode generation)
- Preview / approval loop (`EpisodePreview`, `[EPISODE N PREVIEW]` emails)
- Inbound email webhook, `SubscriberReply` model, intent classifier
- Operator console (Django admin is sufficient)
- Adjust-your-podcast self-serve page
- `[CHECKOUT]`, `[MONTHLY]`, `[ACCESS EXPIRING]`, `[ACCESS REVOKED]`, `[FEED UPDATED]` email templates
- DRIFTING / DISENGAGED engagement monitoring
- Monthly reality-check emails
- Pause / resume automation
- Proper `SuppressionEntry` model (use `do_not_email` bool + Postmark native suppression)
- Agentic reply parsing / state machine orchestration

---

## 5. Must Get Right in MVP (Non-Negotiable)

These are decisions that are **expensive or impossible to change later**. Everything else can iterate freely.

1. **`PodcastSubscription` as a distinct model**, not fields on `Podcast`. Billing is a different concern.
2. **Webhook handler idempotent by `stripe_event_id`.** Stripe retries on failure; a non-idempotent handler creates duplicate subscriptions and billing mess.
3. **`cadence` as a `TextChoices` enum**, not a string. Scheduler logic grows with cadences; start typed.
4. **`topic_focus` as a first-class field** on `PodcastSubscription`, not a JSON blob. Every future AI tool will read it.
5. **SPF / DKIM / DMARC configured on day one.** Domain reputation compounds; early bad sends are expensive to recover from.
6. **`[NEW EPISODE]` email follows the [Email UX spec](./private-podcast-email-ux.md) exactly.** Sets the tone for every future email.
7. **Private feed URL must be stable** per subscriber — already correct via existing `PodcastAccessToken`. Never regenerate tokens in MVP.
8. **CAN-SPAM compliance** in every outbound template: physical address, unsubscribe mechanism (even if it's just "reply STOP"), clear sender identification.

---

## 6. Upgrade Path (Why This MVP Is Forward-Compatible)

Every deferred feature in the parent PRD slots in **additively**, without refactoring MVP models:

| Deferred feature | How it's added |
|---|---|
| `EpisodePreview` preview loop | New model + task that runs 48h before `next_drop_at`. No MVP changes. |
| `SubscriberReply` + reply parsing | New model + Postmark Inbound webhook + classifier. MVP's shared mailbox becomes the fallback. |
| Cold outreach pipeline | New `PitchTarget`, `PodcastPitch` models. Orthogonal to subscriber flow. |
| DRIFTING / DISENGAGED statuses | Extend `PodcastSubscription.Status` enum. Safe additive migration. |
| Pause / resume | Add `paused_at` field. Scheduler task checks it. |
| Proper suppression list | New `SuppressionEntry` model. Migrate existing `do_not_email` booleans. Trivial. |
| Adjust-your-podcast page | New view, reuses existing `PodcastAccessToken` for auth. |
| Operator console | New staff views over existing models. |
| `[CHECKOUT]` email + YES reply automation | New template + reply classifier. MVP's manual Stripe link becomes optional shortcut. |

No model needs to be reshaped. That's the test of the MVP cut.

---

## 7. Scope Estimate

| Work item | Rough effort |
|---|---|
| 2 new models + migrations (gated pending approval) | half day |
| Stripe integration: checkout session creation + webhook handler | 1-2 days |
| `[ACCESS READY]` + `[NEW EPISODE]` templates and send pipeline | 1 day |
| `enqueue_due_episodes` scheduled task | half day |
| `post_save` signal for episode publish → new episode email | half day |
| Postmark setup + domain auth | half day |
| Django admin registrations and usability polish | half day |
| End-to-end test with a real Stripe payment | 1 day |

Not including: manually onboarding the first 3-5 friends (pure calendar time, ~1 week real-world).

---

## 8. Open Decisions (Block MVP Start)

Must be answered before any code is written. Everything not listed here can be decided inline during implementation.

1. **Price**: What's the monthly rate? Single tier or two options (weekly / biweekly)?
2. **Target first subscribers**: Specifically who? Need 5-10 names before building acquisition-less MVP.
3. **Stripe product setup**: One product + one price for weekly? Or one product with price variants for cadence options?
4. **Grace period on churn**: Does the feed stay active for N days after churn, or revoked immediately? (Parent PRD suggests 30 days.)
5. **Operator mailbox**: Does `podcast@ai.yuda.me` exist? Who monitors it, and with what response SLA?
6. **Quality gate for episode 1**: Does the operator manually review each subscriber's first episode before it ships, or do we trust the existing pipeline's quality checks?

Everything else (reply wording, edge cases, monitoring thresholds, retry policies) can be decided during implementation.

---

## 9. Success Criteria

MVP is successful if, within 60 days of launch:

1. **≥ 5 paying subscribers** onboarded end-to-end via Stripe Checkout
2. **≥ 95% of scheduled episodes ship on the promised day**
3. **< 1 churn per 10 subscribers in the first 30 days**
4. **Zero production incidents** causing subscriber-visible downtime
5. **Qualitative feedback** from at least 3 subscribers confirming the weekly episode is useful enough to continue paying

If these are hit, proceed with parent PRD Phase 2+ (cadence automation maturation, preview loop, reply automation, eventually cold outreach).

If not hit, parent PRD work is paused — the core bet hasn't been proven and scaling automation is premature.

---

## 10. Related Documents

- [Parent PRD](./private-podcast-prd.md) — full vision, all features, all open questions
- [Journey Mapping](./private-podcast-journey-mapping.md) — narrative product design
- [Email UX](./private-podcast-email-ux.md) — outgoing email structure and tone
