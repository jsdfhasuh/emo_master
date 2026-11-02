"""Runtime normalizes the upper extent without shrinking its logical viewport."""
from PySide2.QtWidgets import QApplication

from emo_master.core.presentation.models import Component, Page, Presentation
from emo_master.ui.presentation.renderer import RuntimePages


def settle():
    for _ in range(5):
        QApplication.processEvents()


def testExpandedRuntimePreservesFlexibleOccupancyAndScrolls(qtApp):
    config = Presentation(defaultPageId='p', pageOrder=['p'], pages={
        'p': Page(name='Expanded', components=[
            Component(componentId='title', type='page_title'),
            Component(componentId='gap', type='spacer', props={'spacerMode': 'flex'}),
            Component(componentId='body', type='text')])})
    viewer = RuntimePages(config)
    viewer.resize(800, 700)
    viewer.show()
    settle()
    before = {key: card.geometry() for key, card in viewer.cards['p'].items()}
    viewport = viewer.pages['p'].viewport().size()
    config.pages['p'].canvasTop = -120
    config.pages['p'].components[0].responsive.canvasOffsetY = -120
    viewer.reload(config)
    settle()
    try:
        scroll = viewer.pages['p']
        assert scroll.viewport().size() == viewport
        assert scroll.widget().height() == viewport.height() + 120
        assert scroll.verticalScrollBar().maximum() == 120
        for key, old in before.items():
            new = viewer.cards['p'][key].geometry()
            assert new.size() == old.size()
            assert new.y() == old.y() + (0 if key == 'title' else 120)
        viewer.resize(500, 500)
        settle()
        assert scroll.widget().height() == scroll.viewport().height() + 120
        assert scroll.verticalScrollBar().maximum() == 120
        assert viewer.cards['p']['gap'].height() < before['gap'].height()
    finally:
        viewer.close()
        viewer.deleteLater()
