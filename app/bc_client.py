"""Read-only Business Central client. GET only. Decimals stay exact."""

from __future__ import annotations

import base64
import time
from urllib.parse import urlparse

import httpx

from app.bc_url import ConfigError, resolve_api_root, scheme_allowed
from app.constants import API_ROUTE
from app.money import parse_json_exact


class BcError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class BcClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        access_key: str,
        transport: httpx.BaseTransport | None = None,
        api_route: str = API_ROUTE,
        allow_private_http: bool = False,
    ):
        self._allow_private_http = allow_private_http
        self.api_root = resolve_api_root(base_url, api_route, allow_private_http)
        self._username = username
        self._access_key = access_key
        self._client = httpx.Client(transport=transport, timeout=httpx.Timeout(120.0), follow_redirects=False)

    def close(self) -> None:
        self._client.close()

    def get_text(self, url: str) -> str:
        return self._request(url, None).text

    def get_json(self, url: str, params: dict | None = None) -> dict:
        response = self._request(url, params)
        try:
            data = parse_json_exact(response.text)
        except ValueError as exc:
            raise BcError("Business Central did not return JSON.", response.status_code) from exc
        if not isinstance(data, dict):
            raise BcError("Business Central returned a JSON value that is not an object.", response.status_code)
        return data

    def iter_values(self, url: str, params: dict | None = None):
        next_url = url
        next_params = params
        while next_url:
            data = self.get_json(next_url, next_params)
            values = data.get("value")
            if not isinstance(values, list):
                raise BcError("Business Central response has no value array.")
            yield values
            link = data.get("@odata.nextLink")
            if not link:
                break
            self._assert_safe_link(str(link))
            next_url = str(link)
            next_params = None

    def _request(self, url: str, params: dict | None) -> httpx.Response:
        self._assert_safe_link(url)
        token = base64.b64encode(f"{self._username}:{self._access_key}".encode("utf-8")).decode("ascii")
        headers = {
            "Accept": "application/json",
            "Prefer": "odata.maxpagesize=2000",
            "Authorization": f"Basic {token}",
        }
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                response = self._client.get(url, params=params, headers=headers)
            except httpx.TimeoutException as exc:
                last_error = exc
                if attempt == 3:
                    raise BcError("Business Central request timed out.") from exc
                time.sleep(min(8, 2 ** attempt))
                continue
            if response.status_code in {301, 302, 303, 307, 308}:
                raise BcError("Refused a redirect from Business Central.", response.status_code)
            if response.status_code in {401, 403, 404}:
                raise BcError(self._status_message(response), response.status_code)
            if response.status_code == 400:
                raise BcError(self._status_message(response), response.status_code)
            if response.status_code == 503 or response.status_code >= 500:
                last_error = BcError(self._status_message(response), response.status_code)
                retries = 1 if response.status_code == 500 else 3
                if attempt >= retries:
                    raise last_error
                time.sleep(min(8, 2 ** attempt))
                continue
            if response.status_code != 200:
                raise BcError(self._status_message(response), response.status_code)
            return response
        raise BcError("Business Central request failed.") from last_error

    def _status_message(self, response: httpx.Response) -> str:
        detail = ""
        try:
            body = parse_json_exact(response.text)
            if isinstance(body, dict):
                detail = str((body.get("error") or {}).get("message") or "")
        except ValueError:
            detail = ""
        return f"HTTP {response.status_code} {detail}".strip()

    def _assert_safe_link(self, url: str) -> None:
        parsed = urlparse(url)
        root = urlparse(self.api_root)
        if not scheme_allowed(url, self._allow_private_http):
            raise ConfigError("Refused a Business Central URL that is not HTTPS or a private VPN address. No credentials were sent on that URL.")
        if parsed.scheme != root.scheme:
            raise ConfigError("Refused a Business Central URL that changes scheme.")
        defaults = {"https": 443, "http": 80}
        same_port = (parsed.port or defaults.get(parsed.scheme)) == (root.port or defaults.get(root.scheme))
        if (parsed.hostname or "").lower() != (root.hostname or "").lower() or not same_port:
            raise ConfigError("Refused a Business Central URL that changes host or port.")
        if parsed.username or parsed.password:
            raise ConfigError("Refused a Business Central URL that carries a login.")
        if "odatav4" in (parsed.path or "").lower():
            raise ConfigError("Refused a Business Central URL that uses /ODataV4/.")
