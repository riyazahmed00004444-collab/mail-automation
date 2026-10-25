"""
sender/timeutils.py — Timezone-aware time helpers. Fixes D6.
All "today" comparisons use the configured local timezone (Asia/Kolkata by default),
never naïve date.today() which uses the server's system clock (UTC on PA).
"""
import logging
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


def get_app_tz() -> ZoneInfo:
    """Return the configured app timezone as a ZoneInfo object."""
    try:
        return ZoneInfo(settings.TIME_ZONE)
    except Exception:
        return ZoneInfo('Asia/Kolkata')


def now_local() -> datetime:
    """Current datetime in the app's local timezone."""
    return timezone.now().astimezone(get_app_tz())


def today_bounds() -> tuple[datetime, datetime]:
    """
    Return (start_of_day, end_of_day) as timezone-aware datetimes in the
    app's local timezone. Used for daily cap counts (fixes D6).
    """
    local_now = now_local()
    tz = get_app_tz()
    start = datetime(local_now.year, local_now.month, local_now.day, 0, 0, 0, tzinfo=tz)
    end = datetime(local_now.year, local_now.month, local_now.day, 23, 59, 59, 999999, tzinfo=tz)
    return start, end


def in_send_window(now: datetime, window_start: dt_time, window_end: dt_time,
                   allowed_weekdays: list[int]) -> tuple[bool, str]:
    """
    Check whether 'now' is inside the send window.
    Returns (is_inside, reason_if_not).
    weekday: 0=Monday … 6=Sunday.
    """
    local_now = now.astimezone(get_app_tz())
    weekday = local_now.weekday()  # 0=Mon

    if weekday not in allowed_weekdays:
        day_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
        allowed_names = ', '.join(day_names[d] for d in sorted(allowed_weekdays))
        return False, f"Not a send day (today is {day_names[weekday]}; allowed: {allowed_names})"

    current_time = local_now.time().replace(tzinfo=None)
    win_start = window_start.replace(tzinfo=None) if hasattr(window_start, 'tzinfo') else window_start
    win_end = window_end.replace(tzinfo=None) if hasattr(window_end, 'tzinfo') else window_end
    if not (win_start <= current_time <= win_end):
        return False, (
            f"Outside send window "
            f"({window_start.strftime('%H:%M')}–{window_end.strftime('%H:%M')} IST; "
            f"now {current_time.strftime('%H:%M')})"
        )

    return True, ''


def next_window_open(window_start: dt_time, allowed_weekdays: list[int]) -> datetime:
    """
    Return the next datetime when the send window opens (for UI display).
    """
    tz = get_app_tz()
    local_now = now_local()
    for offset in range(8):  # look up to 8 days ahead
        candidate = local_now.replace(hour=window_start.hour, minute=window_start.minute,
                                      second=0, microsecond=0)
        if offset > 0:
            from datetime import timedelta
            candidate = (local_now + timedelta(days=offset)).replace(
                hour=window_start.hour, minute=window_start.minute, second=0, microsecond=0
            )
        if candidate.weekday() in allowed_weekdays and candidate > local_now:
            return candidate
    return local_now  # fallback
