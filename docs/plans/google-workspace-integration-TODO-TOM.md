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
7. **Provide to Valor:** Place at `/Users/valorengels/.config/valor/google_credentials.json`

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
```

## 2. Verify Authorized Redirect URIs

Ensure the OAuth client has this redirect URI configured:
```
http://localhost:8080/
```

This is used during the initial authentication flow.

## 3. Workspace Domain Verification (if applicable)

If your Google Workspace has domain restrictions:
- Verify that `valor@yuda.me` has permission to use these APIs
- Add the OAuth client to the allowed apps list (if internal-only mode is enabled)

## 4. Project Information

Please provide:
- **Google Cloud Project ID:** `_____________________`
- **Project Number (if needed):** `_____________________`
- **Credentials file location:** (confirm it's at the path above)

---

## Questions?

If you need help with any of these steps, let me know. Once the credentials are in place, I can proceed with implementation.
