"""Connection parameters shared by the network-camera operators."""
from __future__ import annotations

import os
from urllib.parse import urlsplit


CREDENTIAL_PROPERTIES = {
    "username": {"title": "\u7528\u6237\u540d", "type": "string", "default": "admin"},
    "password": {"title": "\u5bc6\u7801", "type": "string", "default": "", "xWidget": "password"},
    "passwordEnv": {"title": "\u5bc6\u7801\u73af\u5883\u53d8\u91cf", "type": "string", "default": ""},
}


def cameraUrl(value, *, rtsp=False):
    key = "rtspUrl" if rtsp else "cameraUrl"
    try:
        if not isinstance(value, str) or not value or any(ord(c) <= 32 for c in value):
            raise ValueError
        parsed = urlsplit(value)
        if (parsed.scheme not in (("rtsp", "rtsps") if rtsp else ("http", "https"))
                or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.fragment):
            raise ValueError
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError
        if not rtsp and (parsed.path not in ("", "/") or parsed.query):
            raise ValueError
    except ValueError:
        # Never echo a possibly credential-bearing URL in an error.
        raise ValueError(f"{key} requires a valid {'RTSP URL' if rtsp else 'HTTP(S) origin'} without credentials") from None
    return parsed


def validateCredentials(params):
    for key in CREDENTIAL_PROPERTIES:
        if not isinstance(params.get(key, "admin" if key == "username" else ""), str):
            raise ValueError(f"{key} must be a string")
    if params.get("passwordEnv", "") and ("=" in params["passwordEnv"] or "\x00" in params["passwordEnv"]):
        raise ValueError("passwordEnv must be an environment variable name")


def credentials(params):
    validateCredentials(params)
    env = params.get("passwordEnv", "")
    password = os.environ.get(env) if env else params.get("password", "")
    if env and not password:
        raise ValueError("passwordEnv is not set in the Runtime process")
    return params.get("username", "admin"), password


def integerParam(params, key, default, minimum, maximum):
    value = params.get(key, default)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{key} must be an integer in {minimum}..{maximum}")
    return value


def errorResult(code, message):
    return {"status": "error", "error": {"code": code, "message": message}}
