"""Generated handoff stays consistent with SDK DTOs, RPC JSON and export bytes."""
import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile

import pytest

from emo_master.apps.designer.state.project_store import loadProjectDocument
from emo_panel_protocol.canonical import canonicalJson, revisionOf
from emo_panel_protocol.messages import BrowserMessage, HistoryPage


SCRIPT = Path(__file__).resolve().parents[3] / 'scripts/build_panel_contract.py'
spec = importlib.util.spec_from_file_location('build_panel_contract', SCRIPT)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


@pytest.fixture(scope='module')
def contract(tmp_path_factory):
    output = tmp_path_factory.mktemp('panel-contract')
    manifest = builder.buildContract(output, wheel=False, regenerateProto=False)
    return output, manifest


def testManifestTracksEveryArtifactAndHonestValidationScope(contract):
    output, manifest = contract
    assert manifest['status'] == 'development-without-wheel' and manifest['sdkWheels'] == []
    assert manifest['sampleValidation'] == dict(model='PASS', saveReload='PASS',
        workflowCompile='PASS', normalCapture='PASS', designerOpen='NOT_RUN')
    for name, expected in manifest['files'].items():
        data = (output / name).read_bytes()
        assert expected == dict(sha256=hashlib.sha256(data).hexdigest(), byteSize=len(data))
    document = loadProjectDocument(output / 'sample-panels/source/demo.emoproj')
    assert (document.schemaVersion, document.presentation.schemaVersion) == ('2.9', '1.2')
    with zipfile.ZipFile(output / 'sample-panels/demo.emoweb') as archive:
        snapshot = json.loads(archive.read('presentation.json'))
        assert {'live', 'history'} == set(snapshot['pages'])
        text = archive.read('presentation.json').decode()
        assert 'databasePath' not in text and 'baseDirectory' not in text
        assert any(name.startswith('assets/') for name in archive.namelist())


def testFixturesValidateAndCoverIndependentJobAndHistoryLifecycles(contract):
    output, _ = contract
    with zipfile.ZipFile(output / 'sample-panels/demo.emoweb') as archive:
        package = json.loads(archive.read('manifest.json'))
    for name, (schema, _value) in builder.fixtureValues(package).items():
        builder.MODELS[schema].model_validate_json((output / 'fixtures' / (name + '.json')).read_text(encoding='utf-8'))
    terminal = BrowserMessage.model_validate_json((output / 'fixtures/terminal.json').read_text()).root
    switched = BrowserMessage.model_validate_json((output / 'fixtures/job-changed.json').read_text()).root
    history = HistoryPage.model_validate_json((output / 'fixtures/history-page.json').read_text())
    assert terminal.jobId == switched.previousJobId and terminal.jobId != switched.jobId
    assert not terminal.active and not terminal.ownsResources and terminal.displayResourcesReleased
    assert history.rows[0].imageRefs['record-image'].imageRef
    assert 'jobId' not in HistoryPage.model_fields
    unknown = BrowserMessage.model_validate_json((output / 'fixtures/start-unknown.json').read_text()).root
    assert unknown.startRequestId and unknown.outcome == 'UNKNOWN'


def testGeneratedTypesDeriveFromTheSameSchemasAndPreserveRpc64BitRules(contract):
    output, _ = contract
    schemas = {p.stem: json.loads(p.read_text(encoding='utf-8')) for p in (output / 'schemas').glob('*.json')}
    generated = (output / 'typescript/panel-contract.ts').read_text(encoding='utf-8')
    assert generated == builder.generateTypes(schemas)
    rpc = schemas['runtime-rpc.schema']['$defs']
    assert rpc['RpcDisplaySnapshot']['properties']['cursor']['type'] == 'string'
    assert rpc['RpcHistoryImageReply']['properties']['data']['contentEncoding'] == 'base64'
    assert 'export type BrowserMessage = ' in generated
    assert '"sqlite_table"' in generated and '"table"' in generated


def testCanonicalVectorsPreserveUnicodeFloatAndLargeIntegerSemantics(contract):
    output, _ = contract
    vectors = json.loads((output / 'fixtures/canonical-vectors.json').read_text(encoding='utf-8'))
    for vector in vectors:
        assert canonicalJson(vector['input']) == vector['canonicalUtf8']
        assert revisionOf(vector['input']) == vector['sha256']
    assert '\\u9875\\u9762' in vectors[0]['canonicalUtf8']
    assert '\\ud83d\\ude00' in vectors[0]['canonicalUtf8']
    assert '"float":1.0' in vectors[1]['canonicalUtf8']
    assert '"negativeZero":-0.0' in vectors[1]['canonicalUtf8']
    assert '9007199254740993' in vectors[2]['canonicalUtf8']


def testHistoryDtoRejectsMismatchedRowsAndFalseImageAvailability():
    from pydantic import ValidationError
    from emo_panel_protocol.messages import HistoryImageReference
    with pytest.raises(ValidationError, match='opaque reference'):
        HistoryImageReference(status='AVAILABLE')
    with pytest.raises(ValidationError, match='width'):
        HistoryPage(columns=[dict(name='id')], rows=[dict(rowId='one', values=[])], page=0,
            pageSize=20, hasNext=False, historyDefinitionRevision='a' * 64, stableRowIdentity=True)


def testPreviewContractRequiresExplicitReadOnlyModeAndConnection(contract):
    from emo_panel_protocol.preview import PreviewContext
    from pydantic import ValidationError
    output, manifest = contract
    assert manifest['sdkVersion'] == '1.1.0'
    fixture = json.loads((output / 'fixtures/preview-fixtures.json').read_text())
    online = json.loads((output / 'fixtures/preview-runtime.json').read_text())
    assert PreviewContext.model_validate(online).runtime.target == '192.0.2.10:50051'
    for value in ({**fixture, 'readOnly': False}, {**fixture, 'mode': 'runtime'},
                  {**online, 'mode': 'fixtures'},
                  {**online, 'runtime': {**online['runtime'], 'target': 'https://host:80'}}):
        with pytest.raises(ValidationError):
            PreviewContext.model_validate(value)
