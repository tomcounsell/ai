# Email Infrastructure

Cuttlefish sends transactional email via [Postmark](https://postmarkapp.com) using the `django-anymail` library.

## Architecture

| Component | Value |
|-----------|-------|
| Library | `django-anymail[postmark]` |
| Django setting | `EMAIL_BACKEND = "anymail.backends.postmark.EmailBackend"` |
| Sender domain | `mail.ai.yuda.me` |
| Default sender | `podcast@mail.ai.yuda.me` |

## Environment Variables

| Variable | Description |
|----------|-------------|
| `POSTMARK_SERVER_TOKEN` | Postmark Server API Token — set in Render environment group |
| `DEFAULT_FROM_EMAIL` | Verified sender address (default: `podcast@mail.ai.yuda.me`) |

**Never commit a real token.** Set `POSTMARK_SERVER_TOKEN` only via Render dashboard or local `.env.local`.

## Local Development

Local settings default to the console email backend so no real mail is sent during development:

```python
# settings/local.py
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
```

Uncomment this line in your `settings/local.py` (copied from `settings/local_template.py`).

## Sending a Test Email

```bash
python manage.py test_send_email --to ops@yuda.me
```

This uses the configured backend. Run against staging to verify the Postmark token is working before enabling production sends.

## DNS Records (mail.ai.yuda.me)

Postmark requires these DNS records on the sender domain. Get the exact values from the Postmark dashboard under **Sender Signatures** or **Domains**.

| Type | Name | Value |
|------|------|-------|
| TXT | `mail.ai.yuda.me` | SPF: `v=spf1 a mx include:spf.mtasv.net ~all` |
| TXT | `pm._domainkey.mail.ai.yuda.me` | DKIM public key (from Postmark dashboard) |
| TXT | `_dmarc.mail.ai.yuda.me` | `v=DMARC1; p=quarantine; rua=mailto:dmarc@yuda.me` |

Verify DNS propagation at [mxtoolbox.com](https://mxtoolbox.com) before enabling production traffic.

## Token Rotation

1. Generate a new Server API Token in the Postmark dashboard under **API Tokens**.
2. Update `POSTMARK_SERVER_TOKEN` in the Render environment group.
3. Trigger a redeploy (Render auto-redeploys when env vars change).
4. Verify email delivery with `python manage.py test_send_email --to ops@yuda.me`.
5. Revoke the old token in Postmark after confirming the new one works.

## Postmark Activity Logs

View delivery logs, opens, bounces, and spam complaints at:
`https://account.postmarkapp.com/servers/{server-id}/activity`

API access: `GET https://api.postmarkapp.com/messages/outbound` with `X-Postmark-Server-Token` header.

## Bounce and Complaint Handling

Postmark automatically suppresses future sends to bounced addresses (hard bounces) and spam complainants. The suppression list is visible in the Postmark dashboard under **Suppressions**.

- **Soft bounces**: Postmark retries automatically; no action needed.
- **Hard bounces**: Address is suppressed. Remove from suppression list only if the address owner confirms the issue is fixed.
- **Spam complaints**: Address is suppressed immediately. Do not remove without explicit opt-in.

Anymail surfaces bounce/complaint events as Django signals if webhook processing is configured. See [anymail docs](https://anymail.dev/en/stable/sending/tracking/) for details.
