"""Read RotoGrinders' NBA projected-stats page as the user, with their session cookie.

The cookie is stored only as the RG_COOKIE environment variable in Vercel (sensitive). It is never
logged or returned. Cookies expire every week or two; an expired one is reported plainly and the
build falls back to the grabber button / paste box."""
import gzip
import os
import urllib.error
import urllib.request

from bs4 import BeautifulSoup

from ..http import UA
from .uploads import UploadError

PROJ_URL = "https://rotogrinders.com/projected-stats/nba?site=draftkings"


class RGNotConnected(UploadError):
    pass


def connected() -> bool:
    return bool(os.environ.get("RG_COOKIE", "").strip())


def fetch_html() -> str:
    cookie = os.environ.get("RG_COOKIE", "").strip()
    if not cookie:
        raise RGNotConnected("RotoGrinders isn't connected (no RG_COOKIE in Vercel). Use the grabber button, "
                             "or connect it: see the RotoGrinders section of /rg.html.")
    req = urllib.request.Request(PROJ_URL, headers={"User-Agent": UA, "Accept": "text/html", "Cookie": cookie,
                                                     "Accept-Language": "en-US,en;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            final_url = r.geturl()
    except urllib.error.HTTPError as e:
        raise RGNotConnected(f"RotoGrinders answered HTTP {e.code}; the login cookie may have expired") from e
    html = raw.decode("utf-8", "replace")
    if "/sign-in" in final_url or "/login" in final_url:
        raise RGNotConnected("RotoGrinders sent us to its sign-in page: the login cookie has expired. Update RG_COOKIE "
                             "in Vercel (steps on /rg.html) or use the grabber button for today.")
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


def fetch_table_text() -> tuple[str, bytes]:
    """Returns (table text for the parser, gzipped page HTML for the archive)."""
    html = fetch_html()
    return html_to_table_text(html), gzip.compress(html.encode("utf-8"))


def diagnose() -> dict:
    """Read-only structure report used to fit the reader to the live page (no player rows returned)."""
    html = fetch_html()
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
