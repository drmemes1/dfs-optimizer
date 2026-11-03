import os

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="no TEST_DATABASE_URL")


@pytest.fixture()
def conn(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db
    with db.connect() as c:
        db.migrate(c)
        db.seed_params(c)
        yield c


def test_guard_rules(conn):
    from dfs import db
    from dfs.paramstore import ParamsGuardError, apply_changes
    v0, p0 = db.current_params(conn)
    b2b = p0["signals"]["b2b_mult"]
    ok = {"key": ("signals", "b2b_mult"), "new": round(b2b * 1.009, 6), "sample_n": 60, "slates": 4}
    ev = {"slate_id": 9001, "bias": -0.02}

    with pytest.raises(ParamsGuardError, match="more than 1%"):
        apply_changes(conn, [{**ok, "new": b2b * 1.02}], "measured_bias", "b2b bias", ev)
    with pytest.raises(ParamsGuardError, match="sample too small"):
        apply_changes(conn, [{**ok, "sample_n": 10}], "measured_bias", "b2b bias", ev)
    with pytest.raises(ParamsGuardError, match="frozen"):
        apply_changes(conn, [{"key": ("GPP", "own_penalty"), "new": 0.1201, "sample_n": 500, "slates": 30}],
                      "measured_bias", "x", ev)
    with pytest.raises(ParamsGuardError, match="need 40 over 5"):
        apply_changes(conn, [{"key": ("signals", "pace_exponent"), "new": 0.5025, "sample_n": 300, "slates": 4}],
                      "measured_bias", "pace", ev)
    with pytest.raises(ParamsGuardError, match="graded result"):
        apply_changes(conn, [{"key": ("GPP", "ceil_weight"), "new": 0.5}], "manual", "feels better", {})

    v1 = apply_changes(conn, [ok], "measured_bias", "b2b players beat projection by 2% over 4 slates", ev)
    assert v1 == v0 + 1
    with pytest.raises(ParamsGuardError, match="already adjusted"):
        apply_changes(conn, [ok], "measured_bias", "again", ev)                 # once per key per slate
    v, p = db.current_params(conn)
    assert v == v1 and p["signals"]["b2b_mult"] == ok["new"]
    with conn.cursor() as cur:
        cur.execute("SELECT kind, author, changes FROM params WHERE version=%s", (v1,))
        r = cur.fetchone()
    assert r["kind"] == "measured_bias" and r["author"] == "learner"
    assert r["changes"][0]["old"] == b2b and r["changes"][0]["new"] == ok["new"]
    with pytest.raises(Exception, match="append-only"):                        # database trigger too
        with conn.cursor() as cur:
            cur.execute("UPDATE params SET reason='x' WHERE version=%s", (v1,))
    conn.rollback()

    v2 = apply_changes(conn, [{"key": ("GPP", "ceil_weight"), "new": 0.45}], "manual",
                       "dominance result on graded slates", {"graded_slates": [1, 2, 3]})
    assert v2 == v1 + 1
