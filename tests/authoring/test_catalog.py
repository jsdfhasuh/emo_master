"""Actual Runtime gRPC declarations through the authoring DTO/client boundary."""
import asyncio
from copy import deepcopy
from pathlib import Path
import socket

import pytest

from emo_master.authoring import catalog
from emo_master.authoring.catalog import RuntimeCatalog, operator_dto
from emo_master.authoring.errors import AuthoringError
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.main import createRuntimeServer
from emo_panel_protocol.generated import runtime_pb2 as pb


@pytest.fixture(scope='module')
def target(tmp_path_factory):
    root = tmp_path_factory.mktemp('catalog-runtime')
    runtime = RuntimeService(dbPath=root / 'runtime.db', workspaceRoot=root / 'jobs')
    server, port, _ = createRuntimeServer(port=0, runtimeService=runtime)
    server.start()
    try:
        yield runtime, RuntimeCatalog({'actual': f'127.0.0.1:{port}'}, timeout=5)
    finally:
        server.stop(0).wait()
        runtime.close()


def testProtocolImportIsCurrentGeneratedPackage():
    expected = Path(__file__).resolve().parents[2] / 'src/emo_panel_protocol/generated/runtime_pb2.py'
    assert Path(catalog.pb.__file__).resolve() == expected.resolve()
    assert catalog.pb is pb
    fields = pb.OperatorInfo.DESCRIPTOR.fields_by_name
    assert fields['next_cycle_parameter_paths_json'].number == 18
    assert fields['min_core_version'].number == 19 and fields['max_core_version'].number == 20
    assert pb.ListOperatorsReply.DESCRIPTOR.fields_by_name['runtime_instance_id'].number == 3
    assert 'ValidateCandidateProject' in pb.DESCRIPTOR.services_by_name['RuntimeService'].methods_by_name


def testActualThresholdDirectoryAndAllFourNewValuesReachDto(target):
    runtime, client = target
    listing = client.list_operators('actual', query='threshold')
    assert len(listing['operators']) == 1
    assert listing['runtimeInstanceId'] == runtime.runtimeInstanceId
    assert listing['source'] == 'live' and listing['observedAt']
    item = client.get_operator('actual', listing['operators'][0]['operatorId'])
    manifest = runtime.pluginScanResult.activeOperators[item['operatorId']].manifest
    assert item['version'] == manifest.version
    assert item['nextCycleParameterPaths'] == list(manifest.nextCycleParameterPaths)
    assert item['minCoreVersion'] == manifest.minCoreVersion
    assert item['maxCoreVersion'] == manifest.maxCoreVersion
    assert item['runtimeInstanceId'] == runtime.runtimeInstanceId
    assert item['inputPorts'] == manifest.inputPorts and item['outputPorts'] == manifest.outputPorts
    assert item['paramSchema'] == manifest.paramSchema
    assert item['paramSchema']['properties']['mode']['enum'] == ['fixed', 'otsu', 'triangle', 'adaptiveMean', 'adaptiveGaussian']
    assert item['paramSchema']['properties']['blockSize']['xOdd'] is True
    assert item['paramSchema']['properties']['blockSize']['xEnabledWhen']
    assert item['outputPorts']['threshold']['nullable'] is True
    assert not {'nextCycleParameterPaths', 'minCoreVersion', 'maxCoreVersion', 'runtimeInstanceId'} & set(item['unavailableFields'])
    assert runtime.loadedDocument is None and runtime.jobRepository.all() == []


def testActualRoiNestedAndConditionalSchemasAreNotSimplified(target):
    runtime, client = target
    item = client.get_operator('actual', 'vision.preprocess.roi')
    manifest = runtime.pluginScanResult.activeOperators[item['operatorId']].manifest
    assert item['paramSchema'] == manifest.paramSchema
    properties = item['paramSchema']['properties']
    assert 'regions' in properties['roiType']['enum']
    region = properties['regions']['items']['properties']
    assert region['purpose']['enum'] == ['keep', 'exclude']
    assert region['enabled']['type'] == 'boolean'
    assert region['points']['type'] == 'array'
    assert region['points']['items']['required'] == ['x', 'y']
    for conditional in ('points', 'regions'):
        assert 'xVisibleWhen' in properties[conditional] and 'xValidateWhen' in properties[conditional]
    assert item['editorSpec'] and 'customUi' not in item['unavailableFields']
    for operatorId, field in [('vision.collection.filter', 'contourPointsEnabled'),
                              ('vision.analysis.minimum_enclosing_circle', 'scope')]:
        entry = client.get_operator('actual', operatorId)
        expected = runtime.pluginScanResult.activeOperators[operatorId].manifest
        assert entry['paramSchema'] == expected.paramSchema
        assert entry['paramSchema']['properties'][field] == expected.paramSchema['properties'][field]


def testActualDeviceDeclarationsKeepFullSchemasAndRequirements(target):
    runtime, client = target
    listing = client.list_device_types('actual')
    actual = {item['typeId']: item['declaration'] for item in listing['deviceTypes']}
    assert actual == {key: declaration.model_dump() for key, declaration in runtime.pluginScanResult.deviceTypes.items()}
    assert actual
    for key, declaration in runtime.pluginScanResult.deviceTypes.items():
        detail = client.get_device_type('actual', key)
        assert detail['declaration'] == declaration.model_dump()
        assert 'connectionSchema' in detail['declaration'] and 'defaultsSchema' in detail['declaration']
    expectedRequirements = {key: {slot: requirement.model_dump() for slot, requirement in descriptor.manifest.deviceRequirements.items()}
                            for key, descriptor in runtime.pluginScanResult.activeOperators.items() if descriptor.manifest.deviceRequirements}
    assert listing['requirements'] == expectedRequirements
    assert runtime.loadedDocument is None and runtime.jobRepository.all() == []


@pytest.mark.parametrize('raw,expected,unknown', [('', None, True), ('[]', [], False), ('["threshold", "regions[*].enabled"]',
    ['threshold', 'regions[*].enabled'], False)])
def testMetadataAbsentExplicitEmptyAndNonemptyAreDistinct(raw, expected, unknown):
    info = pb.OperatorInfo(operator_id='fixture.operator', version='9.8.7', next_cycle_parameter_paths_json=raw)
    result = operator_dto(info, target_id='configured-target', runtime_instance_id='', observed_at='fixture-observation')
    assert result['nextCycleParameterPaths'] == expected
    assert ('nextCycleParameterPaths' in result['unavailableFields']) is unknown
    assert result['runtimeInstanceId'] is None
    assert result['minCoreVersion'] is None and result['maxCoreVersion'] is None
    assert {'runtimeInstanceId', 'minCoreVersion', 'maxCoreVersion'} <= set(result['unavailableFields'])
    assert result['version'] == '9.8.7' and result['targetId'] == 'configured-target'


@pytest.mark.parametrize('raw', ['not JSON', 'null', '{}', '[1]', '[false]'])
def testInvalidPathMetadataIsAnErrorInsteadOfAnEmptyList(raw):
    info = pb.OperatorInfo(next_cycle_parameter_paths_json=raw)
    with pytest.raises(AuthoringError) as caught:
        operator_dto(info, target_id='fixture', runtime_instance_id='real-instance', observed_at='observed')
    assert caught.value.code == 'E_CATALOG_METADATA' and caught.value.status == 502


def testSearchAbsenceIsDistinctFromUnavailableTargetAndCacheIsIsolated(target):
    _, client = target
    assert client.list_operators('actual', query='operator-that-does-not-exist')['operators'] == []
    with pytest.raises(AuthoringError) as missing:
        client.get_operator('actual', 'operator-that-does-not-exist')
    assert missing.value.code == 'E_OPERATOR_NOT_LISTED' and missing.value.status == 404
    original = client.get_operator('actual', 'vision.preprocess.threshold')
    cached = client.get_cached_operator('actual', original['operatorId'])
    assert cached['source'] == 'cache'
    changed = deepcopy(cached)
    changed['paramSchema']['properties'].clear()
    assert client.get_cached_operator('actual', original['operatorId']) == cached
    assert client.get_cached_operator('another-target', original['operatorId']) is None
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        unusedPort = listener.getsockname()[1]
    unavailable = RuntimeCatalog({'offline': f'127.0.0.1:{unusedPort}'}, timeout=.2)
    with pytest.raises(AuthoringError) as failed:
        unavailable.list_operators('offline')
    assert failed.value.code == 'E_TARGET_UNAVAILABLE' and failed.value.status == 502
    with pytest.raises(AuthoringError) as unknown:
        unavailable.list_operators('unknown')
    assert unknown.value.code == 'E_TARGET' and unknown.value.status == 404


def testActualAsyncCandidateClientUsesItsRealTargetAndSnapshot(target, tmp_path):
    from tests.runtime.test_candidate_project_precheck import imageProject, package
    runtime, client = target
    payload, image = imageProject(runtime)
    file = tmp_path / 'candidate.zip'
    file.write_bytes(package(payload, image))
    result = asyncio.run(client.precheck('actual', file, 'client-r2', expected_instance=runtime.runtimeInstanceId))
    assert result['scope'] == 'target' and result['draftRevision'] == 'client-r2'
    assert result['status'] == 'PASSED' and result['compileStatus'] == 'passed'
    assert result['runtimeInstanceId'] == runtime.runtimeInstanceId and result['executionStatus'] == 'not_run'
