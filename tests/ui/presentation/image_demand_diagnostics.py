"""Failure-only scalar evidence; no Qt calls, RPC, waits, or pixel access."""
from _thread import RLock
import json


PREFIX = 'IMAGE_DEMAND_FAILURE '
MAX_OUTPUT_BYTES = 4096


def _dict(value):
    if type(value) is not dict:
        raise ValueError('unknown mapping')
    return value


def _fields(value):
    return _dict(object.__getattribute__(value, '__dict__'))


def _scalar(value):
    if value is None or type(value) is bool:
        return value
    if type(value) is int and value.bit_length() <= 64:
        return value
    if type(value) is str:
        return value[:160]
    raise ValueError('unknown scalar')


def _observe(read):
    try:
        return read()
    except Exception:
        return {'unknown': 'unavailable_or_changed'}


def imageDemandFailureDetails(window, session, expectedKey):
    details = {'diagnostics': 'best-effort, non-atomic; unknown is not absence',
               'expected_key': _observe(lambda: _scalar(expectedKey))}

    def lastView():
        view = _fields(window)['lastView']
        return None if view is None else {key: _scalar(_fields(view)[key])
                                         for key in ('connection', 'detail', 'generation')}

    def image():
        rows = _dict(_dict(_fields(window)['widgets'])['overview'])
        item = rows['overview-image']
        if type(item) is not tuple or len(item) != 2:
            raise ValueError('unknown image binding')
        return {key: _scalar(_fields(item[1])[key]) for key in ('message', 'key')}

    def client():
        fields = _fields(session)
        lock = fields['lock']
        if type(lock) is not RLock or not lock.acquire(blocking=False):
            return {'unknown': 'lock_unavailable'}
        try:
            value = {key: _scalar(fields[key]) for key in ('connection', 'connectionDetail', 'generation')}
            value.update({key: _scalar(_dict(fields[key]).get('root', 0)) for key in ('high', 'expired')})
            value['root_loading'] = 'root' in _dict(fields['loading'])
            latest = _dict(fields['latest']).get('root')
            if latest is None:
                value['cached'] = None
            else:
                if type(latest) is not tuple or len(latest) != 3 or type(expectedKey) is not str:
                    raise ValueError('unknown cached result')
                identity = _fields(_fields(latest[0])['identity'])
                key = identity['resultKey']
                if type(key) is not str:
                    raise ValueError('unknown result key')
                value['cached'] = {'key': _scalar(key), 'ordinal': _scalar(identity['resultOrdinal']),
                    'same_expected_key': key == expectedKey, 'image_present': _dict(latest[1]).get('image') is not None}
            return value
        finally:
            lock.release()

    details['last_view'] = _observe(lastView)
    details['image'] = _observe(image)
    details['session'] = _observe(client)
    text = json.dumps(details, ensure_ascii=True, separators=(',', ':'))
    if len((PREFIX + text + '\n').encode('utf-8')) > MAX_OUTPUT_BYTES:
        return '{"unknown":"output_budget_exceeded"}'
    return text


def emitImageDemandFailure(window, session, expectedKey):
    try:
        print(PREFIX + imageDemandFailureDetails(window, session, expectedKey))
    except Exception:
        pass  # Diagnostic collection/output must not replace the original failure.
