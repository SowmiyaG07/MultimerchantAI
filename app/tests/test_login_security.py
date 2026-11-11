"""
Login security:
- unknown email and wrong password give the SAME response
- account status (inactive, missing ExternalID) is only revealed after
  the correct password, and with a neutral message
- the password is compared in constant time

Fake customers and a test encryption secret: no real account or
password is used.
"""
from dotenv import load_dotenv

load_dotenv()

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.api.route.auth as auth  # noqa: E402
from app.api.main import app  # noqa: E402

client = TestClient(app)

PASSWORD = "Correct-Pass-123"
WRONG = "Wrong-Pass-456"


@pytest.fixture
def customers(monkeypatch):
    monkeypatch.setattr(auth, "_CRYPTO_SECRET", "test-crypto-secret")
    stored = auth._encrypt_password(PASSWORD)

    def customer(**changes):
        data = {
            "customer_id": 90001, "first_name": "Test", "last_name": "User",
            "email": "user@example.com", "password": stored,
            "whitelabel_id": 1, "active": True, "external_id": "EXT-90001",
        }
        data.update(changes)
        return data

    accounts = {
        "active@example.com": customer(),
        "inactive@example.com": customer(customer_id=90002, active=False),
        "noext@example.com": customer(customer_id=90003, external_id=None),
    }
    monkeypatch.setattr(auth, "getcustomerid_email", lambda email: accounts.get(email))


def login(email, password):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def test_correct_login_works(customers):
    response = login("active@example.com", PASSWORD)
    assert response.status_code == 200
    assert response.json()["access_token"]
    assert response.json()["session_id"] == "EXT-90001"


def test_unknown_email_and_wrong_password_look_the_same(customers):
    unknown = login("nobody@example.com", WRONG)
    wrong_password = login("active@example.com", WRONG)

    assert unknown.status_code == wrong_password.status_code == 401
    assert unknown.json() == wrong_password.json() == {"detail": auth.INVALID_CREDENTIALS}


def test_inactive_account_is_not_revealed_without_the_password(customers):
    """Before the fix this returned 403 'customer is not active'."""
    response = login("inactive@example.com", WRONG)

    assert response.status_code == 401
    assert response.json() == {"detail": auth.INVALID_CREDENTIALS}
    assert "inactive" not in response.text.lower()
    assert "active" not in response.text.lower()


def test_inactive_account_with_correct_password_gets_neutral_message(customers):
    response = login("inactive@example.com", PASSWORD)

    assert response.status_code == 403
    assert response.json() == {"detail": auth.ACCOUNT_UNAVAILABLE}
    assert "inactive" not in response.text.lower()
    assert "access_token" not in response.text


def test_missing_external_id_gets_neutral_message(customers):
    response = login("noext@example.com", PASSWORD)

    assert response.status_code == 500
    assert response.json() == {"detail": auth.ACCOUNT_UNAVAILABLE}
    assert "external" not in response.text.lower()


def test_missing_external_id_is_not_revealed_without_the_password(customers):
    response = login("noext@example.com", WRONG)
    assert response.status_code == 401
    assert response.json() == {"detail": auth.INVALID_CREDENTIALS}


def test_unknown_email_still_runs_a_password_check(customers, monkeypatch):
    """Same work as a real check, so timing doesn't reveal unknown emails."""
    calls = []
    real = auth._encrypt_password
    monkeypatch.setattr(auth, "_encrypt_password", lambda p: calls.append(1) or real(p))

    login("nobody@example.com", WRONG)
    unknown_calls = len(calls)
    calls.clear()
    login("active@example.com", WRONG)

    assert unknown_calls == len(calls) == 1


def test_verify_password_rules(customers):
    stored = auth._encrypt_password(PASSWORD)
    assert auth._verify_password(PASSWORD, stored) is True
    assert auth._verify_password(WRONG, stored) is False
    assert auth._verify_password("", stored) is False
    assert auth._verify_password(PASSWORD, None) is False
    assert auth._verify_password(PASSWORD, "") is False


def test_missing_crypto_secret_fails_closed(customers, monkeypatch):
    monkeypatch.setattr(auth, "_CRYPTO_SECRET", None)
    response = login("active@example.com", PASSWORD)
    assert response.status_code == 401


def test_backdoor_comment_is_gone():
    source = open(auth.__file__, encoding="utf-8").read().lower()
    assert "backdoor" not in source
    assert "mmddyyyy" not in source
