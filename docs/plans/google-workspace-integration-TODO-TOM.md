# Google Workspace Integration - Prerequisites for Tom

These items are required before Valor can implement the Google Workspace integration.

## 1. Create Google Cloud Project OAuth Credentials

### Steps:
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Select your project (or create one if needed)
3. Navigate to **APIs & Services > Credentials**
4. Click **+ CREATE CREDENTIALS > OAuth client ID**
5. Configure:
   - Application type: **Desktop app**
   - Name: `Valor Desktop Client` (or similar)
6. Download the JSON file
7. **Provide to Valor:** Place at `~/Desktop/claude_code/google_credentials.json` (iCloud-synced across machines)

### Required OAuth Scopes

Enable these APIs and configure the OAuth consent screen with these scopes:

```
https://www.googleapis.com/auth/gmail.modify
https://www.googleapis.com/auth/calendar
https://www.googleapis.com/auth/drive
https://www.googleapis.com/auth/documents
https://www.googleapis.com/auth/spreadsheets
https://www.googleapis.com/auth/presentations
https://www.googleapis.com/auth/userinfo.profile
https://www.googleapis.com/auth/chat.messages
https://www.googleapis.com/auth/chat.spaces.readonly
```

**Note:** `userinfo.profile` is used for `people.getMe()` (user context). Timezone is retrieved via the Calendar API settings, no extra scope needed. Chat scopes enable reading spaces and sending/reading messages.

## 2. ~~Verify Authorized Redirect URIs~~ DONE

The credentials file already has `http://localhost` configured as the redirect URI. This matches the `.env` setting and is correct for a Desktop app OAuth flow (Google's auth library picks an available port automatically).

No action needed.

## 3. Workspace Domain Verification (if applicable)

If your Google Workspace has domain restrictions:
- Verify that `valor@yuda.me` has permission to use these APIs
- Add the OAuth client to the allowed apps list (if internal-only mode is enabled)

## 4. ~~Project Information~~ DONE

- **Google Cloud Project Name:** Yudame General
- **Google Cloud Project ID:** `quickstart-1586433403044`
- **Project Number:** `224102219743`
- **Credentials file location:** `~/Desktop/claude_code/google_credentials.json` (iCloud-synced)

---

## Questions?

If you need help with any of these steps, let me know. Once the credentials are in place, I can proceed with implementation.
