"""Inspection summaries must never become a second image/result store."""
import math

import numpy as np
import pytest

from emo_master.core.contracts.geometry2d import DetectionCollection
from emo_master.core.contracts.run_inspection import (
    MAX_IO_BYTES, MAX_PORTS, clipText, describeValue, encodedBytes,
    finishInspection, normalizeInspection, startInspection, summarizePorts,
)


@pytest.mark.parametrize('value', [0, False, None, 12, -1, 3.5, '中文结果'])
def testScalarValuesAreActualAndPreserveFalsyValues(value):
    assert describeValue(value) == {'kind': 'scalar', 'value': value} or (
        isinstance(value, str) and describeValue(value)['value'] == value)


def testImageAndCollectionInspectionNeverRetainsContents():
    image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    assert describeValue(image) == {'kind': 'image', 'shape': [1080, 1920, 3], 'dtype': 'uint8'}
    assert describeValue([image] * 5000) == {'kind': 'collection', 'count': 5000}
    assert describeValue(DetectionCollection()) == {'kind': 'collection', 'count': 0}
    assert describeValue(DetectionCollection().toPayload()) == {'kind': 'collection', 'count': 0}
    assert describeValue({'a': image}) == {'kind': 'object', 'count': 1}
    assert encodedBytes(startInspection({'image': image})) < 300


def testSummaryFreezesMutableInputDescriptionBeforeExecution():
    values = [1, 2]
    started = startInspection({'items': values})
    values.clear()
    done = finishInspection(started, {'result': False})
    assert done['inputs']['items'][0]['value']['count'] == 2
    assert done['outputs']['items'][0]['value']['value'] is False


def testPortAndByteBudgetsIncludeJsonEscapingAndUtf8():
    for text in ('中' * 5000, '\x00' * 5000, '😀' * 5000):
        values = {str(i): text for i in range(300)}
        summary = finishInspection(startInspection(values), values)
        assert encodedBytes(summary) <= MAX_IO_BYTES
        for side in ('inputs', 'outputs'):
            assert len(summary[side]['items']) <= MAX_PORTS
            assert summary[side]['omitted'] > 0
        assert len(clipText(text).encode('utf-8')) <= 240
    assert clipText('\ud800') == '?'


def testUnsupportedObjectsNeverCallUserCodeOrTraverseCollections():
    class Dangerous:
        def __str__(self):
            raise AssertionError('must not stringify')
        def __len__(self):
            raise AssertionError('must not call custom len')
    class DangerousList(list):
        def __len__(self):
            raise AssertionError('must not call custom len')
    value = Dangerous()
    assert describeValue(value)['kind'] == 'unavailable'
    assert describeValue(DangerousList())['kind'] == 'unavailable'
    assert describeValue([value]) == {'kind': 'collection', 'count': 1}
    assert summarizePorts({value: value, 'valid': 0})['items'][0]['port'] == 'valid'


def testHugeAndNonFiniteNumbersAndSensitivePortsAreBounded():
    assert describeValue(2**10000) == {'kind': 'integer', 'bits': 10001}
    for value in (math.inf, -math.inf, math.nan):
        assert describeValue(value)['kind'] == 'unavailable'
    raw = {name: 'do not expose' for name in ('password', 'api_token', 'apiKey', 'authorization')}
    batch = summarizePorts(raw)
    assert all(item['value'] == {'kind': 'redacted'} for item in batch['items'])


@pytest.mark.parametrize('raw', [None, [], {'version': 1, 'inputs': []},
    {'version': 1, 'outputs': {'items': [{'port': 'x', 'value': {'kind': []}}]}},
    {'version': 1, 'outputs': {'items': [{'port': 'x', 'value': {'kind': 'scalar', 'value': math.inf}}], 'portCount': 2**10000}},
    {'version': 1, 'outputs': {'items': [{'port': 'image', 'value': {'kind': 'image', 'shape': [-1]}}]}},
])
def testRemoteAndLegacyDescriptionsAreValidated(raw):
    result = normalizeInspection(raw, {'count': 0})
    assert encodedBytes(result) <= MAX_IO_BYTES
    if result['legacy']:
        assert result['inputs'] is None
        assert result['outputs']['items'][0]['value']['value'] == 0
    else:
        for batch in (result['inputs'], result['outputs']):
            if batch:
                assert len(batch['items']) <= MAX_PORTS
