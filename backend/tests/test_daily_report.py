from datetime import date, timedelta
from types import SimpleNamespace

from app.services.monitoring.daily_report import REPORT_TZ, _enabled, _window


def test_daily_report_is_opt_in_per_system():
    assert _enabled(SimpleNamespace(infra={"daily_report": {"enabled": True}}))
    assert not _enabled(SimpleNamespace(infra={}))
    assert not _enabled(SimpleNamespace(infra={"daily_report": {"enabled": False}}))


def test_daily_report_window_is_one_shanghai_calendar_day():
    report_date = date(2026, 9, 23)
    start, end = _window(report_date)
    assert end - start == timedelta(days=1)
    assert start.astimezone(REPORT_TZ).date() == report_date
    assert end.astimezone(REPORT_TZ).date() == report_date + timedelta(days=1)
