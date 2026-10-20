from dataclasses import dataclass

from emo_master.plugins.builtins._image_frame import defaultFrame


@dataclass(frozen=True)
class OperatorMeta:
    operatorId: str = "vision.io.simulated_camera"
    displayName: str = "模拟相机采集"
    version: str = "1.0.0"


class SimulatedCameraOperator:
    meta = OperatorMeta()

    def validateParams(self, params):
        if params:
            return {"code": "E_PARAM_INVALID", "message": "simulated work parameters belong to the device"}
        return None

    def executeNode(self, inputs, params, runtimeContext):
        capture = runtimeContext.get("captureDevice")
        if capture is None:
            return dict(status="error", error=dict(code="E_DEVICE_RUNTIME_UNAVAILABLE", message="managed device missing"))
        image, descriptor = capture("camera")
        frame = defaultFrame(image.shape[1], image.shape[0], sourceId=descriptor["frameId"])
        return dict(status="ok", outputs=dict(image=image, frame=frame.toPayload()),
                    diagnostics=dict(deviceFrame=descriptor), metrics={})
