"""Read RotoGrinders' NBA projected-stats page as the user.

The projections table is server-rendered HTML once signed in (probed 2026-11-05: 7 rows signed
out, a 47-column header plus data rows signed in), so a stdlib POST with a cookie jar is enough
and no browser is involved.

Credentials come from RG_USER / RG_PASS (sensitive, Vercel) and are never logged or returned.
The session cookie is cached in rg_session so logins stay roughly weekly rather than dozens a
night, which is the point: a large login footprint is how an account gets flagged. A revoked
password is capped at MAX_LOGIN_FAILURES attempts per FAILURE_COOLDOWN_MINUTES so the
five-minute pre-lock schedule cannot hammer the sign-in form all evening.

RG_COOKIE remains supported and takes precedence, as a manual override when login breaks."""
import gzip
import http.cookiejar
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

from ..http import UA
from .uploads import UploadError

PROJ_URL = "https://rotogrinders.com/projected-stats/nba?site=draftkings"
SIGNIN_URL = "https://rotogrinders.com/sign-in"
MAX_LOGIN_FAILURES = 3
FAILURE_COOLDOWN_MINUTES = 30


class RGNotConnected(UploadError):
    pass


def connected() -> bool:
    return bool(os.environ.get("RG_COOKIE", "").strip() or
                (os.environ.get("RG_USER", "").strip() and os.environ.get("RG_PASS", "").strip()))


def _default_opener():
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def _opener_cookies(opener) -> list[str]:
    names = getattr(opener, "cookies", None)
    if names is not None:
        return list(names)
    jar = next((h.cookiejar for h in getattr(opener, "handlers", [])
                if isinstance(h, urllib.request.HTTPCookieProcessor)), None)
    return [f"{c.name}={c.value}" for c in (jar or [])]


def login(opener_factory=None) -> str:
    """Sign in and return a Cookie header value. Raises RGNotConnected, never echoing the password."""
    user = os.environ.get("RG_USER", "").strip()
    pw = os.environ.get("RG_PASS", "")
    if not user or not pw:
        raise RGNotConnected("RotoGrinders login isn't configured: set RG_USER and RG_PASS in Vercel "
                             "(sensitive), or set RG_COOKIE as a manual override.")
    opener = (opener_factory or _default_opener)()
    data = urllib.parse.urlencode({"username": user, "password": pw}).encode()
    headers = {"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded",
               "Accept": "text/html", "Accept-Language": "en-US,en;q=0.9",
               "Referer": SIGNIN_URL}
    try:
        with opener.open(urllib.request.Request(SIGNIN_URL, data=data, headers=headers),
                         timeout=30) as r:
            final = r.geturl()
            r.read()
    except urllib.error.HTTPError as e:
        raise RGNotConnected(f"RotoGrinders answered HTTP {e.code} to the sign-in request") from e
    except Exception as e:  # noqa: BLE001 - must not leak the request body
        raise RGNotConnected(f"RotoGrinders sign-in failed: {type(e).__name__}") from e
    names = _opener_cookies(opener)
    if not any(n.startswith("rguid=") for n in names) or "/sign-in" in final:
        raise RGNotConnected("RotoGrinders rejected the sign-in (no session cookie came back). "
                             "Check RG_USER / RG_PASS in Vercel.")
    return "; ".join(names)


def _read_session(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT cookie, acquired_at, failures, last_failure_at FROM rg_session WHERE id=1")
        return cur.fetchone() or {"cookie": None, "acquired_at": None,
                                  "failures": 0, "last_failure_at": None}


def _store_session(conn, cookie, now):
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO rg_session(id, cookie, acquired_at, failures, last_failure_at)
                       VALUES (1,%s,%s,0,NULL)
                       ON CONFLICT (id) DO UPDATE SET cookie=EXCLUDED.cookie,
                         acquired_at=EXCLUDED.acquired_at, failures = 0, last_failure_at=NULL""",
                    (cookie, now))
    conn.commit()


def _record_failure(conn, now):
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO rg_session(id, failures, last_failure_at) VALUES (1,1,%s)
                       ON CONFLICT (id) DO UPDATE SET failures = failures + 1,
                         last_failure_at = EXCLUDED.last_failure_at""", (now,))
    conn.commit()


def clear_session(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE rg_session SET cookie = NULL WHERE id=1")
    conn.commit()


def _cooling_off(row, now) -> bool:
    if (row.get("failures") or 0) < MAX_LOGIN_FAILURES:
        return False
    last = row.get("last_failure_at")
    if not last:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return now - last < timedelta(minutes=FAILURE_COOLDOWN_MINUTES)


def session_cookie(conn, login_fn=None, now=None) -> str:
    """Cached read-through: the manual RG_COOKIE override, else the stored cookie, else one login."""
    manual = os.environ.get("RG_COOKIE", "").strip()
    if manual:
        return manual
    now = now or datetime.now(timezone.utc)
    row = _read_session(conn)
    if row.get("cookie"):
        return row["cookie"]
    if _cooling_off(row, now):
        raise RGNotConnected(
            f"RotoGrinders sign-in failed {row['failures']} times; cooling off for "
            f"{FAILURE_COOLDOWN_MINUTES} minutes before trying again so the account isn't hammered. "
            "Check RG_USER / RG_PASS in Vercel.")
    try:
        cookie = (login_fn or login)()
    except Exception:
        _record_failure(conn, now)
        raise
    _store_session(conn, cookie, now)
    return cookie


def fetch_html(cookie: str | None = None) -> str:
    cookie = cookie or os.environ.get("RG_COOKIE", "").strip()
    if not cookie:
        raise RGNotConnected("RotoGrinders isn't connected. Set RG_USER and RG_PASS in Vercel, "
                             "or use the grabber button; see /rg.html.")
    req = urllib.request.Request(PROJ_URL, headers={
        "User-Agent": UA, "Accept": "text/html", "Cookie": cookie,
        "Accept-Language": "en-US,en;q=0.9", "Referer": "https://rotogrinders.com/"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            final_url = r.geturl()
    except urllib.error.HTTPError as e:
        raise RGNotConnected(f"RotoGrinders answered HTTP {e.code}; the session may have expired") from e
    html = raw.decode("utf-8", "replace")
    if "/sign-in" in final_url or "/login" in final_url:
        raise RGNotConnected("RotoGrinders sent us to its sign-in page: the session has expired")
    return html


def _cell_text(c) -> str:
    return " ".join(c.get_text(" ", strip=True).split())


def html_to_table_text(html: str) -> str:
    """Biggest table on the page -> the grabber's tab-separated format, so the same column checks apply."""
    soup = BeautifulSoup(html, "html.parser")
    tables = sorted(soup.find_all("table"), key=lambda t: len(t.find_all("tr")), reverse=True)
    if not tables or len(tables[0].find_all("tr")) < 20:
        raise UploadError("the projections table isn't in the page HTML (it may load by JavaScript); "
                          "use the grabber button until the reader is updated")
    t = tables[0]
    lines, n = [], 0
    for tr in t.find_all("tr"):
        cells = tr.find_all(["td", "th"], recursive=False)
        # skip cells the page hides, to match what a browser's innerText would give
        cells = [c for c in cells if "display:none" not in (c.get("style") or "").replace(" ", "")
                 and "hidden" not in (c.get("class") or [])]
        texts = [_cell_text(c) for c in cells]
        if not "".join(texts):
            continue
        if tr.find_parent("thead") is not None or all(c.name == "th" for c in cells):
            lines.append("#H\t" + "\t".join(texts))
        else:
            lines.append("\t".join(texts))
            n += 1
    return "\n".join([f"#RG-TABLE\t{PROJ_URL}\tfetched\t{n}", *lines])


def _session_html(conn, opener_factory=None) -> str:
    """Read the page with the cached session, re-acquiring it exactly once if it was rejected."""
    lf = (lambda: login(opener_factory=opener_factory)) if opener_factory else None
    cookie = session_cookie(conn, login_fn=lf)
    try:
        return fetch_html(cookie)
    except RGNotConnected:
        clear_session(conn)
        cookie = session_cookie(conn, login_fn=lf)
        return fetch_html(cookie)


def fetch_table_text(conn=None, opener_factory=None) -> tuple[str, bytes]:
    """(table text for the parser, gzipped page HTML for the archive).

    Opens its own short connection when none is given, because run_build calls this before it
    opens the build's connection. A rejected session is re-acquired exactly once."""
    if conn is None:
        from .. import db
        with db.connect() as own:
            db.migrate(own)
            return fetch_table_text(own, opener_factory=opener_factory)
    html = _session_html(conn, opener_factory=opener_factory)
    return html_to_table_text(html), gzip.compress(html.encode("utf-8"))


def diagnose(conn=None) -> dict:
    """Read-only structure report used to fit the reader to the live page (no player rows returned)."""
    if conn is None:
        from .. import db
        with db.connect() as own:
            db.migrate(own)
            return diagnose(own)
    html = _session_html(conn)
    soup = BeautifulSoup(html, "html.parser")
    tables = sorted(soup.find_all("table"), key=lambda t: len(t.find_all("tr")), reverse=True)
    out = {"html_bytes": len(html), "tables": [len(t.find_all("tr")) for t in tables[:5]],
           "title": soup.title.get_text(strip=True) if soup.title else None,
           "scripts_with_data": [s.get("src") or "(inline)" for s in soup.find_all("script")
                                 if "projection" in (s.get("src") or s.get_text()[:2000]).lower()][:10]}
    if tables:
        t = tables[0]
        heads = [tr for tr in t.find_all("tr") if tr.find_parent("thead") is not None or all(c.name == "th" for c in tr.find_all(["td", "th"]))]
        out["header_rows"] = [[_cell_text(c) for c in tr.find_all(["td", "th"], recursive=False)] for tr in heads]
        body = [tr for tr in t.find_all("tr") if tr not in heads]
        out["data_row_widths"] = sorted({len(tr.find_all(["td", "th"], recursive=False)) for tr in body})
        out["data_rows"] = len(body)
    try:
        from . import rg_table
        out["parse"] = f'ok: {len(rg_table.parse(html_to_table_text(html))["rows"])} players'
    except Exception as e:  # noqa: BLE001 - this is a diagnostic
        out["parse"] = f"refused: {e}"
    return out
