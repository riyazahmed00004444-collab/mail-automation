# Deploying BidAlert Comms Hub on PythonAnywhere

> **Plan required:** Hacker ($5/mo) or higher for hourly scheduled tasks and
> unrestricted outbound internet. The free plan will NOT work.

---

## Prerequisites

- A private GitHub repository with your code (no secrets, DB, or CSV files).
- A PythonAnywhere account on the **Hacker plan** or above.
- Your domain `app.bidalert.in` (or a `*.pythonanywhere.com` subdomain).
- Google Cloud project with OAuth credentials already set up.

---

## Step 1 — Push code to GitHub

```bash
# On your local machine
git init   # if not already a git repo
git add .
git commit -m "phase-0: repo hygiene and initial structure"
git remote add origin https://github.com/<you>/bidalert-comms.git
git push -u origin main
```

**Verify no secrets are tracked:**
```bash
git ls-files | grep -E "\.env|\.sqlite3|\.csv"
# Should return nothing
```

---

## Step 2 — Clone on PythonAnywhere

Open a **Bash console** on PythonAnywhere:

```bash
cd ~
git clone https://github.com/<you>/bidalert-comms.git bidalert
```

---

## Step 3 — Create virtualenv and install dependencies

```bash
mkvirtualenv bidalert --python=python3.11
workon bidalert
pip install -r ~/bidalert/requirements.txt

# If using MySQL (recommended), also install:
pip install mysqlclient
```

---

## Step 4 — Create the env file (outside the project directory)

```bash
# Generate fresh secrets
python ~/bidalert/scripts/gen_secrets.py

# Create env file at a path outside the project
nano ~/bidalert.env
```

Paste the contents of `.env.example` and fill in **all** real values:

```ini
SECRET_KEY=<output from gen_secrets.py>
DEBUG=False
ALLOWED_HOSTS=<user>.pythonanywhere.com,app.bidalert.in
BASE_URL=https://app.bidalert.in

# MySQL (recommended):
DATABASE_URL=mysql://<user>:<password>@<user>.mysql.pythonanywhere-services.com/<user>$bidalert

GOOGLE_CLIENT_ID=<your-client-id>.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=<your-secret>
GEMINI_API_KEY=<your-api-key>
GEMINI_MODEL=gemini-1.5-flash-latest
CRON_SECRET=<output from gen_secrets.py>
SENDER_DISPLAY_NAME=BidAlert
REPLY_TO_EMAIL=info@bidalert.in
DEFAULT_SEND_TIMEZONE=Asia/Kolkata
FORCE_HTTPS=False
```

Secure the file:
```bash
chmod 600 ~/bidalert.env
```

---

## Step 5 — Database setup

**Option A — Neon DB (PostgreSQL — Highly Recommended for Serverless & Cloud):**
1. Create a free project at [neon.tech](https://neon.tech)
2. Copy your Connection String (pooled connection recommended):
   ```ini
   DATABASE_URL=postgresql://<user>:<password>@<ep-pooler-id>.us-east-2.aws.neon.tech/neondb?sslmode=require
   ```
3. Paste it as `DATABASE_URL` in `~/bidalert.env` (Note: Requires Hacker $5/mo plan or above on PythonAnywhere for external TCP database access).

**Option B — MySQL on PythonAnywhere (Built-in Free with PA account):**
1. Go to PA **Databases tab** → Create a new MySQL database named `bidalert`
2. Set your MySQL password and note your host (e.g. `<user>.mysql.pythonanywhere-services.com`)
3. Set `DATABASE_URL=mysql://<user>:<password>@<user>.mysql.pythonanywhere-services.com/<user>$bidalert` in `~/bidalert.env`
4. Run `pip install mysqlclient` in your virtualenv.

**Option C — SQLite (Quick start & initial testing):**
```ini
DATABASE_URL=sqlite:///home/<user>/bidalert/db.sqlite3
```
*Note: SQLite is fine for small testing, but locks files when web and background sender write at the same time. Use Neon DB or MySQL for daily automated production sending.*

---

## Step 6 — Run Django setup commands

```bash
cd ~/bidalert
workon bidalert
export ENV_FILE=~/bidalert.env

python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
# Then seed the knowledge base:
python manage.py shell -c "
from knowledge.models import KnowledgeBase
KnowledgeBase.objects.get_or_create(
    title='About BidAlert',
    defaults={'content': 'BidAlert.in is a B2B tender alert and lead intelligence platform for Indian businesses.', 'is_active': True}
)
print('KB seeded.')
"
```

---

## Step 7 — Import data from your local machine

On your **local machine:**
```bash
cd "D:\RIYAZ DAKAIT\mail automation"
python manage.py export_for_prod --out seed.json
# Note the file size — if > 50MB, split it
```

Upload `seed.json` to PythonAnywhere via the **Files tab**, then:
```bash
# On PythonAnywhere Bash console:
cd ~/bidalert
workon bidalert
export ENV_FILE=~/bidalert.env

python manage.py import_seed ~/seed.json --dry-run   # check counts first
python manage.py import_seed ~/seed.json              # import for real

# IMPORTANT: delete the seed file immediately after import
rm ~/seed.json
```

---

## Step 8 — Configure the Web App

1. Go to **Web tab** → *Add a new web app* → **Manual configuration** → Python 3.11
2. Set:
   - **Source directory:** `/home/<user>/bidalert`
   - **Virtualenv:** `/home/<user>/.virtualenvs/bidalert`
3. Click **WSGI configuration file** and replace its contents with:

```python
import sys
import os

# Add project to Python path
path = '/home/<user>/bidalert'
if path not in sys.path:
    sys.path.insert(0, path)

# Point to the env file (outside project dir)
os.environ['ENV_FILE'] = '/home/<user>/bidalert.env'
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'

from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
```

4. **Static files mapping:**
   - URL: `/static/`
   - Directory: `/home/<user>/bidalert/staticfiles`

5. Click **Reload**.

---

## Step 9 — Add Google OAuth redirect URI

In **Google Cloud Console** → APIs & Services → Credentials → your OAuth client:

Add authorized redirect URI:
```
https://app.bidalert.in/gmail/callback/
```
(Exact match including trailing slash)

> **IMPORTANT — OAuth App Type:**
> - If `bidalert.in` is a **Google Workspace** org → set User Type = **Internal**
>   (tokens last indefinitely)
> - If **External** (Testing mode) → tokens expire after **7 days**. Either publish
>   the app (Google review required) or switch to Internal. The dashboard will show
>   a "Gmail needs reconnecting" banner when the token expires.

---

## Step 10 — Connect Gmail

1. Open `https://app.bidalert.in` and log in as the superuser
2. Go to **Settings → Gmail**
3. Click **Connect Gmail** and authorize with the sending account
4. Verify the status shows ✅ Connected

---

## Step 11 — Set up Scheduled Tasks

Go to **Tasks tab** on PythonAnywhere:

### Hourly task (works on Hacker plan):
```
/home/<user>/.virtualenvs/bidalert/bin/python /home/<user>/bidalert/manage.py run_sender --max-seconds 3000
```
Set it to run at minute **:05** each hour (e.g. 10:05, 11:05, …)

Set `ENV_FILE` in the task:
```
ENV_FILE=/home/<user>/bidalert.env /home/<user>/.virtualenvs/bidalert/bin/python /home/<user>/bidalert/manage.py run_sender --max-seconds 3000
```

### Always-on task (if your plan supports it — replaces hourly task):
```
ENV_FILE=/home/<user>/bidalert.env /home/<user>/.virtualenvs/bidalert/bin/python /home/<user>/bidalert/manage.py run_sender --loop --interval 60
```
**Use either the hourly OR the always-on task — not both.**

### Daily backup task (recommended):
```
ENV_FILE=/home/<user>/bidalert.env /home/<user>/.virtualenvs/bidalert/bin/python /home/<user>/bidalert/manage.py backup_db
```

---

## Step 12 — Custom domain (optional but recommended)

1. In your DNS registrar, add a CNAME:
   ```
   app.bidalert.in  →  <user>.pythonanywhere.com
   ```
2. In PA **Web tab** → Custom domain → enter `app.bidalert.in`
3. Enable **HTTPS** in the Web tab (PA provides free Let's Encrypt)
4. Update `BASE_URL=https://app.bidalert.in` in `~/bidalert.env`
5. Reload the web app

---

## Step 13 — DNS (Email Deliverability)

Add these DNS records at your domain registrar for `bidalert.in`:

| Type | Host | Value |
|------|------|-------|
| TXT | `@` | `v=spf1 include:_spf.google.com ~all` |
| TXT | `google._domainkey` | *(from Google Workspace Admin → Apps → Gmail → Authenticate email)* |
| TXT | `_dmarc` | `v=DMARC1; p=none; rua=mailto:dmarc@bidalert.in` |

> Start with `p=none` for 2 weeks to collect reports, then move to `p=quarantine`.
> The Domain checklist in Settings will show you these with copy buttons.

---

## Step 14 — Smoke tests

```bash
# On PythonAnywhere Bash:
cd ~/bidalert
workon bidalert
export ENV_FILE=~/bidalert.env

# System check
python manage.py check --deploy

# Dry run sender
python manage.py run_sender --dry-run

# Verify repair queue is clean
python manage.py repair_queue
```

On the website:
1. ✅ Log in → Home shows the status banner
2. ✅ Settings → Gmail → Connected
3. ✅ New Email → go through wizard → send a test email to yourself
4. ✅ Click the unsubscribe link in the test email
5. ✅ Check Activity page shows the test send
6. ✅ Check sender heartbeat on Home (green after first scheduled task run)

---

## Troubleshooting

| Problem | Check |
|---------|-------|
| 500 error | `/var/log/www.pythonanywhere.com_error.log` and `~/logs/bidalert.log` |
| Emails not sending | Home banner reason · Settings → Gmail status · heartbeat · `repair_queue` |
| Gmail 401 / needs reconnecting | Settings → Disconnect → reconnect OAuth |
| Daily cap hit | Home shows `X/Y sent today` · cap resets at midnight IST |
| Quota blocked | Wait 1 hour OR check `Setting.sending_blocked_until` in Django admin |
| `migrate` fails | Check `DATABASE_URL` in `~/bidalert.env` · `workon bidalert` active? |
| Static files 404 | Run `collectstatic` · Check Web tab static mapping |
| Tokens expire weekly | Set Google OAuth app User Type = Internal (Workspace org) |

---

## Updating the app

```bash
cd ~/bidalert
git pull origin main
workon bidalert
pip install -r requirements.txt   # if requirements changed
python manage.py migrate
python manage.py collectstatic --noinput
# Reload in Web tab
```
