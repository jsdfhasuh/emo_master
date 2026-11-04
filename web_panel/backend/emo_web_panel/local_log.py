"""Gateway-only JSONL diagnostics; deliberately independent of Qt and emo_master."""
import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
import uuid


class JsonlFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({'timestampMs': int(record.created * 1000), 'originApp': 'web',
            'component': 'gateway', 'runtimeInstanceId': getattr(record, 'fields', {}).get('runtimeInstanceId', ''), 'level': record.levelname,
            'eventType': getattr(record, 'event_type', 'gateway.application'),
            'message': record.getMessage(), 'payload': getattr(record, 'fields', {})},
            ensure_ascii=False, allow_nan=False)


class GatewayLog:
    def __init__(self, data_dir):
        self.directory = Path(data_dir) / 'logs'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger('emo_web_panel.gateway.' + uuid.uuid4().hex)
        self.logger.propagate = False
        self.logger.setLevel(logging.DEBUG)
        self.handler = RotatingFileHandler(self.directory / 'gateway.jsonl', maxBytes=8*1024*1024,
                                           backupCount=5, encoding='utf-8', delay=True)
        self.handler.setFormatter(JsonlFormatter())
        self.logger.addHandler(self.handler)
        self.closed = False

    def record(self, event_type, message, *, level=logging.INFO, **fields):
        self.logger.log(level, message, extra={'event_type': event_type, 'fields': fields})

    def close(self):
        if not self.closed:
            self.closed = True
            self.logger.removeHandler(self.handler)
            self.handler.close()

    def snapshot(self):
        self.handler.flush()
        files=sorted(self.directory.glob('gateway.jsonl*'), key=lambda path: 0 if path.name=='gateway.jsonl' else int(path.suffix[1:]), reverse=True)
        return [(path, stat.st_size, stat.st_ino) for path in files for stat in [path.stat()]]

    def records(self, snapshot=None):
        for path, end, identity in self.snapshot() if snapshot is None else snapshot:
            with path.open('rb') as stream:
                stat=os.fstat(stream.fileno())
                if stat.st_ino!=identity or stat.st_size<end:
                    raise OSError('Selected gateway log range changed before reading')
                while stream.tell() < end:
                    line = stream.readline(end - stream.tell())
                    if not line.endswith(b'\n'):
                        break
                    yield json.loads(line)

    def recent(self, limit=100):
        from collections import deque
        return list(deque(self.records(), maxlen=limit))
