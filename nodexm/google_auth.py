"""Verify "Sign in with Google" ID tokens (Google Identity Services).

The browser gets a signed ID token (a JWT) from Google's button and sends it here. We check
Google's RS256 signature against Google's published keys, that the token was issued for our
Client ID, that it is not expired, and that the email is verified. No client secret is needed.
"""
from __future__ import annotations

import logging

import jwt
from fastapi import HTTPException, status

from .config import settings

log = logging.getLogger("nodexm.google")

GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")

_jwks_client: jwt.PyJWKClient | None = None


def _signing_key(token: str):
    """Google's public key for this token (cached; Google rotates keys every few days)."""
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient(GOOGLE_CERTS_URL, cache_keys=True, lifespan=6 * 3600, timeout=10)
    return _jwks_client.get_signing_key_from_jwt(token).key


def verify_google_token(token: str) -> dict:
    """Return the token's claims, or raise a 4xx HTTPException with a user-facing message."""
    if not settings.google_client_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Google sign-in is not set up on this server.")
    try:
        key = _signing_key(token)
    except jwt.PyJWKClientConnectionError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Could not reach Google to check the sign-in. Try again.")
    except jwt.PyJWKClientError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Google sign-in failed: unknown signing key.")
    try:
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=settings.google_client_id,
                            issuer=GOOGLE_ISSUERS, leeway=30, options={"require": ["exp", "iat", "sub", "aud", "iss"]})
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Google sign-in expired. Try again.")
    except jwt.InvalidTokenError as e:
        log.info("rejected Google token: %s", e)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Google sign-in failed: the token is not valid for this app.")
    if not claims.get("email") or claims.get("email_verified") not in (True, "true"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Your Google account email is not verified.")
    return claims
