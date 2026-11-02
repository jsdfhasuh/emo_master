"""Native transparent occupants retain exact layout size in editor and runtime."""
import pytest
from PySide2.QtWidgets import QApplication, QLabel

from emo_master.core.presentation.models import Component, Page, Presentation, ResponsiveLayout
from emo_master.ui.presentation.renderer import RuntimePages
from emo_master.ui.presentation.spacer import SpacerCard


def settle():
    for _ in range(5):
        QApplication.processEvents()


@pytest.mark.parametrize('editing', [False, True])
def testSpacerHasNoVisibleContentOrPaddingAndNoRuntimeBinding(editing, qtApp):
    config = Presentation(defaultPageId='p', pageOrder=['p'], pages={
        'p': Page(name='Space', components=[Component(componentId='s', type='spacer')])})
    viewer = RuntimePages(Presentation())
    if editing:
        viewer.setEditorHost()
    viewer.reload(config)
    viewer.show()
    settle()
    try:
        card = viewer.cards['p']['s']
        assert isinstance(card, SpacerCard)
        assert card.editing == editing
        assert card.size().width() == card.size().height() == 24
        assert not card.findChildren(QLabel)
        assert card.layout().contentsMargins().left() == 0
        assert not card.styleSheet()
        viewer.setSimulationState('OK')
        assert not card.toolTip()
    finally:
        viewer.close()
        viewer.deleteLater()


def testVerticalFlexUsesViewportHeightWithoutCreatingOverflow(qtApp):
    config = Presentation(defaultPageId='p', pageOrder=['p'], pages={
        'p': Page(name='Space', layout=ResponsiveLayout(spacing=0), components=[
            Component(componentId='s', type='spacer', props={'spacerMode': 'flex'}),
            Component(componentId='fixed', type='spacer')])})
    viewer = RuntimePages(config)
    viewer.resize(800, 700)
    viewer.show()
    settle()
    try:
        scroll = viewer.pages['p']
        assert scroll.verticalScrollBar().maximum() == 0
        flexible, fixed = viewer.cards['p']['s'], viewer.cards['p']['fixed']
        assert fixed.height() == 24
        assert flexible.height() > 24
        assert flexible.height() + fixed.height() + 18 == scroll.viewport().height()
    finally:
        viewer.close()
        viewer.deleteLater()


def testSpacerCanBeResizedBelowItsInitial24Pixels(qtApp):
    spacer = Component(componentId='small', type='spacer', responsive=dict(width='content',
        minWidth=0, preferredWidth=12, minHeight=0, preferredHeight=12, alignSelf='start'))
    viewer = RuntimePages(Presentation(defaultPageId='p', pageOrder=['p'],
        pages={'p': Page(name='Small spacer', components=[spacer])}))
    viewer.show()
    settle()
    assert viewer.cards['p']['small'].size().width() == 12
    assert viewer.cards['p']['small'].size().height() == 12
