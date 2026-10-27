from dataclasses import dataclass

from emo_master.plugins.builtins._image_frame import defaultFrame


@dataclass(frozen=True)
class OperatorMeta:
    operatorId: str = 'vision.io.rtsp_capture'
    displayName: str = 'RTSP\u76f8\u673a\u91c7\u96c6\uff08\u8bbe\u5907\uff09'
    version: str = '1.0.0'


class RtspCaptureOperator:
    meta = OperatorMeta()

    def validateParams(self, params):
        if params:
            return dict(code='E_PARAM_INVALID', message='RTSP connection and work parameters belong to the device')
        return None

    def executeNode(self, inputs, params, runtimeContext):
        invalid = self.validateParams(params)
        if invalid:
            return dict(status='error', error=invalid)
        capture = runtimeContext.get('captureDevice')
        if capture is None:
            return dict(status='error', error=dict(code='E_DEVICE_RUNTIME_UNAVAILABLE',
                                                   message='Bind an RTSP device and run through the managed Runtime'))
        image, descriptor = capture('camera')
        acquisition = descriptor['acquisition']
        frame = defaultFrame(image.shape[1], image.shape[0], sourceId=acquisition['sourceId'])
        return dict(status='ok', outputs=dict(image=image, frame=frame.toPayload(), capturedAt=acquisition['capturedAt']),
                    diagnostics=dict(deviceFrame=descriptor), metrics={})
