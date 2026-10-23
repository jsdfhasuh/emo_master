"""Hikvision H8 AudioAlarm playback; never changes persistent configuration."""
from __future__ import annotations

import http.client
import json
from urllib import error, request

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException


AUDIO_PATH = "/ISAPI/Event/triggers/notifications/AudioAlarm"
MAX_RESPONSE = 1024 * 1024


class AudioError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise error.HTTPError(req.full_url, code, "Redirect refused", headers, fp)


class _SingleChallengeDigest(request.HTTPDigestAuthHandler):
    def __init__(self, passwords, checkCancelled):
        super().__init__(passwords)
        self.checkCancelled = checkCancelled

    def http_error_401(self, req, fp, code, msg, headers):
        try:
            self.checkCancelled()
        except BaseException:
            fp.close()
            raise
        if req.has_header("Authorization"):
            return None
        return super().http_error_401(req, fp, code, msg, headers)


class HikvisionAudioClient:
    def __init__(self, origin, username, password, timeout, checkCancelled):
        self.origin = origin.rstrip("/")
        self.timeout = timeout
        self.checkCancelled = checkCancelled
        passwords = request.HTTPPasswordMgrWithDefaultRealm()
        passwords.add_password(None, self.origin + "/", username, password)
        self.opener = request.build_opener(
            request.ProxyHandler({}), _NoRedirect(), _SingleChallengeDigest(passwords, checkCancelled),
        )

    def _request(self, method, suffix):
        self.checkCancelled()
        write = method == "PUT"
        req = request.Request(self.origin + AUDIO_PATH + suffix, method=method,
                              data=b"" if write else None,
                              headers={"Accept": "application/json, application/xml",
                                       "Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE + 1)
        except error.HTTPError as exc:
            exc.close()
            code = ("E_CAMERA_AUDIO_UNKNOWN" if write and exc.code >= 500 else
                    "E_CAMERA_AUTH" if exc.code in (401, 403) else "E_CAMERA_AUDIO_HTTP")
            raise AudioError(code, f"Camera returned HTTP {exc.code}; request not retried") from None
        except (OSError, error.URLError, http.client.HTTPException):
            code = "E_CAMERA_AUDIO_UNKNOWN" if write else "E_CAMERA_CONNECT"
            raise AudioError(code, "Camera connection failed; request not retried") from None
        try:
            if len(raw) > MAX_RESPONSE:
                raise ValueError
            if raw.lstrip().startswith(b"<"):
                root = ElementTree.fromstring(raw)
                data = {child.tag.split("}")[-1]: child.text for child in root}
            else:
                data = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError
        except (ValueError, UnicodeError, ElementTree.ParseError, DefusedXmlException):
            code = "E_CAMERA_AUDIO_UNKNOWN" if write else "E_CAMERA_RESPONSE"
            raise AudioError(code, "Camera response is unreadable; request not retried") from None
        status = data.get("ResponseStatus", data)
        if not isinstance(status, dict):
            status = {}
        if "statusCode" in status:
            accepted = str(status["statusCode"]) == "1"
            if not accepted:
                raise AudioError("E_CAMERA_AUDIO_REJECTED", "Camera rejected the audio request")
        else:
            accepted = str(status.get("statusString", "")).upper() == "OK"
        if write and not accepted:
            raise AudioError("E_CAMERA_AUDIO_UNKNOWN", "No explicit playback acknowledgement; request not retried")
        if not write:
            self.checkCancelled()
        return data

    def play(self, source, audioId, customId, maxVolume, maxAlarmTimes):
        cap = self._request("GET", "/capabilities?format=json").get("AudioAlarmCap")
        if not isinstance(cap, dict) or cap.get("isSupportAudioTest") is not True:
            raise AudioError("E_CAMERA_AUDIO_UNSUPPORTED", "Camera does not advertise AudioAlarm test support")
        if source == "custom":
            if cap.get("isSupportCustomAudio") is not True:
                raise AudioError("E_CAMERA_AUDIO_UNSUPPORTED", "Camera does not advertise custom audio support")
            info = self._request("GET", "/customAudioInfo?format=json").get("CustomAudioInfo", {})
            rows = info.get("CustomAudioList", []) if isinstance(info, dict) else []
            key, selected = "customAudioID", customId
            # Mapping verified in the H8 firmware's classToAudioID implementation.
            playbackId = 12 + customId
        else:
            rows = cap.get("audioTypeListCap", [])
            key, selected, playbackId = "audioID", audioId, audioId
        if not isinstance(rows, list) or not any(
                isinstance(row, dict) and str(row.get(key)) == str(selected) for row in rows):
            raise AudioError("E_CAMERA_AUDIO_MISSING", "Requested audio is unavailable or the custom slot is empty")
        settings = self._request("GET", "?format=json").get("AudioAlarm")
        try:
            volume, repeats = settings["audioVolume"], settings["alarmTimes"]
            if type(volume) is not int or type(repeats) is not int or volume < 1 or repeats < 1:
                raise ValueError
        except (TypeError, KeyError, ValueError):
            raise AudioError("E_CAMERA_RESPONSE", "Camera did not return valid volume and alarm count") from None
        if volume > maxVolume or repeats > maxAlarmTimes:
            raise AudioError("E_CAMERA_AUDIO_LIMIT",
                             f"Camera volume={volume}, alarmTimes={repeats} exceed accepted limits; "
                             "adjust acceptance limits or camera settings explicitly")
        self._request("PUT", f"/{playbackId}/test?format=json")
        return {"triggered": True, "audioId": playbackId, "volume": volume, "alarmTimes": repeats}
