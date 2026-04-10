# Private Podcast Email UX

Specification for all subscriber-facing email communication in the private podcast product.

---

## Core Philosophy

Private podcast emails are a **low-latency access interface**, not a notification system.

Subscribers are busy. They receive a private feed because someone decided their time is worth protecting. Every email must honor that decision.

**Non-negotiables:**
- No guessing intent
- Defaults over questions
- Skimmable in under 10 seconds
- Every interaction is optional unless blocking
- Zero cognitive overhead to respond

---

## Mental Model

Email is a **state machine**, not a conversation thread.

```
Subscriber invited
  → Invite (access delivery)
    → Episode published
      → New episode notification
        → Access expiring
          → Renewal / revocation
```

Each state maps to one predictable email type. Never mix states in a single email.

---

## Design Constraints

### Cognitive Load Budget

Each email must:
- Require ≤ 3 decisions
- Be actionable in under 30 seconds
- Have a safe "do nothing" path

### Determinism

Every email immediately answers:
- What is this?
- Do I need to act?
- How long will it take?
- What happens if I ignore it?

### Structure Invariance

All emails share the same visual skeleton:

1. Header (podcast name + status)
2. 30-second summary
3. Action block (if any)
4. Assets / links
5. Assumptions or context
6. Reply shortcuts (where applicable)

Subscribers should recognize emails instantly without reading the body.

---

## Communication Rules

### Defaults First

Never ask open-ended questions without defaults.

**Bad:**
> "Let us know if you have trouble setting up your feed."

**Good:**
> "Your feed is ready for Apple Podcasts, Spotify, and Overcast.
> Reply only if you need a different format."

### One Email = One Job

| Email type | Does exactly one thing |
|---|---|
| Invite | Delivers access credentials |
| New episode | Delivers episode access |
| Expiration warning | Requests renewal decision |
| Revocation | Confirms access removal |

No mixing.

### Subject Line Taxonomy (Strict)

```
[ACCESS READY]
[NEW EPISODE]
[ACCESS EXPIRING]
[ACCESS REVOKED]
[FEED UPDATED]
```

Never deviate. Recognition matters more than creativity.

### Response Design

Every email must support:
- No response (safe default — system proceeds)
- 1-line reply
- Link-based interaction (preferred)

---

## Audio Strategy

### Principle

Audio is **assistive**, not required. The email must work without it.

### Implementation

- Primary: button linking to hosted audio (episode page or direct MP3)
- Label (fixed, always the same):

```
▶ Listen  [duration]
```

### Rules

- No autoplay
- No dependency on audio rendering in email clients
- Same label and position in every episode email
- Duration displayed inline with the button

---

## Email Templates

### Template 1: Invite (Access Delivery)

**Subject:** [ACCESS READY] Your private feed for {Podcast Title}

---

**{Podcast Title}**
Your private access is ready.

**30-second setup**
1. Copy your feed URL below
2. Open your podcast app
3. Add as a private/custom feed

**Your feed URL**
`https://ai.yuda.me/api/feed/{token}/`

[Copy feed URL]  [Setup instructions]

**Works with:** Apple Podcasts · Overcast · Pocket Casts · Spotify · Castro

**What's included now:** {episode_count} episodes

If you have trouble, reply with your podcast app and we'll send specific instructions.

---

### Template 2: New Episode (Primary Product Surface)

**Subject:** [NEW EPISODE] {Episode Title} — {Podcast Title}

---

**{Podcast Title}**
Episode {number}: {Episode Title}

**30-second view**
- {one-sentence episode summary}
- Duration: {duration}
- Published: {date}

▶ Listen  [{duration}]

[Open in podcast app]  [Episode notes]

**Your feed updates automatically.** If you don't see it yet, pull to refresh in your app.

---

### Template 3: Expiration Warning

**Subject:** [ACCESS EXPIRING] Your feed for {Podcast Title} expires in {N} days

---

**{Podcast Title}**
Your access expires on {date}.

After that date, your feed will stop updating and episodes will be unavailable.

**To continue access, no action needed** — your host will be notified to renew.

If you'd like to stop receiving this podcast, reply: **STOP**

No reply → we assume you want to continue.

---

### Template 4: Access Revoked

**Subject:** [ACCESS REVOKED] {Podcast Title}

---

**{Podcast Title}**
Your access has been removed.

Your feed URL is no longer active. Existing episodes already downloaded to your device remain available.

If this was unexpected, reply and we'll look into it.

---

### Template 5: Feed Updated (Token Regenerated)

**Subject:** [FEED UPDATED] New feed URL for {Podcast Title}

---

**{Podcast Title}**
Your feed URL has changed. Your previous URL no longer works.

**Your new feed URL**
`https://ai.yuda.me/api/feed/{new_token}/`

[Copy new URL]  [Setup instructions]

**Action required:** Update this URL in your podcast app.

---

## Interaction Patterns

### Safe Ignore

If the subscriber does nothing:
- System proceeds (feed stays active, episodes appear automatically)
- No degradation in access quality
- No follow-up nag emails

### Fast Override

Subscriber can respond with:
- A single keyword (`STOP`, `HELP`, `SWITCH`)
- One-line instruction
- Minimal typing

### No Friction Escalation

If setup complexity increases (e.g., subscriber uses an obscure app):
→ Link to a setup guide page, not a longer email thread

---

## Anti-Patterns

Do not allow:
- Asking open-ended questions without defaults
- Mixing access delivery and episode notification in one email
- Variable subject line formats
- Long paragraphs in email body
- Requiring login to understand the email
- Audio as a required medium
- Inline images that break in text-only clients

---

## Success Criteria

The email system is working when:
- Subscribers set up their feed without replying
- New episode emails receive no reply (by design — they just listen)
- No one asks "how do I access this?"
- Emails are opened and acted on in seconds, not minutes

---

## Implementation Notes

### Django Stack

| Concern | Approach |
|---|---|
| Email templates | Django template engine with strict schema — no freeform copy |
| Transactional send | Background task via `@task` decorator |
| Invite trigger | `post_save` signal on `PodcastSubscription` creation |
| Episode trigger | `post_save` signal on `Episode` publish |
| Expiration check | Scheduled management command or cron task |
| Feed URL generation | `PodcastSubscription.feed_url` computed property |
| Audio link | Direct episode audio URL — no login required |

### Key Models

- `PodcastSubscription` — holds `access_token`, `subscriber_email`, `subscriber_name`, `expires_at`
- `Episode` — triggers notification on `published_at` transition
- `Podcast` — provides title, artwork for email header

### Template Location

```
apps/public/templates/email/podcast/
├── invite.html
├── invite.txt
├── new_episode.html
├── new_episode.txt
├── access_expiring.html
├── access_expiring.txt
├── access_revoked.html
├── access_revoked.txt
└── feed_updated.html
    feed_updated.txt
```

Always provide both HTML and plain-text versions. The plain-text version is the canonical design constraint — if it doesn't work as plain text, the email is too complex.
