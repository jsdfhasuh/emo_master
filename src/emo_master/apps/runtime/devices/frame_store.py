"""Runtime-owned originals with bounded reservations and explicit owner leases."""
from dataclasses import dataclass, field
from multiprocessing.shared_memory import SharedMemory
from threading import RLock
from uuid import uuid4

import numpy as np

from .executor import DeviceError


@dataclass
class Frame:
    memory: SharedMemory
    descriptor: dict
    owners: set[str] = field(default_factory=set)


class FrameStore:
    def __init__(self, *, maxFrames=32, maxBytes=256 * 1024 * 1024):
        if maxFrames < 1 or maxBytes < 1:
            raise ValueError("frame limits must be positive")
        self.maxFrames, self.maxBytes = maxFrames, maxBytes
        self.lock = RLock()
        self.frames = {}
        self.reservations = {}
        self.writes = {}
        self.closed = False

    def reserve(self, owner, size):
        with self.lock:
            if self.closed or type(size) is not int or size <= 0:
                raise DeviceError("E_FRAME_CAPACITY", "invalid frame reservation")
            used = sum(frame.descriptor["nbytes"] for frame in self.frames.values())
            used += sum(item[1] for item in self.reservations.values())
            if len(self.frames) + len(self.reservations) >= self.maxFrames or used + size > self.maxBytes:
                raise DeviceError("E_FRAME_CAPACITY", "original frame capacity exhausted")
            key = uuid4().hex
            self.reservations[key] = (owner, size)
            return key

    def discardReservation(self, reservation):
        with self.lock:
            self.reservations.pop(reservation, None)

    def publish(self, reservation, image, *, cycleId, generation, requestId, planRevision=0, metadata=None):
        if not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.ndim not in (2, 3):
            raise DeviceError("E_FRAME_FORMAT", "original must be uint8 grayscale or color")
        if image.ndim == 3 and image.shape[2] not in (1, 3, 4):
            raise DeviceError("E_FRAME_FORMAT", "unsupported channel count")
        with self.lock:
            owner, capacity = self.reservations[reservation]
            if image.nbytes > capacity or image.nbytes <= 0:
                raise DeviceError("E_FRAME_CAPACITY", "frame exceeds its pre-acquisition reservation")
            memory = SharedMemory(create=True, size=image.nbytes)
            try:
                destination = np.ndarray(image.shape, dtype=image.dtype, buffer=memory.buf)
                np.copyto(destination, image)
                del destination
                key = uuid4().hex
                descriptor = dict(frameId=key, name=memory.name, shape=list(image.shape), dtype="uint8",
                                  nbytes=image.nbytes, cycleId=cycleId, generation=generation,
                                  requestId=requestId, planRevision=planRevision)
                if metadata is not None:
                    descriptor["acquisition"] = dict(metadata)
                self.frames[key] = Frame(memory, descriptor, {owner})
                del self.reservations[reservation]
                return dict(descriptor)
            except BaseException:
                memory.close()
                memory.unlink()
                raise

    def retain(self, frameId, sourceOwner, owner):
        with self.lock:
            frame = self.frames[frameId]
            if sourceOwner not in frame.owners:
                raise DeviceError("E_FRAME_OWNER", "source owner has no original lease")
            frame.owners.add(owner)
            return dict(frame.descriptor)

    def beginWrite(self, owner, shape, cycleId, planRevision):
        import math
        if (not isinstance(shape, list) or len(shape) not in (2, 3)
                or any(type(n) is not int or n <= 0 for n in shape)
                or (len(shape) == 3 and shape[2] not in (1, 3, 4))):
            raise DeviceError("E_FRAME_FORMAT", "invalid derived image shape")
        with self.lock:
            reservation = self.reserve(owner, math.prod(shape))
            try:
                memory = SharedMemory(create=True, size=math.prod(shape))
            except BaseException:
                self.discardReservation(reservation)
                raise
            descriptor = dict(frameId=uuid4().hex, name=memory.name, shape=shape, dtype="uint8",
                              nbytes=math.prod(shape), cycleId=cycleId, planRevision=planRevision,
                              generation=0, requestId=uuid4().hex)
            self.writes[descriptor["frameId"]] = (owner, reservation, Frame(memory, descriptor, {owner}))
            return dict(descriptor)

    def commitWrite(self, owner, frameId):
        with self.lock:
            writer, reservation, frame = self.writes[frameId]
            if writer != owner:
                raise DeviceError("E_FRAME_OWNER", "image writer does not own the allocation")
            self.frames[frameId] = frame
            del self.writes[frameId]
            self.discardReservation(reservation)
            return dict(frame.descriptor)

    def release(self, frameId, owner):
        with self.lock:
            frame = self.frames.get(frameId)
            if frame is None:
                return
            frame.owners.discard(owner)
            if not frame.owners:
                frame.memory.close()
                frame.memory.unlink()
                del self.frames[frameId]

    def retireOwner(self, owner):
        """Call only after the actual reader/process exits, never on software timeout."""
        with self.lock:
            for key, (writer, reservation, frame) in list(self.writes.items()):
                if writer == owner:
                    frame.memory.close()
                    frame.memory.unlink()
                    self.discardReservation(reservation)
                    del self.writes[key]
            for key in list(self.frames):
                self.release(key, owner)
            for key, value in list(self.reservations.items()):
                if value[0] == owner:
                    del self.reservations[key]

    def close(self):
        with self.lock:
            self.closed = True
            if self.frames or self.reservations:
                raise DeviceError("E_FRAME_LEASES_ACTIVE", "original readers or acquisition reservations remain")


def copyFrame(descriptor):
    shape = descriptor["shape"]
    if (not isinstance(shape, list) or len(shape) not in (2, 3)
            or any(type(value) is not int or value <= 0 for value in shape)
            or descriptor["dtype"] != "uint8"):
        raise ValueError("invalid shared frame descriptor")
    import math
    size = math.prod(shape)
    if size != descriptor["nbytes"]:
        raise ValueError("shared frame size mismatch")
    memory = SharedMemory(name=descriptor["name"])
    try:
        if size > memory.size:
            raise ValueError("shared frame exceeds allocation")
        view = np.ndarray(tuple(shape), dtype=np.uint8, buffer=memory.buf)
        result = view.copy()
        del view
        return result
    finally:
        memory.close()
