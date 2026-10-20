"""Tiny non-TCP plugin: one background ticker and a custom sampling action."""
from dataclasses import dataclass
from threading import Event, Thread
from typing import ClassVar

from emo_master.core.contracts.device_driver import DeviceDriver, DeviceError
from emo_master.core.contracts.operator_logging import getDeviceLogger


class Ticker(DeviceDriver):
    def __init__(self, definition):
        self.definition = definition
        self.stop = Event()
        self.thread = None
        self.ticks = 0

    @staticmethod
    def physicalIdentity(definition):
        return None

    def connect(self, params, cancelled, deadline):
        if self.thread and self.thread.is_alive():
            return
        self.stop.clear()
        def tick():
            self.runtimeContext.events('device.ticker.started', '后台计数已启动')
            while not self.stop.wait(.05):
                self.ticks += 1
        self.thread = Thread(target=tick, name='example-device-ticker', daemon=True)
        self.thread.start()

    def validateAction(self, action, arguments):
        pass

    def execute(self, action, params, cancelled, deadline, *, arguments, operationContext):
        if arguments.get('reject'):
            raise DeviceError('E_SAMPLE_REJECTED', '示例采样被拒绝；服务可继续使用')
        return dict(ticks=self.ticks)

    def requestStop(self, reason):
        self.stop.set()

    def close(self, deadline=None):
        self.stop.set()
        if self.thread:
            self.thread.join(2)
            if self.thread.is_alive():
                raise DeviceError('E_DEVICE_RELEASE_PENDING', 'ticker has not exited', unhealthy=True)
            self.thread = None
            self.runtimeContext.events('device.ticker.stopped', '后台计数已停止')

    def snapshot(self):
        ready = bool(self.thread and self.thread.is_alive() and not self.stop.is_set())
        return dict(ready=ready, ownsResources=bool(self.thread), ticks=self.ticks)


@dataclass(frozen=True)
class Meta:
    operatorId = 'example.ticker.sample'
    displayName = '采样后台计数'
    version = '1.0.0'
    inputPorts: ClassVar[dict] = {}
    outputPorts = dict(ticks='integer')
    paramSchema = dict(type='object', additionalProperties=False,
        properties=dict(reject=dict(type='boolean', default=False)), xDeviceActionFields=['reject'])


class Sample:
    meta = Meta()

    def validateParams(self, params):
        return None

    def executeNode(self, inputs, params, runtimeContext):
        try:
            result = runtimeContext['executeDevice']('service', 'sample', {'reject': params.get('reject', False)})
            getDeviceLogger(runtimeContext, 'service').info('采样完成')
            return dict(status='ok', outputs=result)
        except DeviceError as error:
            # No manual failure log: Runtime projects node.failed to the device.
            return dict(status='error', error=dict(code=error.code, message=str(error)), diagnostics=error.diagnostics)
