# BidAlert Comms Hub

> Internal email automation platform for **BidAlert.in** — AI-assisted drafting, safe approval gates, predictable scheduling, and deliverability protection via the Gmail API.

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Tech Stack](#tech-stack)
4. [Safety Rails & Deliverability Engine](#safety-rails--deliverability-engine)
5. [Navigation & Screens](#navigation--screens)
6. [Scheduler & Sending Engine](#scheduler--sending-engine)
7. [Quick Start (Local Development)](#quick-start-local-development)
8. [Configuration & Environment Variables](#configuration--environment-variables)
9. [Google Cloud & Gmail Setup](#google-cloud--gmail-setup)
10. [Deployment (PythonAnywhere)](#deployment-pythonanywhere)
11. [Troubleshooting Guide ("Why aren't emails sending?")](#troubleshooting-guide-why-arent-emails-sending)
12. [Management Commands](#management-commands)
13. [Security Notes](#security-notes)

---

## Overview

BidAlert Comms Hub is a specialized internal web app designed to manage email communication to past webinar attendees, members, and event contacts (26,000+ leads) without risking domain reputation or spam blacklists.

Key capabilities:
- **Guided Email Wizard**: Step-by-step composition with live audience exclusions and automated safety checks.
- **Knowledge-Grounded AI**: Drafts emails using Google Gemini conditioned strictly on company facts from the KnowledgeBase.
- **Inbox-Safety Score Gate**: Analyzes draft content for spam triggers; blocks scheduling if safety score < 7.
- **Predictable Scheduling**: Spread sends across days, respect custom business windows (10:00–17:00 IST), and pace emails with human jitter.
- **Deliverability Circuit Breaker**: Auto-pauses if hard bounce rates exceed 5% or 10 bounces occur within 24 hours.
- **RFC 8058 Compliant**: Automatic `List-Unsubscribe` headers, per-recipient signed HMAC unsubscribe links, and automatic suppression handling.

---

## Architecture

```
Client Browser (Vanilla HTML5 / CSS Tokens / Native Dialogs)
   │
   ▼
PythonAnywhere WSGI Web Server (Django 5.0.x)
   ├── SQLite (Local Dev / Lightweight) or MySQL (Production Recommended)
   ├── Google Gemini API (AI Drafting & Content Grounding)
   ├── Gmail API v1 (OAuth 2.0 with Tokens Encrypted at Rest)
   └── Sender Engine (`run_sender` via Hourly Task or Always-On Worker)
```

- **Zero External Frontend Dependencies**: No Tailwind CDN, no Google Fonts, no external JavaScript trackers. All CSS variables and SVG sprites are hosted locally.
- **Dedicated Sender Engine**: Decoupled from HTTP requests. Batches are processed via `python manage.py run_sender` (hourly or continuous loop), preventing web request timeouts.

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Python 3.11+, Django 5.0.x |
| **Database** | SQLite (Dev) / MySQL or PostgreSQL (Production via `dj-database-url`) |
| **Styling** | Hand-crafted Vanilla CSS (`static/css/app.css`) with modern CSS variables |
| **Frontend Scripts** | Vanilla JS (`static/js/app.js`) with native `<dialog>` and `aria-live` notifications |
| **AI Integration** | Google Gemini (`GEMINI_MODEL` configurable, defaults to Flash) |
| **Email Protocol** | Gmail REST API v1 (`google-api-python-client`, `google-auth`) |
| **Security** | Fernet token encryption (`cryptography`), signed unsubscribe tokens (`signing`), CSRF protection |
| **DNS / Deliverability** | `dnspython` for MX validation with database caching |

---

## Safety Rails & Deliverability Engine

1. **Campaign Status Gating**: The engine only touches SendLog rows for campaigns with status `scheduled` or `sending`. Paused or cancelled campaigns stop sending immediately.
2. **Frequency Cap**: Configurable cooldown (default 7 days). Contacts who received an email in the last 7 days are automatically excluded.
3. **Error Classification**:
   - `permanent` (5xx recipient not found): Marks row bounced, auto-suppresses contact, increments bounce rate metric.
   - `transient` (network / timeout / 5xx Google errors): Row stays queued, attempts counter increments with exponential backoff (fails after 3 retries).
   - `quota` (HTTP 429 / Rate Limit): Engine pauses the run and sets `sending_blocked_until`.
   - `auth` (invalid_grant / 401): Halts sending, alerts administrators on the dashboard that Gmail needs re-connection.
4. **Rate-Based Circuit Breaker**: If hard-bounce rate exceeds `bounce_pause_percent` (default 5%) across ≥20 attempts in 24 hours, the system immediately auto-pauses all sending and records `pause_reason`.
5. **Atomic Row Claiming**: Concurrent workers never send the same email twice. Rows are claimed using `UPDATE ... SET status='sending', claim_token=<uuid>` before delivery.
6. **Timezone Accuracy**: All scheduling and daily volume limits calculate against local time (`Asia/Kolkata`), never naive server dates.

---

## Navigation & Screens

The user interface uses plain language and guided steps:

| Section | Purpose |
|---|---|
| **Home** | System status banner (Ready / Paused / Auto-Paused / Stale Heartbeat), daily send progress, deliverability health, and getting-started checklist. |
| **New Email** | 4-step wizard: **Who** (audience & exclusions) → **What** (AI draft, merge tags, safety score) → **Test** (required inbox test) → **When** (send now, date/time, or spread over days). |
| **Campaigns** | Overview of scheduled, running, paused, and finished campaigns with progress bars and action controls (Pause, Resume, Cancel). |
| **Contacts** | Contact database, search, and CSV Import Wizard with duplicate detection, syntax validation, and session undo. |
| **Activity** | Full audit log of every attempt (Sent, Bounced, Couldn't send, Skipped) with timestamps and Gmail Message IDs. |
| **Settings** | Gmail connection status, Sending limits & schedule windows, Company Facts (grounding AI), Do-not-email list, Team RBAC, and DNS checklist. |

---

## Scheduler & Sending Engine

The scheduler operates via `sender/engine.py:run_once()`.

- **Fairness**: Multiple active campaigns are served using **round-robin scheduling** so large volume campaigns do not starve smaller ones.
- **Send Windows**: Outbound delivery occurs exclusively within business hours (e.g., 10:00 to 17:00 IST) and approved weekdays (e.g., Monday through Saturday).
- **Jitter Delay**: Introduces random pauses (20–60s) between consecutive emails to mimic human behavior and comply with provider rate filters.
- **Worker Heartbeat**: Records `last_worker_heartbeat` on every execution, alerting operators if the scheduler stops running.

---

## Quick Start (Local Development)

```bash
# 1. Create and activate virtual environment
python -m venv .venv
# On Windows: .venv\Scripts\activate
# On Linux/macOS: source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Generate secrets and configure environment
python scripts/gen_secrets.py
cp .env.example .env
# Edit .env with your generated keys and Google Cloud credentials

# 4. Run database migrations and seed data
python manage.py migrate
python manage.py createsuperuser
python manage.py seed_knowledge

# 5. Start the development server
python manage.py runserver
```

Open `http://localhost:8000` in your browser.

---

## Configuration & Environment Variables

Key configuration variables (see `.env.example` for the complete annotated list):

| Variable | Description | Default |
|---|---|---|
| `SECRET_KEY` | Primary cryptographic secret key | Required in production |
| `SECRET_KEY_FALLBACKS` | Comma-separated prior keys to keep historical unsubscribe links valid | Optional |
| `DEBUG` | Enable/disable debug mode (`False` in production) | `False` |
| `ALLOWED_HOSTS` | Comma-separated list of valid hostnames | Derived from `BASE_URL` |
| `BASE_URL` | Canonical public URL (e.g., `https://app.bidalert.in`) | `http://localhost:8000` |
| `DATABASE_URL` | Connection URL (`sqlite:///db.sqlite3` or MySQL) | SQLite |
| `GOOGLE_CLIENT_ID` | OAuth2 Client ID from Google Cloud Console | Required for Gmail |
| `GOOGLE_CLIENT_SECRET` | OAuth2 Client Secret from Google Cloud Console | Required for Gmail |
| `GEMINI_API_KEY` | API Key from Google AI Studio | Required for AI Compose |
| `GEMINI_MODEL` | Gemini model name | `gemini-1.5-flash` |
| `CRON_SECRET` | Secret token for authorized API triggers | Required |
| `DEFAULT_SEND_TIMEZONE` | Timezone for windows and daily resets | `Asia/Kolkata` |
| `SENDER_DISPLAY_NAME` | Default display name in email From header | `BidAlert Support` |
| `REPLY_TO_EMAIL` | Reply-To address on outgoing emails | `support@bidalert.in` |

---

## Google Cloud & Gmail Setup

1. **Google Cloud Console**: Navigate to [console.cloud.google.com](https://console.cloud.google.com) and create or select your project.
2. **Enable Gmail API**: Under *APIs & Services* → *Library*, search for and enable **Gmail API**.
3. **Configure OAuth Consent**:
   - If using Google Workspace (`bidalert.in`), choose **Internal** so refresh tokens remain permanent.
   - If External, add your sender email as a Test User (external testing tokens expire every 7 days).
4. **Create Credentials**:
   - Application Type: **Web application**.
   - Authorized Redirect URI: `https://<your-host>/gmail/callback/` (include trailing slash).
5. **Connect**: In the app, navigate to **Settings** → **Gmail** tab → click **Connect Gmail**.

---

## Deployment (PythonAnywhere)

Complete deployment instructions are detailed in [DEPLOY_PYTHONANYWHERE.md](file:///d:/RIYAZ%20DAKAIT/mail%20automation/DEPLOY_PYTHONANYWHERE.md).

### Summary Workflow:
1. Clone private repository to `/home/<username>/bidalert`.
2. Create Python 3.11 virtualenv and install `requirements.txt`.
3. Create `/home/<username>/bidalert.env` (`chmod 600`) and set `ENV_FILE` in WSGI configuration.
4. Run `python manage.py migrate`, `collectstatic`, `createsuperuser`, and `seed_knowledge`.
5. Configure Web app in PythonAnywhere Web tab with static files mapped to `/home/<username>/bidalert/staticfiles`.
6. Add Scheduled Task in Tasks tab:
   ```bash
   /home/<username>/.virtualenvs/bidalert/bin/python /home/<username>/bidalert/manage.py run_sender --max-seconds 3000
   ```

---

## Troubleshooting Guide ("Why aren't emails sending?")

If sending appears stuck, follow this decision tree:

```
Is outbound sending paused globally?
  ├─► YES: Check Home screen banner. If auto-paused, inspect bounce logs, remediate, and click "Resume Sending".
  └─► NO:
       │
Is the current time inside the Send Window (e.g. 10:00–17:00 IST) and on an active weekday?
  ├─► NO: Engine will wait until next scheduled window. Check "Next send" on Home screen.
  └─► YES:
       │
Has today's daily limit been reached?
  ├─► YES: Check progress bar on Home. Sending resumes automatically at 00:00 IST tomorrow.
  └─► NO:
       │
Is the campaign status 'scheduled' or 'sending'?
  ├─► NO: Ensure campaign is approved and not in 'draft', 'paused', or 'done'.
  └─► YES:
       │
Is Gmail connected and token valid?
  ├─► NO: Reconnect Gmail via Settings → Gmail tab.
  └─► YES:
       │
Is the sender worker running?
  ├─► NO: Check "Sender heartbeat" on Home. If red/stale, verify scheduled task or run:
  │        python manage.py run_sender
  └─► YES: Inspect Activity logs for transient errors or recipient skipping reasons.
```

---

## Management Commands

- `python manage.py run_sender [--max-seconds 3000] [--loop --interval 60] [--dry-run]`
  Executes the core sending engine with optional time limits, continuous loop, or dry-run inspection.
- `python manage.py repair_queue [--dry-run]`
  Audits and cleans orphaned rows, cancels rows from non-sending campaigns, and resolves duplicate lead assignments.
- `python manage.py verify_leads [--limit 100]`
  Validates MX and domain records in background batches without blocking web views.
- `python manage.py backup_db`
  Generates automated database backups into `backups/` with 14-day rotation.
- `python manage.py seed_knowledge`
  Initializes baseline BidAlert company facts for Gemini AI grounding.

---

## Security Notes

Refer to [SECURITY-TODO.md](file:///d:/RIYAZ%20DAKAIT/mail%20automation/SECURITY-TODO.md) for critical operational guidelines:
- Never commit `.env` or database files to source control.
- Tokens at rest are encrypted with Fernet keys derived from `SECRET_KEY`.
- Rotate API keys and OAuth credentials when moving to production.
