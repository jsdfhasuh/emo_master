"""§5.1 property editing contracts, intentionally independent of Qt/Runtime."""
from dataclasses import replace

import pytest
from pydantic import ValidationError

from emo_master.apps.designer.page_designer.property_adapters import (
    actionFromFields, decodeIndicatorKey, encodeIndicatorKey, indicatorStatesFromRows,
    tableFieldChoices,
)
from emo_master.core.presentation.catalog import buildOutputCatalog
from emo_master.core.presentation.models import Component, DataSource, Props


def testLegacyPropsGetBoundedAppearanceDefaults(project):
    props = Props.model_validate({'title': 'Legacy page', 'text': 'Ready'})
    assert (props.fontFamily, props.fontSize, props.fontWeight, props.textColor, props.cardStyle) == (
        'system', 0, 'normal', 'default', 'plain')
    assert project.presentation.schemaVersion == '1.0'
    assert Props.model_validate_json(props.model_dump_json()) == props


@pytest.mark.parametrize('size', [0, 8, 12, 24, 48])
def testFontSizeIsAutomaticOrBoundedPixels(size):
    assert Props(fontSize=size).fontSize == size


@pytest.mark.parametrize('size', [-1, 1, 2, 3, 4, 5, 6, 7, 49, True, False, 8.0, '12', None])
def testFontSizeRejectsSmallAndNonIntegerSizes(size):
    with pytest.raises(ValidationError):
        Props(fontSize=size)


@pytest.mark.parametrize(('field', 'values'), [
    ('fontFamily', ['system', 'sans', 'serif', 'monospace']),
    ('fontWeight', ['normal', 'bold']),
    ('textColor', ['default', 'neutral', 'green', 'red', 'amber']),
    ('cardStyle', ['plain', 'soft', 'outlined']),
])
def testAppearanceEnumsRoundTrip(field, values):
    for value in values:
        props = Props.model_validate({field: value})
        assert getattr(Props.model_validate_json(props.model_dump_json()), field) == value


@pytest.mark.parametrize('values', [
    {'fontFamily': 'Arial'}, {'fontFamily': 'sans; color: red'},
    {'fontWeight': 700}, {'fontWeight': 'light'}, {'textColor': '#00ff00'},
    {'cardStyle': 'QLabel { background: red; }'}, {'styleSheet': 'color: red'},
    {'fontSize': 1000000},
])
def testAppearanceRejectsFreeFormStylesAndUnknownProperties(values):
    with pytest.raises(ValidationError):
        Props.model_validate(values)


@pytest.mark.parametrize(('kind', 'value', 'key', 'decoded'), [
    ('boolean', True, 'true', True), ('boolean', False, 'false', False),
    ('boolean', 'true', 'true', True), ('boolean', 'false', 'false', False),
    ('string', 'OK', '"OK"', 'OK'), ('string', 'false', '"false"', 'false'),
    ('string', '', '""', ''), ('string', 'NG 待确认', '"NG 待确认"', 'NG 待确认'),
    ('string', '"OK"', '"\\"OK\\""', '"OK"'),
    ('string', 'ready\nnow', '"ready\\nnow"', 'ready\nnow'),
])
def testIndicatorKeysUseTypedLiteralValues(kind, value, key, decoded):
    assert encodeIndicatorKey(kind, value) == key
    assert decodeIndicatorKey(key) == (kind, decoded)


@pytest.mark.parametrize(('kind', 'value'), [
    ('boolean', ''), ('boolean', 'False'), ('boolean', ' false '),
    ('boolean', '0'), ('boolean', 0), ('boolean', 1), ('boolean', None),
    ('boolean', []), ('string', False), ('string', 1), ('string', None),
    ('number', '1'), ('bool', False), ('', 'OK'), ('string', 'x' * 255),
])
def testIndicatorInputRejectsCoercionUnsupportedTypesAndLongKeys(kind, value):
    with pytest.raises(ValueError):
        encodeIndicatorKey(kind, value)


@pytest.mark.parametrize('key', [
    'OK', 'False', ' false', 'false ', '0', '1', 'null', '{}', '[]',
    '"\\u004fK"', '"x" ' , '"' + 'x' * 255 + '"', None, False,
])
def testIndicatorDecodeRejectsNoncanonicalModelKeys(key):
    with pytest.raises(ValueError):
        decodeIndicatorKey(key)


def testIndicatorRowsPreserveFalseEmptyValuesAndTypeDistinctions():
    states = indicatorStatesFromRows([
        ('boolean', False, 'NG', 'red'),
        ('string', 'false', 'Literal false', 'amber'),
        ('string', '', '', 'neutral'),
        ('string', 'OK', 'Pass', 'green'),
    ])
    assert list(states) == ['false', '"false"', '""', '"OK"']
    assert states['false'].text == 'NG'
    assert states['""'].text == ''
    assert Props.model_validate_json(Props(indicatorStates=states).model_dump_json()).indicatorStates == states


@pytest.mark.parametrize('rows', [
    [('boolean', False, 'NG', 'red'), ('boolean', 'false', 'Fail', 'red')],
    [('string', 'OK', 'Pass', 'green'), ('string', 'OK', 'Ready', 'neutral')],
    [('string', '', '', 'neutral'), ('string', '', 'Empty', 'amber')],
])
def testIndicatorRowsRejectDuplicateCanonicalKeys(rows):
    with pytest.raises(ValueError, match='duplicate'):
        indicatorStatesFromRows(rows)


def testIndicatorRowBoundsAndStylesAreValidated():
    assert indicatorStatesFromRows([]) == {}
    rows = [('string', str(i), str(i), 'neutral') for i in range(17)]
    assert len(indicatorStatesFromRows(rows[:16])) == 16
    with pytest.raises(ValueError, match='16'):
        indicatorStatesFromRows(rows)
    with pytest.raises(ValidationError):
        indicatorStatesFromRows([('string', 'OK', 'Pass', '#00ff00')])
    with pytest.raises(ValidationError):
        indicatorStatesFromRows([('string', 'OK', 'x' * 257, 'green')])


def collectionSource(**changes):
    return DataSource.model_validate({
        'kind': 'node_output', 'resultScopeId': 'root', 'workflowId': 'main',
        'nodeId': 'blob', 'port': 'blobs', 'expectedType': 'collection', **changes,
    })


@pytest.mark.parametrize(('kind', 'paths'), [
    ('blobCollection', [['area'], ['centroid', 'x'], ['centroid', 'y']]),
    ('detectionCollection', [['classId'], ['label'], ['confidence']]),
])
def testTableFieldsComeFromKnownCatalogContract(project, manifests, kind, paths):
    manifest = manifests['vision.analysis.blob']
    manifests[manifest.operatorId] = replace(manifest, outputPorts={
        'blobs': {'type': kind, 'schemaVersion': '1.x'},
    })
    entries = buildOutputCatalog(project, manifests)
    source = collectionSource()
    before = source.model_dump()
    columns = tableFieldChoices(source, entries)
    assert [column.fieldPath for column in columns] == paths
    assert [column.title for column in columns] == ['.'.join(path) for path in paths]
    assert source.model_dump() == before
    columns[0].fieldPath.append('changed')
    assert [column.fieldPath for column in tableFieldChoices(source, entries)] == paths


@pytest.mark.parametrize('path', [['items', '*', 'area'], ['items', '*', 'centroid', 'x']])
def testProjectedTableCollectionsUseScalarRowWithoutRepeatingProjection(project, manifests, path):
    columns = tableFieldChoices(collectionSource(fieldPath=path), buildOutputCatalog(project, manifests))
    assert len(columns) == 1
    assert columns[0].fieldPath == []
    assert columns[0].title == '.'.join(path[2:])


def testKnownWorkflowOutputSupportsSameRowFields(project, manifests):
    project.workflows['main'].outputs['blobs'] = 'blobCollection'
    source = collectionSource(kind='workflow_output', nodeId=None)
    columns = tableFieldChoices(source, buildOutputCatalog(project, manifests))
    assert [column.fieldPath for column in columns] == [['area'], ['centroid', 'x'], ['centroid', 'y']]


@pytest.mark.parametrize('spec', [
    'json', 'object', 'any', 'list<integer>', 'list<any>', 'contourCollection',
    {'type': 'blobCollection', 'schemaVersion': '2.0'},
])
def testUnknownTableSchemasAreExplicitlyUnsupported(project, manifests, spec):
    manifest = manifests['vision.analysis.blob']
    manifests[manifest.operatorId] = replace(manifest, outputPorts={'blobs': spec})
    with pytest.raises(ValueError):
        tableFieldChoices(collectionSource(), buildOutputCatalog(project, manifests))


@pytest.mark.parametrize('changes', [
    {'nodeId': 'missing'}, {'port': 'missing'}, {'expectedType': 'number'},
    {'fieldPath': ['items', '0', 'area']}, {'fieldPath': ['items', '*', 'attributes']},
    {'fieldPath': ['area']},
])
def testTableFieldsRejectMissingOrIncompatibleSources(project, manifests, changes):
    with pytest.raises(ValueError):
        tableFieldChoices(collectionSource(**changes), buildOutputCatalog(project, manifests))


def testTableFieldsRequireTrustedManifestAndMatchingPort(project, manifests):
    source = collectionSource()
    with pytest.raises(ValueError):
        tableFieldChoices(source, buildOutputCatalog(project, {}))
    blob = next(node for node in project.workflows['main'].nodes if node.nodeId == 'blob')
    blob.outputPorts['blobs'] = 'list<integer>'
    with pytest.raises(ValueError, match='disagree'):
        tableFieldChoices(source, buildOutputCatalog(project, manifests))


@pytest.mark.parametrize(('mode', 'target', 'scope', 'expected'), [
    ('none', None, None, None),
    ('navigate', 'detail', None, {'type': 'navigate', 'pageId': 'detail',
                                'context': 'live', 'resultScopeId': None}),
    ('detail', 'detail', 'root', {'type': 'navigate', 'pageId': 'detail',
                                'context': 'displayed_result', 'resultScopeId': 'root'}),
    ('freeze', None, 'root', {'type': 'freeze', 'pageId': None,
                             'context': 'displayed_result', 'resultScopeId': 'root'}),
    ('resume_live', None, None, {'type': 'resume_live', 'pageId': None,
                               'context': 'live', 'resultScopeId': None}),
])
def testActionModesReuseExistingActionWithoutMutation(project, mode, target, scope, expected):
    presentation = project.presentation
    before = presentation.model_dump()
    action = actionFromFields(presentation, 'overview', mode, target, scope)
    assert (None if action is None else action.model_dump()) == expected
    assert presentation.model_dump() == before


@pytest.mark.parametrize(('mode', 'target', 'scope'), [
    ('start_job', None, None), ('stop_job', None, None), ('navigate', None, None),
    ('navigate', 'missing', None), ('navigate', 'detail', 'root'),
    ('detail', 'missing', 'root'), ('detail', 'detail', None),
    ('detail', 'detail', 'missing'), ('freeze', None, None),
    ('freeze', 'detail', 'root'), ('freeze', None, 'missing'),
    ('none', 'detail', None), ('none', None, 'root'),
    ('resume_live', 'detail', None), ('resume_live', None, 'root'),
])
def testActionEditorRejectsMissingTargetsScopesAndIrrelevantInputs(project, mode, target, scope):
    with pytest.raises(ValueError):
        actionFromFields(project.presentation, 'overview', mode, target, scope)


def testActionScopesMustBeDeclaredAndSupportedByBothPages(project):
    presentation = project.presentation
    with pytest.raises(ValueError, match='current page'):
        actionFromFields(presentation, 'missing', 'none')
    presentation.pages['detail'].resultScopeIds = []
    with pytest.raises(ValueError, match='target page'):
        actionFromFields(presentation, 'overview', 'detail', 'detail', 'root')
    # Nested component bindings count as support, via the existing pageScopes.
    presentation.pages['detail'].components = [Component(
        componentId='container', type='container', children=[Component(
            componentId='nested', type='number', bindings={'value': 'count'})])]
    assert actionFromFields(presentation, 'overview', 'detail', 'detail', 'root') is not None
    presentation.pages['overview'].components[0].bindings = {}
    with pytest.raises(ValueError, match='current page'):
        actionFromFields(presentation, 'overview', 'freeze', resultScopeId='root')
    presentation.pages['overview'].resultScopeIds = ['root']
    assert actionFromFields(presentation, 'overview', 'freeze', resultScopeId='root') is not None
    presentation.resultScopes.clear()
    with pytest.raises(ValueError, match='existing result scope'):
        actionFromFields(presentation, 'overview', 'freeze', resultScopeId='root')
