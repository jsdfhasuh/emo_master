"""Bounded, non-replaying camera diagnostics over the production device owner."""
from collections import OrderedDict
from threading import Event, Lock
from uuid import uuid4

import cv2

from .executor import DeviceError
from .frame_store import copyFrame


class CameraDiagnostic:
    def __init__(self, manager, deviceId, definitionRevision, generation):
        self.manager, self.deviceId = manager, deviceId
        self.definitionRevision, self.generation = definitionRevision, generation
        self.owner = "camera-debug-" + uuid4().hex
        self.closed = Event()
        self.lock = Lock()
        self.sequence = 0
        self.receipt = None
        manager.authorizeMaintenance(self.owner, deviceId)

    def capture(self, sequence, confirmed, cancelled):
        if not self.lock.acquire(blocking=False):
            raise DeviceError("E_DEVICE_BUSY", "camera diagnostic request is still running")
        try:
            if self.closed.is_set():
                raise DeviceError("E_DEVICE_CANCELLED", "camera diagnostic lease closed")
            if sequence == self.sequence and self.receipt is not None:
                return self.receipt
            if sequence != self.sequence + 1 or not confirmed:
                raise DeviceError("E_DEVICE_DIAGNOSTIC_SEQUENCE", "confirm one capture with the next sequence; expired requests cannot be replayed")
            self.sequence = sequence
            try:
                self.receipt = self._capture(lambda: self.closed.is_set() or cancelled())
            except Exception as error:
                self.receipt = dict(ok=False, code=getattr(error, "code", "E_CAMERA_DIAGNOSTIC"), message=str(error),
                                    metadata=dict(outcome=getattr(error, "outcome", "UNKNOWN")))
            return self.receipt
        finally:
            self.lock.release()

    def _capture(self, cancelled):
        config = self.manager.configuration
        cycle = config.beginCycle(self.owner, -1, cancelled) if config and config.current else {"cycleId": self.owner}
        descriptor = None
        try:
            if "payload" in cycle:
                from emo_master.core.project.devices import DeviceDefinition
                definition = DeviceDefinition.model_validate(cycle["payload"]["devices"]["definitions"][self.deviceId])
                definition = self.manager.types[definition.deviceTypeId].validateDefinition(definition)
            else:
                definition = self.manager.definitions[self.deviceId]
            descriptor = self.manager.execute(self.owner, self.deviceId, "capture", definition.defaults,
                                              cycleId=cycle["cycleId"], cancelled=cancelled)
            image = copyFrame(descriptor)
            height, width = image.shape[:2]
            if max(height, width) > 960:
                scale = 960 / max(height, width)
                image = cv2.resize(image, (max(1, round(width * scale)), max(1, round(height * scale))))
            ok, preview = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if not ok or preview.nbytes > 2 * 1024 * 1024:
                raise DeviceError("E_CAMERA_PREVIEW", "cannot encode bounded diagnostic preview", outcome="COMPLETED")
            return dict(ok=True, metadata=descriptor, preview=preview.tobytes())
        finally:
            executor = self.manager.executors[self.deviceId]
            with executor.condition:
                # Cancellation of the RPC is not native completion. The Host
                # adapter owns its bounded cancellation/termination path.
                executor.condition.wait_for(lambda: executor.active is None or executor.active.owner != self.owner)
            if descriptor:
                self.manager.frames.release(descriptor["frameId"], self.owner)
            # Caller abandonment may have published a now-unclaimed frame.
            self.manager.frames.retireOwner(self.owner)
            if config and config.current:
                config.endCycle(self.owner, cycle["cycleId"])
            if self.closed.is_set():
                self.manager.retire(self.owner)

    def close(self):
        self.closed.set()
        self.manager.revoke(self.owner)
        if self.lock.acquire(blocking=False):
            try:
                self.manager.retire(self.owner)
                self.receipt = None
            finally:
                self.lock.release()


class CameraDiagnostics:
    def __init__(self, manager):
        self.manager = manager
        self.sessions = OrderedDict()

    def open(self, deviceId, definitionRevision, generation):
        for key, session in list(self.sessions.items()):
            if session.closed.is_set() and session.owner not in self.manager.maintenanceOwners:
                del self.sessions[key]
        if len(self.sessions) >= 8:
            raise DeviceError("E_DEVICE_DIAGNOSTIC_LIMIT", "close existing camera diagnostic sessions first")
        session = CameraDiagnostic(self.manager, deviceId, definitionRevision, generation)
        self.sessions[session.owner] = session
        return session.owner

    def close(self):
        for session in self.sessions.values():
            session.close()
