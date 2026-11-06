"""Pure-logic tests for the pre-lock job's decisions. The database-backed path is exercised by
tests/test_jobs_prelock_db.py when TEST_DATABASE_URL is set."""
from datetime import datetime, timedelta, timezone

from dfs import prelock

LOCK = datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc)


def test_a_slate_outside_the_lead_is_skipped_without_fetching():
    """The 5-minute schedule must touch only Postgres until the lead window opens."""
    assert prelock.should_alert(LOCK, LOCK - timedelta(minutes=60), already_sent=False) is False


def test_two_slates_the_same_evening_are_decided_independently():
    main, turbo = LOCK, LOCK + timedelta(hours=1)
    now = LOCK - timedelta(minutes=18)
    assert prelock.should_alert(main, now, already_sent=False) is True
    assert prelock.should_alert(turbo, now, already_sent=False) is False
