"""Scheduled jobs. Every run writes a job_runs row (ok / errors) so a broken scheduler is visible."""
import json
from datetime import datetime, timedelta, timezone

from . import db, history, pipeline, redact
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
                    errors[f"refresh_{sid}"] = redact.scrub(f"{type(e).__name__}: {e}")[:300]
            summary["refreshed"] = refreshed
        except Exception as e:  # noqa: BLE001
            errors["snapshot"] = redact.scrub(f"{type(e).__name__}: {e}")[:300]
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
                    from . import gamelogs
                    try:
                        ing = gamelogs.ingest_day(conn, d, days[d])
                        summary.setdefault("logged", {})[str(d)] = ing["written"]
                        unplaced = {k: len(ing.get(k) or []) for k in
                                    ("unmatched", "ambiguous", "team_mismatch")}
                        if any(unplaced.values()):
                            summary.setdefault("unplaced", {})[str(d)] = unplaced
                    except Exception as e:  # noqa: BLE001 - logging must never fail grading
                        # recorded as a warning, not an error: grading itself succeeded, and a
                        # 500 here would fail the workflow and alert the phone over a game log
                        summary.setdefault("gamelog_warnings", {})[str(d)] = \
                            redact.scrub(f"{type(e).__name__}: {e}")[:200]
                rep = grading.grade_slate(conn, sid, days[d])
                pending = {g: v for g, v in rep["games"].items() if v != "verified"}
                summary["slates"].append({"slate_id": sid, "date": str(d), "complete": rep["complete"],
                                          "pending": pending,
                                          "mae": (rep.get("grade") or {}).get("mae_ours")})
                if pending:
                    errors[f"slate_{sid}"] = f"{len(pending)} game(s) not verified yet; will retry"
            except Exception as e:  # noqa: BLE001 - recorded on the run
                errors[f"slate_{sid}"] = redact.scrub(f"{type(e).__name__}: {e}")[:300]
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
                if out.get("degraded"):
                    body = f"NOTE: {out['degraded']}\n{body}"
                res = push(title=title, body=body, priority="high", tags="warning")
                delivered = bool(res.get("ok")) or bool(res.get("unconfigured"))
                if not delivered:
                    # a real send failure: record it, and do NOT consume the one-shot marker, so
                    # the next tick inside the lead retries rather than going silent
                    errors[f"push_{s['id']}"] = res.get("error") or "alert send failed"
                    new_errors[f"push_{s['id']}"] = errors[f"push_{s['id']}"]
                summary["alerted"].append({"slate_id": s["id"], "flags": len(chk["flags"]),
                                           "swappable": sum(1 for f in chk["flags"] if not f["locked"]),
                                           "delivered": delivered,
                                           "degraded": out.get("degraded")})
            except Exception as e:  # noqa: BLE001 - recorded on the run AND pushed
                msg = redact.scrub(f"{type(e).__name__}: {e}")[:300]
                errors[f"prelock_{s['id']}"] = msg
                if not s["errored"]:
                    new_errors[f"prelock_{s['id']}"] = msg
                    res = push(title="Pre-lock check FAILED", priority="high", tags="rotating_light",
                               body=f"Slate {s['id']} ({s['date']}): {msg}\nCheck your lineups by hand.")
                    if res.get("ok") or res.get("unconfigured"):
                        _mark_alert(conn, s["id"], "prelock_error")
                continue
            if delivered:
                _mark_alert(conn, s["id"], "prelock")
        ok = not errors
        _finish(conn, rid, ok, summary, errors)
    return {"ok": ok, "summary": summary, "errors": errors, "new_errors": new_errors}


def _stored_dk_text(conn, slate_date):
    """The most recent DKSalaries upload for a slate on this ET date, or None. Decoded the same
    way pipeline._stored_upload does it, so a BOM-prefixed export reads identically."""
    with conn.cursor() as cur:
        cur.execute("""SELECT x.raw FROM source_snapshots x JOIN slates s ON s.id = x.slate_id
                       WHERE s.date = %s AND x.source = 'dk_salaries_csv' AND x.raw IS NOT NULL
                       ORDER BY x.fetched_at DESC, x.id DESC LIMIT 1""", (slate_date,))
        r = cur.fetchone()
    return bytes(r["raw"]).decode("utf-8-sig", errors="replace") if r else None


def _dk_game_count(dk_text) -> int:
    from .sources.uploads import parse_dk_salaries
    return len(parse_dk_salaries(dk_text)["games"])


def _record_send(errors, key, res):
    """A send that failed is recorded; a transport deliberately left unconfigured is not. Without
    the distinction, switching alerts off would fail every run; without the recording, an
    undeliverable nudge would leave the run green with nobody told."""
    if not res.get("ok") and not res.get("unconfigured"):
        errors[key] = res.get("error") or "alert send failed"


def build(now: datetime | None = None, lobby_fn=None, rg_fetch_fn=None, push=None,
          build_fn=None, override_group_id=None) -> dict:
    """The 1pm job. Identifies today's main slate and builds from the stored DKSalaries upload
    plus freshly read RotoGrinders projections.

    DraftKings salaries stay a manual upload by decision (the draftables API blocks ordinary
    clients and automating a real-money login was rejected), so the single manual step is
    *prompted* here rather than remembered: a missing upload pushes a nudge with hours of runway
    before lock."""
    from . import notify, pipeline
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
                _record_send(errors, "push_no_slate", push(
                    title="No main slate tonight", priority="low", tags="zzz",
                    body=f"{today}: no Classic group starts 7-8pm ET. Nothing to build."))
                ok = not errors
                _finish(conn, rid, ok, summary, errors)
                return {"ok": ok, "summary": summary, "errors": errors}
            summary["main_slate"] = {"draft_group_id": main["draft_group_id"],
                                     "games": main.get("game_count"),
                                     "start": str(main.get("start"))}
            dk_text = _stored_dk_text(conn, today)
            if not dk_text:
                summary["blocked"] = "no dk_salaries_csv upload"
                _record_send(errors, "push_nudge", push(
                    title="Upload DKSalaries.csv", priority="high", tags="warning",
                    body=(f"{today}: main slate is {main.get('game_count')} games at "
                          f"{main.get('start')}, but no DKSalaries.csv is stored yet. "
                          "Upload it and the build will run.")))
                ok = not errors
                _finish(conn, rid, ok, summary, errors)
                return {"ok": ok, "summary": summary, "errors": errors}
            # Is the stored upload the slate we just picked? The game count in the message comes
            # from the lobby and the lineups come from the file, so an unnoticed mismatch would
            # build the late slate and announce the main one. The RotoGrinders check cannot catch
            # this: main-slate projections are a superset of a 2-game file and read ~100%.
            file_games = _dk_game_count(dk_text)
            lobby_games = main.get("game_count")
            summary["dk_file_games"] = file_games
            if lobby_games and file_games != lobby_games:
                msg = (f"the stored DKSalaries.csv has {file_games} games but tonight's main slate "
                       f"has {lobby_games}; it looks like a different slate's export")
                errors["dk_file_mismatch"] = msg
                _record_send(errors, "push_mismatch", push(
                    title="Build refused - wrong file", priority="high", tags="rotating_light",
                    body=f"{today}: {msg}. Upload the main slate's export and it will build."))
                _finish(conn, rid, False, summary, errors)
                return {"ok": False, "summary": summary, "errors": errors}
            rg_text, _ = (rg_fetch_fn or _rg_table_text)()
            out = (build_fn or pipeline.run_build)(dk_text=dk_text, rg_text=rg_text) or {}
            summary.update({k: out[k] for k in ("slate_id", "run_id", "dk_coverage") if k in out})
            _record_send(errors, "push", push(
                title="Lineups ready", priority="default", tags="basketball",
                body=(f"{today}: built for the {lobby_games}-game slate, "
                      f"{summary.get('dk_coverage', 0):.0%} of the pool projected. "
                      "Review and enter.")))
        except Exception as e:  # noqa: BLE001 - recorded on the run AND pushed
            msg = redact.scrub(f"{type(e).__name__}: {e}")[:300]
            errors["build"] = msg
            _record_send(errors, "push_failure", push(
                title="1pm build FAILED", priority="high", tags="rotating_light",
                body=f"{today}: {msg}\nBuild by hand before lock."))
        ok = not errors
        _finish(conn, rid, ok, summary, errors)
    return {"ok": ok, "summary": summary, "errors": errors}


def backfill_logs(days: int = 14, now: datetime | None = None) -> dict:
    """One-off seeding of player_games. Safe to re-run: ingest upserts."""
    from . import gamelogs
    now = now or datetime.now(timezone.utc)
    with db.connect() as conn:
        db.migrate(conn)
        rid = _start(conn, "backfill")
        errors, summary = {}, {}
        try:
            out = gamelogs.backfill(conn, history.et_date(now), days=days)
            summary["days"] = {d: v.get("written", 0) for d, v in out["days"].items()}
            # a silent matcher is a matcher nobody checks: a name we cannot place writes no game
            # log at all, and that player then never gets a form adjustment for any reason we
            # would ever see
            for key in ("unmatched", "ambiguous", "team_mismatch"):
                names = sorted({str(n) for v in out["days"].values() for n in v.get(key, [])})
                if names:
                    summary.setdefault("unplaced", {})[key] = {"count": len(names),
                                                               "sample": names[:15]}
            errors.update(out["errors"])
        except Exception as e:  # noqa: BLE001 - recorded on the run
            errors["backfill"] = redact.scrub(f"{type(e).__name__}: {e}")[:300]
        ok = not errors
        _finish(conn, rid, ok, summary, errors)
    return {"ok": ok, "summary": summary, "errors": errors}
