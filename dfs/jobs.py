"""Scheduled jobs. Every run writes a job_runs row (ok / errors) so a broken scheduler is visible."""
import json
from datetime import datetime, timezone

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
                    out = pipeline.run_refresh(slate_id=sid, fetch=lambda: src, now=now)
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
