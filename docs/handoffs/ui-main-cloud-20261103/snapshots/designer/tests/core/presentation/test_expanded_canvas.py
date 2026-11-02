"""Persistent page bounds preserve responsive flow, history and publication."""
import pytest
from pydantic import ValidationError

from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.core.presentation.models import Component, Page, Presentation, ResponsiveItem, ResponsiveLayout
from emo_master.core.presentation.responsive import Measurement, solveResponsive
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.web_panel import buildWebPanel, readWebPanel
from emo_master.core.workflow.compiler import WorkflowCompiler


def expanded():
    return Presentation(defaultPageId='p', pageOrder=['p'], pages={
        'p': Page(name='Expanded', targets=['desktop', 'web'], canvasTop=-120, components=[
            Component(componentId='title', type='page_title', responsive={'canvasOffsetY': -120}),
            Component(componentId='body', type='text', props={'text': 'Body'})])})


def testExpansionRoundtripCompileAndPublishedContract(tmp_path):
    document = ProjectDocument.model_validate(WorkflowStore().toPayload('Expanded'))
    document.presentation = expanded()
    target = tmp_path / 'expanded.emoproj'
    saveProject(target, document.model_dump(mode='json'))
    assert loadProjectDocument(target) == document
    assert WorkflowCompiler().compile(document)
    package = buildWebPanel(document, tmp_path, tmp_path / 'expanded.emoweb')
    manifest, published = readWebPanel(package)
    assert 'layout.expanded-canvas-v1' in manifest['rendererRequirements']
    assert published['pages']['p']['canvasTop'] == -120
    assert published['pages']['p']['components'][0]['responsive']['canvasOffsetY'] == -120
    assert 'canvasOffsetY' not in published['pages']['p']['components'][1]['responsive']
    assert 'canvasTop' not in Page(name='Unexpanded').model_dump()
    assert 'canvasOffsetY' not in ResponsiveItem().model_dump()


@pytest.mark.parametrize('invalid', ['nested', 'grid', 'outside', 'old', 'positive'])
def testInvalidCanvasPlacementCannotBeSaved(invalid):
    payload = expanded().model_dump()
    page = payload['pages']['p']
    if invalid == 'nested':
        page['components'] = [{'componentId': 'group', 'type': 'container', 'children': page['components']}]
    elif invalid == 'grid':
        page['layout'] = {'type': 'grid'}
    elif invalid == 'outside':
        page['components'][0]['responsive']['canvasOffsetY'] = -121
    elif invalid == 'old':
        payload['schemaVersion'] = '1.1'
    else:
        page['canvasTop'] = 1
    with pytest.raises(ValidationError):
        Presentation.model_validate(payload)


@pytest.mark.parametrize('direction,width', [('vertical', 500), ('horizontal', 500), ('wrap', 300)])
def testExpansionRetainsFlowSlotsWidthAndFlexibleHeight(direction, width):
    rules = ResponsiveLayout(direction=direction)
    items = [Measurement('title', ResponsiveItem()), Measurement('gap', ResponsiveItem(
        minWidth=24, preferredWidth=24, minHeight=24, preferredHeight=24), spacerMode='flex'),
        Measurement('body', ResponsiveItem())]
    before, height = solveResponsive(rules, items, width, 600)
    items[0].responsive.canvasOffsetY = -120
    after, expandedHeight = solveResponsive(rules, items, width, 720, canvasTop=-120)
    for index, (old, new) in enumerate(zip(before, after)):
        assert (new.x, new.width, new.height) == (old.x, old.width, old.height)
        assert new.y - 120 == old.y - (120 if index == 0 else 0)
    assert expandedHeight >= height + 120


def testReorderGroupingAndOlderResponsiveUpgradeRemainUndoable():
    from test_responsive_editing import sessionFor, pageOf, text
    from emo_master.apps.designer.state.project_edit_session import ProjectEditSession
    from emo_master.apps.designer.page_designer.responsive_editing import ResponsiveCommands
    from emo_master.apps.designer.page_designer.editing import PageCommands

    session, _commands = sessionFor([text('a'), text('b'), text('c')])
    old = session.payload()
    old['schemaVersion'], old['presentation']['schemaVersion'] = '2.8', '1.1'
    session = ProjectEditSession(old)
    commands = ResponsiveCommands(session)
    before = session.payload()
    commands.place('p', keys=['a'], index=0, canvasTop=-120)
    assert session.document().schemaVersion == '2.9'
    assert session.document().presentation.schemaVersion == '1.2'
    assert pageOf(session).canvasTop == -120
    session.undo()
    assert session.payload() == before
    session.redo()
    copiedId = PageCommands(session, {}).copy('p', 'a')
    copied = next(item for item in pageOf(session).components if item.componentId == copiedId)
    assert copied.responsive.canvasOffsetY == 0
    assert pageOf(session).canvasTop == -120
    assert pageOf(session).components[0].responsive.canvasOffsetY == -120
    session.undo()
    # Dropping beside an already elevated card creates a legal nested group.
    commands.place('p', keys=['b'], besideId='a', direction='horizontal')
    group = pageOf(session).components[0]
    assert group.type == 'container'
    assert all(child.responsive.canvasOffsetY == 0 for child in group.children)
    assert pageOf(session).canvasTop == -120
    commands.ungroup('p', group.componentId)
    assert pageOf(session).canvasTop == -120
    commands.group('p', ['a', 'b'], 'horizontal')
    assert pageOf(session).canvasTop == -120
