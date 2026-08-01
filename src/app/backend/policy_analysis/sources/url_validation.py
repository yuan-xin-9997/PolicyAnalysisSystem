"""Shared URL and DNS-name validation for source boundaries."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import idna

_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


def normalize_dns_name(value: str) -> str:
    """Return a lowercase IDNA DNS name or reject malformed labels."""

    domain = value.strip().rstrip(".")
    if not domain:
        raise ValueError("invalid DNS name")
    try:
        ascii_domain = idna.encode(
            domain,
            uts46=True,
            std3_rules=True,
            transitional=False,
        ).decode("ascii")
    except idna.IDNAError:
        raise ValueError("invalid IDNA DNS name") from None
    ascii_domain = ascii_domain.lower()
    if len(ascii_domain) > 253:
        raise ValueError("DNS name is too long")
    labels = ascii_domain.split(".")
    if any(len(label) > 63 or _DNS_LABEL.fullmatch(label) is None for label in labels):
        raise ValueError("invalid DNS label")
    return ascii_domain


def normalized_http_hostname(value: str) -> str:
    """Validate an unambiguous HTTP(S) URL and return its normalized DNS host."""

    if any(character == "\\" or _is_control(character) for character in value):
        raise ValueError("URL contains an ambiguous character")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except (UnicodeError, ValueError):
        raise ValueError("invalid URL") from None
    del port
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("URL must use HTTP(S) without credentials")
    try:
        return normalize_dns_name(hostname)
    except (UnicodeError, ValueError):
        raise ValueError("invalid URL hostname") from None


def _is_control(character: str) -> bool:
    codepoint = ord(character)
    return codepoint <= 0x1F or 0x7F <= codepoint <= 0x9F
