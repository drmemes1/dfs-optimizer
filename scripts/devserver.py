"""Local dev server: serves public/ and routes /api/<name> to api/<name>.py like Vercel does.
Usage: DATABASE_URL=... python scripts/devserver.py [port]"""
import importlib.util
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load(name):
    path = ROOT / "api" / f"{name}.py"
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location(f"api_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.handler


class Dev(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT / "public"), **kw)

    def _api(self, method):
        p = urlparse(self.path).path
        if not p.startswith("/api/"):
            return False
        h = load(p[len("/api/"):].strip("/"))
        if h is None or not hasattr(h, method):
            self.send_error(404)
        else:
            getattr(h, method)(self)
        return True

    def do_GET(self):
        if not self._api("do_GET"):
            super().do_GET()

    def do_POST(self):
        if not self._api("do_POST"):
            self.send_error(405)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"http://localhost:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Dev).serve_forever()
