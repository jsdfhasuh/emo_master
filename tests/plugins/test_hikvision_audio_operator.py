from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from urllib.request import parse_http_list, parse_keqv_list

import pytest

from emo_master.apps.runtime.workflow.cancellation import CancellationRequested, CancellationToken
from emo_master.plugins.builtins.hikvision_audio import client as module
from emo_master.plugins.builtins.hikvision_audio.operator import HikvisionAudioOperator


PARAMS = {"cameraUrl": "http://camera.invalid", "password": "fixture-only"}
CAPABILITIES = {"AudioAlarmCap": {"isSupportAudioTest": True, "isSupportCustomAudio": True,
                                "audioTypeListCap": [{"audioID": 12, "audioDescription": "Tone"}]}}
SETTINGS = {"AudioAlarm": {"audioVolume": 20, "alarmTimes": 1}}
CUSTOM = {"CustomAudioInfo": {"CustomAudioList": [{"customAudioID": 2, "customAudioName": "Fixture"}]}}


class Response:
    def __init__(self, value):
        self.raw = value if isinstance(value, bytes) else json.dumps(value).encode()
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def read(self, size):
        return self.raw[:size]


class Opener:
    def __init__(self, *, cap=CAPABILITIES, settings=SETTINGS, custom=CUSTOM, acknowledgement=None):
        self.cap, self.settings, self.custom = cap, settings, custom
        self.acknowledgement = {"statusCode": 1, "statusString": "OK"} if acknowledgement is None else acknowledgement
        self.calls = []
    def open(self, req, timeout):
        self.calls.append((req.get_method(), req.full_url, req.data, timeout))
        if req.get_method() == "PUT":
            if isinstance(self.acknowledgement, Exception):
                raise self.acknowledgement
            return Response(self.acknowledgement)
        if "/capabilities?" in req.full_url:
            return Response(self.cap)
        if "/customAudioInfo?" in req.full_url:
            return Response(self.custom)
        return Response(self.settings)


def execute(monkeypatch, opener=None, params=None, inputs=None, context=None):
    opener = opener or Opener()
    monkeypatch.setattr(module.request, "build_opener", lambda *_: opener)
    result = HikvisionAudioOperator().executeNode(inputs or {}, {**PARAMS, **(params or {})}, context or {})
    return result, opener.calls


@pytest.mark.parametrize("source,selected", [("builtin", 12), ("custom", 14)])
def testPlaybackOnlyWritesOneEmptyBodyTestRequest(monkeypatch, source, selected):
    result, calls = execute(monkeypatch, params={"audioSource": source, "customAudioId": 2})
    assert result["outputs"] == {"triggered": True, "audioId": selected, "volume": 20, "alarmTimes": 1}
    puts = [call for call in calls if call[0] == "PUT"]
    assert puts == [("PUT", PARAMS["cameraUrl"] + module.AUDIO_PATH + f"/{selected}/test?format=json", b"", 5)]
    assert len(calls) == (4 if source == "custom" else 3)


def testFalseTriggerDoesNotReadCredentialsOrConnect(monkeypatch):
    result, calls = execute(monkeypatch, inputs={"trigger": False}, params={"passwordEnv": "UNSET_TEST_SECRET"})
    assert not result["outputs"]["triggered"] and not calls


@pytest.mark.parametrize("value", [0, 1, "false", None])
def testTriggerMustBeBoolean(monkeypatch, value):
    result, calls = execute(monkeypatch, inputs={"trigger": value})
    assert result["error"]["code"] == "E_INPUT_TYPE" and not calls


@pytest.mark.parametrize("ack", [
    {"ResponseStatus": {"statusCode": 1}},
    b'<ResponseStatus xmlns="urn:hikvision"><statusCode>1</statusCode><statusString>OK</statusString></ResponseStatus>',
])
def testJsonAndXmlAcknowledgements(monkeypatch, ack):
    result, _ = execute(monkeypatch, Opener(acknowledgement=ack))
    assert result["outputs"]["triggered"]


@pytest.mark.parametrize("ack,code", [
    (TimeoutError("private-url"), "E_CAMERA_AUDIO_UNKNOWN"), (b"not json", "E_CAMERA_AUDIO_UNKNOWN"),
    ({}, "E_CAMERA_AUDIO_UNKNOWN"), ({"statusCode": 4}, "E_CAMERA_AUDIO_REJECTED"),
    ({"ResponseStatus": {"statusCode": 4}}, "E_CAMERA_AUDIO_REJECTED"),
    (b"x" * (module.MAX_RESPONSE + 1), "E_CAMERA_AUDIO_UNKNOWN"),
], ids=["timeout", "invalid-json", "missing-status", "rejected", "nested-rejected", "oversize"])
def testUnknownOrRejectedPlaybackIsNeverRetried(monkeypatch, ack, code):
    result, calls = execute(monkeypatch, Opener(acknowledgement=ack))
    assert result["error"]["code"] == code
    assert len([c for c in calls if c[0] == "PUT"]) == 1
    assert "private-url" not in str(result)
    assert result["diagnostics"]["automaticRetry"] is False


@pytest.mark.parametrize("opener,params,code", [
    (Opener(cap={"AudioAlarmCap": {}}), {}, "E_CAMERA_AUDIO_UNSUPPORTED"),
    (Opener(), {"audioId": 5}, "E_CAMERA_AUDIO_MISSING"),
    (Opener(), {"audioSource": "custom", "customAudioId": 1}, "E_CAMERA_AUDIO_MISSING"),
    (Opener(settings={"AudioAlarm": {"audioVolume": 100, "alarmTimes": 5}}), {}, "E_CAMERA_AUDIO_LIMIT"),
    (Opener(settings={}), {}, "E_CAMERA_RESPONSE"),
])
def testPreflightFailuresNeverSendPlayback(monkeypatch, opener, params, code):
    result, calls = execute(monkeypatch, opener, params)
    assert result["error"]["code"] == code
    assert all(c[0] == "GET" for c in calls)


def testAcceptedLimitsDoNotChangeCameraSettings(monkeypatch):
    result, calls = execute(monkeypatch, Opener(settings={"AudioAlarm": {"audioVolume": 100, "alarmTimes": 5}}),
                            {"maxVolume": 100, "maxAlarmTimes": 5})
    assert result["outputs"]["volume"] == 100 and result["outputs"]["alarmTimes"] == 5
    assert all("/test?" in url for method, url, _, _ in calls if method == "PUT")


@pytest.mark.parametrize("params", [
    {"cameraUrl": "http://user:secret@camera.invalid"}, {"cameraUrl": "http://camera.invalid/path"},
    {"audioId": 13}, {"customAudioId": 4, "audioSource": "custom"}, {"audioSource": "other"},
    {"maxVolume": 101}, {"maxAlarmTimes": 0}, {"timeoutMs": True}, {"username": ""}, {"password": ""},
])
def testInvalidParamsNeverConnect(monkeypatch, params):
    result, calls = execute(monkeypatch, params=params)
    assert result["error"]["code"] == "E_PARAM_INVALID" and not calls
    assert "secret" not in result["error"]["message"]


@pytest.mark.parametrize("afterReads", [0, 1, 2])
def testCancellationDoesNotSendLatePlayback(monkeypatch, afterReads):
    token, opener = CancellationToken(), Opener()
    if afterReads == 0:
        token.cancel()
    original = opener.open
    def readThenCancel(req, timeout):
        response = original(req, timeout)
        if len(opener.calls) == afterReads:
            token.cancel()
        return response
    opener.open = readThenCancel
    with pytest.raises(CancellationRequested):
        execute(monkeypatch, opener, context={"raiseIfCancellationRequested": token.raise_if_cancelled})
    assert len(opener.calls) == afterReads and all(c[0] == "GET" for c in opener.calls)


@contextmanager
def localCamera(*, password="fixture-only", putStatus=200, putBody=None, onChallenge=None):
    calls, played = [], []
    def md5(value):
        return hashlib.md5(value.encode()).hexdigest()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            self.handleCamera()
        def do_PUT(self):
            self.handleCamera()
        def handleCamera(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            auth = self.headers.get("Authorization", "")
            calls.append((self.command, self.path, bool(auth)))
            valid = False
            if auth.startswith("Digest "):
                parts = parse_keqv_list(parse_http_list(auth[7:]))
                ha1 = md5(f"admin:fixture:{password}")
                ha2 = md5(f"{self.command}:{self.path}")
                expected = md5(f"{ha1}:nonce:{parts['nc']}:{parts['cnonce']}:auth:{ha2}")
                valid = parts["response"] == expected and parts["uri"] == self.path
            if not valid:
                if onChallenge:
                    onChallenge(self.command)
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Digest realm="fixture", nonce="nonce", qop="auth", algorithm=MD5')
                self.end_headers()
                return
            if self.command == "PUT":
                assert self.headers["Content-Type"] == "application/json"
                played.append((self.path, body))
                status, value = putStatus, (putBody if putBody is not None else {"statusCode": 1})
            else:
                status = 200
                value = CAPABILITIES if "/capabilities?" in self.path else CUSTOM if "/customAudioInfo?" in self.path else SETTINGS
            raw = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls, played
    finally:
        server.shutdown()
        server.server_close()
        worker.join(3)


@pytest.mark.parametrize("putStatus,putBody,expected", [
    (200, None, "ok"), (200, b"lost acknowledgement", "E_CAMERA_AUDIO_UNKNOWN"),
    (500, {}, "E_CAMERA_AUDIO_UNKNOWN"), (403, {}, "E_CAMERA_AUTH"),
])
def testRealHttpDigestExchangeOnlyPlaysOnce(putStatus, putBody, expected):
    with localCamera(putStatus=putStatus, putBody=putBody) as (url, calls, played):
        result = HikvisionAudioOperator().executeNode({}, {**PARAMS, "cameraUrl": url}, {})
    assert (result["status"] if expected == "ok" else result["error"]["code"]) == expected
    assert played == [(module.AUDIO_PATH + "/12/test?format=json", b"")]
    assert len(calls) == 6


def testWrongPasswordStopsAfterOneChallenge():
    with localCamera(password="different") as (url, calls, played):
        result = HikvisionAudioOperator().executeNode({}, {**PARAMS, "cameraUrl": url}, {})
    assert result["error"]["code"] == "E_CAMERA_AUTH"
    assert len(calls) == 2 and not played


def testCancellationDuringAuthenticationStopsAuthenticatedRequest():
    token = CancellationToken()
    with localCamera(onChallenge=lambda _: token.cancel()) as (url, calls, played):
        with pytest.raises(CancellationRequested):
            HikvisionAudioOperator().executeNode({}, {**PARAMS, "cameraUrl": url},
                                                  {"raiseIfCancellationRequested": token.raise_if_cancelled})
    assert len(calls) == 1 and not played


def testCancellationDuringPlaybackChallengeDoesNotSendAuthenticatedPut():
    token = CancellationToken()
    def challenge(method):
        if method == "PUT":
            token.cancel()
    with localCamera(onChallenge=challenge) as (url, calls, played):
        with pytest.raises(CancellationRequested):
            HikvisionAudioOperator().executeNode({}, {**PARAMS, "cameraUrl": url},
                                                  {"raiseIfCancellationRequested": token.raise_if_cancelled})
    assert len(calls) == 5 and calls[-1][0] == "PUT" and not played
