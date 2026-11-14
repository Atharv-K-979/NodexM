import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from nodexm import google_auth
from nodexm.config import settings

CLIENT_ID = "1234567890-test.apps.googleusercontent.com"
GOOGLE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
FORGED_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def google_token(key=GOOGLE_KEY, **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": "google-user-1",
        "email": "Grace@Example.com", "email_verified": True, "name": "Grace Hopper",
        "picture": "https://lh3.googleusercontent.com/a/photo", "iat": now, "exp": now + 3600,
    }
    claims.update(overrides)
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, key, algorithm="RS256",
                      headers={"kid": "test-key"})


@pytest.fixture()
def client(monkeypatch):
    object.__setattr__(settings, "google_client_id", CLIENT_ID)
    # Real signature checks against our test key instead of fetching Google's published keys.
    monkeypatch.setattr(google_auth, "_signing_key", lambda token: GOOGLE_KEY.public_key())
    from main import app

    with TestClient(app) as c:
        yield c
    object.__setattr__(settings, "google_client_id", "")


def sign_in(client, token):
    return client.post("/api/auth/google", json={"credential": token})


def test_config_exposes_client_id(client):
    assert client.get("/api/config").json()["google_client_id"] == CLIENT_ID


def test_new_google_user_is_created_and_cannot_use_a_password(client):
    r = sign_in(client, google_token())
    assert r.status_code == 200, r.text
    user = r.json()["user"]
    assert user["email"] == "grace@example.com" and user["name"] == "Grace Hopper"
    assert user["sign_in"] == {"password": False, "google": True}
    assert user["avatar_url"].startswith("https://lh3.googleusercontent.com")
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['token']}"})
    assert me.status_code == 200

    again = sign_in(client, google_token())
    assert again.json()["user"]["id"] == user["id"]  # same account on the next sign-in

    pw = client.post("/api/auth/login", json={"email": "grace@example.com", "password": "anything123"})
    assert pw.status_code == 401 and "Google" in pw.json()["detail"]


def test_existing_password_account_gets_linked(client):
    reg = client.post("/api/auth/register", json={"email": "ada@example.com", "password": "longpassword"}).json()
    r = sign_in(client, google_token(sub="google-ada", email="ada@example.com", name="Ada"))
    assert r.status_code == 200
    assert r.json()["user"]["id"] == reg["user"]["id"]
    assert r.json()["user"]["sign_in"] == {"password": True, "google": True}
    assert client.post("/api/auth/login", json={"email": "ada@example.com", "password": "longpassword"}).status_code == 200


def test_email_linked_to_another_google_account_is_refused(client):
    sign_in(client, google_token(sub="google-owner", email="shared@example.com"))
    r = sign_in(client, google_token(sub="google-someone-else", email="shared@example.com"))
    assert r.status_code == 409


@pytest.mark.parametrize("token, status, phrase", [
    (lambda: google_token(key=FORGED_KEY), 401, "not valid"),
    (lambda: google_token(aud="another-app.apps.googleusercontent.com"), 401, "not valid"),
    (lambda: google_token(iss="https://evil.example.com"), 401, "not valid"),
    (lambda: google_token(iat=int(time.time()) - 7200, exp=int(time.time()) - 3600), 401, "expired"),
    (lambda: google_token(email_verified=False), 401, "not verified"),
    (lambda: google_token(email=None), 401, "not verified"),
])
def test_bad_tokens_are_rejected(client, token, status, phrase):
    r = sign_in(client, token())
    assert r.status_code == status and phrase in r.json()["detail"]


def test_google_sign_in_off_without_client_id(client):
    object.__setattr__(settings, "google_client_id", "")
    r = sign_in(client, google_token())
    assert r.status_code == 400 and "not set up" in r.json()["detail"]
