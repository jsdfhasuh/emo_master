"""Spacer participates in native page commands with the same project history."""
from PySide2.QtWidgets import QApplication, QInputDialog

from test_workspace import designer  # noqa: F401
from emo_master.apps.designer.state.presentation_store import _component


def settle():
    for _ in range(6):
        QApplication.processEvents()


def testSpacerModeCopyUndoAndSaveReopen(designer, monkeypatch, tmp_path):  # noqa: F811
    coordinator = designer.pageCoordinator
    coordinator.showPages()
    editor = coordinator.editor
    monkeypatch.setattr(QInputDialog, 'getText', lambda *args, **kwargs: ('空白块验收', True))
    editor.run(editor.newPage)
    key = editor.tools.responsive.commands().place(editor.pageId, kind='spacer')[0]
    editor.refresh()
    editor.tools.select(key)
    settle()
    before = len(coordinator.session._undo)
    field = editor.tools.fields['spacerMode']
    field.setCurrentIndex(field.findData('flex'))
    editor.run(editor.tools.apply)
    assert _component(editor.store.snapshot(), editor.pageId, key).props.spacerMode == 'flex'
    assert len(coordinator.session._undo) == before + 1
    coordinator.history()
    assert _component(editor.store.snapshot(), editor.pageId, key).props.spacerMode == 'fixed'
    coordinator.history(True)
    assert _component(editor.store.snapshot(), editor.pageId, key).props.spacerMode == 'flex'
    editor.tools.select(key)
    editor.run(editor.tools.copy)
    copied = editor.tools.selected
    assert copied != key
    assert _component(editor.store.snapshot(), editor.pageId, copied).type == 'spacer'
    directory = str(tmp_path / 'spacers')
    assert designer.saveProjectToDirectory(directory)
    assert designer.loadProjectDirectory(directory)
    page = next(page for page in coordinator.session.document().presentation.pages.values() if page.name == '空白块验收')
    assert {component.componentId for component in page.components} == {key, copied}
    assert all(component.props.spacerMode == 'flex' for component in page.components)
