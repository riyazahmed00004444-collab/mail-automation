"""
sender/tests.py — Acceptance tests for Phase 1 & 2 fixes.
Run with: python manage.py test sender --verbosity=2
"""
from datetime import timedelta
from unittest.mock import patch, MagicMock

from django.contrib.auth.models import User
from django.test import TestCase, TransactionTestCase, RequestFactory
from django.utils import timezone

from campaigns.models import Campaign
from leads.models import Lead
from sender.models import SendLog, Suppression, Setting, GmailCredential
from sender.gmail_sender import classify_error, ERROR_CLASS_PERMANENT, ERROR_CLASS_TRANSIENT, ERROR_CLASS_QUOTA, ERROR_CLASS_AUTH
from sender.timeutils import today_bounds, in_send_window
from datetime import time as dt_time


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_lead(email='test@example.com', status='new'):
    return Lead.objects.get_or_create(email=email, defaults={
        'name': 'Test User', 'company': 'Acme', 'status': status
    })[0]


def make_campaign(status=Campaign.STATUS_SCHEDULED, name='Test Campaign'):
    user = User.objects.get_or_create(username='testadmin')[0]
    return Campaign.objects.create(
        name=name,
        intent_prompt='Test intent',
        subject='Test subject {{lead_name}}',
        body_text='Hello {{lead_name}}, see {{unsub_url}}',
        body_html='<p>Hello {{lead_name}}, see <a href="{{unsub_url}}">unsub</a></p>',
        status=status,
        spam_score=8,
        created_by=user,
    )


def make_credential():
    return GmailCredential.objects.get_or_create(
        email='sender@bidalert.in',
        defaults={
            'access_token': 'fake-token',
            'refresh_token': 'fake-refresh',
            'scopes': 'https://www.googleapis.com/auth/gmail.send',
        }
    )[0]


# ── D1: Campaign gating ───────────────────────────────────────────────────────

class CampaignGatingTest(TestCase):
    """Paused/cancelled campaign rows must NOT be sent."""

    def _run_engine(self):
        from sender.engine import run_once
        return run_once(max_seconds=60)

    @patch('sender.engine.GmailCredential.get_active')
    @patch('sender.engine.in_send_window', return_value=(True, ''))
    def test_paused_campaign_sends_nothing(self, mock_window, mock_cred):
        mock_cred.return_value = make_credential()
        setting = Setting.get()
        setting.paused = False
        setting.window_start = dt_time(10, 0)
        setting.window_end = dt_time(17, 0)
        setting.save()

        campaign = make_campaign(status=Campaign.STATUS_PAUSED)
        lead = make_lead()
        SendLog.objects.create(campaign=campaign, lead=lead, status=SendLog.STATUS_QUEUED)

        with patch('sender.engine.send_email') as mock_send:
            result = self._run_engine()
            mock_send.assert_not_called()

        self.assertEqual(result.get('sent', 0), 0)

    @patch('sender.engine.GmailCredential.get_active')
    @patch('sender.engine.in_send_window', return_value=(True, ''))
    def test_cancelled_campaign_sends_nothing(self, mock_window, mock_cred):
        mock_cred.return_value = make_credential()
        setting = Setting.get()
        setting.paused = False
        setting.window_start = dt_time(10, 0)
        setting.window_end = dt_time(17, 0)
        setting.save()

        campaign = make_campaign(status=Campaign.STATUS_CANCELLED)
        lead = make_lead()
        SendLog.objects.create(campaign=campaign, lead=lead, status=SendLog.STATUS_QUEUED)

        with patch('sender.engine.send_email') as mock_send:
            result = self._run_engine()
            mock_send.assert_not_called()

        self.assertEqual(result.get('sent', 0), 0)


# ── D2: Frequency cap ────────────────────────────────────────────────────────

class FrequencyCapTest(TestCase):
    """Lead contacted in the last 7 days must be skipped in a second campaign."""

    @patch('sender.engine.GmailCredential.get_active')
    @patch('sender.engine.in_send_window', return_value=(True, ''))
    def test_recently_contacted_lead_skipped(self, mock_window, mock_cred):
        mock_cred.return_value = make_credential()
        setting = Setting.get()
        setting.paused = False
        setting.min_days_between_emails = 7
        setting.window_start = dt_time(10, 0)
        setting.window_end = dt_time(17, 0)
        setting.save()

        lead = make_lead()

        # Campaign 1 — already sent
        camp1 = make_campaign(name='Campaign 1')
        log1 = SendLog.objects.create(
            campaign=camp1, lead=lead,
            status=SendLog.STATUS_SENT,
            sent_at=timezone.now() - timedelta(days=1),  # 1 day ago
        )

        # Campaign 2 — queued for same lead
        camp2 = make_campaign(name='Campaign 2')
        log2 = SendLog.objects.create(campaign=camp2, lead=lead, status=SendLog.STATUS_QUEUED)

        from sender.engine import run_once
        with patch('sender.engine.send_email') as mock_send:
            result = run_once(max_seconds=60)
            mock_send.assert_not_called()

        log2.refresh_from_db()
        self.assertEqual(log2.status, SendLog.STATUS_SKIPPED)
        self.assertIn('recently', log2.error.lower())


# ── D4: Error classification ──────────────────────────────────────────────────

class ErrorClassificationTest(TestCase):
    """Network errors must be transient, not permanent bounces."""

    def test_network_error_is_transient(self):
        exc = Exception('Unable to find the server at gmail.googleapis.com')
        self.assertEqual(classify_error(exc), ERROR_CLASS_TRANSIENT)

    def test_invalid_address_is_permanent(self):
        exc = Exception('550 No such user')
        self.assertEqual(classify_error(exc), ERROR_CLASS_PERMANENT)

    def test_rate_limit_is_quota(self):
        exc = Exception('rateLimitExceeded: Too many requests')
        self.assertEqual(classify_error(exc), ERROR_CLASS_QUOTA)

    def test_invalid_grant_is_auth(self):
        exc = Exception('invalid_grant: Token has been expired or revoked')
        self.assertEqual(classify_error(exc), ERROR_CLASS_AUTH)

    def test_permanent_bounced_is_suppressed(self):
        """Permanent failure → lead gets suppressed."""
        setting = Setting.get()
        setting.paused = False
        setting.window_start = dt_time(10, 0)
        setting.window_end = dt_time(17, 0)
        setting.save()

        campaign = make_campaign()
        lead = make_lead()
        log = SendLog.objects.create(campaign=campaign, lead=lead, status=SendLog.STATUS_QUEUED)
        cred = make_credential()

        with patch('sender.engine.GmailCredential.get_active', return_value=cred), \
             patch('sender.engine.in_send_window', return_value=(True, '')), \
             patch('sender.engine.verify_email', return_value=(True, 'OK')), \
             patch('sender.engine.send_email', side_effect=Exception('550 No such user')):
            from sender.engine import run_once
            result = run_once(max_seconds=60)

        log.refresh_from_db()
        self.assertEqual(log.status, SendLog.STATUS_BOUNCED)
        self.assertTrue(Suppression.objects.filter(email=lead.email).exists())


# ── D6: Timezone-correct daily cap ───────────────────────────────────────────

class TimezoneTest(TestCase):
    """Daily cap counts must use Asia/Kolkata local day, not UTC."""

    def test_today_bounds_are_aware(self):
        start, end = today_bounds()
        self.assertIsNotNone(start.tzinfo)
        self.assertIsNotNone(end.tzinfo)

    def test_today_bounds_span_full_day(self):
        start, end = today_bounds()
        diff = end - start
        # Should be ~24 hours
        self.assertGreater(diff.total_seconds(), 23 * 3600)
        self.assertLess(diff.total_seconds(), 25 * 3600)


# ── D9: CSRF / auth ──────────────────────────────────────────────────────────

class CsrfAuthTest(TestCase):
    """Verify exempt endpoints for frictionless login and cron."""

    def test_login_view_has_csrf_exemption(self):
        from accounts.views import login_view
        self.assertTrue(getattr(login_view, 'csrf_exempt', False))

    def test_send_test_email_is_exempt(self):
        from sender.api_views import send_test_email_api
        self.assertTrue(getattr(send_test_email_api, 'csrf_exempt', False))

    def test_process_queue_api_is_csrf_exempt(self):
        """process_queue_api IS exempt — protected by cron secret instead."""
        from sender.api_views import process_queue_api
        self.assertTrue(getattr(process_queue_api, 'csrf_exempt', False))


# ── D4 bounce rate circuit breaker ───────────────────────────────────────────

class BounceRateTest(TestCase):
    """Auto-pause triggers when bounce rate > threshold with >= 20 attempts."""

    def test_rate_based_pause(self):
        from sender.engine import _auto_pause_if_needed

        setting = Setting.get()
        setting.bounce_pause_percent = 5.0
        setting.paused = False
        setting.save()

        now = timezone.now()
        campaign = make_campaign()

        # 21 delivery attempts: 2 sent + 19 bounced → 90% bounce rate
        for i in range(2):
            lead = make_lead(email=f'sent{i}@example.com')
            SendLog.objects.create(campaign=campaign, lead=lead,
                                   status=SendLog.STATUS_SENT, sent_at=now - timedelta(hours=1))

        for i in range(19):
            lead = make_lead(email=f'bounce{i}@example.com')
            SendLog.objects.create(campaign=campaign, lead=lead,
                                   status=SendLog.STATUS_BOUNCED, sent_at=now - timedelta(hours=1))

        reason = _auto_pause_if_needed(setting)
        self.assertIsNotNone(reason)
        setting.refresh_from_db()
        self.assertTrue(setting.paused)

    def test_no_pause_below_20_attempts(self):
        """Should NOT pause if fewer than 20 attempts even at 100% bounce rate."""
        from sender.engine import _auto_pause_if_needed

        setting = Setting.get()
        setting.paused = False
        setting.save()

        campaign = make_campaign()
        now = timezone.now()
        for i in range(5):  # Only 5 attempts — below the 20 minimum
            lead = make_lead(email=f'fewbounce{i}@example.com')
            SendLog.objects.create(campaign=campaign, lead=lead,
                                   status=SendLog.STATUS_BOUNCED, sent_at=now - timedelta(hours=1))

        reason = _auto_pause_if_needed(setting)
        self.assertIsNone(reason)


# ── Send window logic ─────────────────────────────────────────────────────────

class SendWindowTest(TestCase):
    """in_send_window correctly gates by time and weekday."""

    def test_inside_window_allowed(self):
        import zoneinfo
        # 12:00 IST — clearly inside 10:00–17:00 window, on a Monday
        now = timezone.datetime(2026, 10, 19, 12, 0, 0,
                                tzinfo=zoneinfo.ZoneInfo('Asia/Kolkata'))  # Monday
        ok, reason = in_send_window(now, dt_time(10, 0), dt_time(17, 0), [0, 1, 2, 3, 4, 5, 6])
        self.assertTrue(ok, f'Expected inside window, got: {reason}')

    def test_outside_window_blocked(self):
        now = timezone.now().replace(hour=22, minute=0)  # 22:00
        ok, reason = in_send_window(now, dt_time(10, 0), dt_time(17, 0), [0, 1, 2, 3, 4, 5, 6])
        self.assertFalse(ok)
        self.assertIn('window', reason.lower())

    def test_wrong_weekday_blocked(self):
        # Sunday = 6, not in Mon-Sat (0-5)
        from datetime import datetime
        import zoneinfo
        # Create a Sunday
        now = datetime(2026, 10, 18, 12, 0, 0, tzinfo=zoneinfo.ZoneInfo('Asia/Kolkata'))  # Sunday
        ok, reason = in_send_window(now, dt_time(10, 0), dt_time(17, 0), [0, 1, 2, 3, 4, 5])  # Mon-Sat
        self.assertFalse(ok)
        self.assertIn('day', reason.lower())


# ── Scheduler Hub Tests ────────────────────────────────────────────────────────

class SchedulerHubTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='schedop', password='pass123', is_staff=True)
        self.client.login(username='schedop', password='pass123')

    def test_scheduler_dashboard_renders(self):
        resp = self.client.get('/scheduler/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Scheduler & Delivery Engine')
        self.assertContains(resp, 'Daily Sending Window (IST)')
        self.assertContains(resp, 'Active &amp; Scheduled Campaign Queue')

    def test_update_scheduler_settings(self):
        payload = {
            'daily_cap': '120',
            'per_hour_cap': '30',
            'window_start': '09:30',
            'window_end': '18:30',
            'weekdays': ['0', '1', '2', '3', '4'],  # Mon-Fri
            'min_delay_seconds': '15',
            'max_delay_seconds': '45',
            'min_days_between_emails': '10',
            'bounce_pause_percent': '4.5',
        }
        resp = self.client.post('/scheduler/update-settings/', payload)
        self.assertEqual(resp.status_code, 302)

        s = Setting.get()
        self.assertEqual(s.daily_cap, 120)
        self.assertEqual(s.per_hour_cap, 30)
        self.assertEqual(s.window_start.strftime('%H:%M'), '09:30')
        self.assertEqual(s.window_end.strftime('%H:%M'), '18:30')
        self.assertEqual(s.send_weekdays, '0,1,2,3,4')
        self.assertEqual(s.min_delay_seconds, 15)
        self.assertEqual(s.max_delay_seconds, 45)
        self.assertEqual(s.min_days_between_emails, 10)
        self.assertEqual(s.bounce_pause_percent, 4.5)

    def test_toggle_scheduler_pause(self):
        s = Setting.get()
        initial_paused = s.paused
        resp = self.client.post('/scheduler/toggle-pause/')
        self.assertEqual(resp.status_code, 302)
        s.refresh_from_db()
        self.assertNotEqual(s.paused, initial_paused)

    def test_reschedule_and_run_now_campaign(self):
        camp = make_campaign(status=Campaign.STATUS_SCHEDULED, name='Future Outreach')
        future_time = timezone.now() + timedelta(days=2)
        future_str = future_time.strftime('%Y-%m-%dT%H:%M')

        # Reschedule
        resp = self.client.post(f'/scheduler/campaign/{camp.pk}/reschedule/', {
            'schedule_datetime': future_str
        })
        self.assertEqual(resp.status_code, 302)
        camp.refresh_from_db()
        self.assertGreater(camp.start_at, timezone.now() + timedelta(days=1))

        # Run Now
        resp2 = self.client.post(f'/scheduler/campaign/{camp.pk}/run-now/')
        self.assertEqual(resp2.status_code, 302)
        camp.refresh_from_db()
        self.assertLessEqual(camp.start_at, timezone.now())

        # Toggle pause
        resp3 = self.client.post(f'/scheduler/campaign/{camp.pk}/pause-toggle/')
        self.assertEqual(resp3.status_code, 302)
        camp.refresh_from_db()
        self.assertEqual(camp.status, Campaign.STATUS_PAUSED)

