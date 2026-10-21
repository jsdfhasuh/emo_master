"""Bounded device projection. Device cursors never advance Job event sequences."""
from collections import OrderedDict, deque
from copy import deepcopy
import json
from threading import RLock
from time import time


MAX_BYTE_PREVIEW = 1024


def compactResult(value):
    """Keep transport identity and one bounded byte preview, never Base64 payloads."""
    if not isinstance(value, dict):
        return value
    result = {key: val for key, val in value.items() if key not in {'dataBase64', 'text', 'hex'}}
    encoded = value.get('dataBase64')
    if isinstance(encoded, str):
        import base64
        data = base64.b64decode(encoded[:1368])[:1024]
        result.update(dataUtf8=data.decode('utf-8', errors='replace'), dataHex=data.hex(),
                      dataTruncated=len(encoded) > 1368)
    return result


class DeviceDiagnostics:
    def __init__(self):
        self.lock = RLock()
        self.rows = OrderedDict()
        self.cursor = 0
        self.bytes = 0
        self.count = 0
        self.dropped = 0
        self.writer = None

    def append(self, scope, deviceId, eventType, message, *, write=True, timestampMs=None, **fields):
        # Keep previews bounded; receipts have compact count/outcome metadata.
        if isinstance(fields.get('dataHex'), str):
            data = bytes.fromhex(fields['dataHex'])
            fields['truncated'] = bool(fields.get('truncated')) or len(data) > MAX_BYTE_PREVIEW
            if len(data) > MAX_BYTE_PREVIEW:
                fields['dataHex'] = data[:MAX_BYTE_PREVIEW].hex(' ').upper()
        preview = json.dumps(fields, ensure_ascii=False, default=str)
        if len(preview.encode('utf-8')) > 1024:
            # Frozen attribution and send receipts must survive preview truncation.
            retained = {key: fields[key] for key in ('ownerId', 'jobId', 'cycleId', 'requestId', 'source',
                        'slot', 'selectors', 'code', 'workflowId', 'nodeId', 'deviceGeneration', 'level', 'phase',
                        'planRevision', 'workflowRunId', 'parentWorkflowRunId', 'nodeRunId', 'iterationPath',
                        'operatorId', 'sequence', 'kind', 'channelId', 'bytes', 'dataHex', 'truncated',
                        'serviceGeneration', 'connectionGeneration', 'outcome', 'receipt') if key in fields}
            receipt = fields.get('diagnostics', {}).get('tcpReceipt')
            transaction = fields.get('diagnostics', {}).get('tcpTransaction')
            if receipt is None and isinstance(fields.get('result'), dict) and fields['result'].get('type') == 'tcpSendReceipt':
                receipt = fields['result']
            if receipt:
                retained['diagnostics'] = {'tcpReceipt': receipt}
            if transaction:
                retained.setdefault('diagnostics', {})['tcpTransaction'] = transaction
            fields = dict(retained, preview=preview.encode('utf-8')[:1000].decode('utf-8', errors='ignore'), fieldsTruncated=True)
        with self.lock:
            self.cursor += 1
            row = dict(fields)
            row.update(scope, deviceId=deviceId, cursor=self.cursor, eventType=eventType,
                       message=str(message)[:1024], timestampMs=int(time()*1000) if timestampMs is None else timestampMs)
            size = len(json.dumps(row, ensure_ascii=True).encode())
            key = (scope['projectLoadGeneration'], deviceId)
            bucket = self.rows.setdefault(key, deque())
            bucket.append((row, size))
            self.bytes += size
            self.count += 1
            while len(bucket) > 512 or sum(n for _, n in bucket) > 1024*1024:
                self._drop(bucket)
            while self.count > 8192 or self.bytes > 16*1024*1024:
                firstKey = next(key for key, value in self.rows.items() if value)
                self._drop(self.rows[firstKey])
                if not self.rows[firstKey]:
                    del self.rows[firstKey]
            writer = self.writer if write else None
        if writer is not None:
            from emo_master.apps.runtime.events.models import RuntimeEvent
            writer.enqueue(RuntimeEvent(jobId='', eventType=eventType, message=row['message'],
                projectId=scope['projectId'], payloadJson=json.dumps(row, ensure_ascii=True),
                timestampMs=row['timestampMs'], sequence=0))
        return row

    def _drop(self, bucket):
        _, size = bucket.popleft()
        self.bytes -= size
        self.count -= 1
        self.dropped += 1

    def query(self, scope, deviceId, cursor=0):
        with self.lock:
            rows = [deepcopy(row) for row, _ in self.rows.get((scope['projectLoadGeneration'], deviceId), ())
                    if row['cursor'] > cursor]
            page = rows[:128]
            return dict(scope, deviceId=deviceId, records=page,
                cursor=page[-1]['cursor'] if page else cursor, more=len(rows)>128, dropped=self.dropped,
                truncated=bool(self.dropped))

    def projectEvent(self, event):
        if event.eventType not in {'node.log', 'node.failed'}:
            return
        payload = json.loads(event.payloadJson)
        bindings = payload.get('deviceBindings', {})
        knownSlot = payload.get('diagnostics', {}).get('deviceSlot')
        for slot, binding in bindings.items():
            if knownSlot and slot != knownSlot:
                continue
            scope = binding.get('scope')
            if scope:
                self.append(scope, binding['deviceId'], event.eventType, event.message, write=False,
                    slot=slot, selectors=binding.get('selectors', {}), jobId=event.jobId,
                    workflowId=event.workflowId, nodeId=event.nodeId, code=event.code,
                    timestampMs=event.timestampMs, level=event.level, phase=payload.get('phase', ''),
                    data=payload.get('data', {}), operatorId=payload.get('operatorId', ''),
                    ownerId=payload.get('ownerId', event.jobId), cycleId=payload.get('cycleId', ''),
                    planRevision=payload.get('planRevision'), deviceGeneration=binding.get('deviceGeneration', ''),
                    workflowRunId=event.workflowRunId, parentWorkflowRunId=event.parentWorkflowRunId,
                    nodeRunId=event.nodeRunId, iterationPath=json.loads(event.iterationPathJson),
                    diagnostics=payload.get('diagnostics', {}))
