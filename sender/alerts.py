"""
sender/alerts.py — Automated alerts for low leads depletion and system notifications.
"""
import logging
from datetime import date
from django.utils import timezone
from sender.models import Setting, GmailCredential
from leads.models import Lead

logger = logging.getLogger(__name__)


def check_and_send_low_leads_alert() -> dict:
    """
    Checks if remaining new leads in the database are below the alert threshold.
    If depleted or low, dispatches a daily notification email via connected Gmail.
    Guaranteed to send at most once per calendar day.
    """
    from sender.gmail_sender import send_email

    setting = Setting.get()
    threshold = setting.low_lead_threshold or 50
    daily_cap = setting.daily_cap or 50

    new_leads_count = Lead.objects.filter(status=Lead.STATUS_NEW).count()
    targeted_new = Lead.objects.filter(channel=Lead.CHANNEL_TARGETED, status=Lead.STATUS_NEW).count()
    direct_new = Lead.objects.filter(channel=Lead.CHANNEL_DIRECT, status=Lead.STATUS_NEW).count()

    days_left = (new_leads_count // daily_cap) if daily_cap > 0 else 0
    is_low = new_leads_count <= threshold or days_left <= 3

    result = {
        'is_low': is_low,
        'remaining_total': new_leads_count,
        'targeted_new': targeted_new,
        'direct_new': direct_new,
        'daily_cap': daily_cap,
        'days_left': days_left,
        'threshold': threshold,
        'alert_sent_today': False,
    }

    if not is_low:
        return result

    today = date.today()
    if setting.last_low_lead_alert_date == today:
        result['alert_sent_today'] = True
        return result

    # Send daily notification email via linked Gmail account
    cred = GmailCredential.get_active()
    if not cred or cred.needs_reauth:
        logger.warning('Low leads alert needed (%d remaining), but Gmail credential not active.', new_leads_count)
        return result

    recipient_email = setting.alert_recipient_email or cred.email
    subject = f"⚠️ [BidAlert Action Required] Leads Running Low: Only {new_leads_count} Leads Left"
    body_text = (
        f"Hello,\n\n"
        f"This is an automated daily alert from your BidAlert Email Automation Hub.\n\n"
        f"Your lead database is running out of uncontacted leads:\n"
        f"• Remaining Clean Leads: {new_leads_count}\n"
        f"  - Targeted Campaign Leads: {targeted_new}\n"
        f"  - Direct Draft Leads: {direct_new}\n"
        f"• Current Daily Sending Cap: {daily_cap} emails/day\n"
        f"• Estimated Days Left: {days_left} day(s)\n\n"
        f"To prevent your automated campaign scheduler from stalling, please upload or dump fresh leads.\n\n"
        f"👉 Upload Leads: http://127.0.0.1:8000/leads/upload/\n"
        f"👉 Direct Compose & Recipient Dumper: http://127.0.0.1:8000/compose/direct/\n\n"
        f"— BidAlert System"
    )

    try:
        send_email(
            credential=cred,
            to_email=recipient_email,
            to_name="BidAlert Admin",
            subject=subject,
            body_text=body_text,
        )
        setting.last_low_lead_alert_date = today
        setting.save(update_fields=['last_low_lead_alert_date'])
        result['alert_sent_today'] = True
        logger.info('Low leads daily alert successfully sent to %s (%d leads left).', recipient_email, new_leads_count)
    except Exception as e:
        logger.error('Failed to send daily low leads alert email: %s', e, exc_info=True)

    return result
