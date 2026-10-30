"""Responsive geometry and its portable project contract."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.core.presentation.models import (
    Component, Grid, Page, Presentation, ResponsiveItem, ResponsiveLayout,
)
from emo_master.core.presentation.responsive import Measurement, solveResponsive
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler


def items(count=4, **kwargs):
    return [Measurement(str(i), ResponsiveItem(**kwargs)) for i in range(count)]


def testWrapReflowsInStableOrderAndClampsToNarrowWidth():
    rules = ResponsiveLayout(direction='wrap', spacing=10)
    wide, wideHeight = solveResponsive(rules, items(), 1000)
    middle, middleHeight = solveResponsive(rules, items(), 500)
    narrow, narrowHeight = solveResponsive(rules, items(), 100)
    assert [box.y for box in wide] == [0, 0, 0, 0]
    assert [box.y for box in middle] == [0, 0, 74, 74]
    assert [box.y for box in narrow] == [0, 74, 148, 222]
    assert all(box.width == 100 for box in narrow)
    assert wideHeight < middleHeight < narrowHeight
    assert wide == solveResponsive(rules, items(), 1000)[0]


def testHorizontalWeightsAndContentWidth():
    rules = ResponsiveLayout(direction='horizontal', spacing=10, alignment='start')
    row = [Measurement('a', ResponsiveItem(minWidth=100, weight=1)),
           Measurement('b', ResponsiveItem(minWidth=100, weight=3)),
           Measurement('c', ResponsiveItem(width='content', minWidth=100, preferredWidth=120))]
    boxes, _height = solveResponsive(rules, row, 640)
    assert [box.width for box in boxes] == [175, 325, 120]
    assert boxes[-1].x + boxes[-1].width == 640
    assert solveResponsive(rules, row, 150)[0][-1].y > 0


def testContentAlignmentAndHeightForWidthRetainNaturalContent():
    rules = ResponsiveLayout(direction='vertical', alignment='start', justify='space-between', spacing=8)
    row = [Measurement('a', ResponsiveItem(width='content', preferredWidth=100, minWidth=0,
        minHeight=0, preferredHeight=10, alignSelf='end'), heightForWidth=lambda width: 400 / width),
        Measurement('b', ResponsiveItem(minHeight=20), hintHeight=20)]
    boxes, naturalHeight = solveResponsive(rules, row, 300, 300)
    assert boxes[0].x == 200 and boxes[0].width == 100
    assert boxes[0].height == 10
    assert boxes[1].y + boxes[1].height == 300
    assert naturalHeight == 38
    row[0] = Measurement('a', ResponsiveItem(minHeight=0, preferredHeight=10),
                         heightForWidth=lambda _width: 200)
    assert solveResponsive(rules, row, 300)[0][0].height == 200


def testNewProjectRoundtripCompileAndFormatBoundary(tmp_path):
    payload = WorkflowStore().toPayload('Responsive')
    assert payload['schemaVersion'] == '2.8'
    assert payload['presentation']['schemaVersion'] == '1.1'
    assert payload['devices'] == {'bindings': {}, 'schemaVersion': '1.0', 'definitions': {}}
    assert 'communication' not in payload
    document = ProjectDocument.model_validate(payload)
    document.presentation = Presentation(defaultPageId='p', pageOrder=['p'], pages={
        'p': Page(name='Responsive', layout=ResponsiveLayout(direction='wrap'), components=[
            Component(componentId='title', type='page_title'),
            Component(componentId='clock', type='clock'),
        ])})
    target = tmp_path / 'responsive.emoproj'
    saveProject(target, document.model_dump(mode='json'))
    reloaded = loadProjectDocument(target)
    assert reloaded.presentation == document.presentation
    assert WorkflowCompiler().compile(reloaded)
    old = deepcopy(document.model_dump())
    old['schemaVersion'] = '2.7'
    with pytest.raises(ValidationError, match='requires project 2.8'):
        ProjectDocument.model_validate(old)


def testLegacyPresentationMissingLayoutsStillUsesGrid():
    payload = WorkflowStore().toPayload()
    payload['schemaVersion'] = '2.7'
    payload['presentation'] = {'defaultPageId': 'p', 'pageOrder': ['p'], 'pages': {
        'p': {'name': 'Legacy', 'components': [{'componentId': 'x', 'type': 'text'}]}}}
    project = ProjectDocument.model_validate(payload)
    assert project.presentation.schemaVersion == '1.0'
    assert isinstance(project.presentation.pages['p'].layout, Grid)
    assert isinstance(project.presentation.pages['p'].components[0].grid, Grid)
    assert project.schemaVersion == '2.7'


def testResponsiveLayoutDoesNotChangeExecutionOrCaptureIdentity(tmp_path):
    from emo_master.apps.runtime.presentation.normal_capture import freezeNormalCapture

    document = ProjectDocument.model_validate(WorkflowStore().toPayload())
    document.presentation = Presentation(defaultPageId='p', pageOrder=['p'], pages={
        'p': Page(name='Page', components=[Component(componentId='clock', type='clock')])})
    first = freezeNormalCapture(document, {}, tmp_path, document.entryWorkflowId)
    document.presentation.pages['p'].layout.direction = 'wrap'
    document.presentation.pages['p'].components[0].responsive.preferredWidth = 420
    second = freezeNormalCapture(document, {}, tmp_path, document.entryWorkflowId)
    assert first.executionRevision == second.executionRevision
    assert first.capturePlanRevision == second.capturePlanRevision


@pytest.mark.parametrize('field,value', [('minWidth', -1), ('weight', -1), ('width', 'absolute')])
def testResponsiveItemRejectsInvalidRules(field, value):
    with pytest.raises(ValidationError):
        ResponsiveItem(**{field: value})
