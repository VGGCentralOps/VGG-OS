"""Redact secrets before they reach a log, a notice, or a run error."""

from __future__ import annotations

import logging
import re


_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+")
_SECRET_FIELD = re.compile(
    r'(?i)("(?:password|access_token|refresh_token|apikey)"\s*:\s*")[^"]*(")'
)


def scrub(text: str) -> str:
    cleaned = _SECRET_FIELD.sub(r"\1[redacted]\2", text or "")
    cleaned = _JWT.sub("[redacted]", cleaned)
    cleaned = re.sub(r"(?i)authorization:\s*\S+", "Authorization: [redacted]", cleaned)
    cleaned = re.sub(r"(?i)bearer\s+\S+", "Bearer [redacted]", cleaned)
    cleaned = re.sub(r"(?i)basic\s+[A-Za-z0-9+/=]+", "Basic [redacted]", cleaned)
    return cleaned


def redact(text: str, secrets: list[str]) -> str:
    cleaned = scrub(text)
    for secret in secrets:
        if secret:
            cleaned = cleaned.replace(secret, "[redacted]")
    return cleaned[:2000]


class ScrubFilter(logging.Filter):
    """Drop tokens and passwords if a log line is formatted with them."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = scrub(record.msg)
        if isinstance(record.args, dict):
            record.args = {
                key: scrub(value) if isinstance(value, str) else value
                for key, value in record.args.items()
            }
        elif isinstance(record.args, tuple):
            record.args = tuple(scrub(value) if isinstance(value, str) else value for value in record.args)
        return True
