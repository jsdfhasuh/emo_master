"""Child-importable IMV/ONNX boundaries, for explicitly simulated tests only.

No final coordinates, phase decisions, PLC calls or gateway calls are produced
here. File arrivals stand in for SDK hardware frames; real operators do the rest.
"""
import os
from pathlib import Path
from time import sleep

import numpy as np

from emo_master.plugins.builtins._huaray_imv import IMV_TIMEOUT
from emo_master.plugins.builtins.huaray_camera.operator import HuarayCameraOperator
from emo_master.plugins.builtins.yolo_inference import operator as yolo
from emo_master.plugins.builtins.yolo_inference.onnx_backend import OnnxInferenceResult
from tests.plugins.test_huaray_camera_operator import _FakeImvApi


class FileTriggeredSdk(_FakeImvApi):
    def __init__(self):
        super().__init__()
        self.root = Path(os.environ["EMO_M5_SIMULATED_CAMERA_ROOT"])
        self.key = None
        self.received = 0
        self.waitingPublished = False
        self.ints.update(Width=160, Height=120, OffsetX=0, OffsetY=0)

    def create_handle(self, mode, identifier):
        self.key = str(identifier)
        self.serialNumber = self.key
        self.folder = self.root / self.key
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "opened").write_text("simulated", encoding="ascii")
        return super().create_handle(mode, identifier)

    def get_frame(self, handle, timeoutMs):
        # One armed/waiting marker per SDK instance. Rewriting it on every
        # 1ms empty poll floods the same filesystem as durable Runtime events
        # and makes this boundary simulator an artificial disk stress test.
        if not self.waitingPublished:
            (self.folder / "waiting").touch()
            self.waitingPublished = True
        path = self.folder / f"frame-{self.received + 1}.npy"
        if not path.exists():
            sleep(timeoutMs / 1000)
            return IMV_TIMEOUT, None
        self.current = np.load(path, allow_pickle=False)
        path.unlink()  # A consumed SDK frame cannot reappear in a new test session.
        self.received += 1
        return super().get_frame(handle, timeoutMs)

    def convert_frame(self, handle, frame, outputColor, demosaic):
        return self.current.copy()

    def destroy_handle(self, handle):
        (self.folder / "destroyed").write_text(str(self.received), encoding="ascii")
        return super().destroy_handle(handle)


class TriggeredCamera(HuarayCameraOperator):
    def __init__(self):
        super().__init__(FileTriggeredSdk)


class OnnxBoundary:
    def __init__(self, modelPath):
        # The role survives production input freezing; no dependence on original filenames.
        self.role = Path(modelPath).read_text(encoding="ascii").split(":")[1]

    def predict(self, **kwargs):
        box = {"a": [19, 29, 41, 51], "b": [109, 39, 132, 62], "holes": [61, 71, 79, 89]}[self.role]
        return OnnxInferenceResult(np.array([box], dtype=float), np.array([.99]), np.array([0]),
            {0: "test-target"}, "boundary-simulator", (1, 3, 640, 640))


class BoundaryYolo(yolo.YoloInferenceOperator):
    def executeNode(self, inputs, params, context):
        original = yolo._createModel
        try:
            yolo._createModel = OnnxBoundary
            return super().executeNode(inputs, params, context)
        finally:
            yolo._createModel = original
