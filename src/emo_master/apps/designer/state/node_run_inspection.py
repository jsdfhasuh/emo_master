"""Latest selected-node observations, bounded independently of event history."""
from collections import OrderedDict

from emo_master.core.contracts.run_inspection import clipText, encodedBytes, normalizeInspection, summarizePorts

MAX_INSPECTED_NODES = 64
MAX_NODE_BYTES = 8192


class NodeRunInspection:
    def __init__(self) -> None:
        self._nodes: OrderedDict[tuple[str, str], dict] = OrderedDict()

    def clear(self) -> None:
        self._nodes.clear()

    def get(self, workflowId: str, nodeId: str, jobId: str | None) -> dict | None:
        value = self._nodes.get((workflowId, nodeId))
        return value if value and value['jobId'] == (jobId or '') else None

    def isSuperseded(self, event: dict) -> bool:
        if any(not isinstance(event.get(field, ''), str) for field in ('workflowId', 'nodeId', 'jobId')):
            return True
        previous = self.get(event.get('workflowId', ''), event.get('nodeId', ''), event.get('jobId', ''))
        if previous is None:
            return False
        sequence = event.get('sequence', 0)
        if type(sequence) is int and sequence and sequence < previous['sequence']:
            return True
        execution = event.get('nodeRunId', '')
        return bool(execution and previous['nodeRunId'] and execution != previous['nodeRunId'])

    def applyEvent(self, event: dict) -> bool:
        eventType = event.get('eventType')
        if not isinstance(eventType, str):
            return False
        status = {'node.started': 'RUNNING', 'node.completed': 'COMPLETED',
                  'node.failed': 'FAILED', 'node.skipped': 'SKIPPED'}.get(eventType)
        if not status:
            return True
        identities = {field: event.get(field, '') for field in
                      ('jobId', 'workflowId', 'nodeId', 'nodeRunId', 'workflowRunId')}
        if any(not isinstance(value, str) or len(value.encode('utf-8', errors='replace')) > 128
               for value in identities.values()):
            return False
        key = (identities['workflowId'], identities['nodeId'])
        previous = self._nodes.get(key)
        sequence = event.get('sequence', 0)
        sequence = sequence if type(sequence) is int and 0 <= sequence < 2**63 else 0
        if previous and previous['jobId'] == identities['jobId']:
            if sequence and previous['sequence'] and sequence <= previous['sequence']:
                return False
            execution = event.get('nodeRunId', '')
            if status in {'COMPLETED', 'FAILED'} and execution and previous['nodeRunId'] and execution != previous['nodeRunId']:
                return False
        payload = event.get('payload')
        payload = payload if isinstance(payload, dict) else {}
        io = normalizeInspection(payload.get('ioSummary'), payload.get('outputs'))
        if status != 'COMPLETED':
            io['outputs'] = None
        timestamp = event.get('timestampMs', 0)
        timestamp = timestamp if type(timestamp) is int and 0 <= timestamp < 253402300799000 else 0
        message = payload.get('message') or event.get('message') or ''
        diagnostics = payload.get('diagnostics', {})
        diagnostic = diagnostics.get('text', '') if isinstance(diagnostics, dict) else ''
        iteration = event.get('iterationPath', ())
        iteration = iteration[:16] if isinstance(iteration, (list, tuple)) else ()
        record = {**identities,
                  'iterationPath': list(iteration),
                  'status': status, 'sequence': sequence, 'timestampMs': timestamp, 'io': io,
                  'metrics': summarizePorts(payload.get('metrics')), 'message': _text(message, 512),
                  'diagnostic': _text(diagnostic, 512), 'code': _text(event.get('code') or payload.get('code') or '', 64)}
        if isinstance(diagnostics, dict):
            from emo_master.core.contracts.sqlite_writer import receiptSummary
            receipt = receiptSummary(diagnostics.get('sqliteReceipt'), identities)
            if receipt is not None:
                record['sqliteReceipt'] = receipt
        # Identity from a remote endpoint is data too. Bound it before retention.
        for field in ('jobId', 'workflowId', 'nodeId', 'nodeRunId', 'workflowRunId'):
            record[field] = clipText(record[field], 128)
        record['iterationPath'] = [n for n in record['iterationPath'] if type(n) is int and 0 <= n <= 1_000_000]
        if encodedBytes(record) > MAX_NODE_BYTES:
            record['metrics'] = {'items': [], 'portCount': record['metrics']['portCount'], 'omitted': record['metrics']['portCount']}
            record['diagnostic'] = ''
        if encodedBytes(record) > MAX_NODE_BYTES:
            record['message'] = ''
        if encodedBytes(record) > MAX_NODE_BYTES:
            # JSON escaping can expand even short strings. Retain identity/status
            # and an explicit unavailable marker rather than exceed the budget.
            record['io'] = {'version': 1, 'inputs': None, 'outputs': None, 'legacy': False,
                            'unavailableReason': '摘要超过界面额度，未保留端口值'}
        if encodedBytes(record) > MAX_NODE_BYTES:
            record.pop('sqliteReceipt', None)
        self._nodes[key] = record
        self._nodes.move_to_end(key)
        while len(self._nodes) > MAX_INSPECTED_NODES:
            self._nodes.popitem(last=False)
        return True

    @property
    def retainedBytes(self) -> int:
        return sum(encodedBytes(value) for value in self._nodes.values())


def _text(value: object, limit: int) -> str:
    return clipText(value, limit) if isinstance(value, str) else ''
