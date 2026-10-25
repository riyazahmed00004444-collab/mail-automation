# SECURITY TODO — Action Required Before Production

> Complete ALL items below before going live. This is not optional.

## 1. Rotate Django SECRET_KEY
- Generate a new key: `python scripts/gen_secrets.py`
- Set `SECRET_KEY` in `.env` / `~/bidalert.env` to the new value
- Add the **old key** to `SECRET_KEY_FALLBACKS` (comma-separated) so unsubscribe links
  already emailed to recipients remain valid for ~1 year
- Never share the key; never commit it to git

## 2. Rotate CRON_SECRET
- Generate a new token: `python scripts/gen_secrets.py`
- Update `CRON_SECRET` in `.env` / `~/bidalert.env`
- Update the secret in PythonAnywhere's scheduled task command if you pass it as a header

## 3. Rotate Google OAuth Client Secret
- Go to [Google Cloud Console](https://console.cloud.google.com/) →
  APIs & Services → Credentials → your OAuth 2.0 Client
- Click "Reset Secret" to generate a new `GOOGLE_CLIENT_SECRET`
- Update `.env` / `~/bidalert.env` with the new value
- **Reconnect Gmail** in Settings after updating (old tokens remain valid until revoked)

## 4. Rotate Gemini API Key
- Go to [Google AI Studio](https://aistudio.google.com/) → API keys
- Delete the old key and create a new one
- Update `GEMINI_API_KEY` in `.env` / `~/bidalert.env`

## 5. Reconnect Gmail on Production
- On your production PythonAnywhere site, go to Settings → Gmail
- Click "Disconnect" then "Connect Gmail" and complete OAuth
- This creates a fresh token bound to the production redirect URI
- The old local token from `db.sqlite3` is invalid on production anyway

## 6. Never Upload These Files
- `.env` — contains all secrets
- `db.sqlite3` — contains real lead emails and Gmail OAuth tokens
- `*.csv` — contains 26k+ real contacts
- Do NOT paste these files into any chat, email, zip sent to anyone

## 7. File Permissions on PythonAnywhere
```bash
chmod 600 ~/bidalert.env   # only your user can read it
chmod 600 ~/bidalert/db.sqlite3  # if using SQLite
```

## 8. Git History Cleanup
If `.env`, `db.sqlite3`, or `.csv` files were ever committed:
```bash
# Install git-filter-repo first (pip install git-filter-repo)
git filter-repo --path .env --invert-paths
git filter-repo --path db.sqlite3 --invert-paths
git filter-repo --path a18054022236.csv --invert-paths
git push --force-with-lease origin main
```
Then rotate ALL secrets (assume they are compromised once in git history).

## 9. OAuth App Type (Important for Token Expiry)
- If `bidalert.in` is a **Google Workspace** organisation:
  - Set OAuth consent screen User Type = **Internal** → tokens last indefinitely
- If it's **External** (Testing status):
  - Tokens expire after **7 days** → sending will break weekly
  - Either publish the app (requires Google review) or switch to Internal
  - The app will show a banner when the token is about to expire (auth error detection)
