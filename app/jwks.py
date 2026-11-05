"""Verify appraisal access tokens with the live ES256 JWKS."""

from __future__ import annotations

import re
import threading
import time

import httpx
import jwt
from jwt.algorithms import ECAlgorithm

from app.iam_config import EXPECTED_AUD, EXPECTED_ISS, JWKS_TTL_SECONDS, JWKS_URL, TokenRejected

_UUID = re.compile(r"\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z", re.IGNORECASE)


def keys_from_document(document: dict) -> dict:
    found = {}
    for item in document.get("keys") or []:
        if not isinstance(item, dict):
            continue
        if item.get("kty") != "EC" or item.get("crv") not in (None, "P-256"):
            continue
        if item.get("alg") not in (None, "ES256") or item.get("use") not in (None, "sig"):
            continue
        kid = item.get("kid")
        if not isinstance(kid, str) or not kid:
            continue
        found[kid] = ECAlgorithm.from_jwk(item)
    return found


def fetch_jwks_document(url: str = JWKS_URL) -> dict:
    with httpx.Client(timeout=10.0) as client:
        response = client.get(url)
    if response.status_code != 200:
        raise TokenRejected("jwks")
    document = response.json()
    if not isinstance(document, dict):
        raise TokenRejected("jwks")
    return document


class JwksCache:
    def __init__(self, loader=None, ttl_seconds: int = JWKS_TTL_SECONDS):
        self.loader = loader or fetch_jwks_document
        self.ttl = ttl_seconds
        self.fetched_at = 0.0
        self.keys: dict = {}
        self.fetches = 0
        self._lock = threading.Lock()

    def public_key(self, kid: str):
        with self._lock:
            now = time.monotonic()
            if not self.keys or now - self.fetched_at >= self.ttl:
                self._refresh()
            if kid not in self.keys:
                self._refresh()
            key = self.keys.get(kid)
            if key is None:
                raise TokenRejected("kid")
            return key

    def _refresh(self) -> None:
        document = self.loader()
        if not isinstance(document, dict):
            raise TokenRejected("jwks")
        self.keys = keys_from_document(document)
        self.fetched_at = time.monotonic()
        self.fetches += 1


def verify_access_token(token: str, cache: JwksCache) -> dict:
    """Return claims only after alg, kid, iss, aud, and exp all pass.

    HS256 is rejected before any shared secret is used. The anon API key is not a signing key.
    """
    if not isinstance(token, str) or token.count(".") != 2:
        raise TokenRejected("token")
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError:
        raise TokenRejected("token") from None
    if header.get("alg") != "ES256":
        raise TokenRejected("alg")
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise TokenRejected("kid")
    public_key = cache.public_key(kid)
    try:
        claims = jwt.decode(
            token,
            public_key,
            algorithms=["ES256"],
            audience=EXPECTED_AUD,
            issuer=EXPECTED_ISS,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        raise TokenRejected("exp") from None
    except jwt.InvalidIssuerError:
        raise TokenRejected("iss") from None
    except jwt.InvalidAudienceError:
        raise TokenRejected("aud") from None
    except jwt.InvalidTokenError:
        raise TokenRejected("token") from None
    subject = claims.get("sub")
    if not isinstance(subject, str) or _UUID.fullmatch(subject) is None:
        raise TokenRejected("sub")
    if claims.get("iss") != EXPECTED_ISS or claims.get("aud") != EXPECTED_AUD:
        raise TokenRejected("iss" if claims.get("iss") != EXPECTED_ISS else "aud")
    claims["sub"] = subject.lower()
    return claims
