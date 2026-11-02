import gc
import json
import os
import sys
from pathlib import Path

import pytest

base = Path('D:/jsdfhasuh/documents/my_project/emo_master')
sys.path[:0] = [str(base/'src'), str(base), str(base/'tests/ui/page_designer')]
os.environ['QT_QPA_PLATFORM'] = 'windows'
records = {'platform': 'windows', 'cycles': [], 'exceptions': []}

class Probe:
    def pytest_collection_modifyitems(self, items):
        def exercise(responsivePage, monkeypatch):
            from PySide2.QtCore import QPoint, QPointF, QEvent, Qt
            from PySide2.QtGui import QMouseEvent
            from PySide2.QtTest import QTest
            from PySide2.QtWidgets import QApplication
            from shiboken2 import isValid
            from test_responsive_editor import clickCard, settle

            c, e, keys = responsivePage
            window = c.window
            monkeypatch.setattr(sys, 'excepthook', lambda kind, value, tb:
                                records['exceptions'].append(f'{kind.__name__}: {value}'))
            for index in range(3):
                c.showFlow()
                window.resize(1180-index*70, 812-index*20)
                settle()
                for overlay in (window.floatingToolbox, window.floatingRunPanel):
                    overlay.setExpanded(True)
                    overlay.setExpanded(False)
                    assert isValid(overlay.viewport)
                    assert overlay.viewport is window.flowView.viewport()
                c.showPages()
                e.canvas.setZoomFactor([.5, 1, 1.5][index])
                settle()
                clickCard(e, keys[1])
                e.tools.fields['title'].setText('Pending inspection ' + str(index))
                clickCard(e, keys[0])
                assert e.renderer.config == e.store.snapshot()
                handle = e.tools.responsive.handles[0]
                point = e.canvas.mapFromScene(e.canvas.proxy.mapToScene(QPointF(
                    handle.mapTo(e.renderer, handle.rect().center()))))
                viewport = e.canvas.viewport()
                QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, point)
                end = point + QPoint(20, 15)
                QApplication.sendEvent(viewport, QMouseEvent(QEvent.MouseMove, QPointF(end),
                    QPointF(viewport.mapToGlobal(end)), Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
                QTest.keyClick(e.canvas, Qt.Key_Escape)
                QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, end)
                settle()
                gc.collect()
                records['cycles'].append({'index': index, 'flowViewportValid': isValid(window.flowView.viewport()),
                    'pageViewportValid': isValid(viewport), 'resizeReleased': e.tools.responsive.resize is None})
            assert not records['exceptions']
        items[:] = [pytest.Function.from_parent(items[0].parent, name='test_reported_log_paths', callobj=exercise)]

result = pytest.main([str(base/'tests/ui/page_designer/test_responsive_selection_pending.py'),
                      '-q', '--tb=short', '--timeout=45'], plugins=[Probe()])
Path('D:/tmp/emo-dot-log-probe.json').write_text(json.dumps(records, indent=2), encoding='utf-8')
raise SystemExit(result)
