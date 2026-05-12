"""Tests for _get_real_ip in dashboard/security.py."""

import sys
from unittest.mock import MagicMock, patch

# Stub Flask security extensions — only needed at import time; not exercised by these tests
import types as _types
for _mod in ["flask_cors", "flask_talisman"]:
    sys.modules.setdefault(_mod, MagicMock())

# Stub flask_limiter so the Limiter(...) call at module level succeeds
_fl = _types.ModuleType("flask_limiter")
_fl.Limiter = MagicMock(return_value=MagicMock())
_fl_util = _types.ModuleType("flask_limiter.util")
_fl_util.get_remote_address = MagicMock(return_value="127.0.0.1")
_fl.util = _fl_util
sys.modules.setdefault("flask_limiter",      _fl)
sys.modules.setdefault("flask_limiter.util", _fl_util)

from security import _get_real_ip


# ── _get_real_ip ──────────────────────────────────────────────────────────────
# Flask's `request` is a Werkzeug LocalProxy — patch() with `new=` avoids dereferencing
# it outside a request context (which raises RuntimeError on Python 3.12 / Flask 2.3).

def test_cloudfront_header_strips_port():
    """CloudFront-Viewer-Address '1.2.3.4:12345' → only the IP '1.2.3.4' is returned."""
    mock_request = MagicMock()
    mock_request.headers.get.return_value = "1.2.3.4:12345"
    with patch("security.request", new=mock_request), \
         patch("security.get_remote_address", return_value="10.0.0.1"):
        assert _get_real_ip() == "1.2.3.4"


def test_cloudfront_header_different_ip():
    """Different CloudFront address '203.0.113.42:54321' → '203.0.113.42' returned."""
    mock_request = MagicMock()
    mock_request.headers.get.return_value = "203.0.113.42:54321"
    with patch("security.request", new=mock_request), \
         patch("security.get_remote_address", return_value="10.0.0.1"):
        assert _get_real_ip() == "203.0.113.42"


def test_fallback_when_no_cloudfront_header():
    """Empty CloudFront header triggers the fallback and returns get_remote_address's value."""
    mock_request = MagicMock()
    mock_request.headers.get.return_value = ""  # no CloudFront header present
    with patch("security.request", new=mock_request), \
         patch("security.get_remote_address", return_value="10.0.0.1"):
        assert _get_real_ip() == "10.0.0.1"


def test_fallback_called_once():
    """get_remote_address is invoked exactly once when there is no CloudFront header."""
    mock_request = MagicMock()
    mock_request.headers.get.return_value = ""
    with patch("security.request", new=mock_request), \
         patch("security.get_remote_address", return_value="10.0.0.1") as mock_gra:
        _get_real_ip()
        mock_gra.assert_called_once()  # must not call the fallback more than once
