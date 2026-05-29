# shared fixtures for the extraction tests — give each test a clean module with its network and
# session globals reset, so cached state never leaks between tests
import pytest


class FakeResp:
    """Stand-in for a requests.Response — returns canned JSON / text, never touches the network."""

    def __init__(self, json_data=None, text=""):
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("response had no JSON body")
        return self._json

    def raise_for_status(self):
        pass


@pytest.fixture
def edgar():
    # import here (not at module top) so the reset happens per-test; clear the module-level caches
    import genai.extraction.edgar_fulltext as ef

    ef._cik_cache = None
    ef._session = None
    return ef
