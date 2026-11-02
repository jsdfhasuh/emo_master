"""Bounded device projection. Device cursors never advance Job event sequences."""
from collections import OrderedDict, deque
from copy import deepcopy
import json
from threading import RLock
from time import monotonic, time


MAX_BYTE_PREVIEW = 1024


def compactResult(value):
    """Keep transport identity and one bounded byte preview, never Base64 payloads."""
    if not isinstance(value, dict):
        return value
    result = {key: val for key, val in value.items() if key not in {'dataBase64', 'text', 'hex'}}
    encoded = value.get('dataBase64')
    if isinstance(encoded, str):
        import base64
        byteCount = len(encoded) // 4 * 3 - encoded[-2:].count('=')
        previewChars = 4 * ((MAX_BYTE_PREVIEW + 2) // 3)
        data = base64.b64decode(encoded[:previewChars])[:MAX_BYTE_PREVIEW]
        result.update(dataUtf8=data.decode('utf-8', errors='replace'), dataHex=data.hex(),
                      bytes=byteCount, dataTruncated=byteCount > MAX_BYTE_PREVIEW)
    return result


class DeviceDiagnostics:
    def __init__(self, *, clock=monotonic, logInterval=30.0):
        self.lock = RLock()
        self.rows = OrderedDict()
        self.cursor = 0
        self.bytes = 0
        self.count = 0
        self.dropped = 0
        self.writer = None
        self.eventPublisher = None
        self.clock = clock
        self.logInterval = logInterval
        self._operationalIssues = OrderedDict()
        self.logErrors = deque(maxlen=64)

    def append(self, scope, deviceId, eventType, message, *, write=True, timestampMs=None, **fields):
        defaultLevel = 'ERROR' if eventType.endswith('.failed') else 'INFO'
        level = str(fields.get('level', defaultLevel)).upper().replace('WARNING', 'WARN')
        fields['level'] = level if level in {'DEBUG', 'INFO', 'WARN', 'ERROR'} else defaultLevel
        if fields.get('code') == 'E_DEVICE_CANCELLED':
            fields['level'] = 'INFO'
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
                        'operatorId', 'sequence', 'kind', 'channelId', 'bytes', 'dataHex', 'truncated', 'action',
                        'serviceGeneration', 'connectionGeneration', 'outcome', 'receipt') if key in fields}
            result = fields.get('result')
            if isinstance(result, dict) and isinstance(result.get('dataHex'), str):
                data = bytes.fromhex(result['dataHex'])
                retained['result'] = dict(
                    dataHex=data[:MAX_BYTE_PREVIEW].hex(),
                    dataTruncated=bool(result.get('dataTruncated')) or len(data) > MAX_BYTE_PREVIEW,
                    **{key: result[key] for key in ('bytes', 'sentBytes') if isinstance(result.get(key), int)})
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
            publisher = self.eventPublisher if write else None
            routed, operational = self._operationalRows(row) if publisher is not None else (False, [])
        # EventStore also projects node logs into this class. Publish after
        # releasing the device lock to preserve that existing lock order.
        self._publishRows(writer, publisher, operational)
        if writer is not None and not routed:
            self._write(writer, self._event(row, row.get('jobId', '')))
        return row

    def _publishRows(self, writer, publisher, operational):
        for entry in operational:
            event = self._event(entry, '__runtime__')
            try:
                publisher(event.jobId, event.eventType, event.message, level=event.level,
                    code=event.code, projectId=event.projectId, workflowId=event.workflowId,
                    nodeId=event.nodeId, workflowRunId=event.workflowRunId,
                    parentWorkflowRunId=event.parentWorkflowRunId, nodeRunId=event.nodeRunId,
                    iterationPath=tuple(json.loads(event.iterationPathJson)),
                    timestampMs=event.timestampMs, payload=entry)
            except Exception as error:
                self.logErrors.append(repr(error))
                self._write(writer, event)

    def flushOperational(self):
        with self.lock:
            pending = [self._summary(issue) for issue in self._operationalIssues.values()
                       if issue['count'] > issue['reported']]
            self._operationalIssues.clear()
            writer, publisher = self.writer, self.eventPublisher
        self._publishRows(writer, publisher, pending)

    def _operationalRows(self, row):
        """Summarize native failures; normal device data stays in its own log."""
        if row.get('code') == 'E_DEVICE_CANCELLED':
            return False, []
        eventType = row['eventType']
        family = eventType.rsplit('.', 1)[0]
        key = (row['projectLoadGeneration'], row['deviceId'], family, row.get('action', ''))
        previous = self._operationalIssues.get(key)
        now = self.clock()
        output = []
        if row['level'] in {'WARN', 'ERROR'}:
            if previous is None or previous['row'].get('code') != row.get('code'):
                if previous is not None and previous['count'] > previous['reported']:
                    output.append(self._summary(previous))
                previous = dict(row=row, count=0, reported=0, firstAt=now, loggedAt=now)
                self._operationalIssues[key] = previous
            previous['row'] = row
            previous['count'] += 1
            if previous['count'] == 1 or now - previous['loggedAt'] >= self.logInterval:
                previous['loggedAt'] = now
                output.append(self._summary(previous))
            self._operationalIssues.move_to_end(key)
            while len(self._operationalIssues) > 512:
                _, old = self._operationalIssues.popitem(last=False)
                if old['count'] > old['reported']:
                    output.append(self._summary(old))
            return True, output
        if previous is not None and eventType.endswith('.completed'):
            self._operationalIssues.pop(key)
            if previous['count'] > previous['reported']:
                output.append(self._summary(previous))
            recoveredMessage = ('设备后续操作成功：' if family == 'device.action' else '设备已恢复：')
            output.append(dict(row, eventType=family + '.recovered', level='INFO',
                code='DEVICE_RECOVERED', message=recoveredMessage + row['message'],
                previousCode=previous['row'].get('code', ''),
                previousRequestId=previous['row'].get('requestId', ''), occurrences=previous['count']))
            return True, output
        return False, output

    def _summary(self, issue):
        count = issue['count'] - issue['reported']
        issue['reported'] = issue['count']
        return dict(issue['row'], occurrences=issue['count'], occurrencesSinceLastLog=count,
                    durationMs=int((self.clock() - issue['firstAt']) * 1000))

    @staticmethod
    def _event(row, jobId):
        from emo_master.apps.runtime.events.models import RuntimeEvent
        return RuntimeEvent(jobId=jobId, eventType=row['eventType'], message=row['message'],
            level=row['level'], code=row.get('code', ''), projectId=row['projectId'],
            workflowId=row.get('workflowId', ''), nodeId=row.get('nodeId', ''),
            workflowRunId=row.get('workflowRunId', ''), parentWorkflowRunId=row.get('parentWorkflowRunId', ''),
            nodeRunId=row.get('nodeRunId', ''), iterationPathJson=json.dumps(row.get('iterationPath', [])),
            payloadJson=json.dumps(row, ensure_ascii=True), timestampMs=row['timestampMs'])

    def _write(self, writer, event):
        if writer is not None:
            try:
                writer.enqueue(event)
            except Exception as error:
                self.logErrors.append(repr(error))

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
