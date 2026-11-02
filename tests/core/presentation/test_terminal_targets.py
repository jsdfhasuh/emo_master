"""Remote declarations extend formats without changing old local documents."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from emo_master.apps.designer.state.project_edit_session import ProjectEditSession
from emo_master.apps.designer.state.project_store import saveProject, loadProjectDocument
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.core.presentation.models import Action, Component, DataSource, ImagePathSource, Page, Presentation, ResultScope
from emo_master.core.presentation.targets import effectiveImageLocation, presentationForTarget
from emo_master.core.presentation.validation import validateBindings
from emo_master.core.project.migration import migrateProjectPayload
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.snapshots import captureDefinition
from emo_master.core.workflow.compiler import WorkflowCompiler


def testNewDefaultAndExplicitLegacyUpgradeRoundtrip(tmp_path):
    payload = WorkflowStore().toPayload()
    assert (payload['schemaVersion'], payload['presentation']['schemaVersion']) == ('2.9', '1.2')
    document = ProjectDocument.model_validate(payload)
    assert WorkflowCompiler().compile(document)
    saveProject(tmp_path / 'new.emoproj', payload)
    assert loadProjectDocument(tmp_path / 'new.emoproj') == document
    for version, pageVersion in [('2.7', '1.0'), ('2.8', '1.1')]:
        old = deepcopy(payload)
        old['schemaVersion'], old['presentation']['schemaVersion'] = version, pageVersion
        assert migrateProjectPayload(old)['schemaVersion'] == version
        withoutPageVersion = deepcopy(old)
        withoutPageVersion['presentation'].pop('schemaVersion')
        assert ProjectDocument.model_validate(withoutPageVersion).presentation.schemaVersion == pageVersion
        upgraded = migrateProjectPayload(old, enableRemotePresentation=True)
        assert (upgraded['schemaVersion'], upgraded['presentation']['schemaVersion']) == ('2.9', '1.2')
        assert upgraded['devices'] == old['devices']
        assert upgraded['production'] == old['production']


def testOldFormatsRejectRemoteSemanticsAndKeepLocalSerialization():
    local = Presentation(schemaVersion='1.1', defaultPageId='p', pageOrder=['p'], pages={
        'p': Page(name='Page', components=[Component(componentId='t', type='sqlite_table'),
            Component(componentId='i', type='image', props={'imagePath': {'kind': 'fixed', 'path': 'image.png', 'baseDirectory': '.'}})])})
    wire = local.model_dump()
    assert 'targets' not in wire['pages']['p']
    assert 'location' not in wire['pages']['p']['components'][1]['props']['imagePath']
    bad = deepcopy(wire)
    bad['pages']['p']['targets'] = ['web']
    with pytest.raises(ValidationError, match='schemaVersion 1.2'):
        Presentation.model_validate(bad)
    bad = deepcopy(wire)
    bad['pages']['p']['components'][0]['props']['sqlite'] = {'location': 'runtime'}
    with pytest.raises(ValidationError, match='schemaVersion 1.2'):
        Presentation.model_validate(bad)


@pytest.mark.parametrize('targets', [[], ['web', 'web'], ['mobile']])
def testInvalidTargetsReject(targets):
    with pytest.raises(ValidationError):
        Page(name='Invalid', targets=targets)


def testTerminalViewsFilterNavigationButCaptureRetainsBothSources():
    presentation = Presentation(defaultPageId='web', pageOrder=['web', 'desktop'],
        resultScopes={'scope': ResultScope(entryWorkflowId='main', scopeWorkflowId='main')},
        dataSources={key: DataSource(kind='runtime_status', name=key, expectedType='string', resultScopeId='scope')
                     for key in ('left', 'right')}, pages={
            'desktop': Page(name='Desktop', components=[Component(componentId='d', type='runtime_status', bindings={'value': 'left'})]),
            'web': Page(name='Web', targets=['web'], components=[Component(componentId='w', type='runtime_status', bindings={'value': 'right'})])})
    original = presentation.model_dump()
    desktop = presentationForTarget(presentation, 'desktop')
    web = presentationForTarget(presentation, 'web')
    assert desktop.pageOrder == ['desktop'] and desktop.defaultPageId == 'desktop'
    assert web.pageOrder == ['web'] and web.defaultPageId == 'web'
    assert len(captureDefinition(presentation)['sources']) == 2
    assert presentation.model_dump() == original
    document = ProjectDocument.model_validate(WorkflowStore().toPayload())
    document.presentation = presentation
    presentation.pages['desktop'].components.append(Component(componentId='go', type='navigation_button',
        actions={'clicked': Action(type='navigate', pageId='web')}))
    assert any(issue.code == 'navigation_target_invalid' for issue in validateBindings(document, {}))
    assert presentationForTarget(presentation, 'desktop').pages['desktop'].components[-1].actions == {}


def testTargetAndRuntimeLocationUpgradeUndoCopySave(tmp_path):
    payload = WorkflowStore().toPayload()
    payload['schemaVersion'], payload['presentation']['schemaVersion'] = '2.8', '1.1'
    session = ProjectEditSession(payload)
    pageId = session.presentation.createPage('History')
    session.editPresentation(lambda p: p.pages[pageId].components.extend([
        Component(componentId='t', type='sqlite_table', props={'sqlite': {'databasePath': 'history.db', 'table': 'records', 'columns': ['image']}}),
        Component(componentId='i', type='image', props={'imagePath': {'kind': 'selected_row', 'componentId': 't', 'field': 'image', 'baseDirectory': 'images'}})]))
    old = session.payload()
    def edit(p):
        p.pages[pageId].targets = ['desktop', 'web']
        p.pages[pageId].components[0].props.sqlite.location = 'runtime'
    session.editPresentation(edit)
    assert session.document().schemaVersion == '2.9'
    assert session.undo() and session.payload() == old
    assert session.redo()
    copiedId = session.presentation.copyPage(pageId)
    p = session.presentation.snapshot()
    table, image = p.pages[copiedId].components
    assert table.componentId != 't' and image.props.imagePath.componentId == table.componentId
    assert image.props.imagePath.location is None
    assert effectiveImageLocation(p, copiedId, image) == 'runtime'
    assert p.pages[copiedId].targets == ['desktop', 'web']
    session.save(tmp_path / 'history.emoproj')
    assert loadProjectDocument(tmp_path / 'history.emoproj').presentation == session.document().presentation
    with pytest.raises(ValidationError, match='inherited'):
        ImagePathSource(kind='selected_row', componentId='t', field='image', location='runtime')
