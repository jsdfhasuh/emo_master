"""Deterministic synthetic camera; never enumerates physical cameras or sockets."""
import time
from uuid import uuid4

import numpy as np

from .executor import DeviceError


class SimulatedCamera:
    def __init__(self, definition):
        self.definition = definition
        self.sequence = 0
        self.connected = False
        self.generation = None

    def connect(self, params, cancelled, deadline):
        if cancelled() or time.monotonic() >= deadline:
            raise DeviceError("E_DEVICE_CANCELLED", "synthetic connection cancelled")
        if not self.connected:
            self.connected = True
            self.generation = uuid4().hex

    @staticmethod
    def physicalIdentity(definition):
        return "simulated:" + definition.connection["serial"]

    @staticmethod
    def frameBytes(params):
        return params["width"] * params["height"] * 3

    def execute(self, action, params, cancelled, deadline):
        if action != "capture":
            raise DeviceError("E_DEVICE_ACTION", "unsupported simulated action")
        until = time.monotonic() + params["exposureUs"] / 1_000_000
        while time.monotonic() < until:
            if cancelled():
                raise DeviceError("E_DEVICE_CANCELLED", "synthetic acquisition cancelled", outcome="CANCELLED")
            if time.monotonic() >= deadline:
                raise DeviceError("E_DEVICE_OPERATION_TIMEOUT", "synthetic acquisition timed out", outcome="CANCELLED")
            time.sleep(.001)
        self.sequence += 1
        image = np.empty((params["height"], params["width"], 3), dtype=np.uint8)
        image[:, :, 0] = np.arange(params["width"], dtype=np.uint16) % 256
        image[:, :, 1] = self.sequence % 256
        image[:, :, 2] = min(255, round(params["exposureUs"] / 1000 + params["gain"]))
        return image

    def close(self):
        self.connected = False

    def snapshot(self):
        return dict(connectionState="CONNECTED" if self.connected else "DISCONNECTED",
                    connectionGeneration=self.generation)
