# 📋 BidAlert Comms Hub — Complete Operator Handover & User Guide

> **System URL:** [https://bidalert-comms.onrender.com](https://bidalert-comms.onrender.com)  
> **Source Code:** [GitHub Repository](https://github.com/riyazahmed00004444-collab/mail-automation)  
> **Database:** Neon Cloud Lakebase Postgres (`mail_automation_DB`)  
> **Authenticated Sender Account:** `info@bidalert.in` (Google OAuth2)  
> **Current Lead Inventory:** 27,586 verified leads  

---

## 📑 Table of Contents
1. [System Architecture & Access](#1-system-architecture--access)
2. [Quick Reference: Daily Operator Checklist](#2-quick-reference-daily-operator-checklist)
3. [Step 1: Managing & Uploading Leads](#3-step-1-managing--uploading-leads)
4. [Step 2: Creating & Drafting Campaigns](#4-step-2-creating--drafting-campaigns)
5. [Step 3: Scheduling & Dispatching](#5-step-3-scheduling--dispatching)
6. [Step 4: Monitoring Deliverability & Tracking](#6-step-4-monitoring-deliverability--tracking)
7. [Settings, Anti-Spam & Circuit Breakers](#7-settings-anti-spam--circuit-breakers)
8. [Troubleshooting & FAQ](#8-troubleshooting--faq)

---

## 1. System Architecture & Access

### Accessing the Web Dashboard
* **Login URL:** [https://bidalert-comms.onrender.com/accounts/login/](https://bidalert-comms.onrender.com/accounts/login/)
* **Default Admin Username:** `admin`
* **Default Password:** `admin123` *(operators can update passwords anytime via Django Admin or terminal)*

### Core Infrastructure
* **Web Tier:** Render Cloud Web Service (running Python 3.11 + Gunicorn + WhiteNoise for instant static assets).
* **Database Tier:** Managed Neon Cloud PostgreSQL running OLTP Lakebase Postgres on AWS (`us-east-2`). High availability, automatic backups, and branch support.
* **Email Gateway:** Official Google Workspace Gmail API (`https://www.googleapis.com/auth/gmail.send`) with token encryption at rest.

---

## 2. Quick Reference: Daily Operator Checklist

Every day at or before **10:00 AM IST**:

1. **Check System Banner:** Open the Dashboard. Ensure the top status pill shows `Sending Active` (green). If it says `Sending Paused`, review any pause reason and click to activate.
2. **Review Lead Depletion:** Look at the **Lead Pipeline** card. If fewer than 50 new leads remain, prepare a fresh CSV dump.
3. **Check Gmail Status:** Ensure the Gmail status pill shows `✅ Connected (info@bidalert.in)`.
4. **Review Daily Cap:** Check the circular gauge (`X / 50 sent today`). Sending will automatically halt when the cap is reached.

---

## 3. Step 1: Managing & Uploading Leads

The platform supports two distinct channels:
1. **Targeted Campaigns (`targeted`):** Industry/segment-specific leads for targeted outreach.
2. **Direct Draft Dumper (`direct_draft`):** General outreach / high-volume lead dumper.

### How to Upload a CSV of Leads:
1. Click **Leads** in the top navigation bar.
2. Click the **Upload Leads** button.
3. Choose the target **Channel** (`Targeted Campaign` or `Direct Draft Dumper`).
4. Select the **Lead Category**:
   * `buyers` (Active Buyers / Procurement)
   * `general` (General Inquiries)
   * `promotion` (Promotional Outreach)
   * `service` (Service Inquiries)
   * `plans` (Pricing Plans Interested)
   * `subscription` (Subscription Renewals)
5. **CSV Format Requirements:**
   The uploader automatically detects columns by header name (case-insensitive). Ensure your CSV has at least an email column:

| Column Header | Required? | Example | Description |
| :--- | :--- | :--- | :--- |
| `email` | **YES** | `ravi@example.com` | Lead's email address |
| `name` / `first_name` | Optional | `Ravi Kumar` | Recipient name (used in `{{lead_name}}`) |
| `company` | Optional | `InfraTech Ltd` | Company name (used in `{{company}}`) |
| `phone` | Optional | `+91 9876543210` | Contact phone number |
| `source` | Optional | `TiE Con 2024` | Lead source or exhibition name |

### Automated Sorting & Hygiene on Upload:
When you upload a file, the system automatically:
* **Removes Invalid & Disposable Emails:** Blocks throwaway domains (`tempmail`, `10minutemail`, `discardmail`).
* **Checks MX Records:** Validates that the recipient domain has active mail servers.
* **Deduplicates:** Prevents duplicate emails from being re-added.
* **Suppression Scrubbing:** Automatically rejects any email that previously unsubscribed or bounced.

---

## 4. Step 2: Creating & Drafting Campaigns

1. Go to **Campaigns** → Click **New Campaign**.
2. Fill in the campaign details:
   * **Campaign Name:** Internal descriptive title (e.g. `Q4 Tenders Outreach - Infrastructure`).
   * **Subject Line:** Subject for Gmail. Supports merge tags (e.g. `New Tenders for {{company}}`).
   * **Target Category:** Choose which lead category should receive this campaign (e.g. `buyers`).
   * **Body Template:** Plain text and HTML formatted proposal.

### Merge Variables Available:
* `{{lead_name}}` — Replaced with the contact's name (defaults to username if blank).
* `{{company}}` — Replaced with the lead's company name.
* `{{unsub_url}}` — Mandatory one-click unsubscribe link generated uniquely for each lead.

### AI Assistance (Gemini Flash):
When drafting in the composer, click **"Enhance with Gemini AI"** to generate high-converting B2B copy, refine tone, or create concise executive summaries.

---

## 5. Step 3: Starting Sending (1-Click Approve & Queue)

Starting a campaign is now **1 single click** — no confusing scheduling forms or manual queuing steps required:

### ⚡ 1-Click Approval & Launch:
1. Open **[Campaigns](https://bidalert-comms.onrender.com/campaigns/)** or **[Scheduler](https://bidalert-comms.onrender.com/scheduler/)**.
2. Next to your draft campaign (e.g., **GeM & Non-GeM Procurement Support - Buyers Batch**), click:
   👉 **`⚡ 1-Click Approve & Queue`** (or **`⚡ 1-Click Approve & Start Sending`**).
3. **That's it!** The system automatically:
   * Sets campaign status to **`Scheduled`**
   * Automatically pulls all target uncontacted leads (e.g. 27,385 clean buyers)
   * Populates them directly into the queue (`SendLog.status = 'queued'`)
   * Begins sending during the scheduled delivery window (10:00 AM – 5:00 PM IST)

---

### Sending Rules (Engine Constraints):
* **Operating Hours:** **10:00 AM to 5:00 PM IST** (Sending pauses outside this window to protect open rates).
* **Active Days:** **Monday through Saturday** (Sundays resting).
* **Anti-Spam Jitter Delay:** An organic random delay of **20 to 60 seconds** is injected between every dispatched email to protect domain reputation.
* **Frequency Cap:** Enforces a minimum **7-day cooldown** so the same lead is never emailed twice in one week.

---

### Dispatch Options:

#### Option A: Automated Scheduled Dispatch (100% Hands-free via GitHub Actions)
* A dedicated **GitHub Actions cron runner** (`.github/workflows/scheduled_sender.yml`) runs automatically every 30 minutes between **10:00 AM and 5:00 PM IST** (Monday through Saturday).
* It securely triggers Render's `/api/process-queue` endpoint, wakes the server if asleep, and dispatches queued batches automatically up to your daily cap.
* **Zero operator manual work required!** You can also trigger it manually at any time under the GitHub repository's **Actions** tab by clicking **"Run workflow"**.

#### Option B: "🚀 Live Dispatch" (Send Now & Watch Live)
* Want to send a batch immediately without waiting for the next cron cycle?
* Click **"🚀 Live Dispatch (Watch Sending)"** (or **"Send Now (Live)"** on any campaign).
* Enter how many emails you want to send (e.g. 20).
* Watch the live terminal send emails in real-time with Google Message IDs and live progress bars!

---

## 6. Step 4: Monitoring Deliverability & Tracking

### Checking Send Statuses:
* Go to **Activity / Logs** in the top navigation.
* Every dispatched email records:
  - Recipient lead
  - Exact timestamp
  - Gmail API Message-ID
  - Delivery status (`Sent`, `Waiting`, `Failed`, `Skipped`, `Bounced`)

### Managing Bounces & Unsubscribes:
* **Bounces:** If an address does not exist, the engine marks the lead as `Bounced` and adds them to the permanent **Suppression List** so they are never emailed again.
* **Unsubscribes:** When a lead clicks the unsubscribe link in an email, they are immediately transferred to the **Suppression List** with timestamp and cryptographic verification.

---

## 7. Settings, Anti-Spam & Circuit Breakers

Located under the **Settings** tab:

1. **Daily Cap (Default: 50):** Maximum emails dispatched per calendar day. Resets at midnight IST.
2. **Send Window:** Start (e.g. `10:00`) and End (e.g. `17:00`).
3. **Safety Circuit Breaker (Bounce Pause):**
   * If the hard-bounce rate exceeds **5%** in any 24-hour period, the engine **auto-pauses immediately** to protect your Gmail account from being flagged by Google.
4. **Low Lead Alerts:**
   * When remaining uncontacted leads drop below **50** (or less than 3 days of sending volume), the system automatically triggers a daily email alert to the operator.

---

## 8. Troubleshooting & FAQ

| Symptom | Cause | Solution |
| :--- | :--- | :--- |
| **Status says "Sending Paused"** | Safety lock or bounce trigger | Review reason on Dashboard banner. If clear, click **Unpause** in Settings. |
| **"Daily cap reached"** | 50 emails sent today | Engine will resume automatically tomorrow at 10:00 AM IST. Or increase cap in Settings. |
| **"Outside send window"** | Current time is before 10 AM or after 5 PM IST | Normal behavior. Deliveries resume during business hours. |
| **Gmail says "Needs Reconnecting"** | OAuth refresh token revoked or expired | Go to **Settings → Gmail**, click **Reconnect Gmail**, and re-authorize `info@bidalert.in`. |
| **Lead count decreasing** | Normal operation | Check the **Lead Pipeline** card; upload fresh CSV dumps when leads run low. |
