# Tests for the one Snowflake helper: real key loading with a throwaway key, and connect() with a fake
# connector, so nothing touches the network.

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from agent_data.engine._shared import snowflake as sf
from agent_data.engine._shared.errors import SnowflakeError


@pytest.fixture
def key_file(tmp_path):
    # Write a fresh, unencrypted RSA key in PEM format, like the service account's key file.
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = tmp_path / "rsa_key.p8"
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    return path


@pytest.fixture
def settings(key_file):
    return sf.SnowflakeSettings(account="acct", user="svc", private_key_path=str(key_file),
                                database="PIPELINE_DB", warehouse="WH")


@pytest.fixture
def fake_connector(monkeypatch):
    # Stand-in for snowflake.connector that records the arguments it was called with.
    calls: list[dict] = []
    module = SimpleNamespace(connect=lambda **kwargs: calls.append(kwargs) or "connection")
    monkeypatch.setitem(sys.modules, "snowflake", SimpleNamespace(connector=module))
    monkeypatch.setitem(sys.modules, "snowflake.connector", module)
    return calls


def test_loads_pem_key_as_der(key_file):
    der = sf.load_private_key_der(str(key_file))
    assert isinstance(der, bytes) and der[:1] == b"\x30"   # DER structures start with a SEQUENCE byte


def test_missing_key_file_is_a_snowflake_error(tmp_path):
    with pytest.raises(SnowflakeError, match="Could not read"):
        sf.load_private_key_der(str(tmp_path / "nope.p8"))


def test_garbage_key_file_is_a_snowflake_error(tmp_path):
    bad = tmp_path / "bad.p8"
    bad.write_text("not a key")
    with pytest.raises(SnowflakeError, match="not a valid unencrypted PEM"):
        sf.load_private_key_der(str(bad))


def test_unconfigured_settings_name_the_missing_env_vars():
    with pytest.raises(SnowflakeError, match="SNOWFLAKE_ACCOUNT.*SNOWFLAKE_WAREHOUSE"):
        sf.connect(sf.SnowflakeSettings())


def test_connect_passes_key_role_and_timeouts(settings, fake_connector):
    assert sf.connect(settings) == "connection"
    args = fake_connector[0]
    assert args["account"] == "acct" and args["role"] == "PIPELINE_ROLE"
    assert args["login_timeout"] == 30 and args["network_timeout"] == 60
    assert isinstance(args["private_key"], bytes)


def test_connector_failure_becomes_one_snowflake_error(settings, monkeypatch):
    def boom(**_kwargs):
        raise RuntimeError("250001: login failed")

    module = SimpleNamespace(connect=boom)
    monkeypatch.setitem(sys.modules, "snowflake", SimpleNamespace(connector=module))
    monkeypatch.setitem(sys.modules, "snowflake.connector", module)
    with pytest.raises(SnowflakeError, match="acct.*svc.*250001") as info:
        sf.connect(settings)
    assert isinstance(info.value.__cause__, RuntimeError)   # original error kept for debugging
