"""Bounded, non-replaying camera diagnostics over the production device owner."""
from collections import OrderedDict
from threading import Event, Lock
from uuid import uuid4
import json
from time import monotonic, time

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
        self.receiptIdentity = None
        self.watching = False
        self.watchSequence = 0
        self.observed = None
        self.observedAt = 0.0

    def capture(self, sequence, confirmed, cancelled, *, action='capture', changes=None):
        if not self.lock.acquire(blocking=False):
            raise DeviceError("E_DEVICE_BUSY", "camera diagnostic request is still running")
        try:
            identity = (action, json.dumps(changes or {}, sort_keys=True, allow_nan=False))
            if self.closed.is_set():
                raise DeviceError("E_DEVICE_CANCELLED", "camera diagnostic lease closed")
            if sequence == self.sequence and self.receipt is not None:
                if identity != self.receiptIdentity:
                    raise DeviceError('E_DEVICE_DIAGNOSTIC_SEQUENCE', 'sequence belongs to a different diagnostic request')
                return self.receipt
            if sequence != self.sequence + 1 or not confirmed:
                raise DeviceError("E_DEVICE_DIAGNOSTIC_SEQUENCE", "confirm one capture with the next sequence; expired requests cannot be replayed")
            self.sequence = sequence
            self.receiptIdentity = identity
            try:
                from emo_master.core.project.snapshots import revisionOf
                with self.manager.lock:
                    executor = self.manager.executors.get(self.deviceId)
                    definition = self.manager.definitions.get(self.deviceId)
                    if (self.closed.is_set() or executor is None or definition is None
                            or executor.generation != self.generation
                            or revisionOf(definition.model_dump()) != self.definitionRevision):
                        raise DeviceError('E_DEVICE_GENERATION', 'camera configuration changed; reopen diagnostics')
                    self.manager.authorizeMaintenance(self.owner, self.deviceId)
                def stopped():
                    return self.closed.is_set() or cancelled()
                if action in {'capture', 'preview_capture'}:
                    self.receipt = self._capture(stopped, previewOnly=action == 'preview_capture')
                else:
                    result = self.manager.cameraDiagnostic(self.owner, self.deviceId, action, changes or {}, stopped)
                    if action == 'read_parameters':
                        self.receipt = dict(ok=True, metadata=result)
                    else:
                        image, metadata = result
                        height, width = image.shape[:2]
                        if max(height, width) > 960:
                            scale = 960 / max(height, width)
                            image = cv2.resize(image, (max(1, round(width * scale)), max(1, round(height * scale))))
                        ok, preview = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 80])
                        if not ok or preview.nbytes > 2 * 1024 * 1024:
                            raise DeviceError('E_CAMERA_PREVIEW', 'cannot encode diagnostic preview', outcome='COMPLETED')
                        self.receipt = dict(ok=True, metadata=dict(acquisition=metadata), preview=preview.tobytes())
            except Exception as error:
                self.receipt = dict(ok=False, code=getattr(error, "code", "E_CAMERA_DIAGNOSTIC"), message=str(error),
                                    metadata=dict(outcome=getattr(error, "outcome", "UNKNOWN")))
            return self.receipt
        finally:
            executor = self.manager.executors.get(self.deviceId)
            if executor is not None:
                with executor.condition:
                    executor.condition.wait_for(lambda: executor.active is None or executor.active.owner != self.owner)
            if self.owner in self.manager.maintenanceOwners:
                self.manager.retire(self.owner)
            self.lock.release()

    def _capture(self, cancelled, *, previewOnly=False):
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
                                              cycleId=cycle["cycleId"], cancelled=cancelled, previewOnly=previewOnly)
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
        self.observed = None
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
        self.lock = Lock()
        manager.frameObserver = self.observe

    def watch(self, session, afterSequence):
        production = self.manager.productionOwners(session.deviceId)
        with self.lock:
            if session.closed.is_set():
                raise DeviceError('E_DEVICE_CANCELLED', 'camera observer is closed')
            session.watching = True
            result = session.observed or dict(metadata={}, preview=b'')
            metadata = dict(result['metadata'], productionActive=bool(production), productionOwners=production,
                            watchSequence=session.watchSequence)
            return dict(ok=True, metadata=metadata,
                        preview=result['preview'] if session.watchSequence != afterSequence else b'')

    def observe(self, deviceId, owner, image, descriptor):
        with self.lock:
            sessions = [s for s in tuple(self.sessions.values()) if s.deviceId == deviceId and s.watching
                        and not s.closed.is_set() and s.generation == descriptor['generation']]
            if not sessions or all(monotonic() - s.observedAt < .2 for s in sessions):
                return
        try:
            height, width = image.shape[:2]
            if max(height, width) > 960:
                scale = 960 / max(height, width)
                image = cv2.resize(image, (max(1, round(width * scale)), max(1, round(height * scale))))
            ok, preview = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if not ok or preview.nbytes > 2 * 1024 * 1024:
                return
            result = dict(preview=preview.tobytes(), metadata=dict(source='production', owner=owner,
                frameId=descriptor['frameId'], cycleId=descriptor['cycleId'], planRevision=descriptor['planRevision'],
                acquisition=descriptor.get('acquisition', {}), observedAtMs=int(time() * 1000)))
            with self.lock:
                for session in sessions:
                    if not session.closed.is_set():
                        session.observed, session.observedAt = result, monotonic()
                        session.watchSequence += 1
        except Exception:
            # A display-only failure cannot change a completed production capture.
            return

    def open(self, deviceId, definitionRevision, generation):
        session = CameraDiagnostic(self.manager, deviceId, definitionRevision, generation)
        with self.lock:
            for key, previous in list(self.sessions.items()):
                if previous.closed.is_set() and previous.owner not in self.manager.maintenanceOwners:
                    del self.sessions[key]
            if len(self.sessions) >= 8:
                session.close()
                raise DeviceError("E_DEVICE_DIAGNOSTIC_LIMIT", "close existing camera diagnostic sessions first")
            self.sessions[session.owner] = session
        return session.owner

    def close(self):
        for session in self.sessions.values():
            session.close()
