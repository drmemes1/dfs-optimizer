"""Small helpers shared by the api/*.py handlers."""
import json
import traceback
from urllib.parse import parse_qs, urlparse

from .sources.uploads import UploadError


def query(handler) -> dict:
    return {k: v[0] for k, v in parse_qs(urlparse(handler.path).query).items()}


def read_json(handler) -> dict:
    n = int(handler.headers.get("Content-Length") or 0)
    return json.loads(handler.rfile.read(n) or b"{}")


def send(handler, status: int, body, content_type="application/json", filename=None):
    data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Cache-Control", "no-store")
    if filename:
        handler.send_header("Content-Disposition", f'attachment; filename="{filename}"')
    handler.end_headers()
    handler.wfile.write(data)


def guarded(fn):
    """Run fn(handler); user-facing input errors -> 400, anything else -> 500 (no secrets)."""
    def wrapper(handler):
        try:
            fn(handler)
        except UploadError as e:
            send(handler, 400, {"ok": False, "error": str(e)})
        except Exception as e:
            traceback.print_exc()
            send(handler, 500, {"ok": False, "error": type(e).__name__, "detail": str(e)[:300]})
    return wrapper
