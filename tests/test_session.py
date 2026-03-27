import pytest
from codyclaw.gateway.session_strategy import SessionManager
from codyclaw.automation.cron import CronScheduler


# --- SessionManager ---

def test_set_and_get():
    sm = SessionManager()
    sm.set("key1", "session-abc")
    assert sm.get("key1") == "session-abc"


def test_missing_key_returns_none():
    sm = SessionManager()
    assert sm.get("nonexistent") is None


def test_session_expires(monkeypatch):
    import codyclaw.gateway.session_strategy as sm_module

    fixed_time = [0.0]
    monkeypatch.setattr(sm_module.time, "time", lambda: fixed_time[0])

    sm = SessionManager(idle_timeout_hours=1)
    sm.set("key1", "session-abc")
    fixed_time[0] = 3601.0  # 1 hour + 1 second
    assert sm.get("key1") is None


def test_session_not_expired_within_window(monkeypatch):
    import codyclaw.gateway.session_strategy as sm_module

    fixed_time = [0.0]
    monkeypatch.setattr(sm_module.time, "time", lambda: fixed_time[0])

    sm = SessionManager(idle_timeout_hours=1)
    sm.set("key1", "session-abc")
    fixed_time[0] = 3599.0  # just under 1 hour
    assert sm.get("key1") == "session-abc"


def test_all_returns_current_snapshot():
    sm = SessionManager()
    sm.set("k1", "s1")
    sm.set("k2", "s2")
    assert sm.all() == {"k1": "s1", "k2": "s2"}


def test_overwrite_session():
    sm = SessionManager()
    sm.set("key1", "session-v1")
    sm.set("key1", "session-v2")
    assert sm.get("key1") == "session-v2"


def test_expired_session_removed_from_all(monkeypatch):
    import codyclaw.gateway.session_strategy as sm_module

    fixed_time = [0.0]
    monkeypatch.setattr(sm_module.time, "time", lambda: fixed_time[0])

    sm = SessionManager(idle_timeout_hours=1)
    sm.set("key1", "session-abc")
    fixed_time[0] = 3601.0
    sm.get("key1")  # trigger expiry
    assert "key1" not in sm.all()


def test_all_filters_expired_without_get(monkeypatch):
    """all() should filter expired sessions without requiring get() to be called first (M2 fix)."""
    import codyclaw.gateway.session_strategy as sm_module

    fixed_time = [0.0]
    monkeypatch.setattr(sm_module.time, "time", lambda: fixed_time[0])

    sm = SessionManager(idle_timeout_hours=1)
    sm.set("live", "session-live")
    sm.set("expired", "session-expired")
    fixed_time[0] = 3601.0
    sm.set("live", "session-live")   # refresh only "live"

    snapshot = sm.all()
    assert "live" in snapshot
    assert "expired" not in snapshot


# --- CronScheduler._parse_interval ---

def test_parse_interval_minutes():
    assert CronScheduler._parse_interval("30m") == 30


def test_parse_interval_hours():
    assert CronScheduler._parse_interval("2h") == 120


def test_parse_interval_digits():
    assert CronScheduler._parse_interval("60") == 60


def test_parse_interval_every_minutes():
    assert CronScheduler._parse_interval("every 30m") == 30


def test_parse_interval_every_hours():
    assert CronScheduler._parse_interval("every 2h") == 120


def test_parse_interval_unknown_defaults_to_60():
    assert CronScheduler._parse_interval("unknown") == 60
