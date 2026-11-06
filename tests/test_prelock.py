from datetime import datetime, timedelta, timezone

from dfs import prelock

LOCK = datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc)  # Nov 5, 7:00pm ET (EST)


def at(minutes_before):
    return LOCK - timedelta(minutes=minutes_before)


def test_minutes_to_lock_is_none_when_lock_time_missing():
    assert prelock.minutes_to_lock(None, at(30)) is None


def test_minutes_to_lock_counts_down():
    assert prelock.minutes_to_lock(LOCK, at(18)) == 18.0


def test_minutes_to_lock_is_negative_after_lock():
    assert prelock.minutes_to_lock(LOCK, LOCK + timedelta(minutes=5)) == -5.0


def test_does_not_fire_too_early():
    assert prelock.should_alert(LOCK, at(45), already_sent=False) is False


def test_fires_inside_the_lead_window():
    assert prelock.should_alert(LOCK, at(18), already_sent=False) is True


def test_fires_on_a_jittered_tick_that_skipped_the_15_to_20_band():
    # GitHub delayed the run: ticks landed at T-22 then T-14. The T-14 tick must still fire.
    assert prelock.should_alert(LOCK, at(22), already_sent=False) is False
    assert prelock.should_alert(LOCK, at(14), already_sent=False) is True


def test_fires_only_once():
    assert prelock.should_alert(LOCK, at(18), already_sent=True) is False


def test_does_not_fire_after_lock():
    assert prelock.should_alert(LOCK, LOCK, already_sent=False) is False
    assert prelock.should_alert(LOCK, LOCK + timedelta(minutes=1), already_sent=False) is False


def test_null_lock_time_never_fires():
    assert prelock.should_alert(None, at(18), already_sent=False) is False


def test_decision_is_lock_time_driven_so_dst_cannot_drift_it():
    # Same 7:00pm ET lock on an EDT date is 23:00Z, not 00:00Z. The function is told the real
    # instant, so the same relative moment fires in both offsets.
    edt_lock = datetime(2026, 6, 10, 23, 0, tzinfo=timezone.utc)
    assert prelock.should_alert(edt_lock, edt_lock - timedelta(minutes=18), already_sent=False) is True
    assert prelock.should_alert(edt_lock, edt_lock - timedelta(minutes=45), already_sent=False) is False


def test_accepts_a_naive_datetime_as_utc():
    naive = LOCK.replace(tzinfo=None)
    assert prelock.should_alert(naive, at(18), already_sent=False) is True


def test_format_alert_title_is_ascii_and_body_names_the_players():
    flags = [
        {"name": "Nikola Jokic", "slot": "C", "status": "OUT", "locked": False, "mode": "GPP",
         "rank": 1, "entered": True, "salary_room": 11200,
         "suggestions": [{"name": "Alperen Sengun", "salary": 8700, "proj": 44.1, "proj_delta": -9.2}]},
    ]
    title, body = prelock.format_alert("2026-11-05", flags, lineups_checked=13)
    title.encode("ascii")  # must not raise
    assert "Nikola Jokic" in body
    assert "OUT" in body
    assert "Alperen Sengun" in body


def test_format_alert_says_so_when_nothing_is_flagged():
    title, body = prelock.format_alert("2026-11-05", [], lineups_checked=13)
    title.encode("ascii")
    assert "13" in body
    assert "no" in body.lower()


def test_format_alert_marks_a_locked_player_as_unswappable():
    flags = [{"name": "Devin Booker", "slot": "PG", "status": "OUT", "locked": True, "mode": "CASH",
              "rank": 1, "entered": True, "salary_room": 0, "suggestions": []}]
    _, body = prelock.format_alert("2026-11-05", flags, lineups_checked=1)
    assert "tipped" in body.lower() or "locked" in body.lower()
