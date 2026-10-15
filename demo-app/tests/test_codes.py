import pytest

from shortener.codes import encode, normalize_url, valid_code


def test_encode_base62():
    assert encode(0) == "0" and encode(61) == "Z" and encode(62) == "10"


def test_encode_rejects_negative():
    with pytest.raises(ValueError):
        encode(-1)


def test_valid_code():
    assert valid_code("abc123") and not valid_code("") and not valid_code("a/b") and not valid_code("x" * 13)


def test_normalize_url_strips_fragment_and_whitespace():
    assert normalize_url("  https://example.com/a#top ") == "https://example.com/a"


@pytest.mark.parametrize("bad", ["ftp://x.com", "example.com", "http://", "javascript:alert(1)"])
def test_normalize_url_rejects(bad):
    with pytest.raises(ValueError):
        normalize_url(bad)
