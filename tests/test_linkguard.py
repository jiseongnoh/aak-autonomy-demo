from datetime import datetime, timedelta, timezone
from src.linkguard import is_expired, mask_token

NOW = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)

def test_expired():
    assert is_expired(NOW - timedelta(hours=1), NOW) is True

def test_not_expired():
    assert is_expired(NOW + timedelta(hours=1), NOW) is False

def test_mask():
    assert mask_token("sk-live-abcd1234") == "*************1234"
