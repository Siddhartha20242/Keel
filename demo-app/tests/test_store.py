import pytest

from shortener.store import Store


def test_same_url_same_code():
    s = Store()
    assert s.shorten("https://example.com") == s.shorten("https://example.com#frag")
    assert s.size() == 1


def test_resolve_counts_hits():
    s = Store()
    code = s.shorten("https://example.com/x")
    assert s.resolve(code) == "https://example.com/x"
    s.resolve(code)
    assert s.stats(code)["hits"] == 2


def test_unknown_and_invalid_codes():
    s = Store()
    assert s.resolve("nope") is None and s.resolve("../etc") is None
    assert s.stats("nope") is None


def test_shorten_rejects_bad_url():
    with pytest.raises(ValueError):
        Store().shorten("not a url")
