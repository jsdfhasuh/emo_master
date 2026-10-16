from __future__ import annotations

from concurrent.futures import Future, TimeoutError
from copy import deepcopy
import json
import os
from pathlib import Path
import queue
import threading
import time
from typing import Any, Iterator
from uuid import uuid4

import cv2

from emo_master.plugins.builtins._huaray_imv import HuarayCameraError, IMV_OK


class ConfigurationSession:
    """One SDK owner thread for configuration, acquisition, and release."""

    def __init__(self, sessionId: str, projectId: str, workflowId: str, nodeId: str,
                 operator: Any, params: dict, journalRoot: Path, publisher: Any = None) -> None:
        self.sessionId, self.projectId = sessionId, projectId
        self.workflowId, self.nodeId = workflowId, nodeId
        self.operator, self.params = operator, params
        self.journalPath = journalRoot / (sessionId + ".json")
        self.publisher = publisher
        self.config: Any = None
        self.condition = threading.Condition()
        self.commands: queue.Queue = queue.Queue(maxsize=32)
        self.shutdown = threading.Event()
        self.ready: Future = Future()
        self.thread = threading.Thread(target=self._run, name="camera-config-" + sessionId, daemon=True)
        self.latest: Any = None
        self.sequence = 0
        self.generation = 0
        self.state = "connecting"
        self.error = ""
        self.errorCode = ""
        self.closed = False
        self.releaseError: str | None = None
        self.single = False
        self.pendingTrigger = False
        self.deadline = 0.0
        self.results: dict[str, tuple[str, dict]] = {}
        self._snapshot: dict = {}

    def start(self) -> None:
        self.thread.start()
        self.ready.result(timeout=15)

    def snapshot(self) -> dict:
        with self.condition:
            return deepcopy(self._snapshot)

    def request(self, action: str, payload: dict, identity: str, requestId: str) -> dict:
        try:
            encoded = json.dumps([action, payload, identity], sort_keys=True, allow_nan=False)
        except (ValueError, TypeError):
            return {"ok": False, "code": "E_PARAM_INVALID", "message": "request must contain finite JSON values"}
        if requestId in self.results:
            fingerprint, reply = self.results[requestId]
            if fingerprint == encoded:
                return deepcopy(reply)
            return {"ok": False, "code": "E_REQUEST_CONFLICT", "message": "request id reused with different content"}
        if self.closed or self.shutdown.is_set():
            return {"ok": False, "code": "E_CAMERA_SESSION_EXPIRED", "message": "configuration session is closed"}
        future: Future = Future()
        try:
            self.commands.put_nowait((action, deepcopy(payload), identity, requestId, future))
        except queue.Full:
            return {"ok": False, "code": "E_CAMERA_BUSY", "message": "configuration queue is full"}
        try:
            return future.result(timeout=15)
        except TimeoutError:
            return {"ok": False, "code": "E_CAMERA_RESULT_UNKNOWN",
                    "message": "request is still pending; query with the same request id, do not replay with a new id"}

    def close(self, timeoutSeconds: float = 3.0) -> str | None:
        if self.closed:
            if self.thread.is_alive():
                self.thread.join(max(0.0, timeoutSeconds))
            return self.releaseError or ("configuration worker did not stop" if self.thread.is_alive() else None)
        result = self.request("close", {}, str(self.snapshot().get("identity", "")), str(uuid4()))
        if not result.get("ok"):
            return str(result.get("message", "configuration close failed"))
        self.thread.join(timeoutSeconds)
        return "configuration worker did not stop" if self.thread.is_alive() else None

    def ownsResources(self) -> bool:
        return not self.closed or self.thread.is_alive() or self.releaseError is not None

    def terminate(self, timeoutSeconds: float = 3.0) -> str | None:
        # Process/service shutdown is not a decision to retain or restore.
        self.shutdown.set()
        if self.thread.ident is not None:
            self.thread.join(max(0.0, timeoutSeconds))
        return self.releaseError or ("configuration worker did not stop" if self.thread.is_alive() else None)

    def frames(self, context: object | None = None) -> Iterator:
        delivered = 0
        while True:
            isActive = getattr(context, "is_active", None)
            if callable(isActive) and not isActive():
                return
            with self.condition:
                self.condition.wait_for(lambda: self.closed or (self.latest is not None and self.latest.sequence > delivered), 0.2)
                frame = self.latest if self.latest is not None and self.latest.sequence > delivered else None
                if frame is not None:
                    delivered = frame.sequence
                elif self.closed:
                    return
            if frame is not None:
                yield frame

    def _persist(self, snapshot: dict) -> None:
        payload = {**snapshot, "sessionId": self.sessionId, "projectId": self.projectId,
                   "workflowId": self.workflowId, "nodeId": self.nodeId, "state": self.state,
                   "updatedAtMs": int(time.time() * 1000)}
        self.journalPath.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.journalPath.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.journalPath)

    def _publish(self, captured: Any = None) -> None:
        from emo_master.apps.runtime.preview.live import LivePreviewFrame, _previewImage
        jpeg, width, height, block, timestamp, exposure = b"", 0, 0, 0, 0, 0.0
        if captured is not None:
            preview = _previewImage(captured.image)
            ok, encoded = cv2.imencode(".jpg", preview, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            if not ok:
                raise RuntimeError("failed to encode camera preview")
            jpeg = encoded.tobytes()
            height, width = preview.shape[:2]
            block, timestamp = captured.blockId, captured.deviceTimestamp
            exposure = self.config.camera._getDouble("ExposureTime")
        self.sequence += 1
        with self.condition:
            self._snapshot = {**self.config.snapshot(), "sessionId": self.sessionId,
                              "state": self.state, "generation": self.generation, "sequence": self.sequence,
                              "code": self.errorCode, "message": self.error}
            self.latest = LivePreviewFrame(self.sessionId, jpeg, self.sequence, block, timestamp,
                                           exposure, width, height, self.state, self.generation,
                                           self.errorCode, self.error)
            self.condition.notify_all()

    def _fault(self, error: Exception) -> None:
        self.state, self.error = "fault", str(error)
        self.errorCode = str(getattr(error, "code", "E_CAMERA_IO"))
        self.config.failed = True
        if self.errorCode in {"E_CAMERA_DISCONNECTED", "E_CAMERA_IDENTITY"}:
            self.config.actual = {}
            self.config.properties = {}
        self.pendingTrigger = False
        try:
            self.config.camera.stopAcquisition()
        except Exception as cleanup:
            self.error += "; stop failed: " + str(cleanup)
        try:
            self._persist(self.config.snapshot())
        except Exception as journalError:
            self.error += "; adjustment journal failed: " + str(journalError)
        self._publish()
        self._log("ERROR", self.error, self.errorCode)

    def _log(self, level: str, message: str, code: str = "", **payload: Any) -> None:
        if self.publisher is not None:
            try:
                self.publisher(jobId="__runtime__", eventType="camera.configuration", level=level,
                               code=code, message=message, projectId=self.projectId, workflowId=self.workflowId,
                               nodeId=self.nodeId, payload={"sessionId": self.sessionId,
                                                          "identity": self.config.identity, **payload})
            except Exception:
                pass

    def _run(self) -> None:
        try:
            self.config = self.operator.createConfiguration(self.params, self._persist)
            self.config.connect()
            self.state = "connected"
            self._publish()
            self.ready.set_result(True)
            nextFrame = 0.0
            nextHealth = time.monotonic() + 1.0
            while not self.shutdown.is_set():
                try:
                    command = self.commands.get(timeout=0.01)
                except queue.Empty:
                    command = None
                if command:
                    self._command(*command)
                    continue
                camera = self.config.camera
                now = time.monotonic()
                if now >= nextHealth and self.state != "fault":
                    nextHealth = now + 1
                    try:
                        self.config.checkIdentity()
                    except Exception as err:
                        self._fault(err)
                mode = camera.settings["triggerMode"]
                if not camera.grabbing or self.state == "fault" or (mode == "software" and not self.pendingTrigger) or now < nextFrame:
                    continue
                try:
                    captured = camera.capture(100, str(self.config.software["outputColor"]),
                                              str(self.config.software["demosaic"]), sendSoftwareTrigger=False)
                    self.pendingTrigger = False
                    self.deadline = time.monotonic() + float(self.config.software["captureTimeoutMs"]) / 1000
                    self.state = "waitingNext" if mode == "hardware" else "softwareReady" if mode == "software" else "acquiring"
                    if self.single:
                        camera.stopAcquisition()
                        self.single = False
                        self.state = "connected"
                    self._publish(captured)
                    nextFrame = time.monotonic() + 0.1
                except HuarayCameraError as err:
                    failure: Exception = err
                    if err.code == "E_CAMERA_TIMEOUT":
                        try:
                            self.config.checkIdentity()
                            if mode == "hardware":
                                continue
                            if time.monotonic() < self.deadline:
                                continue
                        except Exception as healthError:
                            failure = healthError
                    self._fault(failure)
                except Exception as err:
                    self._fault(err)
        except Exception as err:
            if not self.ready.done():
                self.ready.set_exception(err)
        finally:
            if self.config is not None:
                try:
                    self.config.close()
                except Exception as err:
                    self.error = str(err)
                    self.releaseError = str(err)
            self.closed = True
            with self.condition:
                self.condition.notify_all()
            while not self.commands.empty():
                *_, future = self.commands.get_nowait()
                future.set_result({"ok": False, "code": "E_CAMERA_SESSION_EXPIRED", "message": "session closed"})

    def _command(self, action: str, payload: dict, identity: str, requestId: str, future: Future) -> None:
        fingerprint = json.dumps([action, payload, identity], sort_keys=True, allow_nan=False)
        if action != "read" and not requestId:
            future.set_result({"ok": False, "code": "E_PARAM_INVALID", "message": "request id is required"})
            return
        if requestId in self.results:
            previous, result = self.results[requestId]
            future.set_result(deepcopy(result) if previous == fingerprint else
                              {"ok": False, "code": "E_REQUEST_CONFLICT", "message": "request id reused with different content"})
            return
        if action not in {"read", "stop", "recover", "close"} and len(self.results) >= 4096:
            future.set_result({"ok": False, "code": "E_CAMERA_SESSION_LIMIT", "message": "finish this session before further edits"})
            return
        firstRecord = len(self.config.records)
        try:
            if action != "read" and identity != self.config.identity:
                raise HuarayCameraError("E_CAMERA_IDENTITY", "request targets a different device")
            self._dispatch(action, payload)
            self._publish()
            result = {"ok": True, "snapshot": self.snapshot()}
        except Exception as err:
            self._fault(err)
            result = {"ok": False, "code": self.errorCode, "message": self.error, "snapshot": self.snapshot()}
        for record in self.config.records[firstRecord:]:
            self._log("INFO" if record["status"] == "confirmed" else "ERROR", "camera feature adjustment", record=record)
        if action != "read":
            self.results[requestId] = (fingerprint, deepcopy(result))
        future.set_result(result)

    def _dispatch(self, action: str, payload: dict) -> None:
        camera = self.config.camera
        if action == "read":
            self.config.refresh()
        elif action == "apply":
            self.generation += 1
            self.pendingTrigger = False
            self.config.apply(payload)
            self.state = self._acquiringState() if camera.grabbing else "connected"
            self.deadline = time.monotonic() + float(self.config.software["captureTimeoutMs"]) / 1000
        elif action == "start":
            if self.config.failed:
                raise HuarayCameraError("E_CAMERA_SESSION_FAULT", "verify the failed session before restarting")
            camera.stopAcquisition()
            self.pendingTrigger = False
            self.config.apply({"triggerMode": payload.get("triggerMode", "freeRun")})
            self.config.prepareAcquisition()
            camera.settings.update(self.config.actualParams())
            camera.clearBuffer()
            camera.startAcquisition()
            self.generation += 1
            self.single = bool(payload.get("single", False))
            self.state = self._acquiringState()
            self.deadline = time.monotonic() + float(self.config.software["captureTimeoutMs"]) / 1000
        elif action == "trigger":
            if self.config.failed or not camera.grabbing or camera.settings["triggerMode"] != "software" or self.pendingTrigger:
                raise HuarayCameraError("E_CAMERA_BUSY", "software trigger is not ready")
            self.config.checkIdentity()
            self.pendingTrigger = True
            self.state = "softwareWaiting"
            result = camera.api.execute_command(camera.handle, "TriggerSoftware")
            if result != IMV_OK:
                raise HuarayCameraError("E_CAMERA_IO", f"software trigger failed ({result}); not retried")
            self.deadline = time.monotonic() + float(self.config.software["captureTimeoutMs"]) / 1000
        elif action in {"stop", "recover"}:
            camera.stopAcquisition()
            self.pendingTrigger = False
            self.single = False
            self.generation += 1
            if action == "recover":
                self.config.recover()
                self.error, self.errorCode = "", ""
            self.state = "fault" if self.config.failed else "connected"
        elif action == "close":
            decision = payload.get("decision", "")
            if decision not in {"", "restore", "retain", "abandon"}:
                raise HuarayCameraError("E_PARAM_INVALID", "invalid close decision")
            if decision != "abandon":
                camera.stopAcquisition()
            if decision == "restore":
                self.config.restore()
            elif decision == "retain":
                self.config.retain()
            elif decision == "abandon":
                self.state = "abandoned"
                self._persist(self.config.snapshot())
            elif self.config.pending:
                raise HuarayCameraError("E_CAMERA_ADJUSTMENTS_PENDING", "camera adjustments need retain/restore confirmation")
            try:
                self.config.close()
            except Exception as err:
                if decision != "abandon" or camera.handle is not None:
                    raise
                self._log("WARNING", "abandoned adjustments; camera cleanup reported: " + str(err))
            self.state = "closed" if decision != "abandon" else "abandoned"
            self._persist(self.config.snapshot())
            self.shutdown.set()
        else:
            raise HuarayCameraError("E_PARAM_INVALID", "unsupported configuration action")

    def _acquiringState(self) -> str:
        return {"hardware": "waiting", "software": "softwareReady", "freeRun": "acquiring"}[self.config.camera.settings["triggerMode"]]
