"""Server-side calls to the appraisal Auth project. Responses are not logged."""

from __future__ import annotations

import httpx

from app.iam_config import AUTH_BASE


class CanonicalEmailFailed(Exception):
    pass


class AuthRejected(Exception):
    """The password grant returned 400. Callers use one generic message."""


class AuthUnavailable(Exception):
    pass


class RefreshFailed(Exception):
    pass


class ProfileUnavailable(Exception):
    pass


class AppraisalClient:
    def __init__(self, base_url: str, anon_key: str, transport=None):
        self.base_url = base_url.rstrip("/")
        self.anon_key = anon_key
        self._client = httpx.Client(transport=transport, timeout=15.0)

    def close(self) -> None:
        self._client.close()

    def _anon_headers(self) -> dict[str, str]:
        return {
            "apikey": self.anon_key,
            "Authorization": f"Bearer {self.anon_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def canonical_email(self, typed: str) -> str:
        try:
            response = self._client.post(
                f"{self.base_url}/rest/v1/rpc/canonical_login_email",
                headers=self._anon_headers(),
                json={"_email": typed},
            )
        except httpx.HTTPError:
            raise CanonicalEmailFailed() from None
        if response.status_code != 200:
            raise CanonicalEmailFailed()
        try:
            body = response.json()
        except ValueError:
            raise CanonicalEmailFailed() from None
        if not isinstance(body, str) or "@" not in body:
            raise CanonicalEmailFailed()
        return body.strip()

    def password_grant(self, email: str, password: str) -> tuple[str, str]:
        try:
            response = self._client.post(
                f"{self.base_url}/auth/v1/token",
                params={"grant_type": "password"},
                headers={"apikey": self.anon_key, "Content-Type": "application/json", "Accept": "application/json"},
                json={"email": email, "password": password},
            )
        except httpx.HTTPError:
            raise AuthUnavailable() from None
        if response.status_code == 400:
            raise AuthRejected()
        if response.status_code != 200:
            raise AuthUnavailable()
        return _token_pair(response)

    def profile(self, subject: str, access_token: str) -> dict | None:
        try:
            response = self._client.get(
                f"{self.base_url}/rest/v1/profiles",
                params={"id": f"eq.{subject}", "select": "must_change_password,name,email"},
                headers={
                    "apikey": self.anon_key,
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                },
            )
        except httpx.HTTPError:
            raise ProfileUnavailable() from None
        if response.status_code != 200:
            raise ProfileUnavailable()
        try:
            rows = response.json()
        except ValueError:
            raise ProfileUnavailable() from None
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            return None
        return rows[0]

    def refresh(self, refresh_token: str) -> tuple[str, str]:
        try:
            response = self._client.post(
                f"{self.base_url}/auth/v1/token",
                params={"grant_type": "refresh_token"},
                headers={"apikey": self.anon_key, "Content-Type": "application/json", "Accept": "application/json"},
                json={"refresh_token": refresh_token},
            )
        except httpx.HTTPError:
            raise RefreshFailed() from None
        if response.status_code != 200:
            raise RefreshFailed()
        try:
            return _token_pair(response)
        except AuthUnavailable:
            raise RefreshFailed() from None

    def logout(self, access_token: str) -> None:
        try:
            response = self._client.post(
                f"{self.base_url}/auth/v1/logout",
                headers={
                    "apikey": self.anon_key,
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={"scope": "global"},
            )
        except httpx.HTTPError:
            raise AuthUnavailable() from None
        if response.status_code not in (200, 204):
            raise AuthUnavailable()


def _token_pair(response: httpx.Response) -> tuple[str, str]:
    try:
        body = response.json()
    except ValueError:
        raise AuthUnavailable() from None
    access = body.get("access_token") if isinstance(body, dict) else None
    refresh = body.get("refresh_token") if isinstance(body, dict) else None
    if not isinstance(access, str) or not isinstance(refresh, str) or not access or not refresh:
        raise AuthUnavailable()
    return access, refresh


def default_client(base_url: str, anon_key: str) -> AppraisalClient:
    return AppraisalClient(base_url or AUTH_BASE, anon_key)
