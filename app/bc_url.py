"""Business Central base URL checks. The browser never supplies this URL."""

from __future__ import annotations

from urllib.parse import urlparse

from app.constants import API_ROUTE

SAMPLE_HOSTS = {"10.250.50.9", "bc-host.example.local"}


class ConfigError(Exception):
    pass


def resolve_api_root(base_url: str) -> str:
    raw = (base_url or "").strip()
    if not raw:
        raise ConfigError(
            "Business Central base URL is not set. Ask Elijah for the HTTPS base URL before calling Business Central."
        )
    if not raw.lower().startswith("https://"):
        raise ConfigError(
            "Business Central base URL must be HTTPS. The guide's HTTP sample is not used. No request was sent."
        )
    parsed = urlparse(raw.rstrip("/"))
    host = (parsed.hostname or "").lower()
    if host in SAMPLE_HOSTS:
        raise ConfigError(
            "That host is the guide's sample address. Ask Elijah for the live base URL. No request was sent."
        )
    path = parsed.path.rstrip("/")
    if "odatav4" in path.lower():
        raise ConfigError(
            "This URL contains /ODataV4/. The custom API is not appended to that path. Ask Elijah for the HTTPS custom API base URL. No request was sent."
        )
    if path.endswith(API_ROUTE):
        return raw.rstrip("/")
    if "/api/vgg/finance/" in path:
        raise ConfigError(
            "The base URL already contains a finance API route other than the one expected. Confirm it with Elijah. No request was sent."
        )
    return raw.rstrip("/") + API_ROUTE
