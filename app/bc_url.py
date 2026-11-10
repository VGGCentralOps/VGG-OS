"""Business Central base URL checks. The browser never supplies this URL."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

from app.constants import API_ROUTE

EXAMPLE_HOSTS = {"bc-host.example.local"}
ROUTE_PATTERN = re.compile(r"/api/vgg/[A-Za-z0-9_-]+/v1\.0\Z")


class ConfigError(Exception):
    pass


def private_ip_host(host: str) -> bool:
    """True only for an IP literal inside RFC 1918 space. Names are never trusted for plain HTTP."""
    try:
        address = ipaddress.ip_address((host or "").strip("[]"))
    except ValueError:
        return False
    if address.version != 4:
        return False
    return any(
        address in ipaddress.ip_network(block)
        for block in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    )


def scheme_allowed(url: str, allow_private_http: bool) -> bool:
    """HTTPS always. Plain HTTP only when opted in and the host is a private VPN address."""
    parsed = urlparse(url)
    if parsed.scheme == "https":
        return True
    return bool(allow_private_http) and parsed.scheme == "http" and private_ip_host(parsed.hostname or "")


def normalise_api_route(route: str | None) -> str:
    """Guide default is /api/vgg/finance/v1.0. BCTEST publishes /api/vgg/integration/v1.0."""
    raw = (route or "").strip() or API_ROUTE
    if not raw.startswith("/"):
        raw = "/" + raw
    raw = raw.rstrip("/")
    if "odata" in raw.lower() or not ROUTE_PATTERN.fullmatch(raw):
        raise ConfigError(
            "The API route must look like /api/vgg/finance/v1.0. No request was sent."
        )
    return raw


def resolve_api_root(base_url: str, api_route: str | None = None, allow_private_http: bool = False) -> str:
    route = normalise_api_route(api_route)
    raw = (base_url or "").strip()
    if not raw:
        raise ConfigError(
            "Business Central base URL is not set. Put VGGOS_BC_BASE_URL in the server secrets file."
        )
    if not scheme_allowed(raw, allow_private_http):
        if raw.lower().startswith("http://"):
            raise ConfigError(
                "Business Central base URL is plain HTTP. That is accepted only for a private VPN IP address "
                "with VGGOS_BC_ALLOW_PRIVATE_HTTP=1. No request was sent."
            )
        raise ConfigError("Business Central base URL must be HTTPS. No request was sent.")
    parsed = urlparse(raw.rstrip("/"))
    if parsed.username or parsed.password:
        raise ConfigError("The base URL must not contain a user name or password. No request was sent.")
    if parsed.query or parsed.fragment:
        raise ConfigError("The base URL must not contain a query string. No request was sent.")
    host = (parsed.hostname or "").lower()
    if host in EXAMPLE_HOSTS:
        raise ConfigError(
            "That host is the guide's example address. Ask Elijah for the live base URL. No request was sent."
        )
    path = parsed.path.rstrip("/")
    if "odatav4" in path.lower():
        raise ConfigError(
            "This URL contains /ODataV4/. The custom API is not appended to that path. No request was sent."
        )
    if path.endswith(route):
        return raw.rstrip("/")
    if "/api/vgg/" in path.lower():
        raise ConfigError(
            "The base URL already contains an API route. Send the instance base only, such as http://10.250.50.9:8048/BCTEST. No request was sent."
        )
    return raw.rstrip("/") + route
