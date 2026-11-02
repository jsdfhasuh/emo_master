"""Unavailable preview reasons remain understandable and copyable without logs."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide2.QtCore import QPoint
from PySide2.QtGui import QContextMenuEvent
from PySide2.QtWidgets import QMenu

from emo_master.core.presentation.results import ClosedSource
from emo_master.ui.presentation import renderer
from emo_master.ui.presentation.renderer import RuntimePages
from examples.runtime_pages_p3 import sampleProjectP3
from tests.ui.presentation.test_latest_sampling_contract import view


BUSY = 'source image lane is busy; capture never waits'


def unavailableView(window, reason, code='BUDGET_EXCEEDED'):
    state = view(window, 2)
    scope = state.scopes['root']
    source = ClosedSource(sourceId='image', state='UNAVAILABLE', reasonCode=code, reason=reason)
    result = scope.result.model_copy(update={'sources': (scope.result.sources[0], source), 'status': 'INCOMPLETE'})
    return replace(state, scopes={'root': replace(scope, result=result, images={}, imageStates={})})


@pytest.mark.parametrize('reason,expected', [
    (BUSY, '预览通道正忙，本帧未显示\n请等待后续画面更新'),
    ('image slab quarantined until actual owner retirement',
     '预览通道异常后尚未释放，本帧未显示\n若持续出现，请根据提示详情排查'),
    ('raw image 6220800 bytes exceeds source lane limit 4194304; no implicit resize',
     '预览图片过大：5.93 MiB，上限 4.00 MiB\n请缩小用于显示的预览图'),
    ('raw image 9000000 bytes exceeds image limit 8388608; no implicit resize',
     '预览图片过大：8.58 MiB，上限 8.00 MiB\n请缩小用于显示的预览图'),
    ('raw images 18000000 bytes exceeds result limit 16777216; no implicit resize',
     '本次预览图片总量过大：17.17 MiB，上限 16.00 MiB\n请减少或缩小本次显示的图片'),
    ('result values 1200000 bytes exceeds result limit 1048576',
     '本次显示数据过多：1.14 MiB，上限 1.00 MiB\n请减少用于显示的数据'),
    ('BUDGET_EXCEEDED', '显示资源受限，本次结果未显示\nRuntime 未提供具体原因'),
    ('future budget detail: busy elsewhere', '显示资源受限，本次结果未显示\n具体原因见提示详情'),
])
def testPreviewExplainsActualReasonAndRecovers(qtApp, tmp_path, reason, expected):
    window = RuntimePages(sampleProjectP3(tmp_path).presentation)
    try:
        window.submit(view(window, 1))
        image = window.widgets['overview']['overview-image'][1]
        assert not image.image.isNull()
        window.submit(unavailableView(window, reason))
        assert image.image.isNull() and image.key == 'result-2'
        assert image.message == expected + '\n右键可复制提示详情'
        details = image.toolTip()
        assert expected in details and reason in details and 'BUDGET_EXCEEDED' in details
        for field in ('数据源：image', '流程 / 节点 / 端口：', 'Runtime：runtime', '任务：job',
                      '结果范围：root', '结果：result-2（序号 2）', '执行版本：', '采集版本：'):
            assert field in details
        assert window.widgets['overview']['overview-count'][1].text() == '2 个'
        window.submit(view(window, 3))
        assert not image.image.isNull() and image.key == 'result-3'
        assert image.message == '' and image.toolTip() == ''
    finally:
        window.close()


@pytest.mark.parametrize('code', ['OPTIONAL_ABSENT', 'BRANCH_SKIPPED'])
def testDetailedOptionalReasonPreservesConfiguredEmptyText(qtApp, tmp_path, code):
    config = sampleProjectP3(tmp_path).presentation
    component = next(c for c in config.pages['overview'].components if c.componentId == 'overview-image')
    component.props.emptyText = '本次没有人员图像'
    window = RuntimePages(config)
    try:
        window.submit(unavailableView(window, 'optional crop was not produced', code))
        image = window.widgets['overview']['overview-image'][1]
        assert image.message == '本次没有人员图像'
        assert code + ' · optional crop was not produced' in image.toolTip()
    finally:
        window.close()


def testContextCopyKeepsTheErrorSelectedBeforeLiveRecovery(qtApp, tmp_path, monkeypatch):
    window = RuntimePages(sampleProjectP3(tmp_path).presentation)
    copied = []
    monkeypatch.setattr(renderer, 'QApplication', SimpleNamespace(clipboard=lambda: SimpleNamespace(setText=copied.append)))
    try:
        window.submit(unavailableView(window, BUSY))
        image = window.widgets['overview']['overview-image'][1]
        selected = image.toolTip()
        opened = []

        class SelectingMenu(QMenu):
            def exec_(self, _position):
                opened.append(True)
                assert [action.text() for action in self.actions()] == ['复制提示详情']
                window.submit(view(window, 3))
                assert image.toolTip() == ''
                assert self.actions()[0].isEnabled()
                self.actions()[0].trigger()

        monkeypatch.setattr(renderer, 'QMenu', SelectingMenu)
        image.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Mouse, QPoint(1, 1)))
        assert opened == [True]
        assert copied == [selected]
        assert 'result-2' in copied[0] and BUSY in copied[0]
    finally:
        window.close()


def testQtImageBudgetReportsClientDiagnosticAndSuccessfulConversion(qtApp):
    import numpy as np
    from emo_master.ui.presentation.hub import DisplayHub
    from tests.runtime.presentation.test_live_decisions import result

    reports = []
    session = SimpleNamespace(reportImageIssue=lambda *args, **kwargs: reports.append((args, kwargs)))
    hub = DisplayHub(session)
    scope = SimpleNamespace(result=result(1))
    pixels = np.zeros((2, 2, 3), dtype=np.uint8)
    hub.imageLimit = 1
    with pytest.raises(ValueError, match='UI_IMAGE_BUDGET'):
        hub.image(scope, 'image', pixels)
    assert reports[0][0][:3] == (scope.result, 'image', 'UI_IMAGE_BUDGET')
    assert reports[0][1] == {'level': 'WARN', 'stage': 'qt_image'}
    hub.imageLimit = 1024
    assert not hub.image(scope, 'image', pixels).isNull()
    assert reports[1] == ((scope.result, 'image'), {'stage': 'qt_image'})
