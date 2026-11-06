"""Database URL helpers. Never log or print credentials."""

from __future__ import annotations

import os
from urllib.parse import urlparse, urlunparse


class DatabaseConfigError(ValueError):
    """Raised when DATABASE_URL is missing or malformed."""


def require_database_url(env: dict[str, str] | None = None) -> str:
    """Return DATABASE_URL from env or raise."""
    source = env if env is not None else os.environ
    url = (source.get("DATABASE_URL") or "").strip()
    if not url:
        raise DatabaseConfigError(
            "DATABASE_URL is required (postgresql://user@host:5432/mnemosyne). "
            "Do not commit secrets; set via environment or secret store."
        )
    return normalize_database_url(url)


def normalize_database_url(url: str) -> str:
    """Accept postgresql:// or postgres://; reject empty path/db name."""
    raw = url.strip()
    if raw.startswith("postgres://"):
        raw = "postgresql://" + raw[len("postgres://") :]
    if not raw.startswith("postgresql://"):
        raise DatabaseConfigError(
            "DATABASE_URL must start with postgresql:// or postgres://"
        )
    parsed = urlparse(raw)
    dbname = (parsed.path or "").lstrip("/")
    if not dbname:
        raise DatabaseConfigError("DATABASE_URL must include a database name path")
    if parsed.scheme != "postgresql":
        raise DatabaseConfigError("DATABASE_URL scheme must be postgresql")
    return raw


def redacted_database_url(url: str) -> str:
    """Safe string for logs/CLI — password replaced with ***."""
    parsed = urlparse(url)
    if parsed.password is None:
        return url
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    user = parsed.username or ""
    netloc = f"{user}:***@{host}" if user else f"***@{host}"
    return urlunparse(
        (parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)
    )
