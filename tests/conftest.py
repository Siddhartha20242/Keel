import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


import os, pytest


@pytest.fixture(autouse=True)
def _no_store_unless_asked(monkeypatch):
    """The Docker setup sets KEEL_STORE_*; tests that expect the file fallback must not silently use that store.
    Store tests set what they need themselves, and KEEL_TEST_ON_STORE=1 keeps the environment's store for them."""
    if os.environ.get("KEEL_TEST_ON_STORE") != "1":
        for k in ("KEEL_STORE_HOST", "KEEL_STORE_PORT", "KEEL_STORE_USERNAME", "KEEL_STORE_PASSWORD", "KEEL_STORE_SSL", "KEEL_STORE_REPO"):
            monkeypatch.delenv(k, raising=False)
