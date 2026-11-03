"""Import DraftKings contest results and capture contest context on builds."""
import hashlib
import json

from .names import NameIndex, match_key
from .sources.contest_results import contest_id_from_filename, parse_standings, score_quantiles
from .sources.uploads import UploadError

CONTEXT_KEYS = {"name": str, "entry_fee": float, "field_size": int, "top_heaviness": str,
                "entry_mode": str, "max_entries": int}
TOP_HEAVINESS = {"top_heavy", "flat", "double_up", "fifty_fifty"}


def clean_context(raw: dict | None) -> dict | None:
    if not raw:
        return None
    out = {}
    for k, typ in CONTEXT_KEYS.items():
        v = raw.get(k)
        if v not in (None, ""):
            out[k] = typ(v)
    if out.get("top_heaviness") and out["top_heaviness"] not in TOP_HEAVINESS:
        raise UploadError(f"top_heaviness must be one of {', '.join(sorted(TOP_HEAVINESS))}")
    if out.get("entry_mode") and out["entry_mode"] not in ("SE", "ME"):
        raise UploadError("entry_mode must be SE (single entry) or ME (multi-entry)")
    return out or None


def _pick_slate(cur, owner_keys: set, slate_id=None):
    if slate_id:
        return slate_id, None
    cur.execute("SELECT id FROM slates ORDER BY date DESC, id DESC LIMIT 15")
    best, best_overlap = None, 0.0
    for r in cur.fetchall():
        cur.execute("SELECT p.name FROM pool pl JOIN players p ON p.id = pl.player_id WHERE pl.slate_id=%s", (r["id"],))
        keys = {match_key(x["name"]) for x in cur.fetchall()}
        if keys:
            overlap = len(owner_keys & keys) / max(1, len(owner_keys))
            if overlap > best_overlap:
                best, best_overlap = r["id"], overlap
    if best_overlap < 0.8:
        return None, f"no stored slate matches this contest's players (best overlap {best_overlap:.0%})"
    return best, None


def import_standings(conn, raw: bytes, filename: str | None, meta: dict) -> dict:
    text = raw.decode("utf-8-sig", errors="replace")
    parsed = parse_standings(text)
    contest_id = int(meta["contest_id"]) if meta.get("contest_id") else contest_id_from_filename(filename)
    if not contest_id:
        raise UploadError("contest id not found in the filename; enter it")
    username = (meta.get("dk_username") or "").strip().lower()
    places_paid = int(meta["places_paid"]) if meta.get("places_paid") else None
    entries = parsed["entries"]
    points = [e["points"] for e in entries]
    by_rank = sorted(entries, key=lambda e: e["rank"])
    min_cash = None
    if places_paid:
        paid = [e["points"] for e in by_rank if e["rank"] <= places_paid]
        min_cash = min(paid) if paid else None
    warnings = []
    with conn.cursor() as cur:
        owner_keys = {o["name_key"] for o in parsed["ownership"]}
        slate_id, warn = _pick_slate(cur, owner_keys, meta.get("slate_id"))
        if warn:
            warnings.append(warn)
        pool_index, pid_by_name = None, {}
        if slate_id:
            cur.execute("""SELECT p.id, p.name, pl.team FROM pool pl JOIN players p ON p.id = pl.player_id
                           WHERE pl.slate_id=%s""", (slate_id,))
            pool_rows = cur.fetchall()
            pool_index = NameIndex(pool_rows)
            pid_by_name = {match_key(r["name"]): r["id"] for r in pool_rows}
        cur.execute("""INSERT INTO contests(id, slate_id, name, entry_fee, entries, max_entries_per_user, prize_pool,
                                            places_paid, top_heaviness, winning_score, min_cash_score, score_quantiles)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (id) DO UPDATE SET slate_id=EXCLUDED.slate_id, name=EXCLUDED.name,
                         entry_fee=EXCLUDED.entry_fee, entries=EXCLUDED.entries,
                         max_entries_per_user=EXCLUDED.max_entries_per_user, prize_pool=EXCLUDED.prize_pool,
                         places_paid=EXCLUDED.places_paid, top_heaviness=EXCLUDED.top_heaviness,
                         winning_score=EXCLUDED.winning_score, min_cash_score=EXCLUDED.min_cash_score,
                         score_quantiles=EXCLUDED.score_quantiles, imported_at=now()""",
                    (contest_id, slate_id, meta.get("name"), meta.get("entry_fee") or None, len(entries),
                     meta.get("max_entries") or None, meta.get("prize_pool") or None, places_paid,
                     meta.get("top_heaviness") or None, max(points), min_cash,
                     json.dumps(score_quantiles(points))))
        cur.execute("DELETE FROM contest_entries WHERE contest_id=%s", (contest_id,))
        cur.execute("DELETE FROM contest_ownership WHERE contest_id=%s", (contest_id,))
        unmatched_owner = []
        for o in parsed["ownership"]:
            pid = pid_by_name.get(o["name_key"])
            if pid is None and pool_index:
                hit, _ = pool_index.find(o["name"])
                pid = hit["id"] if hit else None
            if pid is None and slate_id:
                unmatched_owner.append(o["name"])
            cur.execute("""INSERT INTO contest_ownership(contest_id, name, name_key, player_id, roster_pos, pct_drafted, fpts)
                           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                        (contest_id, o["name"], o["name_key"], pid, o["roster_pos"], o["pct_drafted"], o["fpts"]))
        # our saved lineups for the slate, keyed by their player set
        ours = {}
        if slate_id:
            cur.execute("SELECT id, players FROM lineups WHERE slate_id=%s ORDER BY entered DESC, built_at DESC", (slate_id,))
            for r in cur.fetchall():
                ours.setdefault(frozenset(match_key(x["name"]) for x in r["players"]), r["id"])
        mine = matched = 0
        for e in entries:
            is_mine = bool(username) and e["entry_name"].lower().split(" (")[0] == username
            lineup_id = None
            if is_mine:
                mine += 1
                lineup_id = ours.get(frozenset(match_key(x["name"]) for x in e["lineup"]))
                if lineup_id:
                    matched += 1
                    cur.execute("""UPDATE lineups SET entered=true, entered_at=COALESCE(entered_at, now()),
                                     actual_score=%s, contest_result=%s WHERE id=%s""",
                                (e["points"], json.dumps({"contest_id": contest_id, "rank": e["rank"],
                                                          "entries": len(entries), "points": e["points"],
                                                          "min_cash_score": min_cash}), lineup_id))
            cur.execute("""INSERT INTO contest_entries(contest_id, entry_id, entry_name, rank, points, lineup,
                                                       is_mine, lineup_id)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (contest_id, e["entry_id"], e["entry_name"], e["rank"], e["points"], json.dumps(e["lineup"]),
                         is_mine, lineup_id))
        cur.execute("""INSERT INTO source_snapshots(slate_id, source, filename, raw, sha256, payload)
                       VALUES (%s,'contest_standings_csv',%s,%s,%s,%s)""",
                    (slate_id, filename, raw, hashlib.sha256(raw).hexdigest(),
                     json.dumps({"contest_id": contest_id, "bytes": len(raw)})))
    conn.commit()
    if username and not mine:
        warnings.append(f'no entries named "{meta.get("dk_username")}" in this contest')
    if mine and matched < mine:
        warnings.append(f"{mine - matched} of your entries don't match a saved lineup (built elsewhere or edited on DK)")
    if unmatched_owner:
        warnings.append(f"{len(unmatched_owner)} ownership names didn't match the slate pool: "
                        + ", ".join(unmatched_owner[:10]))
    return {"contest_id": contest_id, "slate_id": slate_id, "entries": len(entries), "mine": mine,
            "matched_to_saved_lineups": matched, "winning_score": max(points), "min_cash_score": min_cash,
            "score_quantiles": score_quantiles(points), "ownership_rows": len(parsed["ownership"]),
            "warnings": warnings}
