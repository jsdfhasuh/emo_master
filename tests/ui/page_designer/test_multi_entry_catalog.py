"""Static discovery and native editing; no Runtime/device execution."""
from types import SimpleNamespace

from PySide2.QtCore import Qt
from PySide2.QtWidgets import QInputDialog, QLabel

from emo_master.apps.designer.page_designer.editing import outputChoices, PageCommands
from emo_master.apps.designer.state.project_edit_session import ProjectEditSession
from emo_master.core.presentation.models import DataSource, ResultScope
from emo_master.core.presentation.validation import validateBindings
from emo_master.core.project.models import ProjectDocument
from test_editing import sendDrop


def project():
    return ProjectDocument.model_validate({
        'schemaVersion': '2.4',
        'project': {'projectId': 'two-roots', 'name': 'Two roots',
                    'createdAt': '2026-10-11T00:00:00Z', 'updatedAt': '2026-10-11T00:00:00Z'},
        'entryWorkflowId': 'one', 'workflowOrder': ['one', 'two', 'child'],
        'workflows': {
            key: {'name': 'Main', 'nodes': [{'nodeId': 'call', 'kind': 'subflow', 'targetWorkflowId': 'child'}]}
            for key in ('one', 'two')
        } | {'child': {'name': 'Shared child', 'outputs': {'count': 'integer'}}},
        'globalVariables': {'count': {'name': '拍次', 'type': 'integer', 'initialValue': 0}},
        'presentation': {}, 'resources': {}, 'production': {},
    })


def testFreshIndependentRootsAndSharedChildKeepFullIdentity():
    document = project()
    before = document.model_dump()
    choices = outputChoices(document, {})
    outputs = [c for c in choices if c.source.kind == 'workflow_output']
    assert len(outputs) == 2
    assert {c.scope.entryWorkflowId for c in outputs} == {'one', 'two'}
    assert len({c.title for c in outputs}) == 2  # duplicate workflow names disambiguated
    assert all(c.source.callPath[0].nodeId == 'call' for c in outputs)
    variables = [c for c in choices if c.source.kind == 'global_variable']
    assert {(c.scope.entryWorkflowId, c.scope.scopeWorkflowId) for c in variables} == {
        ('one', 'one'), ('one', 'child'), ('two', 'two'), ('two', 'child')}
    assert all(c.source.workflowId is None and c.source.nodeId is None and c.source.port is None for c in variables)
    assert document.model_dump() == before


def testExplicitSelectedAndDeclaredChildRootsAndInvalidScope():
    document = project()
    choices = outputChoices(document, {}, entryWorkflowIds=['child', 'deleted'])
    assert any(c.scope.entryWorkflowId == 'child' and not c.source.callPath for c in choices)
    document.presentation.resultScopes = {
        'standalone': ResultScope(entryWorkflowId='child', scopeWorkflowId='child'),
        'missing': ResultScope(entryWorkflowId='deleted', scopeWorkflowId='deleted'),
        'stale': ResultScope(entryWorkflowId='two', scopeWorkflowId='child', callPath=[{'nodeId': 'deleted'}]),
    }
    choices = outputChoices(document, {})
    assert any(c.scope.entryWorkflowId == 'child' for c in choices)
    assert all(c.scope not in [document.presentation.resultScopes[k] for k in ('missing', 'stale')] for c in choices)
    assert {issue.code for issue in validateBindings(document, {})} == {'scope_invalid'}


def testSavedAncestorScopeAndWidenedTypeAreNotReplaced():
    document = project()
    scope = ResultScope(entryWorkflowId='two', scopeWorkflowId='two')
    source = DataSource(kind='workflow_output', resultScopeId='old', workflowId='child',
                        callPath=[{'nodeId': 'call'}], port='count', expectedType='number')
    document.presentation.resultScopes['old'] = scope
    document.presentation.dataSources['old'] = source
    choices = outputChoices(document, {})
    exact = [c for c in choices if c.scope == scope and c.source == source]
    assert len(exact) == 1
    assert '快照' in exact[0].title and 'two' in exact[0].title
    session = ProjectEditSession(document.model_dump())
    page = session.presentation.createPage('Page')
    commands = PageCommands(session, {})
    number = commands.add(page, 'number', 0, 0)
    commands.bind(page, number, exact[0])
    assert session.document().presentation.pages[page].components[0].bindings == {'value': 'old'}
    assert not validateBindings(session.document(), {})


def testNativeVariableDragAndSecondJobReselectionSaveReload(tmp_path, ownedDesignerWindow, retainedQtApplication, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('page editing must not start/load execution')
    window = ownedDesignerWindow(SimpleNamespace(listOperators=lambda: [], loadProject=forbidden, startJob=forbidden))
    session = ProjectEditSession(project().model_dump())
    commands = PageCommands(session, {})
    pages = {}
    for root in ('one', 'two'):
        page = pages[root] = session.presentation.createPage(root)
        for row, kind in enumerate(('global_variable', 'workflow_output')):
            number = commands.add(page, 'number', row, 0)
            choice = next(c for c in outputChoices(session.document(), {})
                          if c.source.kind == kind and c.scope.entryWorkflowId == root and c.scope.scopeWorkflowId == 'child')
            commands.bind(page, number, choice)
    target = tmp_path / 'native.emoproj'
    session.save(target)
    assert window.loadProjectDirectory(str(target), confirmed=True)
    before = window.pageCoordinator.session.document().presentation
    window.pageCoordinator.showPages()
    editor = window.pageCoordinator.editor
    assert window.pageCoordinator.pageActive()
    assert not window.pageCoordinator.session.dirty
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *a, **k: ('bind', True))
    for root, page in pages.items():
        editor.navigatePage(page)
        for component in before.pages[page].components:
            editor.tools.select(component.componentId)
            index = editor.tools.binding.currentData()
            choice = editor.tools.choices[index]
            assert choice.scope.entryWorkflowId == root
            assert choice.scope.scopeWorkflowId == 'child'
            assert root in editor.tools.binding.currentText()
            assert 'None.None' not in editor.tools.title.toolTip()
            widget = editor.renderer.widgets[page][component.componentId][1]
            hint = widget.parentWidget().findChild(QLabel, 'bindingHint').text()
            assert 'None' not in hint
            if choice.source.kind == 'global_variable':
                assert '全局变量 count' in hint
            # Clear and actually recreate the binding, not merely open the form.
            editor.run(editor.tools.clearBinding)
            assert editor.tools.binding.currentIndex() == -1
            widget = editor.renderer.widgets[page][component.componentId][1]
            assert sendDrop(widget, {'choice': index}), editor.message.text()
            editor.tools.select(component.componentId)
            assert editor.tools.binding.currentData() == index
        assert not validateBindings(editor.session.document(), {})
    assert editor.store.snapshot() == before
    # Shared child same path under both roots must not collapse to one tree node.
    leaves = []
    def visit(item):
        if item.data(0, Qt.UserRole) is not None:
            leaves.append(item.data(0, Qt.UserRole))
        for i in range(item.childCount()):
            visit(item.child(i))
    for i in range(editor.tools.outputs.topLevelItemCount()):
        visit(editor.tools.outputs.topLevelItem(i))
    assert sorted(leaves) == list(range(len(editor.tools.choices)))
    assert window.saveProjectToDirectory(str(target))
    assert window.loadProjectDirectory(str(target), confirmed=True)
    assert window.pageCoordinator.session.document().presentation == before
    assert window.currentJobId is None and not window.isJobRunning
