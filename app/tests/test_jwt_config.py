"""
JWT configuration is checked at startup, and decode rejects unsafe tokens.

Each test sets its own environment and reloads app.api.jwt, so the
real .env values are never used or printed.
"""
import importlib
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt as jose_jwt

GOOD_KEY = secrets.token_urlsafe(48)          # 64 characters

BASE_ENV = {
    "VALID_ISSUER": "test-issuer",
    "VALID_AUDIENCE": "test-audience",
    "_CLAIM_NAME": "name",
    "_CLAIM_EMAIL": "email",
    "_CLAIM_ROLE": "role",
    "ISSUER_SIGNING_KEY": GOOD_KEY,
    "ACCESS_TOKEN_EXPIRE_MINUTES": "60",
    "ALGORITHM": "HS256",
}

CUSTOMER = {"customer_id": 123, "first_name": "Test", "last_name": "User"}


def load_jwt(monkeypatch, **changes):
    """Reload app.api.jwt with BASE_ENV plus changes (None = unset)."""
    for name, value in {**BASE_ENV, **changes}.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    import app.api.jwt as jwt_module
    return importlib.reload(jwt_module)


def startup_error(monkeypatch, **changes) -> str:
    with pytest.raises(RuntimeError) as error:
        load_jwt(monkeypatch, **changes)
    return str(error.value)


def payload(**changes):
    data = {
        "iss": "test-issuer",
        "aud": "test-audience",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        "CustomerId": "123",
    }
    data.update(changes)
    return {k: v for k, v in data.items() if v is not None}


# ---------------------------------------------
# Startup checks
# ---------------------------------------------

def test_valid_config_loads_and_round_trips(monkeypatch):
    module = load_jwt(monkeypatch)
    token = module.create_access_token(CUSTOMER)
    assert module.decode_access_token(token) == 123
    assert module.ALGORITHM == "HS256"


def test_missing_signing_key_stops_startup(monkeypatch):
    assert "ISSUER_SIGNING_KEY" in startup_error(monkeypatch, ISSUER_SIGNING_KEY=None)


def test_blank_signing_key_stops_startup(monkeypatch):
    assert "ISSUER_SIGNING_KEY" in startup_error(monkeypatch, ISSUER_SIGNING_KEY="   ")


def test_short_signing_key_stops_startup(monkeypatch):
    message = startup_error(monkeypatch, ISSUER_SIGNING_KEY="x" * 15)
    assert "at least 16 bytes" in message


def test_16_byte_key_is_accepted(monkeypatch):
    load_jwt(monkeypatch, ISSUER_SIGNING_KEY="k" * 16)


def test_placeholder_key_stops_startup(monkeypatch):
    assert "placeholder" in startup_error(monkeypatch, ISSUER_SIGNING_KEY="changeme")


def test_error_message_never_contains_the_key(monkeypatch):
    short_key = "short-key-12"
    assert short_key not in startup_error(monkeypatch, ISSUER_SIGNING_KEY=short_key)


@pytest.mark.parametrize("algorithm", ["none", "RS256", "HS512", "hs384"])
def test_other_algorithm_in_env_stops_startup(monkeypatch, algorithm):
    assert "Only HS256" in startup_error(monkeypatch, ALGORITHM=algorithm)


@pytest.mark.parametrize("algorithm", [None, "HS256", "hs256", '"HS256"'])
def test_hs256_or_unset_algorithm_is_accepted(monkeypatch, algorithm):
    assert load_jwt(monkeypatch, ALGORITHM=algorithm).ALGORITHM == "HS256"


@pytest.mark.parametrize("minutes", ["abc", "", "1.5", "0", "-5", "100000"])
def test_bad_expire_minutes_stops_startup(monkeypatch, minutes):
    assert "ACCESS_TOKEN_EXPIRE_MINUTES" in startup_error(
        monkeypatch, ACCESS_TOKEN_EXPIRE_MINUTES=minutes,
    )


def test_missing_issuer_stops_startup(monkeypatch):
    assert "VALID_ISSUER" in startup_error(monkeypatch, VALID_ISSUER=None)


# ---------------------------------------------
# Decode rejects unsafe tokens
# ---------------------------------------------

def rejected(module, token):
    with pytest.raises(ValueError):
        module.decode_access_token(token)
    return True


def test_token_without_exp_is_rejected(monkeypatch):
    module = load_jwt(monkeypatch)
    token = jose_jwt.encode(payload(exp=None), GOOD_KEY, algorithm="HS256")
    assert rejected(module, token)          # would never expire otherwise


def test_token_without_iss_or_aud_is_rejected(monkeypatch):
    module = load_jwt(monkeypatch)
    assert rejected(module, jose_jwt.encode(payload(iss=None), GOOD_KEY, algorithm="HS256"))
    assert rejected(module, jose_jwt.encode(payload(aud=None), GOOD_KEY, algorithm="HS256"))


def test_expired_token_is_rejected(monkeypatch):
    module = load_jwt(monkeypatch)
    old = datetime.now(timezone.utc) - timedelta(minutes=1)
    assert rejected(module, jose_jwt.encode(payload(exp=old), GOOD_KEY, algorithm="HS256"))


def test_wrong_issuer_audience_or_key_is_rejected(monkeypatch):
    module = load_jwt(monkeypatch)
    assert rejected(module, jose_jwt.encode(payload(iss="other"), GOOD_KEY, algorithm="HS256"))
    assert rejected(module, jose_jwt.encode(payload(aud="other"), GOOD_KEY, algorithm="HS256"))
    assert rejected(module, jose_jwt.encode(payload(), "w" * 40, algorithm="HS256"))


def test_token_with_other_algorithm_is_rejected(monkeypatch):
    module = load_jwt(monkeypatch)
    assert rejected(module, jose_jwt.encode(payload(), GOOD_KEY, algorithm="HS512"))


def test_unsigned_alg_none_token_is_rejected(monkeypatch):
    module = load_jwt(monkeypatch)
    import base64
    import json

    def b64(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    body = payload()
    body["exp"] = int(body["exp"].timestamp())
    token = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(body)}."
    assert rejected(module, token)
