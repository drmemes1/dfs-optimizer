"""Read-side queries for the UI."""
from .optimizer import SLOTS


def list_slates(conn, limit=60):
    with conn.cursor() as cur:
        cur.execute("""SELECT s.id, s.date, s.n_games, s.lock_time_utc, s.slate_key,
                              (SELECT max(built_at) FROM lineups l WHERE l.slate_id = s.id) AS last_built
                       FROM slates s ORDER BY s.date DESC, s.id DESC LIMIT %s""", (limit,))
        return cur.fetchall()


def latest_run(cur, slate_id):
    cur.execute("""SELECT run_id, max(built_at) AS built_at, max(params_version) AS params_version
                   FROM lineups WHERE slate_id=%s GROUP BY run_id ORDER BY 2 DESC LIMIT 1""", (slate_id,))
    return cur.fetchone()


LINEUP_COLS = """id, mode, rank, salary, proj, ceil, own_sum, players, actual_score, entered, edited,
                 settings, locks, excludes, cost_vs_unconstrained, built_at, params_version,
                 contest_context, contest_result"""


def slate_detail(conn, slate_id=None):
    with conn.cursor() as cur:
        if slate_id is None:
            cur.execute("SELECT id FROM slates ORDER BY date DESC, updated_at DESC LIMIT 1")
            r = cur.fetchone()
            if not r:
                return None
            slate_id = r["id"]
        cur.execute("SELECT * FROM slates WHERE id=%s", (slate_id,))
        slate = cur.fetchone()
        if not slate:
            return None
        run = latest_run(cur, slate_id)
        lineups = {}
        if run:
            cur.execute(f"SELECT {LINEUP_COLS} FROM lineups WHERE run_id=%s ORDER BY mode, rank", (run["run_id"],))
            for r in cur.fetchall():
                lineups.setdefault(r["mode"], []).append(r)
        cur.execute(f"""SELECT {LINEUP_COLS} FROM lineups WHERE slate_id=%s AND entered
                        AND run_id IS DISTINCT FROM %s ORDER BY built_at DESC""",
                    (slate_id, run["run_id"] if run else None))
        entered_elsewhere = cur.fetchall()
        cur.execute("""SELECT pl.draftable_id AS key, p.name, pl.team, pl.opp, pl.game, pl.game_start, pl.pos,
                              pl.salary, pl.minutes, pl.proj_rg, pl.proj_adj, pl.floor, pl.ceil, pl.value,
                              pl.value_grade, pl.own_pct, pl.own_estimated, pl.inj_status, pl.starter_consensus,
                              pl.contingent, pl.signals, pl.signal_mult, pl.m_form
                       FROM pool pl JOIN players p ON p.id = pl.player_id
                       WHERE pl.slate_id=%s ORDER BY pl.proj_adj DESC NULLS LAST""", (slate_id,))
        pool = cur.fetchall()
    with conn.cursor() as cur:
        cur.execute("SELECT created_at, payload FROM grades WHERE slate_id=%s ORDER BY created_at DESC LIMIT 1", (slate_id,))
        grade = cur.fetchone()
    board = sorted([p for p in pool if p["value_grade"]],
                   key=lambda p: ("ABCD".index(p["value_grade"]), -(p["value"] or 0)))
    watch = [p for p in pool if (p["inj_status"] or "") in ("Q", "GTD", "D", "O", "OUT", "NOPROJ") or p["contingent"]]
    return {"slate": slate, "run": run, "lineups": lineups, "entered_other_runs": entered_elsewhere,
            "value_board": board, "watch": watch, "pool": pool, "grade": grade}


def dk_upload_csv(conn, slate_id, mode="GPP", entered_only=False) -> str:
    """DK bulk-upload format: header of roster slots, one row of draftable IDs per lineup."""
    with conn.cursor() as cur:
        if entered_only:
            cur.execute("SELECT players FROM lineups WHERE slate_id=%s AND entered ORDER BY mode, rank", (slate_id,))
        else:
            run = latest_run(cur, slate_id)
            if not run:
                return ",".join(SLOTS) + "\n"
            cur.execute("SELECT players FROM lineups WHERE run_id=%s AND mode=%s ORDER BY rank",
                        (run["run_id"], mode))
        rows = [",".join(str(p["draftable_id"]) for p in r["players"]) for r in cur.fetchall()]
    return "\n".join([",".join(SLOTS), *rows]) + "\n"
