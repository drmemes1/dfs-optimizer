"""Alerts to the owner: email through Resend, or a phone push through ntfy.

Transport is chosen by what is configured, Resend first. All credentials live in Vercel
environment variables (sensitive) and are scrubbed out of any error this module returns, because
those errors are written to job_runs and returned by /api/cron.

push() never raises: a notifier outage must not fail a build that otherwise succeeded. The caller
records the returned error on the job run instead, and uses `unconfigured` to tell "the owner
switched alerts off" from "alerting is broken" — the first is not a failure worth reporting."""
import json
import os
import urllib.request

from . import redact

RESEND_URL = "https://api.resend.com/emails"
DEFAULT_FROM = "onboarding@resend.dev"
DEFAULT_SERVER = "https://ntfy.sh"


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _post(url: str, data: bytes, headers: dict) -> None:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15):
        pass


def _scrub(text: str) -> str:
    """Never let a key or topic reach job_runs or an HTTP response."""
    return redact.scrub(text)


def configured() -> bool:
    return bool((_env("RESEND_API_KEY") and _env("RESEND_TO")) or _env("NTFY_TOPIC"))


def _resend_request(title: str, body: str) -> tuple[str, bytes, dict]:
    payload = {"from": _env("RESEND_FROM") or DEFAULT_FROM,
               "to": [_env("RESEND_TO")], "subject": title, "text": body}
    return (RESEND_URL,
            json.dumps(payload).encode("utf-8"),
            {"Authorization": f"Bearer {_env('RESEND_API_KEY')}",
             "Content-Type": "application/json"})


def _ntfy_request(title: str, body: str, priority: str, tags: str) -> tuple[str, bytes, dict]:
    server = (_env("NTFY_SERVER") or DEFAULT_SERVER).rstrip("/") or DEFAULT_SERVER
    headers = {"Title": title, "Priority": priority}
    if tags:
        headers["Tags"] = tags
    return f"{server}/{_env('NTFY_TOPIC')}", body.encode("utf-8"), headers


def push(title: str, body: str, priority: str = "default", tags: str = "", post=None) -> dict:
    """Returns {"ok": bool, "error": str | None, "unconfigured": bool}.

    For ntfy, title/tags become HTTP headers and so must be ASCII; every player name belongs in
    body, which is sent as UTF-8 either way."""
    if _env("RESEND_API_KEY"):
        if not _env("RESEND_TO"):
            return {"ok": False, "unconfigured": True,
                    "error": "RESEND_API_KEY is set but RESEND_TO is not; email alerts are disabled"}
        build = lambda: _resend_request(title, body)  # noqa: E731
    elif _env("NTFY_TOPIC"):
        build = lambda: _ntfy_request(title, body, priority, tags)  # noqa: E731
    else:
        return {"ok": False, "unconfigured": True,
                "error": "no alert transport configured (set RESEND_API_KEY + RESEND_TO, or NTFY_TOPIC)"}
    try:
        url, data, headers = build()
        (post or _post)(url, data, headers)
        return {"ok": True, "error": None, "unconfigured": False}
    except Exception as e:  # noqa: BLE001 - a failed alert must never fail the job
        return {"ok": False, "unconfigured": False,
                "error": _scrub(f"{type(e).__name__}: {e}")[:200]}
