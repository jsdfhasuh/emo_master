from dataclasses import fields
import json
from pathlib import Path

import pytest
from PySide2.QtCore import QPoint, QPointF, Qt
from PySide2.QtGui import QDragEnterEvent, QDropEvent
from PySide2.QtWidgets import QApplication, QInputDialog, QWidget

from test_workspace import designer  # noqa: F401
from emo_master.apps.designer.page_designer.editing import PageCommands, outputChoices
from emo_master.apps.designer.page_designer.tools import mime
from emo_master.apps.designer.state.project_edit_session import ProjectEditSession
from emo_master.core.plugin.models import PluginManifest
from emo_master.core.presentation.models import Presentation
from examples.runtime_pages_p2 import sampleProject


def metadata():
    root = Path(__file__).resolve().parents[3] / 'src/emo_master/plugins/builtins'
    values = []
    for folder in ['blob_analysis', 'collection_count', 'image_loader']:
        raw = json.loads((root/folder/'manifest.json').read_text(encoding='utf-8'))
        values.append(PluginManifest(**{f.name: raw[f.name] for f in fields(PluginManifest)
                                      if f.name in raw and f.name != 'editor'}))
    return {v.operatorId: v for v in values}


def blank(root):
    document = sampleProject(root)
    document.presentation = Presentation()
    return document


def sendDrop(target, payload):
    data = mime(payload)
    point = QPoint(15, 15)
    enter = QDragEnterEvent(point, Qt.CopyAction, data, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(target, enter)
    event = QDropEvent(QPointF(point), Qt.CopyAction, data, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(target, event)
    QApplication.processEvents()
    return event.isAccepted()


def testBindingCopyRollbackAndMissingNode(tmp_path):
    doc = blank(tmp_path)
    second = doc.workflows['main'].nodes[3].model_copy(deep=True)
    second.nodeId = 'count-two'
    doc.workflows['main'].nodes.append(second)
    session = ProjectEditSession(doc.model_dump())
    p = session.presentation.createPage('overview')
    commands = PageCommands(session, metadata())
    number = commands.add(p, 'number', 0, 0)
    choices = outputChoices(session.document(), metadata())
    one = next(c for c in choices if c.source.nodeId == 'count')
    two = next(c for c in choices if c.source.nodeId == 'count-two')
    image = next(c for c in choices if c.source.nodeId == 'load' and c.source.port == 'image')
    commands.bind(p, number, one)
    original = session.payload()
    with pytest.raises(ValueError, match='类型不兼容'):
        commands.bind(p, number, image)
    assert session.payload() == original
    copy = session.presentation.copyPage(p)
    copied = session.presentation.snapshot().pages[copy].components[0].componentId
    commands.bind(copy, copied, two)
    state = session.presentation.snapshot()
    assert state.pages[p].components[0].bindings != state.pages[copy].components[0].bindings
    session.undo()
    assert session.presentation.snapshot().pages[p].components[0].bindings == session.presentation.snapshot().pages[copy].components[0].bindings
    session.redo()
    with session.transaction():
        session.workflows.workflows['main'].nodes = [n for n in session.workflows.workflows['main'].nodes if n['nodeId'] != 'count-two']
    from emo_master.core.presentation.validation import validateBindings
    assert any('missing' in issue.message for issue in validateBindings(session.document(), metadata()))
    session.undo()
    assert not validateBindings(session.document(), metadata())


def testNativeDragComponentVariablePropertiesAndOverlap(designer, monkeypatch, tmp_path):  # noqa: F811
    c = designer.pageCoordinator
    designer.workflowController.loadPayload(blank(tmp_path).model_dump())
    c.session.acceptLoaded()
    from test_preview import waitFor
    waitFor(lambda: designer.operatorCatalogController.state == 'ready')
    catalog = []
    from dataclasses import asdict
    for manifest in metadata().values():
        catalog.append(asdict(manifest))
    designer.operatorCatalog = catalog
    c.showPages()
    e = c.editor
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('首页', True))
    e.run(e.newPage)
    QApplication.processEvents()
    body = next(w for w in e.renderer.findChildren(QWidget) if w.property('pageGrid') == e.pageId)
    assert sendDrop(body, {'kind': 'number'})
    item = e.store.snapshot().pages[e.pageId].components[0]
    original = c.session.payload()
    widget = e.renderer.widgets[e.pageId][item.componentId][1]
    assert not sendDrop(widget, {'kind': 'text'})  # occupied cell, atomic rollback
    assert c.session.payload() == original
    widget = e.renderer.widgets[e.pageId][item.componentId][1]
    index = next(i for i, choice in enumerate(e.tools.choices) if choice.source.nodeId == 'count')
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *a, **k: ('bind', True))
    assert sendDrop(widget, {'choice': index}), e.message.text()
    assert e.store.snapshot().pages[e.pageId].components[0].bindings
    e.tools.select(item.componentId)
    e.tools.fields['title'].setText('真实数量')
    e.run(e.tools.apply)
    assert e.store.snapshot().pages[e.pageId].components[0].props.title == '真实数量'
    assert w_no_extra_job(designer)


def w_no_extra_job(window):
    return window.currentJobId is None and not window.isJobRunning


def testContainerMoveRejectsCycleAndOverlaps(tmp_path):
    session = ProjectEditSession(blank(tmp_path).model_dump())
    p = session.presentation.createPage('page')
    commands = PageCommands(session, metadata())
    box = commands.add(p, 'container', 0, 0)
    child = commands.add(p, 'number', 0, 0, box)
    before = session.payload()
    with pytest.raises(ValueError):
        commands.move(p, box, 1, 0, box)
    with pytest.raises(ValueError):
        commands.move(p, child, 0, 0)
    assert session.payload() == before
    commands.move(p, child, 1, 0)
    assert len(session.presentation.snapshot().pages[p].components) == 2
    session.undo()
    assert session.payload() == before


def testTwoImagesUseExplicitBoundedProfileAndThirdBindingRollsBack(tmp_path):
    session = ProjectEditSession(blank(tmp_path).model_dump())
    page = session.presentation.createPage('one')
    secondPage = session.presentation.createPage('two')
    commands = PageCommands(session, metadata())
    first = commands.add(page, 'image', 0, 0)
    second = commands.add(secondPage, 'image', 0, 0)
    choices = outputChoices(session.document(), metadata())
    original = next(c for c in choices if c.source.nodeId == 'load' and c.source.port == 'image')
    overlay = next(c for c in choices if c.source.nodeId == 'blob' and c.source.port == 'overlay')
    commands.bind(page, first, original)
    commands.bind(secondPage, second, original)
    limits = commands.bind(secondPage, second, overlay)
    assert set(limits['rawBytesBySource'].values()) == {4 * 1024 * 1024}
    assert len(set(limits['imageLaneBySource'].values())) == 2
    third = commands.add(page, 'image', 1, 0)
    mask = next(c for c in choices if c.source.nodeId == 'blob' and c.source.port == 'mask')
    before = session.payload()
    with pytest.raises(ValueError, match='at most two'):
        commands.bind(page, third, mask)
    assert session.payload() == before
    commands.delete(page, first)
    limits = commands.bind(secondPage, second, overlay)
    assert set(limits['rawBytesBySource'].values()) == {8 * 1024 * 1024}


def testTwoInvocationScopesBindIndependentlyAndUndoTogether(tmp_path):
    from emo_master.core.project.models import WorkflowNode
    document = blank(tmp_path)
    document.workflows['child'] = document.workflows['main'].model_copy(deep=True)
    document.workflowOrder.append('child')
    document.workflows['main'].nodes.append(WorkflowNode(nodeId='call-child', kind='subflow',
                                                       targetWorkflowId='child'))
    session = ProjectEditSession(document.model_dump())
    one = session.presentation.createPage('root scope')
    two = session.presentation.createPage('child scope')
    commands = PageCommands(session, metadata())
    first = commands.add(one, 'number', 0, 0)
    second = commands.add(two, 'number', 0, 0)
    choices = outputChoices(session.document(), metadata())
    root = next(c for c in choices if c.source.nodeId == 'count' and not c.source.callPath)
    child = next(c for c in choices if c.source.nodeId == 'count' and c.source.callPath)
    commands.bind(one, first, root)
    before = session.payload()
    limits = commands.bind(two, second, child)
    assert limits['scopeCount'] == 2
    presentation = session.presentation.snapshot()
    sourceIds = [presentation.pages[p].components[0].bindings['value'] for p in (one, two)]
    assert presentation.dataSources[sourceIds[0]].resultScopeId != presentation.dataSources[sourceIds[1]].resultScopeId
    assert presentation.dataSources[sourceIds[1]].callPath[0].nodeId == 'call-child'
    assert session.undo()
    assert session.payload() == before
    assert session.redo()
    assert commands.session.presentation.snapshot().pages[two].components[0].bindings


def testUnsupportedOutputKeepsSupportedChoicesAndReportsReason(tmp_path):
    document = blank(tmp_path)
    document.workflows['main'].outputs['custom'] = {'type': 'vendorTensor'}
    unsupported = []
    choices = outputChoices(document, metadata(),
        onUnsupported=lambda title, reason: unsupported.append((title, reason)))
    assert any(choice.source.nodeId == 'count' for choice in choices)
    assert not any(choice.source.port == 'custom' for choice in choices)
    assert len(unsupported) == 1
    assert 'custom' in unsupported[0][0] and 'vendortensor' in unsupported[0][1]
    assert 'unsupported' in unsupported[0][1]


def testUnsupportedOutputEditorRemainsUsable(designer, tmp_path):  # noqa: F811
    from dataclasses import asdict
    from test_preview import waitFor
    document = blank(tmp_path)
    document.workflows['main'].outputs['custom'] = {'type': 'vendorTensor'}
    coordinator = designer.pageCoordinator
    designer.workflowController.loadPayload(document.model_dump())
    coordinator.session.acceptLoaded()
    waitFor(lambda: designer.operatorCatalogController.state == 'ready')
    designer.operatorCatalog = [asdict(value) for value in metadata().values()]
    coordinator.showPages()
    editor = coordinator.editor
    tools = editor.tools
    groups = [tools.outputs.topLevelItem(index) for index in range(tools.outputs.topLevelItemCount())]
    group = next(item for item in groups if item.text(0) == '暂不支持的输出')
    item = group.child(0)
    assert 'custom' in item.text(0) and 'vendortensor' in item.toolTip(0)
    assert not item.flags() & Qt.ItemIsDragEnabled
    assert not item.flags() & Qt.ItemIsEnabled
    assert item.data(0, Qt.UserRole) is None
    assert tools.binding.count() == len(tools.choices)
    assert any(choice.source.nodeId == 'count' for choice in tools.choices)
    assert w_no_extra_job(designer)


def testTrustedUnsupportedPortDoesNotHideSupportedNodeOutputs(tmp_path):
    document = blank(tmp_path)
    manifests = metadata()
    manifests['vision.analysis.blob'].outputPorts['custom'] = 'vendorTensor'
    reasons = []
    choices = outputChoices(document, manifests,
        onUnsupported=lambda title, reason: reasons.append((title, reason)))
    assert any(choice.source.nodeId == 'blob' and choice.source.port == 'overlay' for choice in choices)
    assert not any(choice.source.port == 'custom' for choice in choices)
    assert len(reasons) == 1 and '[blob]' in reasons[0][0]
    assert 'vendortensor' in reasons[0][1]


def testCatalogIncludesSupportedCallPathDepth32AndStopsAtSchemaBound(tmp_path):
    from emo_master.core.project.models import WorkflowDefinition, WorkflowNode
    document = blank(tmp_path)
    for depth in range(34):
        key = 'main' if depth == 0 else f'depth-{depth}'
        nextKey = f'depth-{depth + 1}'
        document.workflows[key] = WorkflowDefinition(name=key, outputs={'count': 'integer'},
            nodes=[WorkflowNode(nodeId=f'call-{depth}', kind='subflow', targetWorkflowId=nextKey)]
                if depth < 33 else [])
    document.workflowOrder = list(document.workflows)
    choices = outputChoices(document, {})
    assert {len(choice.source.callPath) for choice in choices} == set(range(33))
    deepest = next(choice for choice in choices if len(choice.source.callPath) == 32)
    assert deepest.source.workflowId == 'depth-32'
    assert deepest.scope.scopeWorkflowId == 'depth-32'
