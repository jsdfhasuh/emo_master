"""Explicit conversion of a one-shot RTSP node into a managed-device draft."""
from copy import deepcopy
from urllib.parse import urlsplit, urlunsplit


LEGACY_OPERATOR = 'vision.io.rtsp_camera'
MANAGED_OPERATOR = 'vision.io.rtsp_capture'


def migrationDraft(node, legacyDefinition):
    if node['operatorId'] != LEGACY_OPERATOR or node.get('deviceBindings'):
        raise ValueError('Only an unbound one-shot RTSP node can be migrated')
    draft = deepcopy(node)
    draft['operatorId'] = MANAGED_OPERATOR
    # The wizard needs the old defaults until it has built the shared device.
    draft['paramSchema'] = deepcopy(legacyDefinition['paramSchema'])
    draft['paramSchema']['xDeviceActionFields'] = []
    return draft


def redactDeviceValues(value):
    if isinstance(value, list):
        return [redactDeviceValues(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key.lower() in {'password', 'secret', 'token'}:
            result[key] = '******' if item else ''
        elif key == 'rtspUrl' and isinstance(item, str):
            try:
                parsed = urlsplit(item)
                if parsed.scheme not in ('rtsp', 'rtsps') or not parsed.hostname:
                    raise ValueError
                result[key] = urlunsplit((parsed.scheme, parsed.netloc.rsplit('@', 1)[-1],
                                         parsed.path, '***' if parsed.query else '', ''))
            except ValueError:
                result[key] = '(hidden URL)'
        else:
            result[key] = redactDeviceValues(item)
    return result
