import time
import pytest
from codyclaw.channel.dedup import MessageDeduplicator


def test_fresh_message_not_duplicate():
    dedup = MessageDeduplicator()
    assert dedup.is_duplicate("msg-001") is False


def test_same_message_is_duplicate():
    dedup = MessageDeduplicator()
    dedup.is_duplicate("msg-001")
    assert dedup.is_duplicate("msg-001") is True


def test_different_messages_not_duplicate():
    dedup = MessageDeduplicator()
    dedup.is_duplicate("msg-001")
    assert dedup.is_duplicate("msg-002") is False


def test_max_size_eviction():
    dedup = MessageDeduplicator(max_size=3)
    for i in range(4):
        dedup.is_duplicate(f"msg-{i:03d}")
    # msg-000 should have been evicted by the fourth insertion
    assert dedup.is_duplicate("msg-000") is False


def test_window_expiry(monkeypatch):
    import codyclaw.channel.dedup as dedup_module

    fixed_time = [100.0]
    monkeypatch.setattr(dedup_module.time, "time", lambda: fixed_time[0])

    dedup = MessageDeduplicator(window_seconds=10)
    dedup.is_duplicate("msg-001")  # seen at t=100

    fixed_time[0] = 111.0  # 11 seconds later, outside window
    assert dedup.is_duplicate("msg-001") is False


def test_within_window_still_duplicate(monkeypatch):
    import codyclaw.channel.dedup as dedup_module

    fixed_time = [100.0]
    monkeypatch.setattr(dedup_module.time, "time", lambda: fixed_time[0])

    dedup = MessageDeduplicator(window_seconds=10)
    dedup.is_duplicate("msg-001")

    fixed_time[0] = 109.0  # 9 seconds later, still within window
    assert dedup.is_duplicate("msg-001") is True
