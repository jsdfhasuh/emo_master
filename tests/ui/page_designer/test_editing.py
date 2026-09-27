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
    assert sendDrop(widget, {'choice': index})
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
