from copy import deepcopy
from pathlib import Path
from queue import Empty, Queue
from threading import Event, get_ident
from time import sleep

import numpy as np

from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.core.contracts.port_types import normalizePortType
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.devices import DeviceDefinition
from emo_master.core.project.models import ProjectDocument


ROOT = Path(__file__).resolve().parents[2] / 'src/emo_master/plugins/builtins'


def scan():
    return PluginRegistry('0.6.1').scan(ROOT)


def definition(**changes):
    declaration = scan().deviceTypes['device.camera.rtsp']
    raw = dict(name='Fixture RTSP', deviceTypeId=declaration.deviceTypeId, driverVersion=declaration.version,
               connection=dict(rtspUrl='rtsp://camera.invalid/live', username='', maxFrameBytes=1024, readTimeoutMs=100),
               defaults={}, policies=dict(operationTimeoutMs=2000))
    for key, value in changes.items():
        raw[key].update(value)
    return declaration.validateDefinition(DeviceDefinition.model_validate(raw))


def project(*, legacy=False):
    registry = scan().activeOperators
    operatorId = 'vision.io.rtsp_camera' if legacy else 'vision.io.rtsp_capture'
    manifest = registry[operatorId].manifest
    payload = WorkflowStore().toPayload('RTSP fixture')
    ports = {key: normalizePortType(value) for key, value in manifest.outputPorts.items()}
    node = dict(nodeId='capture', kind='operator', operatorId=operatorId, displayName='Capture',
                inputPorts={}, outputPorts=ports, paramSchema=deepcopy(manifest.paramSchema), params={})
    if legacy:
        node['params'] = dict(rtspUrl='rtsp://camera.invalid/live?token=fixture-token', username='fixture-user',
                              password='fixture-password', colorMode='grayscale', openTimeoutMs=1200, readTimeoutMs=300)
    else:
        node['deviceBindings'] = {'camera': {'deviceId': 'camera'}}
        payload['devices']['definitions']['camera'] = definition().model_dump()
    flow = payload['workflows']['main']
    flow['outputs'] = ports
    flow['nodes'] = [dict(nodeId='input', kind='workflow_input'), node,
                     dict(nodeId='output', kind='workflow_output', inputPorts=ports)]
    flow['edges'] = [dict(fromNode='capture', fromPort=key, toNode='output', toPort=key) for key in ports]
    payload['dependencies']['operators'] = [dict(operatorId=operatorId, version=manifest.version)]
    return ProjectDocument.model_validate(payload)


class FakeCapture:
    def __init__(self, *, automatic=False, opened=True):
        self.automatic, self.opened = automatic, opened
        self.frames = Queue()
        self.readStarted = Event()
        self.released = Event()
        self.calls = []
        self.count = 0

    def open(self, url, backend, options):
        self.calls.append(('open', get_ident(), url, backend, options))
        return self.opened

    def read(self):
        self.calls.append(('read', get_ident()))
        self.readStarted.set()
        if self.automatic:
            sleep(.01)
            self.count += 1
            return True, np.full((5, 7, 3), self.count % 255, np.uint8)
        try:
            frame = self.frames.get(timeout=.4)
        except Empty:
            return False, None
        if isinstance(frame, Exception):
            raise frame
        return (False, None) if frame is None else (True, frame)

    def release(self):
        self.calls.append(('release', get_ident()))
        self.released.set()

    def put(self, value):
        self.frames.put(np.full((5, 7, 3), value, np.uint8))
