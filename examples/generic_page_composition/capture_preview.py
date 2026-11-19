"""Capture real Qt pages after software Runtime execution in a temporary copy."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
from time import monotonic, sleep

import emo_master  # noqa: F401 - Qt/runtime bootstrap before importing PySide2
from PySide2.QtCore import QSettings, Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication

from emo_master.apps.operator_runtime.main import OperatorWindow

from build_example import DATABASE, HISTORY, PROJECT


def waitFor(predicate, seconds=40):
    end = monotonic() + seconds
    while monotonic() < end:
        QApplication.processEvents()
        if predicate():
            return
        sleep(.01)
    raise TimeoutError('Qt software preview did not reach the expected state')


def capture(project, output):
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    repo = Path(__file__).resolve().parents[2]
    report = dict(platform=app.platformName(), project=str(project),
                  sourceSha=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip(),
                  sourceDirty=bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=repo, text=True).strip()),
                  projectSha256=hashlib.sha256(project.read_bytes()).hexdigest(),
                  source='Qt widgets with a real software Runtime; temporary project copy',
                  nativeDesktop=[app.primaryScreen().size().width(), app.primaryScreen().size().height()],
                  viewport='Requested widget sizes, overriding RuntimePages desktop size limit for offline rendering',
                  screenshots=[], runs=[])
    with TemporaryDirectory(prefix='emo-page-preview-') as directory:
        root = Path(directory)
        copy = root / 'project'
        (copy / DATABASE).parent.mkdir(parents=True)
        for name in (PROJECT, DATABASE):
            shutil.copyfile(project.parent / name, copy / name)
        shutil.copytree(project.parent / 'assets', copy / 'assets')
        with closing(sqlite3.connect(copy / DATABASE)) as database:
            assert database.execute('SELECT COUNT(*) FROM records').fetchone() == (0,)
        window = OperatorWindow(copy / PROJECT, dataRoot=root / 'runtime',
                                preferences=QSettings(str(root / 'preferences.ini'), QSettings.IniFormat))
        window.resize(1920, 1080)
        window.show()
        try:
            waitFor(lambda: window.pages is not None and not window.busy or window.error)
            assert not window.error, window.error
            assert window.startButton.text() == '开始检测'
            assert window.stopButton.text() == '停止检测'
            # Four normal runs create a full list and one older record for the
            # existing external next/previous controls to cross a query page.
            for sequence in range(1, 5):
                waitFor(lambda: window.startButton.isEnabled() and not window.busy)
                previous = window.controller.jobId
                QTest.mouseClick(window.startButton, Qt.LeftButton)
                waitFor(lambda: bool(window.controller.jobId) and window.controller.jobId != previous or window.error)
                assert not window.error, window.error
                job = window.controller.jobId
                waitFor(lambda: not window.busy and window.state.get('canStart') and bool(window.pages.displayed)
                        or window.error)
                assert not window.error, window.error
                status = window.controller.runtime.jobRepository.get(job)
                assert status.status == 'COMPLETED', (status.status, status.errorCode, status.message)
                report['runs'].append(dict(sequence=sequence, jobId=job, state=status.status))

            def navigate(pageId):
                tabs = window.pages.pageNavigation.tabs
                index = next(i for i in range(tabs.count()) if tabs.tabData(i) == pageId)
                QTest.mouseClick(tabs, Qt.LeftButton, pos=tabs.tabRect(index).center())
                waitFor(lambda: window.pages.currentPageId == pageId)

            def historyReady():
                widgets = window.pages.widgets['history']
                table = widgets[HISTORY][1]
                return (not table.busy and table.selectedRow() is not None
                        and all(not widgets[name][1].image.isNull() for name in ('history-main', 'history-detail')))

            navigate('history')
            waitFor(historyReady)
            table = window.pages.widgets['history'][HISTORY][1]
            waitFor(lambda: table.count == 4)
            assert all(bar.isHidden() for bar in (table.filterBar, table.sortBar, table.paginationBar))
            initial = table._currentKey
            for _ in range(3):
                key = table._currentKey
                QTest.mouseClick(window.pages.widgets['history']['history-next'][1], Qt.LeftButton)
                waitFor(lambda: historyReady() and table._currentKey != key)
            assert table.page == 1
            QTest.mouseClick(window.pages.widgets['history']['history-first'][1], Qt.LeftButton)
            waitFor(lambda: historyReady() and table.page == 0 and table._currentKey == initial)
            report['history'] = dict(toolbarHidden=True, crossedPage=True, returnedToLatest=True,
                                     pageSize=table.pageSize, records=table.count)
            for width, height in ((1366, 768), (1920, 1080)):
                # Offscreen Qt reports a small synthetic desktop. Set the render
                # viewport explicitly; the authored layout still performs sizing.
                window.pages.setMaximumSize(width, height)
                window.resize(width, height)
                for page in ('production', 'history'):
                    navigate(page)
                    if page == 'history':
                        waitFor(historyReady)
                    else:
                        waitFor(lambda: not window.pages.widgets['production']['main-image'][1].image.isNull())
                    window.pages.pages[page].verticalScrollBar().setValue(0)
                    settled = monotonic() + .18
                    waitFor(lambda: monotonic() >= settled, seconds=2)
                    name = f'qt-{page}-{width}x{height}.png'
                    shot = window.grab()
                    assert (shot.width(), shot.height()) == (width, height)
                    assert shot.save(str(output / name))
                    report['screenshots'].append(name)
            report['controls'] = dict(start=window.startButton.text(), stop=window.stopButton.text(),
                                      logoSize=[window.pages.logo.width(), window.pages.logo.height()])
        finally:
            window.close()
            waitFor(lambda: window.done)
    (output / 'qt-preview.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(capture(args.project.resolve(), args.output.resolve()), ensure_ascii=False, indent=2))
