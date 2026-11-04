"""Native Qt walkthrough using real query RPC code and temporary mock records only."""
# ruff: noqa: E402
import argparse
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
import emo_master  # noqa: F401 - Windows Qt bootstrap before importing PySide2.
from PySide2.QtCore import QTimer
from PySide2.QtWidgets import QApplication, QHBoxLayout, QPushButton, QWidget
from emo_master.apps.designer.ui.log_dialog import LogDialog, StructuredLogEntry
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.services.global_logs import RuntimeGlobalLogs
from emo_master.apps.designer.services.local_log import DesignerLogWriter
from emo_master.apps.runtime.grpc_server.service import RuntimeService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=600)
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    from emo_master.apps.designer.main import applyDesignerStyle
    applyDesignerStyle(app)
    with tempfile.TemporaryDirectory(prefix="emo-logging-native-") as temp:
        root = Path(temp)
        services = [RuntimeService(dbPath=root / name / "runtime.sqlite3", workspaceRoot=root / name / "jobs",
            logDirectory=root / name / "logs", runtimeInstanceId="mock-" + name) for name in ("alpha", "beta")]
        for service in services:
            service.eventStore.append("old-job-A", "node.log", "旧工程定位完成", projectId="old-project",
                workflowId="inspection", nodeId="locate", payload={"data": {"count": 2}, "operatorId": "mock.locate"})
            service.eventStore.append("__runtime__", "device.connect.failed", "模拟相机连接失败（没有连接硬件）",
                level="ERROR", projectId="old-project", payload={"deviceId": "mock-camera", "jobId": "old-job-A"})
        client = RuntimeClient(runtimeService=services[0])
        local = DesignerLogWriter(root / "designer")
        local.record("designer.startup.failed", "模拟初始化失败：可写目录中的测试记录", level="ERROR")
        dialog = LogDialog()
        dialog.setWindowTitle("日志整合走查 · 临时模拟数据 · 无硬件")
        state = {"reader": RuntimeGlobalLogs(services[0]), "rows": [], "index": 0, "tick": 0, "cursor": None, "instance": ""}
        dialog.view.setLogDirectory(root / "designer")
        dialog.view.setRuntimeClient(client, lambda: dialog.setEntries(state["rows"]))
        toolbar = QWidget()
        row = QHBoxLayout(toolbar)
        switch = QPushButton("切换模拟 Runtime alpha / beta")
        reconnect = QPushButton("暂停 / 重连日志查询")
        row.addWidget(switch)
        row.addWidget(reconnect)
        dialog.layout().insertWidget(0, toolbar)
        def stop():
            reader = state["reader"]
            if reader is not None:
                reader.close()
                state["cursor"], state["instance"] = reader.cursor, reader.runtimeInstanceId
                state["reader"] = None
        def toggle():
            if state["reader"] is not None:
                stop()
            else:
                state["reader"] = RuntimeGlobalLogs(services[state["index"]], afterEventId=state["cursor"], runtimeInstanceId=state["instance"])
        def change():
            stop()
            state["index"] = 1 - state["index"]
            state["cursor"], state["instance"], state["rows"] = None, "", []
            client.runtimeService = services[state["index"]]
            dialog.view.setRuntimeClient(client, lambda: dialog.setEntries(state["rows"]))
            dialog.view.rangeCombo.setCurrentIndex(0)
            dialog.setEntries([])
            state["reader"] = RuntimeGlobalLogs(services[state["index"]])
        switch.clicked.connect(change)
        reconnect.clicked.connect(toggle)
        def tick():
            state["tick"] += 1
            service = services[state["index"]]
            if state["tick"] % 8 == 0:
                service.eventStore.append("live-job", "node.log", "模拟流程定位完成 " + service.runtimeInstanceId,
                    projectId="mock-project", workflowId="inspection", nodeId="locate", payload={"data": {"count": state["tick"]}})
            reader = state["reader"]
            if reader:
                for kind, value in reader.take():
                    if kind == "event":
                        entry = StructuredLogEntry.fromRuntimeEvent(value)
                        state["rows"].append(entry)
                        dialog.appendEntry(entry)
        timer = QTimer()
        timer.timeout.connect(tick)
        timer.start(250)
        QTimer.singleShot(args.seconds * 1000, app.quit)
        dialog.show()
        print(str(root), flush=True)
        try:
            return app.exec_()
        finally:
            timer.stop()
            dialog.view.cancelHistory()
            dialog.view._historyExecutor.shutdown(wait=True, cancel_futures=True)
            stop()
            local.close()
            client.close()
            for service in services:
                service.close()


if __name__ == "__main__":
    raise SystemExit(main())
