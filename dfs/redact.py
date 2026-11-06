"""Strip credential values out of text that is about to be stored or returned.

Job errors are written to job_runs and returned by /api/cron, so an exception message that
happens to carry a password would publish it. Scrubbing at that boundary makes the spec's
promise true rather than dependent on every library's good manners."""
import os

SECRET_ENV_VARS = ("RG_PASS", "RG_USER", "RG_COOKIE", "RESEND_API_KEY", "NTFY_TOPIC",
                   "CRON_SECRET", "DATABASE_URL", "POSTGRES_URL")

# Shorter values are left alone: blind substring replacement of a short secret corrupts the
# message it is protecting (a one-character topic turns "connection refused" into
# "connec<redacted>ion refused"), and a secret that short is guessable anyway.
MIN_SCRUB_LEN = 8
PLACEHOLDER = "<redacted>"


def scrub(text: str) -> str:
    if not text:
        return text
    for name in SECRET_ENV_VARS:
        value = os.environ.get(name, "").strip()
        if len(value) >= MIN_SCRUB_LEN:
            text = text.replace(value, PLACEHOLDER)
    return text
