"""The generated acceptance project opens through the real Designer entry."""
from PySide2.QtCore import QSize

from test_workspace import designer  # noqa: F401
from test_responsive_editor import settle
from scripts.create_designer_space_acceptance import createProject


def testAcceptanceProjectOpenAndRealOverflow(designer, tmp_path):  # noqa: F811
    source = createProject(tmp_path / 'acceptance.emoproj')
    assert designer.loadProjectSelection(str(source))
    coordinator = designer.pageCoordinator
    coordinator.showPages()
    editor = coordinator.editor
    assert (coordinator.session.document().schemaVersion,
            coordinator.session.document().presentation.schemaVersion) == ('2.9', '1.2')
    editor.canvas.setPreviewSize(360, 640)
    settle()
    before, history = coordinator.session.payload(), len(coordinator.session._undo)
    for pageId, overflow in (('empty', False), ('short', False), ('long', True)):
        editor.navigatePage(pageId)
        settle()
        scroll = editor.renderer.pages[pageId]
        assert scroll.viewport().size() == QSize(360, 640)
        assert (scroll.verticalScrollBar().maximum() > 0) == overflow
        assert scroll.overlayVertical.isVisible() == overflow
    assert coordinator.session.payload() == before
    assert len(coordinator.session._undo) == history
