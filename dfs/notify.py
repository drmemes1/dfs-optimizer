"""Phone push via ntfy. The topic name is the only secret, so it lives in NTFY_TOPIC
(sensitive, Vercel) and is never logged or echoed back by any endpoint.

push() never raises: a notifier outage must not fail a build that otherwise succeeded. The
caller records the returned error on the job run instead."""
import os
import urllib.request

DEFAULT_SERVER = "https://ntfy.sh"


def _post(url: str, data: bytes, headers: dict) -> None:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15):
        pass


def configured() -> bool:
    return bool(os.environ.get("NTFY_TOPIC", "").strip())


def push(title: str, body: str, priority: str = "default", tags: str = "", post=None) -> dict:
    """Returns {"ok": bool, "error": str | None}. title/tags must be ASCII (HTTP headers);
    body carries anything, sent as UTF-8."""
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return {"ok": False, "error": "NTFY_TOPIC is not set in Vercel; push alerts are disabled"}
    server = os.environ.get("NTFY_SERVER", DEFAULT_SERVER).strip().rstrip("/") or DEFAULT_SERVER
    headers = {"Title": title, "Priority": priority}
    if tags:
        headers["Tags"] = tags
    try:
        (post or _post)(f"{server}/{topic}", body.encode("utf-8"), headers)
        return {"ok": True, "error": None}
    except Exception as e:  # noqa: BLE001 - a failed push must never fail the job
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}
