"""The pre-lock alert's decisions, kept pure so they can be tested without a clock, a database
or a network.

Why an upper bound rather than a 15-20 minute band: GitHub's scheduler is late by several
minutes and sometimes much more, so consecutive ticks can land at T-22 and then T-14, stepping
clean over any band. Firing on the first tick at or inside the lead, with a once-only marker in
alerts_sent, guarantees an alert no later than roughly 15 minutes out and tolerates the jitter.
Correctness comes from the lock time in the database, never from the cron's hours, so daylight
saving cannot drift it."""
from datetime import datetime, timezone

from .names import name_norm

ALERT_LEAD_MINUTES = 20


def _utc(ts):
    if ts is None:
        return None
    t = ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts))
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t


def minutes_to_lock(lock_time_utc, now) -> float | None:
    lock, n = _utc(lock_time_utc), _utc(now)
    if lock is None or n is None:
        return None
    return (lock - n).total_seconds() / 60.0


def should_alert(lock_time_utc, now, already_sent: bool, lead_minutes: int = ALERT_LEAD_MINUTES) -> bool:
    if already_sent:
        return False
    m = minutes_to_lock(lock_time_utc, now)
    if m is None:
        return False
    return 0 < m <= lead_minutes


def overlap_ratio(rg_names, dk_names) -> float:
    """Share of the DraftKings pool that RotoGrinders also returned, matched on normalized names.
    The RotoGrinders projections page is slate-scoped, so a low ratio means we were handed a
    different slate's players and must not build from them."""
    dk = {name_norm(n) for n in dk_names if n}
    if not dk:
        return 0.0
    rg = {name_norm(n) for n in rg_names if n}
    return len(dk & rg) / len(dk)


def format_alert(slate_date, flags: list[dict], lineups_checked: int) -> tuple[str, str]:
    """(title, body). The title is an ASCII constant because ntfy sends it as an HTTP header;
    every player name goes in the body, which is UTF-8."""
    swappable = [f for f in flags if not f["locked"]]
    if not flags:
        title = "Lineups clean"
        body = f"{slate_date}: {lineups_checked} lineup(s) checked, no flagged players."
        return title, body
    title = f"{len(flags)} flagged - {len(swappable)} swappable"
    lines = [f"{slate_date}: {lineups_checked} lineup(s) checked."]
    for f in flags:
        who = f"{f['name']} ({f['slot']}, {f['status']})"
        tag = "GPP" if f["mode"] == "GPP" else f["mode"]
        entered = " [ENTERED]" if f.get("entered") else ""
        if f["locked"]:
            lines.append(f"- {who} {tag}#{f['rank']}{entered}: game already tipped, cannot swap")
            continue
        subs = ", ".join(f"{s['name']} ${s['salary']} ({s['proj']:+.1f})" for s in f["suggestions"][:3])
        lines.append(f"- {who} {tag}#{f['rank']}{entered}: {subs or 'no fitting replacement'}")
    return title, "\n".join(lines)
