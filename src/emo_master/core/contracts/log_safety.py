"""Redact known credentials from copies prepared for logs and inspection."""
from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import unquote_plus

REDACTED = "<redacted>"
_SENSITIVE_KEY_PARTS = (
    "password", "secret", "token", "authorization", "credential", "apikey", "privatekey",
)
_CREDENTIAL_KEYS = {
    "password", "passwordenv", "passphrase", "secret", "secretkey", "apisecret", "clientsecret",
    "token", "apitoken", "authtoken", "accesstoken", "bearertoken", "refreshtoken", "idtoken",
    "authorization", "credential", "credentials", "apikey", "privatekey", "leaseid",
}
_URL_USER_INFO = re.compile(
    r"(?P<scheme>[a-zA-Z][a-zA-Z\d+.-]*://)[^/\s?#]+@(?=[^/\s?#]+)"
)
_URL_QUERY_SECRET = re.compile(
    r"([?&])([^=&#\s]+)=([^&#\s]*)"
)
_URL = re.compile(r"[a-zA-Z][a-zA-Z\d+.-]*://[^\s<>\x22\x27]+")
_AUTH_QUERY_KEYS = {"auth", "sig", "signature", "username", "x-amz-signature", "x-goog-signature"}


def _normalizedKey(key: str) -> str:
    return "".join(character for character in key.casefold() if character.isalnum())


def isCredentialKey(key: str) -> bool:
    # A TCP session lease ID authorizes follow-up operations on its live peer.
    return _normalizedKey(key) in _CREDENTIAL_KEYS


def isSensitiveLogKey(key: str) -> bool:
    normalized = _normalizedKey(key)
    return normalized == "leaseid" or any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def isSensitiveParameter(key: str, schema: Mapping) -> bool:
    return isCredentialKey(key) or schema.get("xSensitive") is True or schema.get("xWidget") == "password"


def redactLogText(text: str) -> str:
    if "://" not in text:
        return text

    def redactUrl(match: re.Match[str]) -> str:
        url = match.group()
        if url[:7].casefold() == "file://":
            return url
        url = _URL_USER_INFO.sub(r"\g<scheme><redacted>@", url, count=1)
        if url.casefold().startswith(("rtsp://", "rtsps://")) and "?" in url:
            path, _, tail = url.partition("?")
            query, separator, fragment = tail.partition("#")
            # Cameras may authenticate using an opaque query component with no name.
            query = "&".join(REDACTED if part and "=" not in part else part for part in query.split("&"))
            url = path + "?" + query + separator + fragment

        def redactQuery(query: re.Match[str]) -> str:
            key = unquote_plus(query.group(2)).casefold()
            if key in _AUTH_QUERY_KEYS or isCredentialKey(key):
                return query.group(1) + query.group(2) + "=" + REDACTED
            return query.group()

        return _URL_QUERY_SECRET.sub(redactQuery, url)

    return _URL.sub(redactUrl, text)


def redactLogValue(value: object, schema: Mapping | None = None) -> object:
    """Return a JSON-shaped display copy, leaving runtime values untouched."""
    schema = schema or {}
    if isSensitiveParameter("", schema):
        return REDACTED
    if isinstance(value, str):
        return redactLogText(value)
    if isinstance(value, Mapping):
        properties = schema.get("properties", {})
        return {
            key: REDACTED if isSensitiveParameter(str(key), properties.get(key, {}))
            else redactLogValue(item, properties.get(key, {}))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redactLogValue(item, schema.get("items", {})) for item in value]
    return value


def displayOutputUrls(value: object) -> object:
    """Copy JSON business data, masking authentication only within recognized URLs."""
    if isinstance(value, dict):
        return {key: displayOutputUrls(item) for key, item in value.items()}
    if isinstance(value, list):
        return [displayOutputUrls(item) for item in value]
    return redactLogText(value) if isinstance(value, str) else value
