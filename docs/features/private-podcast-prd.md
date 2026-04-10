# Private Podcast — Product Requirements Document

Status: **Draft** · Owner: Product · Companion docs: [MVP PRD](./private-podcast-mvp.md) · [Journey Mapping](./private-podcast-journey-mapping.md) · [Email UX](./private-podcast-email-ux.md)

> **Build order**: Start with the [MVP PRD](./private-podcast-mvp.md) — it's the minimum cut that validates the core economic bet with 5-10 paying subscribers. This document is the full vision; features here are picked up in later phases once the MVP proves out.

---

## 1. Overview

### 1.1 Executive Summary

The **Private Podcast** product delivers a custom-generated, weekly audio briefing to individual subscribers, operated end-to-end by an agentic email system. The subscriber never logs in. Pitch, onboarding, episode intake, and ongoing service all happen in their inbox. The product is positioned as a **concierge service**, not a SaaS tool.

### 1.2 Vision

A subscriber receives a real 4-minute sample episode in a cold email. If they reply "yes", they complete Stripe checkout and begin receiving a weekly personalized podcast on their chosen topic — produced, previewed, and shipped by an autonomous agent. They redirect the agent with single-word email replies when they want something different. Silence is approval.

### 1.3 Strategic Context

- The existing podcast production pipeline (12-phase, DB-backed) already supports high-quality personalized episode generation.
- Private feed hosting (restricted bucket + tokenized URLs) already exists via `Podcast.privacy = restricted` and `PodcastAccessToken`.
- What's missing: the business layer (subscriptions, billing, outreach) and the agentic email layer (inbound reply parsing, state machine orchestration).
- This product monetizes the production pipeline directly, without requiring brand marketing or audience-building.

---

## 2. Problem & Opportunity

### 2.1 Problem

Busy professionals want personalized, high-signal audio content but:
- Generic podcasts are optimized for broad audiences, not individual interests.
- Newsletter curation requires reading time they don't have.
- AI summarizers produce text, not ambient-consumable audio.
- Existing "custom podcast" products are self-serve SaaS with high cognitive overhead.

### 2.2 Opportunity

Use AI-generated audio to create **truly personalized weekly briefings** at a price point that reflects the white-glove nature of the service ($25-100/mo range). Remove all configuration friction by operating the entire lifecycle through email.

### 2.3 Why Now

- NotebookLM and similar models produce acceptable two-host audio.
- The existing 12-phase pipeline proves we can generate episodes autonomously at quality.
- Stripe Checkout + inbound email parsing are mature, low-friction primitives.
- The inbox-native pattern is emerging as a trust-building alternative to dashboards for premium services.

---

## 3. Goals & Non-Goals

### 3.1 Goals

| # | Goal | Measurement |
|---|---|---|
| G1 | Deliver personalized weekly podcasts with zero subscriber-side configuration | ≥ 80% of subscribers never open the adjust page after initial checkout |
| G2 | Convert cold outreach at materially higher rates than traditional cold email | Sample-reply rate ≥ 5%; reply-to-paid ≥ 60% |
| G3 | Maintain high retention through proactive service | 30-day retention ≥ 85%; 6-month retention ≥ 50% |
| G4 | Operate agentically with minimal human intervention | ≤ 10% of replies escalated to a human operator |
| G5 | Ship episodes on promised cadence reliably | ≥ 99% of scheduled episodes ship on the promised date |

### 3.2 Non-Goals

- **No subscriber dashboard.** A minimal "adjust" page is the only web surface subscribers interact with.
- **No mobile app.** The feed URL works in any podcast app; that's the app.
- **No content discovery / browsing / search.** Each subscriber gets their feed, not a catalog.
- **No social features.** No comments, sharing, likes, ratings.
- **No multi-tenant teams in v1.** One subscriber = one feed. Team/shared feeds are a future consideration.
- **No custom voice selection in v1.** All episodes use the default NotebookLM two-host format.
- **No real-time chat.** All interaction is asynchronous email.

### 3.3 Top-Line Success Metrics

- **Paid subscriptions**: cumulative count
- **MRR**: monthly recurring revenue
- **Retention curves**: 30/60/90-day
- **Reply-intent accuracy**: % of inbound replies correctly auto-classified
- **Delivery SLA**: % of episodes shipped on promised date

---

## 4. Personas

### 4.1 Primary: The Subscriber

- **Who**: Professional with high income and low available time (exec, founder, investor, researcher, senior IC).
- **Motivation**: Wants curated, specific information on an ongoing basis, delivered in a format that fits their existing audio habits (commute, gym, walk).
- **Pain**: Signed up for too many newsletters, none of which they read. Podcasts they subscribe to are 10% relevant.
- **Mental model**: "I'm paying a concierge. They should handle it. If I have to configure it, I'll churn."
- **Tolerance**: Will tolerate imperfect episodes if the agent corrects quickly. Will NOT tolerate nag emails, forms, or login requirements.

### 4.2 Secondary: The Human Operator

- **Who**: Internal ops staff member monitoring the system.
- **Role**: Reviews ambiguous inbound replies, approves pitch targets, handles billing escalations, monitors deliverability.
- **Need**: A focused ops console (not Django admin) with a queue of flagged items and one-click actions.
- **SLA**: 1-business-day response to any escalated reply. Billing issues within 4 business hours.

### 4.3 Tertiary: The Prospect

- **Who**: Cold outreach recipient, pre-conversion.
- **State**: Hasn't heard of us. Receives our sample email alongside 50 other cold emails.
- **Decision window**: 10 seconds of attention budget.
- **Win condition**: They listen to the sample and reply "yes" (or forward to a friend who does).

---

## 5. User Journey Summary

See [Journey Mapping](./private-podcast-journey-mapping.md) for the full narrative. At a glance:

![Lifecycle overview](./private-podcast-diagrams/01-lifecycle.png)

1. **Pitch** — Cold email contains a real 4-minute sample episode made for the recipient
2. **Conversion** — "yes" reply → Stripe checkout → access delivery (< 60s post-payment)
3. **First Episode** — Preview email proposes Episode 1; silence = ship as proposed
4. **Steady Service** — Weekly rhythm: Friday preview → Monday delivery → (repeat). Monthly reality check is the only "open" question.

Agent-wide rules: single-word replies drive state transitions; ignore = approval; humans handle exceptions.

The full subscriber state machine:

![Subscriber state machine](./private-podcast-diagrams/02-subscriber-states.png)

---

## 6. Functional Requirements

Grouped by journey stage, with ID prefixes (FR-P = Pitch, FR-C = Conversion, FR-F = First episode, FR-S = Steady, FR-X = Cross-cutting).

### 6.1 Pitch (Stage 1)

| ID | Requirement | Priority |
|---|---|---|
| FR-P-01 | System shall maintain a pool of pitch targets with email, name, source, enrichment data, and status | P0 |
| FR-P-02 | Operator shall approve each pitch target before any outbound email is sent | P0 |
| FR-P-03 | System shall generate a 3-5 minute sample episode tailored to the target's public work/interests | P0 |
| FR-P-04 | Sample episode shall be hosted at a URL that works without authentication | P0 |
| FR-P-05 | System shall send a `[SAMPLE]` email with exactly one CTA: listen to the sample | P0 |
| FR-P-06 | System shall parse inbound replies to pitch emails and classify intent as YES / NO / AMBIGUOUS | P0 |
| FR-P-07 | YES intent shall trigger the Conversion flow (FR-C-01) | P0 |
| FR-P-08 | NO intent shall add the target to the suppression list permanently | P0 |
| FR-P-09 | AMBIGUOUS intent shall route the reply to the operator queue | P0 |
| FR-P-10 | Silence for 7 days may trigger at most one nudge email | P1 |
| FR-P-11 | Silence for 14 days shall stop all outreach to that target permanently | P0 |
| FR-P-12 | System shall honor the suppression list on every outbound attempt | P0 |
| FR-P-13 | Sample email shall include CAN-SPAM-compliant physical address and unsubscribe link | P0 |
| FR-P-14 | Sample email shall disclose AI-generated content per applicable regulations | P0 |

### 6.2 Conversion (Stage 2)

| ID | Requirement | Priority |
|---|---|---|
| FR-C-01 | System shall send a `[CHECKOUT]` email within 5 minutes of YES classification | P0 |
| FR-C-02 | Checkout email shall contain exactly one button linking to a Stripe Checkout session | P0 |
| FR-C-03 | Stripe Checkout session shall pre-fill the subscriber's email from the pitch record | P0 |
| FR-C-04 | System shall receive and verify `checkout.session.completed` webhooks from Stripe | P0 |
| FR-C-05 | On successful checkout, system shall create a `PodcastSubscription` record atomically | P0 |
| FR-C-06 | System shall create a `Podcast` with default title `{First Name}'s Weekly Brief`, privacy `restricted`, and a `PodcastAccessToken` | P0 |
| FR-C-07 | System shall send `[ACCESS READY]` email within 60 seconds of webhook receipt | P0 |
| FR-C-08 | `[ACCESS READY]` email shall include the feed URL, setup instructions link, and adjust-your-podcast link | P0 |
| FR-C-09 | Adjust page shall expose at most 6 fields and save changes without requiring a submit button | P1 |
| FR-C-10 | Adjust page shall use the `PodcastAccessToken` for authentication (no password) | P0 |
| FR-C-11 | Webhook handler shall be idempotent by `stripe_event_id` | P0 |
| FR-C-12 | Failed webhook delivery shall retry per Stripe's standard backoff without creating duplicate subscriptions | P0 |
| FR-C-13 | Checkout session expiration (abandon > 24h) shall not auto-nag; the sample reply thread ends | P1 |

### 6.3 First Episode (Stage 3)

| ID | Requirement | Priority |
|---|---|---|
| FR-F-01 | System shall generate an `EpisodePreview` record within 24 hours of subscription creation | P0 |
| FR-F-02 | Preview shall include a proposed title, 3 segment topics, length, and drop date | P0 |
| FR-F-03 | System shall send an `[EPISODE 1 PREVIEW]` email containing the preview details | P0 |
| FR-F-04 | Subscriber replies to preview shall be classified: APPROVE / SWAP / PIVOT / SKIP / AMBIGUOUS | P0 |
| FR-F-05 | APPROVE or silence for 48h shall enqueue the existing `produce_episode` task pipeline | P0 |
| FR-F-06 | SWAP intent shall update segment topics and re-queue production without re-sending preview | P1 |
| FR-F-07 | PIVOT intent shall update subscription topic focus and regenerate the preview | P0 |
| FR-F-08 | SKIP intent shall reschedule production for the next cycle | P1 |
| FR-F-09 | On episode publish, system shall send `[NEW EPISODE]` email per Email UX spec | P0 |
| FR-F-10 | If preview generation fails, the system shall escalate to the operator queue (not skip the episode) | P0 |

### 6.4 Steady Service (Stage 4)

| ID | Requirement | Priority |
|---|---|---|
| FR-S-01 | System shall generate an `EpisodePreview` 48 hours before each scheduled drop | P0 |
| FR-S-02 | System shall reuse the Stage 3 preview → ship loop for every subsequent episode | P0 |
| FR-S-03 | System shall detect subscriber engagement via `PodcastAccessToken.last_accessed_at` | P0 |
| FR-S-04 | No access for > 14 days shall flag the subscription as DRIFTING | P1 |
| FR-S-05 | No access for > 30 days shall flag the subscription as DISENGAGED | P1 |
| FR-S-06 | System shall send one `[MONTHLY]` reality-check email per 30-day window per subscription | P0 |
| FR-S-07 | Monthly email shall list recent episode titles and offer a one-reply pivot path | P0 |
| FR-S-08 | Subscriber PAUSE shall halt episode generation without canceling billing | P1 |
| FR-S-09 | Subscriber RESUME shall restart the cycle from the next scheduled drop | P1 |
| FR-S-10 | Subscriber STOP shall cancel the Stripe subscription and send `[ACCESS REVOKED]` | P0 |
| FR-S-11 | Billing failure shall escalate to the operator queue (not to the subscriber directly) | P0 |
| FR-S-12 | System shall NEVER send unsolicited marketing or re-engagement nag emails | P0 |

### 6.5 Cross-Cutting (Inbound, Suppression, Operator Queue)

| ID | Requirement | Priority |
|---|---|---|
| FR-X-01 | System shall receive inbound emails via a provider webhook (e.g., Postmark Inbound) | P0 |
| FR-X-02 | System shall verify inbound email authenticity (SPF at minimum) | P0 |
| FR-X-03 | System shall persist every inbound reply in `SubscriberReply` | P0 |
| FR-X-04 | System shall classify intent using a deterministic rules engine for single-word replies | P0 |
| FR-X-05 | Multi-word or unmatched replies shall be marked AMBIGUOUS and routed to the operator queue | P0 |
| FR-X-06 | LLM-based intent classification may be added in v2 but must still default to human routing on low confidence | P2 |
| FR-X-07 | System shall maintain a global suppression list keyed by email hash | P0 |
| FR-X-08 | All outbound email sends shall consult the suppression list before sending | P0 |
| FR-X-09 | Operator queue shall support: view reply, see subscriber context, reply manually, mark as handled, apply intent override | P0 |
| FR-X-10 | Operator actions shall be logged with actor, timestamp, and decision | P1 |

---

## 7. Non-Functional Requirements

### 7.1 Performance

- **P-01**: `[ACCESS READY]` email delivered within 60 seconds of Stripe webhook receipt
- **P-02**: Inbound reply classification completes within 30 seconds of receipt
- **P-03**: Sample episode generation completes within 15 minutes of operator approval
- **P-04**: Preview generation completes within 5 minutes
- **P-05**: Feed URL responds within 500ms (p95)

### 7.2 Reliability

- **R-01**: 99.5% uptime for feed URL endpoint
- **R-02**: 99% delivery success rate for outbound transactional emails (excl. subscriber-side bounces)
- **R-03**: Stripe webhook handler idempotent under retries
- **R-04**: Episode production task pipeline resumes from last successful step on worker restart
- **R-05**: Zero data loss on inbound email — every received email persisted before classification

### 7.3 Security

- **SEC-01**: Stripe webhooks verified via signing secret on every request
- **SEC-02**: Inbound email webhooks verified via provider signing mechanism
- **SEC-03**: `PodcastAccessToken` uses cryptographically secure random (already implemented via `secrets.token_urlsafe(32)`)
- **SEC-04**: Rate limiting on public checkout endpoint (10 req/min per IP)
- **SEC-05**: Adjust page token scoped to a single subscription; expires if subscription is revoked
- **SEC-06**: No PII in logs beyond what's necessary for debugging; email addresses hashed where possible
- **SEC-07**: Supabase private bucket URLs signed with short TTL

### 7.4 Privacy & Compliance

- **COMP-01**: CAN-SPAM compliance on all outbound email (physical address, unsubscribe, sender identity)
- **COMP-02**: GDPR compliance for EU recipients (consent basis, data export, right to erasure)
- **COMP-03**: AI-generated content disclosure in pitch emails per applicable law
- **COMP-04**: Terms of Service and Privacy Policy linked in all outbound emails
- **COMP-05**: PCI compliance satisfied entirely via Stripe Checkout (no card data ever touches our servers)
- **COMP-06**: Retention policy: inbound email content purged after 90 days unless flagged for legal hold
- **COMP-07**: Right-to-erasure request handled within 30 days, including sample episodes generated for the target

### 7.5 Accessibility

- **A-01**: All outbound emails provide a plain-text version that communicates the full message
- **A-02**: HTML emails degrade gracefully in text-only clients
- **A-03**: Audio is never the sole channel for critical information (per Email UX spec)
- **A-04**: Adjust page meets WCAG 2.1 AA

---

## 8. Data Model

![Data model](./private-podcast-diagrams/06-data-model.png)

### 8.1 New Models

#### `PodcastSubscription`

Represents a paying relationship between a subscriber and a `Podcast`.

```python
class PodcastSubscription(Timestampable):
    podcast = OneToOneField("podcast.Podcast", on_delete=CASCADE)
    subscriber_email = EmailField(db_index=True)
    subscriber_name = CharField(max_length=200)
    stripe_customer_id = CharField(max_length=100, unique=True)
    stripe_subscription_id = CharField(max_length=100, unique=True)

    class Status(TextChoices):
        ACTIVE = "active"
        PAUSED = "paused"
        DRIFTING = "drifting"       # no access > 14d
        DISENGAGED = "disengaged"   # no access > 30d
        CHURNED = "churned"
    status = CharField(choices=Status.choices, default=ACTIVE)

    class Cadence(TextChoices):
        WEEKLY = "weekly"
        BIWEEKLY = "biweekly"
        MONTHLY = "monthly"
    cadence = CharField(choices=Cadence.choices, default=WEEKLY)

    length_minutes = PositiveIntegerField(default=15)
    depth_level = CharField(default="accessible")  # mirror PodcastConfig
    topic_focus = TextField()

    next_drop_at = DateTimeField(null=True, blank=True)
    last_monthly_checkin_at = DateTimeField(null=True, blank=True)
    paused_at = DateTimeField(null=True, blank=True)
    churned_at = DateTimeField(null=True, blank=True)
```

#### `PodcastPitch`

Represents one cold-outreach attempt to a `PitchTarget`.

```python
class PodcastPitch(Timestampable):
    target = OneToOneField("podcast.PitchTarget", on_delete=CASCADE)
    sample_episode = ForeignKey("podcast.Episode", on_delete=PROTECT)
    sent_at = DateTimeField()

    class Status(TextChoices):
        SENT = "sent"
        REPLIED_YES = "replied_yes"
        REPLIED_NO = "replied_no"
        REPLIED_AMBIGUOUS = "replied_ambiguous"
        NUDGED = "nudged"
        SILENT_TIMEOUT = "silent_timeout"
        CONVERTED = "converted"
        SUPPRESSED = "suppressed"
    status = CharField(choices=Status.choices, default=SENT)

    replied_at = DateTimeField(null=True, blank=True)
    nudge_sent_at = DateTimeField(null=True, blank=True)
```

#### `PitchTarget`

Pool of prospective subscribers, pre-outreach. Kept separate from `PodcastPitch` so the same target can be re-evaluated across multiple product positioning angles.

```python
class PitchTarget(Timestampable):
    email = EmailField(unique=True)
    name = CharField(max_length=200)
    source = CharField(max_length=100)  # "manual", "linkedin_scrape", "referral"
    enrichment_data = JSONField(default=dict)  # public bio, work samples, etc.

    class Status(TextChoices):
        PENDING_REVIEW = "pending_review"
        APPROVED = "approved"
        REJECTED = "rejected"
        PITCHED = "pitched"
        SUPPRESSED = "suppressed"
    status = CharField(choices=Status.choices, default=PENDING_REVIEW)

    approved_by = ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=SET_NULL)
    approved_at = DateTimeField(null=True)
```

#### `EpisodePreview`

A proposed-but-not-yet-produced episode awaiting subscriber approval (or silence).

```python
class EpisodePreview(Timestampable):
    subscription = ForeignKey("podcast.PodcastSubscription", on_delete=CASCADE)
    proposed_title = CharField(max_length=200)
    segments = JSONField(default=list)  # list of {title, description}
    drop_date = DateTimeField()

    class Status(TextChoices):
        PROPOSED = "proposed"
        APPROVED = "approved"
        REDIRECTED = "redirected"  # swap / pivot received
        SHIPPED = "shipped"
        SKIPPED = "skipped"
    status = CharField(choices=Status.choices, default=PROPOSED)

    episode = OneToOneField("podcast.Episode", null=True, on_delete=SET_NULL)
    preview_email_sent_at = DateTimeField(null=True)
```

#### `SubscriberReply`

Inbound email log. Every reply, across all stages, persisted here.

```python
class SubscriberReply(Timestampable):
    from_email = EmailField(db_index=True)
    subject = CharField(max_length=500)
    body = TextField()
    raw_headers = JSONField(default=dict)

    class Intent(TextChoices):
        YES = "yes"
        NO = "no"
        PERFECT = "perfect"
        SWAP = "swap"
        PIVOT = "pivot"
        SKIP = "skip"
        PAUSE = "pause"
        RESUME = "resume"
        STOP = "stop"
        HELP = "help"
        AMBIGUOUS = "ambiguous"
    parsed_intent = CharField(choices=Intent.choices, default=AMBIGUOUS)

    parsed_payload = JSONField(default=dict)  # e.g., {"swap_from": "X", "swap_to": "Y"}
    escalated = BooleanField(default=False)
    handled_at = DateTimeField(null=True)
    handled_by = ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=SET_NULL)

    # Polymorphic linkage — one of these will be set
    pitch = ForeignKey("podcast.PodcastPitch", null=True, on_delete=SET_NULL)
    subscription = ForeignKey("podcast.PodcastSubscription", null=True, on_delete=SET_NULL)
```

#### `BillingEvent`

Stripe webhook event log for audit and debugging.

```python
class BillingEvent(Timestampable):
    subscription = ForeignKey("podcast.PodcastSubscription", null=True, on_delete=SET_NULL)
    stripe_event_id = CharField(max_length=100, unique=True)
    event_type = CharField(max_length=100)  # checkout.session.completed, etc.
    payload = JSONField()
    processed_at = DateTimeField(null=True)
    error = TextField(blank=True)
```

#### `SuppressionEntry`

Global email suppression list.

```python
class SuppressionEntry(Timestampable):
    email_hash = CharField(max_length=64, unique=True, db_index=True)  # sha256 of lowercased email
    reason = CharField(max_length=100)  # "unsubscribed", "bounced", "complained", "manual"
    source_reply = ForeignKey("podcast.SubscriberReply", null=True, on_delete=SET_NULL)
```

### 8.2 Modifications to Existing Models

- **`Podcast`**: No schema changes. Existing `privacy = restricted` and `PodcastAccessToken` continue to handle feed auth.
- **`PodcastConfig`**: Extend with `topic_focus` field OR leave separate in `PodcastSubscription` (decision pending — see Open Questions).
- **`Episode`**: Add `is_sample = BooleanField(default=False)` to distinguish pitch-sample episodes from regular production.

### 8.3 Migrations

- **M-01**: Create 6 new models above
- **M-02**: Add `Episode.is_sample` field
- **M-03**: Backfill: none (product is greenfield)

### 8.4 Retention & Deletion

| Entity | Retention | Trigger |
|---|---|---|
| `SubscriberReply.body` | 90 days | Scheduled purge |
| `BillingEvent.payload` | 7 years | Financial record requirement |
| `PitchTarget` + `PodcastPitch` | Indefinite unless erasure request | Manual |
| `Episode` (sample) | 30 days post-churn | Scheduled |
| `PodcastSubscription` | Indefinite (anonymized on erasure) | Manual |

Right-to-erasure honored within 30 days by anonymizing subscriber PII while preserving `BillingEvent` records for legal compliance.

---

## 9. System Architecture

### 9.1 Service Boundaries

```
┌──────────────────────────────────────────────────────────┐
│                    apps/podcast                           │
│  ┌────────────────┐  ┌────────────────┐  ┌──────────────┐│
│  │ Existing:      │  │ New:           │  │ New:         ││
│  │ Production     │  │ Subscription   │  │ Outreach     ││
│  │ Pipeline       │  │ Lifecycle      │  │ & Pitch      ││
│  │ (12 phases)    │  │ (Stages 2-4)   │  │ (Stage 1)    ││
│  └────────────────┘  └────────────────┘  └──────────────┘│
└─────────────┬──────────────────┬────────────────┬────────┘
              │                  │                │
              ▼                  ▼                ▼
       ┌─────────────┐  ┌──────────────┐  ┌─────────────┐
       │  Supabase   │  │   Stripe     │  │  Email Svc  │
       │  Storage    │  │   (billing)  │  │ (in + out)  │
       └─────────────┘  └──────────────┘  └─────────────┘
```

### 9.2 New Components

- **`apps/podcast/services/subscription.py`** — subscription lifecycle service functions
- **`apps/podcast/services/outreach.py`** — pitch generation, send, classification
- **`apps/podcast/services/billing.py`** — Stripe webhook handlers + subscription sync
- **`apps/podcast/services/reply_router.py`** — inbound email intake, classification, routing
- **`apps/podcast/services/preview.py`** — `EpisodePreview` generation + state transitions
- **`apps/podcast/tasks.py`** — new tasks: `generate_sample_episode`, `send_preview_email`, `classify_reply`, `check_engagement`, `send_monthly_checkin`
- **`apps/podcast/webhooks.py`** — Stripe and inbound email webhook endpoints
- **`apps/public/views/subscription.py`** — adjust-your-podcast page
- **`apps/staff/views/operator_queue.py`** — operator console for ambiguous replies

### 9.3 Signal Flow

- `post_save(PodcastSubscription)` with `created=True` → enqueue `generate_first_preview` task
- `post_save(EpisodePreview)` with status transitioning to APPROVED → enqueue `produce_episode`
- `post_save(Episode)` with `published_at` transitioning from null → enqueue `send_new_episode_email`
- `post_save(SubscriberReply)` → enqueue `classify_reply_task`

---

## 10. Integrations

### 10.1 Stripe

| Capability | Usage |
|---|---|
| Stripe Checkout (hosted) | Subscriber payment collection |
| Subscriptions API | Recurring billing management |
| Webhooks | Event-driven state sync |
| Customer Portal | Self-serve cancellation / payment method update |

**Events consumed:**
- `checkout.session.completed` → create `PodcastSubscription`
- `invoice.payment_succeeded` → log `BillingEvent`
- `invoice.payment_failed` → escalate to operator queue
- `customer.subscription.deleted` → transition subscription to CHURNED
- `customer.subscription.updated` → sync status changes

**Signing secret** stored in env, never logged.

### 10.2 Outbound Email

**Candidates**: Postmark, Resend, SES, Sendgrid.

**Requirements:**
- Transactional send API with sub-minute latency
- Template rendering handled server-side (Django template engine), not vendor-side
- Dedicated subdomain for deliverability isolation (e.g., `mail.ai.yuda.me`)
- SPF, DKIM, DMARC configured
- Bounce / complaint webhook
- Per-send message ID for threading replies

**Recommendation**: **Postmark** — best reputation for transactional, clean API, good inbound support (see below).

### 10.3 Inbound Email

**Options:**
- **Postmark Inbound** (if using Postmark for outbound — unified provider)
- **SES + S3 + Lambda** (cheapest, most complex)
- **CloudMailin** (simple webhook)

**Requirements:**
- Receive reply to any `reply+{token}@mail.ai.yuda.me` address
- Parse `In-Reply-To` and `References` headers to thread with original send
- Strip quoted history before classification
- Detect auto-responders and route them to a silent sink
- Deliver as webhook with JSON payload within 30 seconds of receipt

**Recommendation**: **Postmark Inbound** — pairs naturally with outbound.

### 10.4 Sample Episode Generation

Lightweight variant of the existing 12-phase pipeline:

- Target: 3-5 minute audio, single segment, single topic
- Research depth: 1 source minimum (Perplexity or GPT-Researcher)
- Pipeline steps: setup → single research call → brief synthesis → audio generation → publish
- Target cost per sample: under $2 (TBD based on API spend analysis)
- Target latency: under 15 minutes end-to-end

New task: `generate_sample_episode(pitch_target_id)` invoking a subset of the existing production tasks with a compressed configuration.

### 10.5 Pitch Target Enrichment

Initial implementation: **manual** — operator adds targets one by one with enrichment data pasted from LinkedIn/public bios.

Future: LinkedIn scraping (via approved partner), Clay integration, or Apollo-style APIs. All gated behind operator approval for compliance.

### 10.6 Observability

- **Sentry** — error tracking (already integrated)
- **Render logs** — service logs (already integrated)
- **Custom metrics** — via a dedicated `apps/podcast/metrics.py` module emitting counts and timings
- **Deliverability dashboard** — Postmark provides built-in; surface key metrics in operator console

---

## 11. Workflow Specifications

### 11.1 Cold Pitch Workflow

1. Operator adds `PitchTarget` (manually, with enrichment data)
2. Operator reviews and approves target → status transitions to APPROVED
3. `post_save` signal enqueues `generate_sample_episode(target_id)`
4. Sample generation pipeline runs (~15 min)
5. Episode published, `PodcastPitch` created linking target to sample episode
6. `send_pitch_email` task sends `[SAMPLE]` email
7. Pitch status transitions to SENT
8. [System waits for reply or timeout]
9. On reply: see Inbound Reply Workflow (11.3)
10. On 7-day silence: optionally enqueue single nudge
11. On 14-day silence: status transitions to SILENT_TIMEOUT, no further action

### 11.2 Conversion Workflow

1. Subscriber clicks checkout link in `[CHECKOUT]` email
2. Stripe Checkout session created with subscriber_email prefilled
3. Subscriber completes payment
4. Stripe sends `checkout.session.completed` webhook
5. Webhook handler verifies signature, checks idempotency by `stripe_event_id`
6. Creates `Podcast` with defaults (title, privacy=restricted, config)
7. Creates `PodcastSubscription` linking subscriber to podcast
8. Creates `PodcastAccessToken` for feed auth
9. Creates `BillingEvent` record
10. Enqueues `send_access_ready_email` task
11. `PodcastPitch.status` transitions to CONVERTED
12. 24h later, `generate_first_preview` task runs (Stage 3 begins)

### 11.3 Inbound Reply Workflow

1. Email provider receives reply, POSTs to webhook
2. Webhook handler verifies signature
3. Parses `From`, `Subject`, `In-Reply-To`, body (strip quoted history)
4. Resolves context: look up `PodcastPitch` or `PodcastSubscription` by reply-to token or sender email
5. Creates `SubscriberReply` record (raw state)
6. Enqueues `classify_reply_task(reply_id)`
7. Classification runs rule-based intent extraction
8. If confident match (single-word YES/NO/PERFECT/etc.), updates related entity and triggers next action
9. If ambiguous, sets `escalated=True` and surfaces in operator queue
10. Operator reviews and applies intent override

![Preview to ship loop](./private-podcast-diagrams/05-preview-ship-loop.png)

### 11.4 Preview → Ship Workflow

1. Scheduled task (48h before drop) enqueues `generate_preview(subscription_id)`
2. Named AI tool generates proposed title + 3 segments based on topic focus + recent episodes
3. `EpisodePreview` created
4. `send_preview_email` task fires
5. [Wait for reply or silence window closes at drop_date]
6. If APPROVE or silence: enqueue `produce_episode` pipeline
7. If SWAP: update segments, enqueue production
8. If PIVOT: update `PodcastSubscription.topic_focus`, regenerate preview, restart from step 3
9. If SKIP: mark preview SKIPPED, schedule next cycle
10. On episode publish: `send_new_episode_email` task fires
11. Schedule next preview cycle per `cadence`

### 11.5 Engagement Monitoring

- Scheduled daily task iterates active subscriptions
- Computes days since `PodcastAccessToken.last_accessed_at`
- Updates `PodcastSubscription.status` to DRIFTING (>14d) or DISENGAGED (>30d)
- Emits metrics for operator dashboard
- Does NOT send automatic re-engagement emails (principle from Email UX)

### 11.6 Monthly Check-In

- Scheduled daily task finds active subscriptions where `last_monthly_checkin_at < now - 30 days`
- Enqueues `send_monthly_checkin(subscription_id)`
- Email lists recent 4 episode titles + one pivot CTA
- Updates `last_monthly_checkin_at`

### 11.7 Churn

Three churn triggers:
1. **Explicit STOP reply** — classify, send `[ACCESS REVOKED]`, cancel Stripe subscription
2. **Stripe cancellation event** (from Customer Portal) — send `[ACCESS REVOKED]`, transition to CHURNED
3. **Billing failure** — escalate to operator, operator decides refund / retry / cancel

Post-churn:
- Feed URL continues to serve existing episodes for 30 days (grace period)
- `PodcastAccessToken` remains active to avoid bricking already-downloaded episodes
- After 30 days: token deactivated, feed 410s

---

## 12. UI Surface

### 12.1 What's NOT in the UI

- No subscriber dashboard
- No login / password / account creation UI
- No catalog / browse / search
- No notifications panel
- No settings page beyond the 6-field adjust page

### 12.2 Public Web Surfaces

| Page | URL pattern | Auth | Purpose |
|---|---|---|---|
| Landing | `/briefing/` (exists) | None | Product value prop, signup CTA |
| Adjust podcast | `/podcast/adjust/{token}/` | Token | Modify cadence, length, topic, name |
| Setup instructions | `/podcast/setup/` | None | Static guide for adding private feeds to podcast apps |
| Stripe Checkout (hosted) | Stripe-hosted | Stripe | Payment collection |

### 12.3 Operator Console

Separate from Django admin. Lives at `/staff/operator/` with staff-only access.

| View | Purpose |
|---|---|
| Queue | List of ambiguous `SubscriberReply` items ordered by received_at |
| Reply detail | Reply body + context (pitch or subscription) + action buttons |
| Pitch targets | Pending-review targets with enrichment data, approve/reject |
| Subscription list | All active subscriptions with status, last access, MRR contribution |
| Billing failures | Failed `BillingEvent`s requiring intervention |
| Deliverability | Bounce rate, complaint rate, suppression list size |

---

## 13. Security & Compliance

### 13.1 Authentication & Authorization

- **Feed access**: `PodcastAccessToken` (already implemented)
- **Adjust page**: scoped token valid for single subscription, no login
- **Operator console**: Django staff user + group membership
- **Webhooks**: cryptographic signature verification (Stripe + email provider)

### 13.2 Secret Management

- `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`
- `POSTMARK_SERVER_TOKEN`, `POSTMARK_INBOUND_SECRET`
- All secrets in env vars, never in code, never in logs

### 13.3 Rate Limiting

- Checkout endpoint: 10 req/min per IP
- Adjust page token: 60 req/min per token
- Inbound webhook: trust provider-level rate limits

### 13.4 Compliance

**CAN-SPAM:**
- Physical mailing address in every email
- Clear sender identification
- Unsubscribe mechanism (single-click or `STOP` reply) honored within 10 business days
- Subject line truthfulness
- No header forgery

**GDPR** (EU recipients):
- Legal basis: legitimate interest for cold outreach (B2B), consent post-opt-in
- Privacy notice linked in every email
- Data Subject Access Request (DSAR) fulfillment within 30 days
- Right to erasure honored within 30 days
- Data Processing Agreement with Stripe and email provider

**AI Disclosure:**
- Pitch emails explicitly state the sample was AI-generated
- Episode descriptions note AI-assisted production

---

## 14. Operations & Support

### 14.1 Operator Responsibilities

- Review and approve `PitchTarget` additions before outreach
- Triage ambiguous replies in the operator queue (SLA: 1 business day)
- Handle billing failures (SLA: 4 business hours)
- Monitor deliverability metrics daily
- Review sample episodes before send (initial period; may be automated later)

### 14.2 Runbooks (to be written post-MVP)

- Stripe webhook failure / replay
- Inbound email provider outage
- Sample generation pipeline stuck
- Mass unsubscribe event (deliverability crisis)
- Individual subscriber complaint / takedown

### 14.3 Monitoring

- Sentry alerts on exception rates above threshold
- Daily digest of operator queue depth
- Weekly deliverability report
- MRR and churn dashboard (Stripe native + internal)

---

## 15. Metrics & Analytics

### 15.1 Funnel Metrics

| Metric | Target | Stage |
|---|---|---|
| Pitch targets approved per week | ≥ 50 | Stage 1 |
| Samples sent per week | ≥ 50 | Stage 1 |
| Sample → YES reply rate | ≥ 5% | Stage 1 |
| YES → checkout click rate | ≥ 80% | Stage 2 |
| Checkout → paid rate | ≥ 75% | Stage 2 |
| Paid → episode 1 approved | ≥ 95% | Stage 3 |
| Monthly retention | ≥ 85% | Stage 4 |

### 15.2 Engagement Metrics

- Average episodes downloaded per subscriber per month
- Median time from publish to first listen
- % subscribers listening within 24h of drop
- % subscribers flagged DRIFTING / DISENGAGED

### 15.3 Operational Metrics

- Operator queue depth (p50, p95)
- Reply classification accuracy (sampled weekly)
- Email delivery rate / bounce rate / complaint rate
- Episode ship-on-time rate
- Sample generation success rate

### 15.4 Financial Metrics

- MRR, ARR
- Churn rate (voluntary vs. involuntary)
- LTV, CAC (cost per converted subscriber)
- Gross margin per subscription (cost of generation + hosting vs. price)

---

## 16. Rollout Plan

> **Phase 1 is specified in detail in the [MVP PRD](./private-podcast-mvp.md).** The outline below summarizes the full phased plan; start with the MVP doc and come back here after it ships.

### Phase 0 — Foundation (prerequisites)

- Set up Postmark account, configure SPF/DKIM/DMARC for `mail.ai.yuda.me`
- Set up Stripe account, products, and webhook endpoint
- Create the 6 new data models
- Write migrations (gated pending approval per project policy)
- Build suppression list infrastructure
- Extend existing production pipeline to support `is_sample` episodes

### Phase 1 — Manual Conversion (Stage 2 MVP)

- Build Stripe Checkout flow and webhook handler
- Build `[ACCESS READY]` email template and send pipeline
- Build adjust-your-podcast page
- Manual testing: create test subscription end-to-end
- **Milestone**: one paying subscriber onboarded entirely via Stripe + email

### Phase 2 — Cadence (Stage 4 MVP)

- Build `EpisodePreview` model and generation task
- Build preview email template
- Build scheduled task for preview generation (48h before drop)
- Build new episode notification
- Build engagement monitoring (DRIFTING / DISENGAGED transitions)
- Build monthly check-in
- **Milestone**: first subscriber completes a full week → receives new episode → receives next preview

### Phase 3 — Intake (Stage 3 MVP)

- Build first-episode preview generation (distinct from Stage 4 because it has less history to draw from)
- Build topic inference from checkout metadata
- **Milestone**: Episode 1 auto-proposed and shipped with zero subscriber intervention

### Phase 4 — Inbound Reply Handling

- Build Postmark Inbound webhook handler
- Build rule-based intent classifier
- Build operator queue view
- Wire up reply routing
- **Milestone**: "perfect", "swap", "pivot", "skip", "stop" replies all handled automatically

### Phase 5 — Outreach (Stage 1 MVP)

- Build `PitchTarget` + `PodcastPitch` models and flows
- Build sample episode generation pipeline (lightweight variant)
- Build operator approval flow for pitch targets
- Build pitch email template
- Build reply classification for pitch replies
- **Milestone**: first cold pitch sent with AI-generated sample, first conversion from cold outreach

### Phase 6 — Optimization & Scale

- A/B test pitch copy, sample length, cadence defaults
- LLM-based intent classification for nuanced replies
- Automated pitch target sourcing (Clay, Apollo, LinkedIn partner)
- Subscriber segmentation for tailored monthly check-ins
- Team/shared feed support (separate PRD)

Phase ordering rationale: **reverse of the user journey.** Building conversion (Phase 1) and cadence (Phase 2) first lets us validate the product with friendly manual pilots before investing in the hardest and most sensitive part (cold outreach).

---

## 17. Risks & Mitigations

| ID | Risk | Severity | Mitigation |
|---|---|---|---|
| R-01 | Cold outreach triggers spam reports / domain reputation damage | High | Dedicated subdomain, strict suppression, manual target approval, very low send volume in Phase 5 |
| R-02 | Sample episode generation costs exceed willingness to pay for uncertain conversions | Med | Budget cap per week; pause sample generation if weekly spend exceeds threshold |
| R-03 | Intent classification gets replies wrong, subscriber feels unheard | High | Conservative rules; default to escalation; never auto-act on ambiguous replies |
| R-04 | Subscriber receives wrong episode or missed drop date, loses trust | High | Production pipeline already has retry + failure handling; add explicit ship-on-time monitoring |
| R-05 | Stripe webhook delivery failures cause duplicate or missing subscriptions | High | Idempotent handler by `stripe_event_id`; monitor for unprocessed events |
| R-06 | GDPR complaint / DSAR we can't fulfill quickly | Med | Build erasure tooling in Phase 0 before any EU outreach |
| R-07 | AI-generated content regulation changes invalidate our pitch approach | Low | Disclosure-first design; separate AI content clearly from human communication |
| R-08 | Operator queue grows faster than ops can handle | Med | Start with extremely conservative automation; tune classifier over time; cap outreach volume to operator capacity |
| R-09 | Topic inference produces irrelevant first episodes, subscribers churn after Episode 1 | High | Use sample episode topic as strong prior; offer one free reroll |
| R-10 | Private feed URL leaked by subscriber, token abuse | Low | Per-subscription tokens; monitor access counts; rotate on anomaly |
| R-11 | Price discovery wrong — product too cheap for cost or too expensive for market | Med | Start with small pilot pool; iterate price before Phase 5 scale |

---

## 18. Open Questions

### Product

1. **Pricing model**: Flat monthly ($49? $99?), tiered by cadence/length, or custom by topic depth?
2. **Target segment**: B2B executives vs. B2C professionals vs. researchers/investors? Different personas → different pitch copy and price points.
3. **First-episode reroll policy**: Free one-time reroll? Money-back guarantee? Nothing?
4. **Monthly check-in format**: Email only, or permit subscribers to optionally request a brief voice note?
5. **Sample episode persistence**: Does the sample count as Episode 0 of the subscriber's feed, or is it discarded post-conversion?
6. **Cadence defaults**: Weekly feels right for some topics but too much for others. Single default or inferred from topic?

### Technical

7. **`topic_focus` location**: On `PodcastSubscription` or extend `PodcastConfig`? Tradeoff: separation of concerns vs. duplication.
8. **Email provider**: Postmark vs. Resend vs. SES? Decision criteria: deliverability track record, inbound support, cost at scale.
9. **Intent classification**: Rules-only for MVP, or introduce LLM classification in Phase 4? Impact on operator queue volume.
10. **Sample generation**: Full pipeline subset vs. dedicated lightweight pipeline?
11. **Inbound email threading**: How do we reliably match a reply to its pitch/subscription when the subscriber changes their display name?

### Legal / Compliance

12. **Cold outreach jurisdiction**: Which regions are we comfortable pitching into? GDPR complicates EU; PIPEDA complicates Canada; CASL has strict consent rules.
13. **AI disclosure language**: What specific wording satisfies current regulations (EU AI Act, California AB-2655, etc.)?
14. **Terms of service**: Need to draft. Who owns the generated podcast audio — us, the subscriber, or joint?
15. **Data retention**: Is 90 days for inbound reply bodies sufficient, or do we need longer for dispute resolution?

### Operations

16. **Operator staffing**: How many operators at what subscriber count? What's the break-even?
17. **Escalation hours**: 24/5, 24/7, or business hours only in initial release?
18. **Quality review**: Do operators review every sample episode before send, or only flagged ones?

---

## 19. Appendix

### 19.1 Glossary

- **Pitch**: Single outbound cold email containing a sample episode
- **Sample**: 3-5 minute AI-generated episode used as a pitch artifact
- **Preview**: Proposal for an upcoming episode sent 48h before drop
- **Drop**: Scheduled publish time for an episode
- **Cadence**: Frequency of episode delivery (weekly/biweekly/monthly)
- **Operator**: Internal staff member handling ambiguous automation cases
- **Suppression**: Permanent state preventing any outbound email to a given address
- **Drifting / Disengaged**: Behavioral states tracking subscriber listening activity

### 19.2 Related Documents

- [Journey Mapping](./private-podcast-journey-mapping.md) — narrative product design
- [Email UX](./private-podcast-email-ux.md) — outgoing email structure and tone
- [Podcast Services API](./podcast-services.md) — existing production pipeline reference
- [File Storage Service](./file-storage-service.md) — feed hosting (already implemented)

### 19.3 References

- Existing models: `apps/podcast/models/{podcast,podcast_config,access_token,episode,episode_workflow,episode_artifact}.py`
- Existing pipeline: `apps/podcast/tasks.py`
- Existing services: `apps/podcast/services/`
- Existing production URL: `https://ai.yuda.me`

---

**Document status**: Draft v1 — awaiting review on open questions before Phase 0 kickoff.
