import json
import threading
import urllib.error
import urllib.request

import pytest

from shortener.server import serve


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


@pytest.fixture()
def base():
    srv = serve(host="127.0.0.1", port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def call(url, data=None):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(NoRedirect)
    try:
        r = opener.open(req)
        return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_health(base):
    status, _, body = call(base + "/health")
    assert status == 200 and json.loads(body)["status"] == "ok"


def test_shorten_redirect_and_stats(base):
    status, _, body = call(base + "/shorten", {"url": "https://example.com/p"})
    assert status == 201
    code = json.loads(body)["code"]
    status, headers, _ = call(base + "/" + code)
    assert status == 302 and headers["Location"] == "https://example.com/p"
    status, _, body = call(base + "/stats/" + code)
    assert json.loads(body)["hits"] == 1


def test_bad_requests(base):
    assert call(base + "/shorten", {"url": "nope"})[0] == 400
    assert call(base + "/shorten", {})[0] == 400
    assert call(base + "/missing")[0] == 404
