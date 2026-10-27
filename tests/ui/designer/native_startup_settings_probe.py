"""Native Qt startup-chooser screenshots; no Runtime, project or hardware access."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    output = arguments.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    os.environ["QT_QPA_PLATFORM"] = "windows" if os.name == "nt" else "offscreen"
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
    from emo_master.apps.designer.main import applyDesignerStyle, configureHighDpi
    configureHighDpi()
    from PySide2.QtCore import QSettings, Qt
    from PySide2.QtGui import QFontInfo
    from PySide2.QtWidgets import QApplication, QLabel
    from emo_master.apps.designer.ui.startup_settings_dialog import DesignerStartupSettingsDialog

    app = QApplication([])
    applyDesignerStyle(app)
    store = QSettings(str(output / "designer.ini"), QSettings.IniFormat)
    reports = []
    for mode, width in (("local", 640), ("local", 480), ("remote", 640), ("error", 640)):
        dialog = DesignerStartupSettingsDialog(store)
        dialog.setAttribute(Qt.WA_DontShowOnScreen, True)
        dialog.setAttribute(Qt.WA_ShowWithoutActivating, True)
        root = output / "独立运行目录 with spaces"
        dialog.selectDirectory(str(root))
        if mode == "remote":
            dialog.remoteButton.click()
            dialog.targetCombo.setCurrentText("127.0.0.1:50051")
        elif mode == "error":
            dialog.setStartupError("运行目录已被另一个 Designer 或 Runtime 使用：\n" + str(root)
                                   + "\n请选择其他目录，或等待原实例正常退出后重试。不要删除锁文件或数据库。")
        dialog.resize(width, 420)
        dialog.show()
        app.processEvents()
        path = output / f"{mode}-{width}.png"
        assert dialog.grab().save(str(path))
        titleImage = dialog.findChild(QLabel, "startupHeroTitle").grab().toImage()
        titleInkPixels = sum(titleImage.pixelColor(x, y).alpha() > 100 and titleImage.pixelColor(x, y).lightness() < 120
                             for y in range(titleImage.height()) for x in range(titleImage.width()))
        assert titleInkPixels > 50, "startup title text did not render"
        for widget in (dialog.localButton, dialog.remoteButton, dialog.startButton, dialog.cancelButton):
            point = widget.mapTo(dialog, widget.rect().bottomRight())
            assert point.x() < dialog.width() and point.y() < dialog.height()
            assert widget.fontMetrics().horizontalAdvance(widget.text()) < widget.width() - 20
        assert not root.exists()
        assert 'emo_master.apps.runtime.grpc_server.service' not in sys.modules
        assert 'emo_master.apps.designer.ui.main_window' not in sys.modules
        reports.append(dict(mode=mode, image=str(path), size=[dialog.width(), dialog.height()],
                            titleInkPixels=titleInkPixels))
        dialog.close()
    report = dict(platform=app.platformName(), font=QFontInfo(app.font()).family(),
                  status="PASS", runtimeCreated=False, preferencesSaved=False, screenshots=reports)
    (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))


if __name__ == "__main__":
    main()
