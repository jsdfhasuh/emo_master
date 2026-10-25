"""One decoder per managed RTSP stream, with a single latest-frame slot."""
from __future__ import annotations

import hashlib
from threading import Condition, Event, Thread
from time import monotonic, time
from urllib.parse import quote, urlunsplit

import cv2
import numpy as np

from emo_master.core.contracts.device_driver import DeviceCaptureResult, DeviceDriver, DeviceError
from emo_master.plugins.builtins._network_camera import cameraUrl, credentials, validateCredentials


class ManagedRtsp(DeviceDriver):
    def __init__(self, definition):
        self.definition = definition
        self.condition = Condition()
        self.stop = Event()
        self.thread = None
        self.connected = False
        self.decoding = False
        self.latest = None
        self.sequence = 0
        self.delivered = 0
        self.error = None
        self.releaseFailed = False
        self.disposed = False

    @staticmethod
    def physicalIdentity(definition):
        parsed = cameraUrl(definition.connection['rtspUrl'], rtsp=True)
        validateCredentials(definition.connection)
        # Neither credentials nor signed query strings belong in diagnostics.
        endpoint = (parsed.scheme, parsed.hostname.lower(), parsed.port or
                    (322 if parsed.scheme == 'rtsps' else 554), parsed.path or '/', parsed.query)
        return 'rtsp://' + hashlib.sha256(repr(endpoint).encode('utf-8')).hexdigest()

    def frameBytes(self, params):
        return self.definition.connection['maxFrameBytes']

    @staticmethod
    def _check(cancelled, deadline, *, connecting=False):
        if cancelled():
            raise DeviceError('E_DEVICE_CANCELLED', 'RTSP operation cancelled')
        if monotonic() >= deadline:
            if connecting:
                raise DeviceError('E_CAMERA_CONNECT_TIMEOUT', 'RTSP connection timed out; check address and connection timeout')
            raise DeviceError('E_CAMERA_FRAME_TIMEOUT', 'No fresh RTSP frame before the operation timeout')

    def connect(self, params, cancelled, deadline):
        self._check(cancelled, deadline, connecting=True)
        with self.condition:
            if self.disposed:
                raise DeviceError('E_DEVICE_UNAVAILABLE', 'RTSP driver is disposed')
            if self.connected and not self.stop.is_set() and self.error is None:
                return
            if self.releaseFailed or self.thread is not None and self.thread.is_alive():
                raise DeviceError('E_DEVICE_RELEASE_PENDING', 'RTSP reader is still exiting; retry recovery after it exits',
                                  unhealthy=True)
            # Resolve the environment only at explicit connection time, not at registration.
            try:
                parsed = cameraUrl(self.definition.connection['rtspUrl'], rtsp=True)
                user, password = credentials(self.definition.connection)
            except ValueError as error:
                raise DeviceError('E_DEVICE_PARAMETERS', str(error)) from None
            authority = parsed.netloc
            if user or password:
                authority = f'{quote(user, safe="")}:{quote(password, safe="")}@{authority}'
            url = urlunsplit((parsed.scheme, authority, parsed.path, parsed.query, ''))
            timeout = max(1, min(self.definition.connection['openTimeoutMs'], int((deadline - monotonic()) * 1000)))
            self.stop.clear()
            self.error, self.latest = None, None
            self.decoding = False
            self.thread = Thread(target=self._read, args=(url, timeout), name='rtsp-reader', daemon=True)
            self.thread.start()
            try:
                while not self.connected:
                    self._check(cancelled, deadline, connecting=True)
                    if self.error is not None:
                        raise self.error
                    if self.stop.is_set():
                        raise DeviceError('E_DEVICE_CANCELLED', 'RTSP connection stopped')
                    self.condition.wait(min(.02, max(0, deadline - monotonic())))
                self._check(cancelled, deadline, connecting=True)
            except DeviceError:
                self.stop.set()
                self.condition.notify_all()
                raise

    def _read(self, url, openTimeout):
        capture = None
        phase = 'connect'
        failure = None
        try:
            capture = cv2.VideoCapture()
            opened = capture.open(url, cv2.CAP_FFMPEG, [
                cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, openTimeout,
                cv2.CAP_PROP_READ_TIMEOUT_MSEC, self.definition.connection['readTimeoutMs'],
            ])
            if self.stop.is_set():
                return
            if not opened:
                raise DeviceError('E_CAMERA_CONNECT', 'Cannot open RTSP stream; check address, credentials and codec')
            with self.condition:
                self.connected = True
                self.condition.notify_all()
                # Testing/opening the connection must not consume a frame.
                self.condition.wait_for(lambda: self.decoding or self.stop.is_set())
            phase = 'read'
            while not self.stop.is_set():
                ok, image = capture.read()
                received, capturedAt = monotonic(), time()
                if self.stop.is_set():
                    break
                if not ok or image is None or image.size == 0:
                    raise DeviceError('E_CAMERA_READ', 'RTSP stream disconnected or read timed out; reconnect the device',
                                      unhealthy=True)
                if (image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3):
                    raise DeviceError('E_CAMERA_FORMAT', 'RTSP decoder must return an 8-bit BGR image', unhealthy=True)
                if image.nbytes > self.frameBytes({}):
                    raise DeviceError('E_CAMERA_FRAME_SIZE', 'RTSP frame exceeds maxFrameBytes; increase the device limit',
                                      unhealthy=True)
                owned = image.copy()
                with self.condition:
                    if self.stop.is_set():
                        break
                    self.sequence += 1
                    self.latest = (owned, self.sequence, received, capturedAt)
                    self.condition.notify_all()
        except DeviceError as error:
            failure = error
        except Exception:
            failure = DeviceError('E_CAMERA_CONNECT' if phase == 'connect' else 'E_CAMERA_READ',
                                  'RTSP capture failed; check connectivity, credentials and decoder support',
                                  unhealthy=phase == 'read')
        finally:
            with self.condition:
                self.connected = False
                self.latest = None
                if failure is not None and not self.stop.is_set():
                    self.error = failure
                self.condition.notify_all()
            if capture is not None:
                try:
                    capture.release()
                except Exception:
                    with self.condition:
                        self.releaseFailed = True
                        self.error = DeviceError('E_DEVICE_RELEASE_PENDING', 'RTSP decoder release failed', unhealthy=True)
            with self.condition:
                self.condition.notify_all()

    def execute(self, action, params, cancelled, deadline, *, arguments, operationContext):
        if action != 'capture':
            raise DeviceError('E_DEVICE_ACTION', 'RTSP supports capture only')
        self.validateAction(action, arguments)
        with self.condition:
            self._check(cancelled, deadline)
            self.decoding = True
            self.condition.notify_all()
            while True:
                self._check(cancelled, deadline)
                if self.error is not None:
                    raise self.error
                if not self.connected or self.stop.is_set():
                    raise DeviceError('E_CAMERA_NOT_CONNECTED', 'RTSP device is not connected')
                latest = self.latest
                if latest is not None:
                    image, sequence, received, capturedAt = latest
                    age = monotonic() - received
                    if sequence > self.delivered and age * 1000 <= params['maxFrameAgeMs']:
                        self.delivered = sequence
                        break
                self.condition.wait(min(.02, max(0, deadline - monotonic())))
        image = (cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if params['colorMode'] == 'grayscale' else image.copy())
        self._check(cancelled, deadline)
        return DeviceCaptureResult(image, dict(capturedAt=capturedAt, sequence=sequence,
            receivedAgeMs=round(age * 1000, 3), sourceId=self.physicalIdentity(self.definition)))

    def requestStop(self, reason):
        with self.condition:
            self.stop.set()
            self.latest = None
            self.condition.notify_all()

    def close(self, deadline=None):
        self.requestStop('close')
        thread = self.thread
        if thread is not None:
            thread.join(max(0, (deadline if deadline is not None else monotonic() + 5) - monotonic()))
        with self.condition:
            if self.releaseFailed or thread is not None and thread.is_alive():
                raise DeviceError('E_DEVICE_RELEASE_PENDING', 'RTSP reader has not released its decoder yet',
                                  outcome='UNKNOWN', unhealthy=True)
            self.thread = None
            self.connected = self.decoding = False
            self.error = self.latest = None

    def snapshot(self):
        with self.condition:
            owns = self.releaseFailed or self.thread is not None and self.thread.is_alive()
            ready = self.connected and not self.stop.is_set() and self.error is None
            return dict(ready=ready, ownsResources=owns, connected=ready, sequence=self.sequence,
                        connectionState='FAULTED' if self.error else 'CONNECTED' if ready else 'DISCONNECTED',
                        state='FAILED' if self.error else 'STOPPING' if owns and self.stop.is_set() else
                              'STREAMING' if ready and self.decoding else 'CONNECTED' if ready else 'DISCONNECTED',
                        lastError=str(self.error) if self.error else '',
                        lastErrorCode=self.error.code if self.error else '',
                        cachedFrames=int(self.latest is not None))
