"""Drive the real standalone entry with Qt events; no Designer imports."""
import argparse
import json
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
import emo_master  # noqa: E402,F401
from PySide2.QtCore import QTimer, Qt  # noqa: E402
from PySide2.QtTest import QTest  # noqa: E402
from PySide2.QtWidgets import QApplication  # noqa: E402
from emo_master.apps.operator_view.main import OperatorView, main as viewMain  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ready-file', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--job')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    app = QApplication.instance() or QApplication([])
    deadline = time.monotonic() + 40
    record = {'platform': app.platformName(), 'job': args.job, 'steps': []}
    state = {'step': 0, 'view': None, 'error': None, 'renderer': None}
    def tick():
        try:
            if time.monotonic() > deadline:
                raise TimeoutError('standalone GUI probe deadline')
            view = state['view']
            if view is None:
                view = next((w for w in app.topLevelWidgets() if isinstance(w, OperatorView)), None)
                if view is None:
                    return
                state['view'] = view
            if view.error:
                raise AssertionError(view.error)
            step = state['step']
            if step == 0:
                if view.busy or view.document is None:
                    return
                assert not any(n.startswith('emo_master.apps.designer') for n in sys.modules)
                record['designer_imported'] = False
                if not args.job:
                    assert view.waitingPage is not None and view.session is None
                    assert view.waitingPage.grab().save(str(args.output/'01-ready-not-running.png'))
                    for pageId in view.document.presentation.pageOrder:
                        QTest.mouseClick(view.waitingPage.buttons[pageId], Qt.LeftButton)
                    QTest.mouseClick(view.startButton, Qt.LeftButton)
                state['step'] = 1
            elif step == 1:
                if not view.hub or not view.hub.windows:
                    return
                renderer = next(iter(view.hub.windows))
                if not renderer.displayed:
                    return
                scope = next(iter(renderer.displayed.values()))
                assert scope.result.status == 'COMPLETE' and len(scope.images) == 1
                assert scope.result.identity.mode == 'release'
                value = next(s.valueJson for s in scope.result.sources if s.valueJson is not None)
                assert value == '2'
                record.update(job=view.jobId, result_key=scope.result.identity.resultKey, count=value,
                    image_sha256=next(s.image.sha256 for s in scope.result.sources if s.image),
                    mode=scope.result.identity.mode, release_revision=view.info['revision'])
                state['renderer'] = renderer
                assert renderer.grab().save(str(args.output/'02-real-overview.png'))
                QTest.mouseClick(renderer.buttons[renderer.config.pageOrder[1]], Qt.LeftButton)
                state['step'] = 2
            elif step == 2:
                renderer = state['renderer']
                if not renderer.displayed:
                    return
                assert next(iter(renderer.displayed.values())).result.identity.resultKey == record['result_key']
                assert renderer.grab().save(str(args.output/'03-real-detail.png'))
                record['timing'] = list(renderer.records)
                QTest.mouseClick(view.secondButton, Qt.LeftButton)
                assert len(view.hub.windows) == 2
                state['step'] = 3
            elif step == 3:
                if not all(w.displayed for w in view.hub.windows):
                    return
                record['hub'] = view.hub.stats()
                record['session'] = dict(view.session.stats)
                assert view.session.stats['decoded'] == 1
                state['renderer'].close()
                assert len(view.hub.windows) == 1 and not view.session.stop.is_set()
                record['single_window_exit_preserved_session'] = True
                state['step'] = 4
                view.close()
            elif step == 4 and view.done:
                timer.stop()
                app.quit()
        except BaseException as error:
            state['error'] = repr(error)
            record['traceback'] = traceback.format_exc()
            timer.stop()
            if state['view']:
                state['view'].close()
            app.exit(1)
    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(30)
    arguments = ['--ready-file', args.ready_file] + (['--job', args.job] if args.job else [])
    code = viewMain(arguments)
    record['error'] = state['error']
    record['closed'] = bool(state['view'] and state['view'].done)
    (args.output/'view.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    return int(code != 0 or state['error'] is not None or not record['closed'])


if __name__ == '__main__':
    raise SystemExit(main())
