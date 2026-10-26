# Optional: connect Gmail + Google Calendar (read-only)

MUIS OS Lite can read a summary of recent Gmail and today's Calendar events, for the agent
(`gmail_recent`, `calendar_today` tools) and the daily brief. It is off unless all three env vars
are set, and it only ever requests **read-only** scopes.

## 1. Create an OAuth client (5 minutes)

1. Open <https://console.cloud.google.com/>, create (or pick) a project.
2. **APIs & Services → Library**: enable **Gmail API** and **Google Calendar API**.
3. **APIs & Services → OAuth consent screen**: type *External*, add yourself as a *Test user*.
4. **Credentials → Create credentials → OAuth client ID**, type **Web application**, and add
   `https://developers.google.com/oauthplayground` as an authorised redirect URI.
   Copy the **Client ID** and **Client secret**.

## 2. Mint a refresh token with the OAuth Playground

1. Open <https://developers.google.com/oauthplayground>, click the gear icon, tick
   **Use your own OAuth credentials**, paste the client ID and secret.
2. In *Step 1*, enter these two scopes and click **Authorize APIs**:
   ```
   https://www.googleapis.com/auth/gmail.readonly
   https://www.googleapis.com/auth/calendar.readonly
   ```
3. Sign in with your Google account, then in *Step 2* click **Exchange authorization code for
   tokens** and copy the **Refresh token**.

## 3. Configure

```env
GOOGLE_CLIENT_ID=...apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=...
GOOGLE_REFRESH_TOKEN=1//...
```

Restart (`docker compose up -d`). Settings shows **Google: Gmail + Calendar, read-only** and the
two tools appear in the agent's tool list. While the consent screen is in *Testing* mode, Google
expires refresh tokens after 7 days; publish the app (no verification needed for personal use with
under 100 users) to keep it working.
