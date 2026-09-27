"""Capture actual rendered PySide2 windows, never generated mockups."""
import argparse
import json
import os
from pathlib import Path
import sys
import time


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--native',action='store_true')
    parser.add_argument('--scale',default='1')
    args=parser.parse_args()
    os.environ['QT_QPA_PLATFORM']='windows' if args.native else 'offscreen'
    os.environ['QT_SCALE_FACTOR']=args.scale
    import p3_demo
    from PySide2.QtCore import Qt
    from PySide2.QtTest import QTest
    app=p3_demo.QApplication.instance() or p3_demo.QApplication([])
    launcher=p3_demo.Launcher()
    args.output.mkdir(parents=True,exist_ok=False)

    def until(check,timeout=25):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            app.processEvents()
            if launcher.error:
                raise AssertionError(launcher.error)
            if check():
                return
            time.sleep(.01)
        raise AssertionError('visual check deadline')

    launcher.show()
    report={}
    try:
        app.processEvents()
        QTest.mouseClick(launcher.start,Qt.LeftButton)
        until(lambda:launcher.hub and launcher.hub.windows and all(w.displayed for w in launcher.hub.windows))
        window=next(iter(launcher.hub.windows))
        until(lambda:window.widgets['overview']['overview-count'][1].text()=='2 个')
        app.processEvents()
        assert window.grab().save(str(args.output/'overview.png'))
        first=window.displayed['root'].result.identity.resultKey
        QTest.mouseClick(window.buttons['detail'],Qt.LeftButton)
        until(lambda:window.widgets['detail']['detail-count'][1].text()=='3 个')
        app.processEvents()
        assert window.grab().save(str(args.output/'detail.png'))
        second=window.displayed['root'].result.identity.resultKey
        assert first!=second
        QTest.mouseClick(launcher.second,Qt.LeftButton)
        until(lambda:len(launcher.hub.windows)==2)
        assert len(launcher.backend.presentation.jobs)==1
        window.resize(500,480)
        app.processEvents()
        assert window.grab().save(str(args.output/'narrow.png'))
        QTest.keyClick(window.buttons['overview'],Qt.Key_Space)
        assert window.currentPageId=='overview'
        report={'platform':app.platformName(),'scale':args.scale,'device_pixel_ratio':window.devicePixelRatioF(),
                'keys':[first,second],'observed_counts':[2,3],'jobs':len(launcher.backend.presentation.jobs),
                'resources':launcher.hub.stats(),'keyboard_navigation':True,'window_visible':window.isVisible()}
    finally:
        launcher.close()
        until(lambda:launcher.done,timeout=20)
    report['shutdown_confirmed']=True
    (args.output/'visual.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2),flush=True)
    return 0


if __name__=='__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
