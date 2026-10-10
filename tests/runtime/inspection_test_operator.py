"""Controlled test operator: local arrays/cancellation only, never a device."""
import time
from types import SimpleNamespace

import numpy as np


class InspectionTestOperator:
    meta = SimpleNamespace(operatorId='test.inspection', displayName='受控测试', version='1.0.0',
                           inputPorts={'index': {'type': 'integer', 'required': False}},
                           outputPorts={'image': 'image'}, paramSchema={'type': 'object'})
    def validateParams(self, params):
        return None

    def executeNode(self, inputs, params, runtimeContext):
        if params.get('block'):
            while True:
                if params.get('cooperative'):
                    runtimeContext['raiseIfCancellationRequested']()
                time.sleep(.01)
        index = inputs.get('index', 0)
        if index == params.get('failIndex', -1):
            return {'status': 'error', 'error': {'code': 'E_TEST_LAST_ITERATION', 'message': 'controlled final failure'}}
        return {'status': 'ok', 'outputs': {'image': np.full((12, 16, 3), 20 + int(index), np.uint8)},
                'metrics': {'latencyMs': 1}}
