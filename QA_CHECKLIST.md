# BidAlert Comms Hub — Manual QA & Verification Checklist

This manual QA script walks through the complete end-to-end lifecycle of the BidAlert Comms Hub application to verify safety, correctness, UI clarity, and deliverability guardrails.

---

## 1. Fresh Installation & Setup
- [ ] **Virtualenv & Dependencies**:
  ```bash
  python -m venv .venv
  # Windows: .venv\Scripts\activate | Linux: source .venv/bin/activate
  pip install -r requirements.txt
  ```
- [ ] **Environment Configuration**:
  ```bash
  python scripts/gen_secrets.py
  # Copy SECRET_KEY and CRON_SECRET into .env (or ~/bidalert.env)
  ```
- [ ] **Database Migrations & Seed**:
  ```bash
  python manage.py migrate
  python manage.py createsuperuser
  python manage.py seed_knowledge
  ```
- [ ] **Static Assets**:
  ```bash
  python manage.py collectstatic --noinput
  ```

---

## 2. First-Run Checklist (Home Screen)
- [ ] Log in as the newly created Admin user at `/login/`.
- [ ] Confirm the top banner displays the initial state (e.g., "Ready" or "Connect Gmail").
- [ ] Verify the **Getting Started Checklist** displays all 5 steps:
  1. Connect Gmail account
  2. Import contacts
  3. Add company facts
  4. Send a test email
  5. Schedule first campaign
- [ ] Click each item in the checklist and confirm it routes to the correct view.

---

## 3. Gmail OAuth Connection
- [ ] Go to **Settings** → **Gmail** tab.
- [ ] Click **Connect Gmail** and complete the Google OAuth consent flow.
- [ ] Verify success redirect back to Settings with status badge: **Connected** (showing connected sender email).
- [ ] Verify tokens are encrypted at rest in the database.

---

## 4. Contact Import & Sanitization
- [ ] Prepare a test CSV (`test_contacts.csv`) with:
  - 3 valid emails
  - 1 duplicate email
  - 1 invalid syntax email (`invalid-email-at-domain`)
  - 1 disposable domain email (e.g. `@mailinator.com`)
  - 1 already unsubscribed/suppressed email
- [ ] Go to **Contacts** → **Import Wizard**.
- [ ] Upload the CSV, map columns (`Email`, `Name`, `Company`).
- [ ] Confirm import preview summary correctly distinguishes:
  - Valid new contacts imported
  - Duplicates detected and skipped
  - Invalid/disposable addresses flagged
- [ ] Test the **Undo Import** button within the active session to verify rollback capability.

---

## 5. New Email Wizard & Safety Score Gate
- [ ] Navigate to **New Email** (`/campaigns/compose/`).
- [ ] **Step 1 (Who)**:
  - Select target audience segment.
  - Check live recipient count calculations (Total, Excluded for suppression, Excluded for 7-day frequency cap).
  - Click **Next**.
- [ ] **Step 2 (What)**:
  - Test AI Draft: Click "Write with AI", enter topic prompt, verify grounding in Company Facts.
  - Insert merge tags: `{{lead_name}}`, `{{company}}`.
  - Intentionally insert high-risk spam keywords (e.g., "FREE MONEY GUARANTEED 100% ACT NOW").
  - Check **Inbox-safety score**: verify it drops below 7 and lists plain-English remediation items.
  - Attempt to proceed to Step 4 without fixing: verify the wizard **blocks** progress with a clear explanation.
  - Fix content (remove spam words, ensure `{{unsub_url}}` is present) until safety score is ≥ 7.
- [ ] **Step 3 (Test Send)**:
  - Enter test recipient email and send a test message.
  - Verify received test email in inbox: check headers (`Reply-To`, `List-Unsubscribe`, custom `Message-ID`), logo rendering, and clean footer.
  - Confirm `tested_at` timestamp is recorded on the draft.
- [ ] **Step 4 (When)**:
  - Select **Pick a date & time** (set 2 minutes into the future within send window).
  - Review the plan summary: "About X emails/day, Mon–Sat 10:00–17:00 IST. ETA: [Date]".
  - Click **Approve & Schedule** (Admin) or **Request Approval** (Operator).

---

## 6. Scheduler & Engine Execution
- [ ] Verify campaign status in **Campaigns** list is `scheduled`.
- [ ] Run dry run to test safety:
  ```bash
  python manage.py run_sender --dry-run
  ```
  - Confirm zero emails are sent.
  - Confirm stdout logs report what the engine would have done.
- [ ] Once the scheduled time arrives:
  ```bash
  python manage.py run_sender --max-seconds 60
  ```
- [ ] Confirm:
  - Campaign transitions from `scheduled` to `sending`.
  - Rows are claimed atomically with `claim_token`.
  - Recipient emails are delivered with configured jitter delay.
  - `last_worker_heartbeat` is updated in `Setting`.

---

## 7. Campaign Controls (Pause / Resume / Cancel)
- [ ] While a campaign is in `sending` or `scheduled` state:
  - Click **Pause Campaign** on the Dashboard / Campaign Detail.
  - Verify status changes to `paused`.
- [ ] Run `python manage.py run_sender`:
  - Verify **0 rows** are sent for the paused campaign.
- [ ] Click **Resume Campaign**:
  - Verify confirmation modal and status reverts to `sending` or `scheduled`.
- [ ] Click **Cancel Campaign**:
  - Verify remaining queued rows are cancelled.

---

## 8. Unsubscribe Handling
- [ ] Open a delivered email from the test campaign.
- [ ] Click the unsubscribe link in the footer.
- [ ] Verify arrival on friendly confirmation page ("You have been unsubscribed").
- [ ] Check **Settings** → **Do-not-email list**: verify email address is now listed.
- [ ] Attempt to create a new campaign targeting this contact: verify they are counted as excluded and never sent to.

---

## 9. Bounce Handling & Circuit Breaker
- [ ] Trigger a permanent bounce (e.g., send to invalid domain or simulate 550 No such user):
  - Verify SendLog marks status as `bounced`.
  - Verify lead is automatically added to suppression list.
- [ ] Simulate threshold breach (≥5% hard bounce rate over ≥20 attempts or ≥10 hard bounces in 24h):
  - Verify engine triggers auto-pause.
  - Check Home screen: top banner switches to red alert: **Auto-paused: High bounce rate detected ([X]% > 5%)**.
  - Verify sender worker halts all outbound sends while auto-paused.
  - In Admin Settings, review bounce log, remediate, and click **Resume Sending**.

---

## 10. Queue Repair & Maintenance Commands
- [ ] Test queue repair command:
  ```bash
  python manage.py repair_queue --dry-run
  python manage.py repair_queue
  ```
- [ ] Test lead verification command:
  ```bash
  python manage.py verify_leads --limit 50
  ```
- [ ] Test backup command:
  ```bash
  python manage.py backup_db
  ```
  - Verify JSON / SQLite backup created with timestamp under `backups/`.
