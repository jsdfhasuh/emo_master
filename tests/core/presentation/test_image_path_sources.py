"""Page-local image paths persist without becoming Runtime capture sources."""
import pytest
from pydantic import ValidationError

from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.designer.state.project_store import saveProject, loadProjectDocument
from emo_master.apps.designer.state.project_edit_session import ProjectEditSession
from emo_master.apps.designer.state.presentation_store import _component
from emo_master.apps.designer.page_designer.editing import PageCommands, Choice
from emo_master.core.presentation.models import (
    Component, Props, ImagePathSource, Presentation, Page, DataSource, ResultScope, walkComponents,
)
from emo_master.core.presentation.validation import validateBindings
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.snapshots import captureDefinition
from emo_master.core.workflow.compiler import WorkflowCompiler


def documentWithPaths():
    document = ProjectDocument.model_validate(WorkflowStore().toPayload())
    document.presentation = Presentation(defaultPageId='page', pageOrder=['page'], pages={'page': {
        'name': '图片历史', 'components': [
            {'componentId': 'records', 'type': 'sqlite_table',
             'props': {'sqlite': {'databasePath': 'C:/history/records.db', 'table': 'records', 'columns': ['time']}}},
            {'componentId': 'image', 'type': 'image', 'layout': {'row': 1},
             'props': {'imagePath': {'kind': 'selected_row', 'componentId': 'records', 'field': 'image path',
                                     'baseDirectory': 'C:/history/images'}}},
        ]}})
    return document


def testPathImageRoundTripAndCaptureAreIndependent(tmp_path):
    document = documentWithPaths()
    assert document.schemaVersion == '2.9'
    assert validateBindings(document, {}, publish=True) == []
    before = captureDefinition(document.presentation)
    saveProject(tmp_path, document.model_dump(mode='json'))
    loaded = loadProjectDocument(tmp_path)
    assert loaded.presentation == document.presentation
    assert loaded.presentation.pages['page'].components[1].props.imagePath.field == 'image path'
    assert captureDefinition(loaded.presentation) == before == {'ruleVersion': '1.0', 'sources': [], 'scopes': {}}
    WorkflowCompiler().compile(loaded)
    assert 'imagePath' not in Props().model_dump()


@pytest.mark.parametrize('payload', [
    {'kind': 'fixed', 'componentId': 'table'},
    {'kind': 'fixed', 'field': 'path'},
    {'kind': 'selected_row', 'componentId': 'table'},
    {'kind': 'selected_row', 'componentId': 'table', 'field': 'path', 'path': 'x.png'},
    {'kind': 'fixed', 'path': 'x\x00.png'},
])
def testImagePathAddressRejectsAmbiguousModes(payload):
    with pytest.raises(ValidationError):
        ImagePathSource.model_validate(payload)


@pytest.mark.parametrize('kind,bindings,resource', [('text', {}, None), ('image', {'image': 'frame'}, None),
                                                   ('image', {}, 'asset')])
def testImageSourceModesAreExclusive(kind, bindings, resource):
    with pytest.raises(ValidationError, match='image path requires'):
        Component(componentId='image', type=kind, bindings=bindings,
                  props={'imageResourceId': resource, 'imagePath': {'kind': 'fixed', 'path': 'C:/x.png'}})


def testIncompleteFixedDraftAndMissingSelectionReferencesHaveClearIssues():
    document = documentWithPaths()
    item = document.presentation.pages['page'].components[1]
    item.props.imagePath = ImagePathSource(kind='fixed')
    assert validateBindings(document, {}) == []
    assert [i.code for i in validateBindings(document, {}, publish=True)] == ['image_path_unconfigured']
    item.props.imagePath = ImagePathSource(kind='fixed', path='images/one.png')
    assert [i.code for i in validateBindings(document, {})] == ['image_path_invalid']
    item.props.imagePath.baseDirectory = 'C:/data'
    assert validateBindings(document, {}, publish=True) == []
    item.props.imagePath.baseDirectory = 'relative-root'
    assert validateBindings(document, {}, publish=True) == []
    item.props.imagePath.baseDirectory = 'https://example/images'
    assert [i.code for i in validateBindings(document, {})] == ['image_path_invalid']
    item.props.imagePath = ImagePathSource(kind='selected_row', componentId='missing', field='path')
    assert ProjectDocument.model_validate(document.model_dump()).presentation == document.presentation
    assert [i.code for i in validateBindings(document, {})] == ['image_path_source_missing']
    item.props.imagePath.componentId = 'image'
    assert [i.code for i in validateBindings(document, {}, publish=True)] == ['image_path_source_missing']
    item.props.imagePath.componentId = 'records'
    table = document.presentation.pages['page'].components.pop(0)
    document.presentation.pages['other'] = Page(name='其他页面', components=[table])
    document.presentation.pageOrder.append('other')
    assert [i.code for i in validateBindings(document, {})] == ['image_path_source_missing']


def testCopiesRemapInternalReferencesAndSingleImageKeepsItsSource():
    document = documentWithPaths()
    session = ProjectEditSession(document.model_dump())
    copiedPage = session.presentation.copyPage('page')
    table, image = session.presentation.snapshot().pages[copiedPage].components
    assert image.props.imagePath.componentId == table.componentId != 'records'
    commands = PageCommands(session, {})
    copiedImage = commands.copy('page', 'image')
    assert _component(session.presentation.snapshot(), 'page', copiedImage).props.imagePath.componentId == 'records'
    container = Component(componentId='group', type='container', layout={'row': 3}, children=[
        Component(componentId='nested-table', type='sqlite_table'),
        Component(componentId='nested-image', type='image', layout={'row': 1}, props={
            'imagePath': {'kind': 'selected_row', 'componentId': 'nested-table', 'field': 'path'}})])
    session.presentation.putComponent('page', container)
    copiedContainer = commands.copy('page', 'group')
    nested = _component(session.presentation.snapshot(), 'page', copiedContainer).children
    assert nested[1].props.imagePath.componentId == nested[0].componentId != 'nested-table'
    assert session.undo()
    assert copiedContainer not in {item.componentId for item in walkComponents(session.presentation.snapshot().pages['page'].components)}


def testWorkflowBindingReplacesLocalPathForBothCommandEntrypoints():
    document = documentWithPaths()
    workflow = document.entryWorkflowId
    document.workflows[workflow].outputs['frame'] = {'type': 'image'}
    session = ProjectEditSession(document.model_dump())
    source = DataSource(kind='workflow_output', resultScopeId='scope', workflowId=workflow, port='frame', expectedType='image')
    scope = ResultScope(entryWorkflowId=workflow, scopeWorkflowId=workflow)
    PageCommands(session, {}).bind('page', 'image', Choice('frame', source, scope, ''))
    image = _component(session.presentation.snapshot(), 'page', 'image')
    assert image.bindings and image.props.imagePath is None
    assert session.undo()
    session.presentation.rebind('page', 'image', 'image', source)
    assert _component(session.presentation.snapshot(), 'page', 'image').props.imagePath is None


def testStaticResourceUpdateReplacesLocalPathAndUndoRestoresIt():
    session = ProjectEditSession(documentWithPaths().model_dump())
    image = _component(session.presentation.snapshot(), 'page', 'image')
    PageCommands(session, {}).update('page', 'image', props=Props(imageResourceId='asset'), layout=image.layout)
    updated = _component(session.presentation.snapshot(), 'page', 'image')
    assert updated.props.imageResourceId == 'asset' and updated.props.imagePath is None
    assert session.undo()
    assert _component(session.presentation.snapshot(), 'page', 'image').props.imagePath == image.props.imagePath
