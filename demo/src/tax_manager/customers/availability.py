"""Track when a customer entered the current unavailable period."""

from datetime import datetime

from tax_manager.customers.validation import CHINA_TZ


def deactivated_at_for_change(
    was_available: bool, is_available: bool, previous: datetime | None, now: datetime,
) -> datetime | None:
    if is_available:
        return None
    return now if was_available else previous


def format_china_hour(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(CHINA_TZ).strftime("%Y-%m-%d %H") + "\u65f6"
