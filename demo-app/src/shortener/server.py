"""HTTP layer: GET /health, POST /shorten, GET /<code>, GET /stats/<code>."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .store import Store

MAX_BODY = 4096


class Handler(BaseHTTPRequestHandler):
    store: Store
    base_url: str

    def _json(self, status: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/health":
            return self._json(200, {"status": "ok", "links": self.store.size()})
        if path.startswith("/stats/"):
            info = self.store.stats(path[len("/stats/"):])
            return self._json(200, info) if info else self._json(404, {"error": "not found"})
        url = self.store.resolve(path.lstrip("/"))
        if url is None:
            return self._json(404, {"error": "not found"})
        self.send_response(302)
        self.send_header("Location", url)
        self.end_headers()

    def do_POST(self):
        if self.path != "/shorten":
            return self._json(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length", 0))
        if length > MAX_BODY:
            return self._json(413, {"error": "body too large"})
        try:
            data = json.loads((self.rfile.read(length) or b"{}").decode())
            code = self.store.shorten(data["url"])
        except (KeyError, ValueError, TypeError):
            return self._json(400, {"error": "send JSON like {\"url\": \"https://example.com\"}"})
        self._json(201, {"code": code, "short_url": f"{self.base_url}/{code}"})

    def log_message(self, *args):   # keep test and CI output quiet
        pass


def make_handler(store: Store, base_url: str):
    return type("BoundHandler", (Handler,), {"store": store, "base_url": base_url})


def serve(host: str = "0.0.0.0", port: int = 8080, base_url: str | None = None) -> ThreadingHTTPServer:
    store = Store()
    return ThreadingHTTPServer((host, port), make_handler(store, base_url or f"http://localhost:{port}"))
