"""Business Central base URL checks. The browser never supplies this URL."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from app.constants import API_ROUTE

EXAMPLE_HOSTS = {"bc-host.example.local"}
ROUTE_PATTERN = re.compile(r"/api/vgg/[A-Za-z0-9_-]+/v1\.0\Z")


class ConfigError(Exception):
    pass


def normalise_api_route(route: str | None) -> str:
    """Guide default is /api/vgg/finance/v1.0. Elijah's browser showed integration."""
    raw = (route or "").strip() or API_ROUTE
    if not raw.startswith("/"):
        raw = "/" + raw
    raw = raw.rstrip("/")
    if "odata" in raw.lower() or not ROUTE_PATTERN.fullmatch(raw):
        raise ConfigError(
            "The API route must look like /api/vgg/finance/v1.0. No request was sent."
        )
    return raw


def resolve_api_root(base_url: str, api_route: str | None = None) -> str:
    route = normalise_api_route(api_route)
    raw = (base_url or "").strip()
    if not raw:
        raise ConfigError(
            "Business Central base URL is not set. Ask Elijah for the HTTPS base URL before calling Business Central."
        )
    if not raw.lower().startswith("https://"):
        raise ConfigError(
            "Business Central base URL must be HTTPS. The HTTP address opened in the browser is not used. No request was sent."
        )
    parsed = urlparse(raw.rstrip("/"))
    host = (parsed.hostname or "").lower()
    if host in EXAMPLE_HOSTS:
        raise ConfigError(
            "That host is the guide's example address. Ask Elijah for the live HTTPS base URL. No request was sent."
        )
    path = parsed.path.rstrip("/")
    if "odatav4" in path.lower():
        raise ConfigError(
            "This URL contains /ODataV4/. The custom API is not appended to that path. Ask Elijah for the HTTPS custom API base URL. No request was sent."
        )
    if path.endswith(route):
        return raw.rstrip("/")
    if "/api/vgg/" in path.lower():
        raise ConfigError(
            "The base URL already contains an API route. Send the instance base only, such as https://host:port/BC230. No request was sent."
        )
    return raw.rstrip("/") + route
