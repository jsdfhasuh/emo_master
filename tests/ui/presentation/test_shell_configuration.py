"""Project shell styles reach real Qt widgets without changing command ownership."""
import hashlib

from PySide2.QtCore import Qt
from PySide2.QtGui import QColor, QImage
from PySide2.QtTest import QTest

from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.operator_runtime.main import OperatorWindow
from emo_master.core.presentation.models import Page, PageShell, Presentation
from emo_master.ui.presentation.renderer import RuntimePages
from emo_panel_protocol.theme import LIGHT_THEME
from tests.runtime.current_production_fixture import currentProductionProject
from tests.ui.operator_view.test_production_window import closeWindow, preferences
from tests.ui.operator_view.test_view import waitFor


def testConfiguredHeaderAndLogoKeepAspectRatio(qtApp, tmp_path):
    path = tmp_path / 'logo.png'
    image = QImage(200, 80, QImage.Format_RGB32)
    image.fill(QColor('#2459d1'))
    assert image.save(str(path))
    content = path.read_bytes()
    shell = PageShell(title='生产装配', subtitle='操作运行端', logoResourceId='brand', logoWidth=120, logoHeight=60,
        startControl={'label': '开始检测'}, stopControl={'label': '停止检测'},
        headerSurface={'kind': 'card', 'background': 'subtle', 'radius': 10,
                       'padding': {'left': 20, 'right': 30, 'top': 12, 'bottom': 16}},
        headerTypography={'title': {'size': 28, 'color': 'primary'}, 'helper': {'size': 16}})
    config = Presentation(shell=shell, defaultPageId='p', pageOrder=['p'], pages={'p': Page(name='生产')})
    window = RuntimePages(config, assets={'brand': (path, hashlib.sha256(content).hexdigest(), len(content))},
                          editorHost=True)
    window.show()
    qtApp.processEvents()
    try:
        assert window.shellTitle.font().pixelSize() == 28
        assert window.shellSubtitle.font().pixelSize() == 16
        assert LIGHT_THEME['primary'] in window.shellTitle.styleSheet()
        assert LIGHT_THEME['subtle'] in window.header.styleSheet()
        margins = window.header.layout().contentsMargins()
        assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (20, 12, 30, 16)
        assert (window.logo.width(), window.logo.height()) == (120, 60)
        pixmap = window.logo.pixmap()
        assert (pixmap.width() / pixmap.devicePixelRatio(), pixmap.height() / pixmap.devicePixelRatio()) == (120, 48)
        assert window.logo.isVisible()
        assert window.previewControls['start'].text() == '开始检测'
        assert window.previewControls['stop'].text() == '停止检测'
        assert all(not button.isEnabled() for button in window.previewControls.values())
        assert window.hub is None
    finally:
        window.close()


def testCustomControlLabelsSurviveRealStartStopAndStateRefresh(qtApp, tmp_path):
    path, document = currentProductionProject(tmp_path / 'project', mode='continuous')
    document.presentation.shell = PageShell(
        startControl={'label': '开始检测', 'appearance': {'style': 'primary', 'text': {'size': 20},
                      'radius': 10, 'minWidth': 128, 'minHeight': 52, 'showIcon': False}},
        stopControl={'label': '停止检测', 'appearance': {'style': 'secondary', 'showIcon': True}})
    saveProject(path, document.model_dump(mode='json'))
    window = OperatorWindow(path, dataRoot=tmp_path / 'runtime', preferences=preferences(tmp_path))
    window.show()
    try:
        waitFor(lambda: window.pages is not None and not window.busy or window.error)
        assert not window.error
        assert window.startButton.text() == '开始检测' and window.stopButton.text() == '停止检测'
        assert window.startButton.font().pixelSize() == 20
        assert window.startButton.minimumWidth() == 128 and window.startButton.minimumHeight() == 52
        assert window.startButton.icon().isNull() and not window.stopButton.icon().isNull()
        assert window.startButton.isEnabled() and not window.stopButton.isEnabled()
        QTest.mouseClick(window.startButton, Qt.LeftButton)
        waitFor(lambda: not window.busy and window.state.get('jobStatus') == 'RUNNING' or window.error)
        assert not window.error
        job = window.controller.jobId
        assert job and not window.startButton.isEnabled() and window.stopButton.isEnabled()
        assert window.startButton.text() == '开始检测'
        QTest.mouseClick(window.stopButton, Qt.LeftButton)
        waitFor(lambda: not window.busy and window.state['canStart'] or window.error)
        assert not window.error
        assert window.controller.runtime.jobRepository.get(job).status == 'ABORTED'
        assert window.startButton.text() == '开始检测' and window.stopButton.text() == '停止检测'
        assert window.startButton.isEnabled() and not window.stopButton.isEnabled()
    finally:
        closeWindow(window)
