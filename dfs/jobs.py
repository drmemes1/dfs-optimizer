"""Scheduled jobs. Every run writes a job_runs row (ok / errors) so a broken scheduler is visible."""
import json
from datetime import datetime, timedelta, timezone

from . import db, history, pipeline
from .sources import lineups as lineup_sources


def _start(conn, job):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO job_runs(job) VALUES (%s) RETURNING id", (job,))
        rid = cur.fetchone()["id"]
    conn.commit()
    return rid


def _finish(conn, rid, ok, summary, errors):
    with conn.cursor() as cur:
        cur.execute("UPDATE job_runs SET finished_at=now(), ok=%s, summary=%s, errors=%s WHERE id=%s",
                    (ok, json.dumps(summary, default=str), json.dumps(errors, default=str), rid))
    conn.commit()


def snapshot(now: datetime | None = None, fetch=None) -> dict:
    """Record lineup/injury/Vegas changes; then, for today's slates that still have games to tip,
    refresh the pool from the fresh pages and run the late-scratch check."""
    now = now or datetime.now(timezone.utc)
    src = (fetch or lineup_sources.fetch_all)()
    with db.connect() as conn:
        db.migrate(conn)
        rid = _start(conn, "snapshot")
        errors = dict(src.get("errors") or {})
        summary = {}
        try:
            summary["history"] = history.record(conn, src, now)
            with conn.cursor() as cur:
                cur.execute("""SELECT s.id FROM slates s WHERE s.date = %s AND EXISTS (
                                 SELECT 1 FROM source_snapshots x WHERE x.slate_id = s.id
                                   AND x.source = 'rg_projections_csv' AND x.raw IS NOT NULL)""",
                            (history.et_date(now),))
                slate_ids = [r["id"] for r in cur.fetchall()]
            refreshed = []
            for sid in slate_ids:
                try:
                    out = pipeline.run_refresh(slate_id=sid, fetch=lambda: src, now=now, save_raw_pages=False)
                    refreshed.append({"slate_id": sid, "flags": len(out["check"]["flags"]),
                                      "swappable": sum(1 for f in out["check"]["flags"] if not f["locked"])})
                except Exception as e:  # noqa: BLE001 - recorded on the run
                    errors[f"refresh_{sid}"] = f"{type(e).__name__}: {e}"[:300]
            summary["refreshed"] = refreshed
        except Exception as e:  # noqa: BLE001
            errors["snapshot"] = f"{type(e).__name__}: {e}"[:300]
        ok = not errors
        _finish(conn, rid, ok, summary, errors)
    return {"ok": ok, "summary": summary, "errors": errors}


def recent_runs(conn, limit=5):
    with conn.cursor() as cur:
        cur.execute("""SELECT job, started_at, finished_at, ok, summary, errors FROM job_runs
                       ORDER BY started_at DESC LIMIT %s""", (limit,))
        return cur.fetchall()


def grade(today=None, fetch_day=None) -> dict:
    """Grade every recent slate that has no grade yet. Unverified games are retried on the next run."""
    from datetime import datetime as _dt
    from . import grading, history
    from .sources import boxscores
    today = today or history.et_date(_dt.now(timezone.utc))
    fetch_day = fetch_day or boxscores.fetch_day
    with db.connect() as conn:
        db.migrate(conn)
        rid = _start(conn, "grade")
        errors, summary = {}, {"slates": []}
        days = {}
        for sid in grading.slates_to_grade(conn, today):
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT date FROM slates WHERE id=%s", (sid,))
                    d = cur.fetchone()["date"]
                if d not in days:
                    days[d] = fetch_day(d)
                rep = grading.grade_slate(conn, sid, days[d])
                pending = {g: v for g, v in rep["games"].items() if v != "verified"}
                summary["slates"].append({"slate_id": sid, "date": str(d), "complete": rep["complete"],
                                          "pending": pending,
                                          "mae": (rep.get("grade") or {}).get("mae_ours")})
                if pending:
                    errors[f"slate_{sid}"] = f"{len(pending)} game(s) not verified yet; will retry"
            except Exception as e:  # noqa: BLE001 - recorded on the run
                errors[f"slate_{sid}"] = f"{type(e).__name__}: {e}"[:300]
        ok = not errors
        _finish(conn, rid, ok, summary, errors)
    return {"ok": ok, "summary": summary, "errors": errors}


def _rg_table_text():
    from .sources import rg_fetch
    return rg_fetch.fetch_table_text()


def _mark_alert(conn, slate_id, kind):
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO alerts_sent(slate_id, kind) VALUES (%s,%s)
                       ON CONFLICT (slate_id, kind) DO NOTHING""", (slate_id, kind))
    conn.commit()


def prelock(now: datetime | None = None, fetch=None, rg_fetch_fn=None, push=None) -> dict:
    """Called every five minutes through the evening. Reads today's slates and does nothing until
    a slate is inside the alert lead, so almost every run touches only Postgres. Inside the lead
    it re-reads RotoGrinders and the lineup pages, re-runs the late-scratch check and pushes one
    alert per slate.

    A failed sweep pushes the error: at 6:45pm "the check broke, go look yourself" is useful and
    silence is not. The error is pushed once per slate, not on every five-minute tick, so a
    source that is down all evening does not bury the phone."""
    from . import notify, pipeline, prelock as pl
    now = now or datetime.now(timezone.utc)
    push = push or notify.push
    with db.connect() as conn:
        db.migrate(conn)
        rid = _start(conn, "prelock")
        errors, new_errors = {}, {}
        summary = {"considered": 0, "alerted": [], "skipped": []}
        with conn.cursor() as cur:
            cur.execute("""SELECT s.id, s.date, s.lock_time_utc,
                                  EXISTS (SELECT 1 FROM alerts_sent a
                                          WHERE a.slate_id = s.id AND a.kind='prelock') AS sent,
                                  EXISTS (SELECT 1 FROM alerts_sent a
                                          WHERE a.slate_id = s.id AND a.kind='prelock_error') AS errored
                           FROM slates s
                           WHERE s.date BETWEEN %s AND %s
                           ORDER BY s.lock_time_utc NULLS LAST""",
                        (history.et_date(now) - timedelta(days=1), history.et_date(now)))
            slates = cur.fetchall()
        summary["considered"] = len(slates)
        for s in slates:
            if not pl.should_alert(s["lock_time_utc"], now, already_sent=s["sent"]):
                summary["skipped"].append({"slate_id": s["id"],
                                           "minutes_to_lock": pl.minutes_to_lock(s["lock_time_utc"], now)})
                continue
            try:
                rg_text, _ = (rg_fetch_fn or _rg_table_text)()
                out = pipeline.run_refresh(rg_text=rg_text, slate_id=s["id"], fetch=fetch, now=now,
                                           save_raw_pages=False)
                chk = out["check"]
                title, body = pl.format_alert(str(s["date"]), chk["flags"], chk["checked_lineups"])
                res = push(title=title, body=body, priority="high", tags="warning")
                if not res.get("ok"):
                    errors[f"push_{s['id']}"] = res.get("error") or "push failed"
                    new_errors[f"push_{s['id']}"] = errors[f"push_{s['id']}"]
                summary["alerted"].append({"slate_id": s["id"], "flags": len(chk["flags"]),
                                           "swappable": sum(1 for f in chk["flags"] if not f["locked"])})
            except Exception as e:  # noqa: BLE001 - recorded on the run AND pushed
                msg = f"{type(e).__name__}: {e}"[:300]
                errors[f"prelock_{s['id']}"] = msg
                if not s["errored"]:
                    new_errors[f"prelock_{s['id']}"] = msg
                    push(title="Pre-lock check FAILED", priority="high", tags="rotating_light",
                         body=f"Slate {s['id']} ({s['date']}): {msg}\nCheck your lineups by hand.")
                    _mark_alert(conn, s["id"], "prelock_error")
                continue
            _mark_alert(conn, s["id"], "prelock")
        ok = not errors
        _finish(conn, rid, ok, summary, errors)
    return {"ok": ok, "summary": summary, "errors": errors, "new_errors": new_errors}


MIN_RG_DK_OVERLAP = 0.6


def _stored_dk_text(conn, slate_date):
    """The most recent DKSalaries upload for a slate on this ET date, or None. Decoded the same
    way pipeline._stored_upload does it, so a BOM-prefixed export reads identically."""
    with conn.cursor() as cur:
        cur.execute("""SELECT x.raw FROM source_snapshots x JOIN slates s ON s.id = x.slate_id
                       WHERE s.date = %s AND x.source = 'dk_salaries_csv' AND x.raw IS NOT NULL
                       ORDER BY x.fetched_at DESC, x.id DESC LIMIT 1""", (slate_date,))
        r = cur.fetchone()
    return bytes(r["raw"]).decode("utf-8-sig", errors="replace") if r else None


def _dk_names(dk_text):
    from .sources.uploads import parse_dk_salaries
    return [p["name"] for p in parse_dk_salaries(dk_text)["players"]]


def _rg_names(rg_text):
    from .sources.rg_table import parse
    return [r["name"] for r in parse(rg_text)["rows"]]


def build(now: datetime | None = None, lobby_fn=None, rg_fetch_fn=None, push=None,
          build_fn=None, override_group_id=None) -> dict:
    """The 1pm job. Identifies today's main slate and builds from the stored DKSalaries upload
    plus freshly read RotoGrinders projections.

    DraftKings salaries stay a manual upload by decision (the draftables API blocks ordinary
    clients and automating a real-money login was rejected), so the single manual step is
    *prompted* here rather than remembered: a missing upload pushes a nudge with hours of runway
    before lock."""
    from . import notify, pipeline, prelock as pl
    from .sources import dk
    now = now or datetime.now(timezone.utc)
    push = push or notify.push
    today = history.et_date(now)
    with db.connect() as conn:
        db.migrate(conn)
        db.seed_params(conn)
        rid = _start(conn, "build")
        errors, summary = {}, {"date": str(today)}
        try:
            lobby = (lobby_fn or dk.fetch_lobby)()
            main = dk.pick_main_slate(lobby, date=today, override_group_id=override_group_id)
            if not main:
                summary["main_slate"] = None
                push(title="No main slate tonight", priority="low", tags="zzz",
                     body=f"{today}: no Classic group starts 7-8pm ET. Nothing to build.")
                _finish(conn, rid, True, summary, errors)
                return {"ok": True, "summary": summary, "errors": errors}
            summary["main_slate"] = {"draft_group_id": main["draft_group_id"],
                                     "games": main.get("game_count"),
                                     "start": str(main.get("start"))}
            dk_text = _stored_dk_text(conn, today)
            if not dk_text:
                summary["blocked"] = "no dk_salaries_csv upload"
                push(title="Upload DKSalaries.csv", priority="high", tags="warning",
                     body=(f"{today}: main slate is {main.get('game_count')} games at "
                           f"{main.get('start')}, but no DKSalaries.csv is stored yet. "
                           "Upload it and the build will run."))
                _finish(conn, rid, True, summary, errors)
                return {"ok": True, "summary": summary, "errors": errors}
            rg_text, _ = (rg_fetch_fn or _rg_table_text)()
            overlap = pl.overlap_ratio(_rg_names(rg_text), _dk_names(dk_text))
            summary["rg_dk_overlap"] = round(overlap, 3)
            if overlap < MIN_RG_DK_OVERLAP:
                msg = (f"RotoGrinders returned players matching only {overlap:.0%} of the "
                       f"DraftKings pool (need {MIN_RG_DK_OVERLAP:.0%}); refusing to build from "
                       "what looks like a different slate")
                errors["slate_overlap"] = msg
                push(title="Build refused - wrong slate", priority="high", tags="rotating_light",
                     body=f"{today}: {msg}. Build by hand from the page.")
                _finish(conn, rid, False, summary, errors)
                return {"ok": False, "summary": summary, "errors": errors}
            out = (build_fn or pipeline.run_build)(dk_text=dk_text, rg_text=rg_text) or {}
            summary.update({k: out[k] for k in ("slate_id", "run_id") if k in out})
            res = push(title="Lineups ready", priority="default", tags="basketball",
                       body=(f"{today}: built for the {main.get('game_count')}-game slate. "
                             f"{summary.get('rg_dk_overlap', 0):.0%} pool match. Review and enter."))
            if not res.get("ok"):
                errors["push"] = res.get("error") or "push failed"
        except Exception as e:  # noqa: BLE001 - recorded on the run AND pushed
            msg = f"{type(e).__name__}: {e}"[:300]
            errors["build"] = msg
            push(title="1pm build FAILED", priority="high", tags="rotating_light",
                 body=f"{today}: {msg}\nBuild by hand before lock.")
        ok = not errors
        _finish(conn, rid, ok, summary, errors)
    return {"ok": ok, "summary": summary, "errors": errors}
