"""The component library must settle after Qt changes scrollbar geometry."""
from pathlib import Path

import pytest
from PySide2.QtCore import QEventLoop, QTimer

from emo_master.apps.designer.page_designer.palette import Palette


def observeLayout(palette):
    samples = []
    loop = QEventLoop()
    timer = QTimer()

    def sample():
        rects = [palette.visualItemRect(palette.item(i))
                 for i in range(palette.count()) if not palette.item(i).isHidden()]
        samples.append((palette.viewport().width(), palette.gridSize().width(),
                        palette.verticalScrollBar().isVisible(),
                        tuple((r.x(), r.y(), r.width(), r.height()) for r in rects)))
        if len(samples) == 20:
            loop.quit()

    timer.timeout.connect(sample)
    timer.start(10)
    loop.exec_()
    timer.stop()
    assert len(set(samples[-10:])) == 1, 'palette keeps rearranging while idle'
    return samples[-1]


@pytest.mark.parametrize('styled', [False, True])
@pytest.mark.parametrize('width,height', [(220, 400), (230, 400), (240, 400),
                                         (300, 390), (460, 400)])
def testPaletteLayoutSettles(qtApp, styled, width, height):
    palette = Palette()
    if styled:
        qss = Path(__file__).resolve().parents[3] / 'src/emo_master/apps/designer/ui/styles/app.qss'
        palette.setStyleSheet(qss.read_text(encoding='utf-8'))
    palette.resize(width, height)
    palette.show()
    observeLayout(palette)

    # Search removes the need for a scrollbar, then clearing it restores all
    # eight cards. Resizing must still allow the narrow one-column layout.
    palette.search('image')
    state = observeLayout(palette)
    assert len(state[-1]) == 1
    assert not state[2]
    palette.search('')
    observeLayout(palette)
    palette.resize(200, 400)
    state = observeLayout(palette)
    assert len({rect[0] for rect in state[-1]}) == 1
    palette.resize(460, 400)
    state = observeLayout(palette)
    assert len({rect[0] for rect in state[-1]}) == 2
    assert not state[2]
    assert all(x >= 0 and x + w <= state[0] for x, y, w, h in state[-1])
