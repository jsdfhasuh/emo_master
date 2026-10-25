import os
from pathlib import Path
import sys
from time import perf_counter


_ENTRY_TIME = perf_counter()


def resolveRuntimeTarget() -> str:
    return os.getenv("EMO_RUNTIME_TARGET", "").strip()


def applyDesignerStyle(app) -> None:
    from emo_master.apps.designer.ui.theme import configureTheme

    configureTheme(app)
    stylePath = Path(__file__).resolve().parent / "ui" / "styles" / "app.qss"
    if not stylePath.exists():
        return
    styleText = stylePath.read_text(encoding="utf-8")
    app.setStyleSheet(styleText)


def _configureHighDpi(qCoreApplication, qt, qGuiApplication) -> bool:
    instance = getattr(qCoreApplication, "instance", None)
    if callable(instance) and instance() is not None:
        return False

    configured = False
    setAttribute = getattr(qCoreApplication, "setAttribute", None)
    if callable(setAttribute):
        for attributeName in ["AA_EnableHighDpiScaling", "AA_UseHighDpiPixmaps"]:
            attribute = getattr(qt, attributeName, None)
            if attribute is None:
                continue
            setAttribute(attribute, True)
            configured = True

    policyType = getattr(qt, "HighDpiScaleFactorRoundingPolicy", None)
    passThrough = getattr(policyType, "PassThrough", None)
    setRoundingPolicy = getattr(
        qGuiApplication, "setHighDpiScaleFactorRoundingPolicy", None
    )
    if callable(setRoundingPolicy) and passThrough is not None:
        setRoundingPolicy(passThrough)
        configured = True
    return configured


def configureHighDpi() -> bool:
    from PySide2.QtCore import QCoreApplication, Qt
    from PySide2.QtGui import QGuiApplication

    return _configureHighDpi(QCoreApplication, Qt, QGuiApplication)


def runDesigner() -> None:
    from emo_master.apps.designer.startup import StartupTiming, prepareRuntime

    timing = StartupTiming(_ENTRY_TIME)
    timing.mark("entry")
    timing.begin("qt")
    configureHighDpi()
    from PySide2.QtWidgets import QApplication
    from emo_master.apps.designer.ui.startup_dialog import DesignerStartupDialog

    app = QApplication([])
    timing.begin("theme")
    applyDesignerStyle(app)
    timing.begin("startup-window")
    startup = DesignerStartupDialog(timing)
    quitOnClose = app.quitOnLastWindowClosed()
    app.setQuitOnLastWindowClosed(False)
    runtimeClient = None
    window = None
    try:
        runtimeClient = startup.prepare(lambda cancelled: prepareRuntime(timing, cancelled))
        timing.finish()
        if startup.cancelled.is_set():
            return

        def advance(stage):
            timing.begin(stage)
            startup.refreshStatus()
            app.processEvents()
            startup.repaint()
            return not startup.cancelled.is_set()

        if not advance("editor-imports"):
            return
        from emo_master.apps.designer.ui.main_window import MainWindow
        from emo_master.apps.designer.ui.project_entry_dialog import ProjectEntryDialog

        class TimedProjectEntryDialog(ProjectEntryDialog):
            _readyReported = False

            def paintEvent(self, event):
                super().paintEvent(event)
                if not self._readyReported:
                    self._readyReported = True
                    timing.finish()
                    timing.mark("ready")

        if not advance("editor-ui"):
            return
        window = MainWindow(runtimeClient, showStartupEntry=True,
                            projectEntryDialogFactory=lambda: TimedProjectEntryDialog(window))
        if not advance("project-entry"):
            return
        startup.finish()
        shouldShow = window.showStartupProjectEntry()
        if not shouldShow:
            return
        window.show()
        app.setQuitOnLastWindowClosed(quitOnClose)
        app.exec_()
    except Exception as error:
        timing.finish("failed")
        startup.finish()
        _showStartupError(error)
    finally:
        try:
            if window is not None:
                window.shutdownOperatorDisplay()
        finally:
            try:
                if runtimeClient is not None:
                    runtimeClient.close()
            finally:
                startup.finish()
                app.setQuitOnLastWindowClosed(quitOnClose)
                if startup.cancelled.is_set():
                    timing.finish("cancelled")
                    timing.mark("cancelled")


def _showStartupError(error) -> None:
    from PySide2.QtWidgets import QMessageBox
    from emo_master.apps.runtime.context.runtime_lock import RuntimeDataInUseError

    dialog = QMessageBox()
    if isinstance(error, RuntimeDataInUseError):
        message = (
            "运行数据目录正被另一个 Designer 或 Runtime 使用。\n\n"
            f"目录：{error.directory}\n\n"
            "请返回已打开的 Designer；如需重新启动，先停止任务并正常关闭原窗口。\n"
            "如果窗口已关闭，请等待原进程退出后重试。\n"
            "不要删除锁文件或数据库来强制启动。"
        )
        dialog.setIcon(QMessageBox.Warning)
        dialog.setWindowTitle("Designer 已在运行或数据目录被占用")
        code = 2
    else:
        import traceback

        traceback.print_exception(type(error), error, error.__traceback__)
        message = f"Designer 启动失败：\n{error}"
        dialog.setIcon(QMessageBox.Critical)
        dialog.setWindowTitle("Designer 启动失败")
        code = 1
    print(f"[designer] {message}", file=sys.stderr, flush=True)
    dialog.setText(message)
    dialog.exec_()
    raise SystemExit(code) from None


if __name__ == "__main__":
    runDesigner()
