"""Short-code generation and URL validation."""
import re
from urllib.parse import urlparse

ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
CODE_RE = re.compile(r"^[0-9a-zA-Z]{1,12}$")


def encode(n: int) -> str:
    """Base62 of a non-negative integer (0 encodes to "0")."""
    if n < 0:
        raise ValueError("n must be >= 0")
    if n == 0:
        return ALPHABET[0]
    out = []
    while n:
        n, r = divmod(n, 62)
        out.append(ALPHABET[r])
    return "".join(reversed(out))


def valid_code(code: str) -> bool:
    return bool(CODE_RE.match(code))


def normalize_url(url: str) -> str:
    """Trim, require http(s) and a host, drop the fragment."""
    url = url.strip()
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("url must be http(s) with a host")
    return parts._replace(fragment="").geturl()
