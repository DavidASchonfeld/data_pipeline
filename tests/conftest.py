# shared setup loaded automatically by pytest before any test in tests/ runs
# centralises sys.path additions and common dependency stubs so each test file stays clean
import sys
import os
from unittest.mock import MagicMock

# make dashboard/ and airflow/dags/ importable from any test file without per-file path setup
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_ROOT, "dashboard"))
sys.path.insert(0, os.path.join(_ROOT, "airflow", "dags"))

# stub packages that are not installed in the test environment (or conflict with other pins)
for _mod in ["sqlalchemy", "pymysql"]:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# dotenv: only stub when the package isn't installed — if it is installed, leave the real
# load_dotenv alone so genai/config.py can read .env locally and production env vars
# (set via K8s secrets) are already in os.environ so load_dotenv() is a harmless no-op there
try:
    import dotenv  # noqa: F401
except ImportError:
    _m = MagicMock()
    _m.load_dotenv = MagicMock()
    sys.modules.setdefault("dotenv", _m)
