"""Render production While widgets in isolated Qt windows, without Runtime calls."""
from pathlib import Path
import argparse
import json
import os
import sys
from math import ceil


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--platform", choices=["windows", "offscreen"], default="windows")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    os.environ["QT_QPA_PLATFORM"] = args.platform
    import emo_master  # noqa: F401 - preload native DLLs before Qt
    from PySide2.QtCore import QCoreApplication, QEvent, QEventLoop, QRectF, QSettings, QTimer, Qt
    from PySide2.QtGui import QFontDatabase, QGuiApplication, QImage, QPainter, QTextDocument, QTextOption
    from PySide2.QtWidgets import QApplication, QGraphicsSimpleTextItem, QLabel, QMessageBox
    from emo_master.apps.designer.main import _configureHighDpi, applyDesignerStyle
    from emo_master.apps.designer.ui.main_window import MainWindow
    from emo_master.apps.designer.ui.workflow_relationship_tree import WorkflowRelationshipTree

    _configureHighDpi(QCoreApplication, Qt, QGuiApplication)
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(args.output / "settings"))
    app = QApplication([])
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    applyDesignerStyle(app)
    # Discard only synthetic test drafts, never invoke a save dialog on teardown.
    QMessageBox.question = lambda *_args, **_kwargs: QMessageBox.Discard

    def settle(milliseconds):
        loop = QEventLoop()
        QTimer.singleShot(milliseconds, loop.quit)
        loop.exec_()
    probe = QLabel("布尔值继续条件判断循环体输入输出")
    probe.resize(480, 50)
    probe.show()
    app.processEvents()
    raster = probe.grab().toImage()
    assert len({raster.pixel(x, y) for y in range(raster.height())
                for x in range(raster.width())}) > 2, "Chinese text rasterization failed"
    probe.close()
    probe.deleteLater()

    class RuntimeStub:
        runtimeScope = "isolated-native-validation"

        def listOperators(self):
            return []

        def __getattr__(self, name):
            raise AttributeError(name)

    report = {"platform": args.platform, "scale": os.environ.get("QT_SCALE_FACTOR", "1"),
              "font": app.font().family(), "runtimeCalls": 0, "cases": []}
    examples = {
        "boolean": ROOT / "examples/image_batch_while_boolean/image-batch-while-boolean.emoproj",
        "legacy": ROOT / "examples/image_batch_while_portable/image-batch-while.emoproj",
    }
    for mode, example in examples.items():
        window = MainWindow(RuntimeStub())
        print(f"Validating {mode} widgets", flush=True)
        tree = None
        try:
            window.workflowController.loadPayload(json.loads(example.read_text(encoding="utf-8")), preserveEdges=True)
            window.activeWorkflowId = "main"
            window._refreshWorkflowTabs()
            window.resize(1380, 900)
            window.show()
            settle(120)
            node = window.flowScene._nodeItems["while"]
            labels = [child for child in node.childItems() if isinstance(child, QGraphicsSimpleTextItem)]
            for label in labels:
                assert node.rect().contains(label.mapRectToParent(label.boundingRect())), label.text()
            expected = "hasNext（布尔值）" if mode == "boolean" else "continue（布尔值）"
            assert any(expected in label.text() for label in labels)
            rect = node.sceneBoundingRect().adjusted(-24, -20, 24, 20)
            density = window.devicePixelRatioF()
            canvas = QImage(int(rect.width() * density), int(rect.height() * density), QImage.Format_ARGB32)
            canvas.setDevicePixelRatio(density)
            canvas.fill(Qt.white)
            painter = QPainter(canvas)
            window.flowScene.render(painter, QRectF(0, 0, rect.width(), rect.height()), rect)
            painter.end()
            assert canvas.save(str(args.output / f"{mode}-canvas.png"))

            window.openNodeParamDialog("while")
            editor = window.nodeParamDialog
            form = editor._schemaForm
            if mode == "boolean":
                assert form._controls["conditionWorkflowId"].isHidden()
                assert form._controls["conditionPort"].currentText() == "hasNext : boolean"
            else:
                assert form._controls["conditionPort"].isHidden()
                assert form._controls["conditionMode"].currentData() == "workflow"
            assert form.validationMessage() == ""
            for width in (640, 460):
                editor.resize(width, 560)
                editor.show()
                settle(80)
                for label in editor.findChildren(QLabel):
                    if label.isVisible() and label.wordWrap():
                        assert label.heightForWidth(label.width()) <= label.height() + 2, label.text()
                assert editor.grab().save(str(args.output / f"{mode}-parameters-{width}.png"))
                report["cases"].append({"mode": mode, "requestedWidth": width,
                                        "actualWidth": editor.width(), "widget": "parameters"})
            editor.hide()

            window.refreshWorkflowDependencyTree()
            tree = WorkflowRelationshipTree()
            tree.setHeaderHidden(True)
            for index in range(window.workflowDependencyTree.topLevelItemCount()):
                tree.addTopLevelItem(window.workflowDependencyTree.topLevelItem(index).clone())
            tree.expandAll()  # only the isolated validation tree, never production refresh
            for width in (308, 240):
                tree.resize(width, 800)
                tree.show()
                settle(80)
                entries = [item for _key, item in tree._itemsWithKeys()]
                if mode == "boolean":
                    condition = next(item for item in entries
                        if (item.data(0, Qt.UserRole) or {}).get("relation") == "boolean-condition")
                    assert "hasNext : boolean" in condition.text(0)
                    measured = QTextDocument()
                    measured.setDocumentMargin(0)
                    measured.setDefaultFont(tree.font())
                    textOption = QTextOption()
                    textOption.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
                    measured.setDefaultTextOption(textOption)
                    measured.setPlainText(condition.text(0))
                    measured.setTextWidth(max(1, tree.visualItemRect(condition).width() - 8))
                    assert tree.visualItemRect(condition).height() >= ceil(measured.size().height()) + 6
                    assert any("下一轮继续条件.hasNext" in item.text(0) for item in entries)
                for before, after in zip(entries, entries[1:]):
                    assert tree.visualItemRect(before).bottom() < tree.visualItemRect(after).top()
                assert tree.horizontalScrollBar().maximum() == 0
                assert tree.grab().save(str(args.output / f"{mode}-relationships-{width}.png"))
                report["cases"].append({"mode": mode, "width": width, "widget": "relationships"})
        finally:
            if tree is not None:
                tree.close()
                tree.deleteLater()
            window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
