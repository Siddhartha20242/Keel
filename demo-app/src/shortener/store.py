"""In-memory link store. A restart wipes it."""
import threading

from .codes import encode, normalize_url, valid_code


class Store:
    def __init__(self):
        self._lock = threading.Lock()
        self._urls = {}      # code -> url
        self._hits = {}      # code -> visit count
        self._by_url = {}    # url -> code (same URL gets the same code)
        self._next = 1

    def shorten(self, url: str) -> str:
        url = normalize_url(url)
        with self._lock:
            if url in self._by_url:
                return self._by_url[url]
            code = encode(self._next)
            self._next += 1
            self._urls[code] = url
            self._hits[code] = 0
            self._by_url[url] = code
            return code

    def resolve(self, code: str):
        """Return the URL for a code and count the visit, or None."""
        if not valid_code(code):
            return None
        with self._lock:
            url = self._urls.get(code)
            if url is not None:
                self._hits[code] += 1
            return url

    def stats(self, code: str):
        if not valid_code(code):
            return None
        with self._lock:
            if code not in self._urls:
                return None
            return {"code": code, "url": self._urls[code], "hits": self._hits[code]}

    def size(self) -> int:
        with self._lock:
            return len(self._urls)
