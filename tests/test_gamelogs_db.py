"""Game-log ingest. Uses hand-built ESPN-shaped payloads; nothing is fetched."""
import datetime
import os

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="no TEST_DATABASE_URL")

D = datetime.date(2026, 10, 20)


def _day():
    """One finished game, OKC at CLE, with two box lines per side."""
    box = {
        "OKC": [{"name": "Alpha One", "starter": True, "dnp": False, "min": 36, "pts": 30,
                 "fg3m": 4, "reb": 5, "ast": 6, "stl": 2, "blk": 1, "tov": 3},
                {"name": "Beta Two", "starter": False, "dnp": False, "min": 12, "pts": 6,
                 "fg3m": 0, "reb": 2, "ast": 1, "stl": 0, "blk": 0, "tov": 1}],
        "CLE": [{"name": "Gamma Three", "starter": True, "dnp": False, "min": 34, "pts": 24,
                 "fg3m": 2, "reb": 8, "ast": 4, "stl": 1, "blk": 1, "tov": 2},
                {"name": "Delta Four", "starter": False, "dnp": True, "min": 0, "pts": 0,
                 "fg3m": 0, "reb": 0, "ast": 0, "stl": 0, "blk": 0, "tov": 0}],
    }
    game = {"event_id": "401", "teams": {"OKC": {"score": 118, "home": False},
                                         "CLE": {"score": 120, "home": True}},
            "final": True, "period": 4}
    return {"games": [game], "boxes": {"401": box}, "bref": {}}


def _players(conn, names):
    from dfs.names import name_norm
    ids = {}
    with conn.cursor() as cur:
        for n in names:
            cur.execute("INSERT INTO players(name, name_norm) VALUES (%s,%s) RETURNING id",
                        (n, name_norm(n)))
            ids[n] = cur.fetchone()["id"]
    conn.commit()
    return ids


def test_ingest_writes_a_row_per_player_with_dk_points(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db, gamelogs
    from dfs.scoring import dk_points
    with db.connect() as conn:
        db.migrate(conn)
        ids = _players(conn, ["Alpha One", "Beta Two", "Gamma Three", "Delta Four"])
        monkeypatch.setattr("dfs.sources.boxscores.verify_game", lambda g, b, r: [])
        out = gamelogs.ingest_day(conn, D, _day())
        assert out["written"] == 4
        with conn.cursor() as cur:
            cur.execute("SELECT min, dk_pts, dnp FROM player_games WHERE player_id=%s AND game_date=%s",
                        (ids["Alpha One"], D))
            r = cur.fetchone()
    assert float(r["min"]) == 36
    assert float(r["dk_pts"]) == dk_points(pts=30, fg3m=4, reb=5, ast=6, stl=2, blk=1, tov=3)
    assert r["dnp"] is False


def test_ingest_skips_an_unverified_game(monkeypatch):
    """A box score that fails verification must never reach the log."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db, gamelogs
    with db.connect() as conn:
        db.migrate(conn)
        _players(conn, ["Eps Five"])
        monkeypatch.setattr("dfs.sources.boxscores.verify_game",
                            lambda g, b, r: ["minutes do not add up"])
        out = gamelogs.ingest_day(conn, datetime.date(2026, 10, 21), _day())
    assert out["written"] == 0
    assert out["skipped_games"]


def test_ingest_is_idempotent(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db, gamelogs
    with db.connect() as conn:
        db.migrate(conn)
        _players(conn, ["Zed One", "Zed Two", "Zed Three", "Zed Four"])
        monkeypatch.setattr("dfs.sources.boxscores.verify_game", lambda g, b, r: [])
        day = _day()
        day["boxes"]["401"]["OKC"][0]["name"] = "Zed One"
        day["boxes"]["401"]["OKC"][1]["name"] = "Zed Two"
        day["boxes"]["401"]["CLE"][0]["name"] = "Zed Three"
        day["boxes"]["401"]["CLE"][1]["name"] = "Zed Four"
        d2 = datetime.date(2026, 10, 22)
        gamelogs.ingest_day(conn, d2, day)
        gamelogs.ingest_day(conn, d2, day)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM player_games WHERE game_date=%s", (d2,))
            n = cur.fetchone()["n"]
    assert n == 4


def test_backfill_walks_back_and_records_per_day(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db, gamelogs
    with db.connect() as conn:
        db.migrate(conn)
        seen = []

        def fake_fetch_day(d):
            seen.append(d)
            return {"games": [], "boxes": {}, "bref": {}}

        out = gamelogs.backfill(conn, datetime.date(2026, 10, 30), days=3, fetch_day=fake_fetch_day)
    assert seen == [datetime.date(2026, 10, 29), datetime.date(2026, 10, 28),
                    datetime.date(2026, 10, 27)], "walks back from the day before"
    assert len(out["days"]) == 3


def test_backfill_records_a_failing_day_without_stopping(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db, gamelogs
    with db.connect() as conn:
        db.migrate(conn)
        calls = {"n": 0}

        def flaky(d):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("espn down")
            return {"games": [], "boxes": {}, "bref": {}}

        out = gamelogs.backfill(conn, datetime.date(2026, 11, 1), days=3, fetch_day=flaky)
    assert out["errors"], "the bad day is recorded"
    assert len(out["days"]) == 2, "the other days still ran"


def _log(conn, name, entries):
    from dfs.names import name_norm
    with conn.cursor() as cur:
        cur.execute("INSERT INTO players(name, name_norm) VALUES (%s,%s) RETURNING id",
                    (name, name_norm(name)))
        pid = cur.fetchone()["id"]
        for d, mins, dk in entries:
            cur.execute("""INSERT INTO player_games(player_id, game_date, min, dk_pts)
                           VALUES (%s,%s,%s,%s)""", (pid, d, mins, dk))
    conn.commit()
    return name_norm(name)


def test_recent_rate_is_dk_points_per_minute(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db, gamelogs
    with db.connect() as conn:
        db.migrate(conn)
        nn = _log(conn, "Rate One", [(datetime.date(2026, 10, 1), 30, 45),
                                     (datetime.date(2026, 10, 3), 20, 15)])
        out = gamelogs.recent_rates(conn, [nn], datetime.date(2026, 10, 10), lookback=10)
    assert out[nn]["games"] == 2 and out[nn]["minutes"] == 50
    assert abs(out[nn]["rate"] - (60 / 50)) < 1e-9


def test_games_on_or_after_the_slate_date_are_excluded(monkeypatch):
    """A player's own evening must not leak into the projection meant to predict it."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db, gamelogs
    with db.connect() as conn:
        db.migrate(conn)
        nn = _log(conn, "Rate Two", [(datetime.date(2026, 10, 4), 30, 30),
                                     (datetime.date(2026, 10, 5), 30, 90)])
        out = gamelogs.recent_rates(conn, [nn], datetime.date(2026, 10, 5), lookback=10)
    assert out[nn]["games"] == 1 and abs(out[nn]["rate"] - 1.0) < 1e-9


def test_lookback_takes_only_the_most_recent_games(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db, gamelogs
    with db.connect() as conn:
        db.migrate(conn)
        nn = _log(conn, "Rate Three", [(datetime.date(2026, 10, d), 10, 10) for d in range(1, 9)])
        out = gamelogs.recent_rates(conn, [nn], datetime.date(2026, 10, 20), lookback=3)
    assert out[nn]["games"] == 3 and out[nn]["last_game_date"] == datetime.date(2026, 10, 8)


def test_zero_minute_games_do_not_count(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db, gamelogs
    with db.connect() as conn:
        db.migrate(conn)
        nn = _log(conn, "Rate Four", [(datetime.date(2026, 10, 1), 0, 0),
                                      (datetime.date(2026, 10, 2), 25, 50)])
        out = gamelogs.recent_rates(conn, [nn], datetime.date(2026, 10, 10), lookback=10)
    assert out[nn]["games"] == 1 and abs(out[nn]["rate"] - 2.0) < 1e-9


def test_unknown_players_are_simply_absent(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db, gamelogs
    with db.connect() as conn:
        db.migrate(conn)
        out = gamelogs.recent_rates(conn, ["nobody at all"], datetime.date(2026, 10, 10), lookback=10)
    assert out == {}


def test_a_shared_normalised_name_is_not_merged(monkeypatch):
    """Two players normalising to the same name must not have their games pooled into one rate."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db, gamelogs
    with db.connect() as conn:
        db.migrate(conn)
        _log(conn, "Dup Name", [(datetime.date(2026, 10, 1), 10, 60)])
        _log(conn, "Dup Name", [(datetime.date(2026, 10, 2), 10, 0)])
        out = gamelogs.recent_rates(conn, ["dup name"], datetime.date(2026, 10, 10), lookback=10)
    assert "dup name" not in out, "ambiguous names yield no rate rather than a merged one"
