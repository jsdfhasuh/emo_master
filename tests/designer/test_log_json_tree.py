import json

from PySide2.QtCore import Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QDialog, QPushButton, QTableWidget

from emo_master.apps.designer.ui import log_json_tree, log_record_detail
from emo_master.apps.designer.ui.log_dialog import StructuredLogEntry
from emo_master.apps.designer.ui.log_json_tree import LogJsonTree
from emo_master.apps.designer.ui.log_record_detail import LogRecordDetail


def child(parent, name):
    return next(parent.child(index) for index in range(parent.childCount())
                if parent.child(index).text(0) == name)


def testJsonTreePreservesTypesAndExpandsNestedData(designerApplication):
    payload = {"message": "图片失败\n查看原因", "payload": {"enabled": False, "value": None,
               "attempt": 3, "path": ["one", {"source": "相机"}]}, "empty": []}
    tree = LogJsonTree()
    tree.setPayload(payload)
    root = tree.topLevelItem(0)
    assert root.isExpanded()
    assert root.childCount() == 3
    assert child(root, "message").text(2) == "string"
    nested = child(root, "payload")
    assert not nested.isExpanded()
    nested.setExpanded(True)
    assert child(nested, "enabled").text(1) == "false"
    assert child(nested, "value").text(1) == "null"
    assert child(nested, "attempt").text(2) == "number"
    assert child(root, "empty").text(2) == "array"
    assert child(root, "empty").childCount() == 0
    assert json.loads(tree.toPlainText()) == payload


def testLargeArraysExpandInPagesWithoutTruncatingCopies(designerApplication, monkeypatch):
    copies = []
    monkeypatch.setattr(log_json_tree, "_copyText", copies.append)
    values = [{"index": index} for index in range(235)]
    tree = LogJsonTree()
    tree.setPayload({"values": values})
    array = tree.topLevelItem(0).child(0)
    assert array.childCount() == 1  # An expansion placeholder, not 235 row widgets.
    array.setExpanded(True)
    assert array.childCount() == 101
    assert array.child(100).text(0) == "更多…"
    tree.more(array.child(100), 0)
    assert array.childCount() == 201
    assert array.child(200).text(0) == "更多…"
    tree.more(array.child(200), 0)
    assert array.childCount() == 235
    tree.setCurrentItem(array)
    tree.copySelectedValue()
    assert json.loads(copies[-1]) == values
    tree.copyJson()
    assert json.loads(copies[-1]) == {"values": values}


def testLongStringAndNullCopyFullJsonValues(designerApplication, monkeypatch):
    copies = []
    monkeypatch.setattr(log_json_tree, "_copyText", copies.append)
    text = "日志\n" * 1200
    tree = LogJsonTree()
    tree.setPayload({"long": text, "missing": None})
    root = tree.topLevelItem(0)
    row = child(root, "long")
    assert len(row.text(1)) == 500
    assert row.data(1, Qt.UserRole + 1) == text  # Existing paged long-text viewer.
    tree.setCurrentItem(row)
    QTest.keyClick(tree, Qt.Key_C, Qt.ControlModifier)
    assert json.loads(copies[-1]) == text
    tree.setCurrentItem(child(root, "missing"))
    tree.copySelectedValue()
    assert copies[-1] == "null"


def testReplacingAndClearingDetailsDropsOldSelection(designerApplication, monkeypatch):
    copies = []
    monkeypatch.setattr(log_json_tree, "_copyText", copies.append)
    tree = LogJsonTree()
    tree.setPayload({"old": {"value": 1}})
    tree.setCurrentItem(tree.topLevelItem(0).child(0))
    tree.setPayload({"new": 2})
    assert tree.topLevelItemCount() == 1
    assert tree.topLevelItem(0).child(0).text(0) == "new"
    tree.copySelectedValue()
    assert json.loads(copies[-1]) == {"new": 2}
    tree.clear()
    tree.copyJson()
    tree.copySelectedValue()
    assert tree.topLevelItemCount() == 0
    assert tree.toPlainText() == ""
    assert len(copies) == 1


def testWorkbenchReadableValuesAndRawCopyShareOneRecord(designerApplication, monkeypatch):
    copies = []
    monkeypatch.setattr(log_json_tree, "_copyText", copies.append)
    monkeypatch.setattr(log_record_detail, "_copyText", copies.append)
    data = {"zero": 0, "off": False, "nil": None, "text": "", "array": [], "object": {},
            "rows": [{"index": index, "passed": False} for index in range(51)],
            "mixed": [None, {"passed": False}], "long": "日志\n" * 600, "private": "<redacted>"}
    record = {"eventType": "node.log", "jobId": "job", "nodeRunId": "old-round",
              "payload": {"data": data, "phase": "lifecycle", "truncated": True}}
    detail = LogRecordDetail()
    detail.setEvent(record)
    root = detail.readable.invisibleRootItem()
    assert child(root, "阶段").text(1) == "生命周期" and "截断" in detail.notice.text()
    fields = child(root, "日志数据")
    assert fields.isExpanded()
    assert [child(fields, key).text(1) for key in ("zero", "off", "nil", "text", "array", "object")] == [
        "0", "false", "null", '""', "[]", "{}"]
    assert child(fields, "private").text(1) == "<redacted>"
    detail.readable.setCurrentItem(child(fields, "mixed"))
    assert detail.arrayTableButton.isHidden()
    child(fields, "mixed").setExpanded(True)
    assert child(child(fields, "mixed"), "0").text(1) == "null"
    detail.readable.setCurrentItem(child(fields, "long"))
    assert child(fields, "long").data(1, Qt.UserRole + 1) == data["long"]
    detail.copyValueButton.click()
    assert json.loads(copies[-1]) == data["long"]

    detail.readable.setCurrentItem(child(fields, "rows"))
    assert not detail.arrayTableButton.isHidden()
    detail.arrayTableButton.click()
    tableDialog = detail.findChild(QDialog)
    table = tableDialog.findChild(QTableWidget)
    assert table.rowCount() == 50 and table.item(0, 1).text() == "false"
    next(button for button in tableDialog.findChildren(QPushButton) if button.text() == "下一页").click()
    assert table.rowCount() == 1 and table.item(0, 0).text() == "50"
    tableDialog.close()

    detail.copyDataButton.click()
    assert json.loads(copies[-1]) == data
    detail.tabs.setCurrentWidget(detail.rawTree)
    detail.copyRecordButton.click()
    assert json.loads(copies[-1]) == record == json.loads(detail.rawTree.toPlainText())
    detail.setEvent({"eventType": "node.log", "payload": {}})
    assert child(root, "日志数据").text(1) == "本记录未提供"
    assert detail.copyDataButton.isHidden()


def testWorkbenchReadsReceivedIoSummaryMetricsAndReasons(designerApplication):
    record = {"eventType": "node.completed", "payload": {
        "ioSummary": {"version": 1, "inputs": {"items": [], "portCount": 0}, "outputs": {
            "items": [{"port": "value", "value": {"kind": "scalar", "value": 0}},
                      {"port": "image", "value": {"kind": "image", "shape": [3, 5], "dtype": "uint8"}},
                      {"port": "matches", "value": {"kind": "collection", "count": 8}}], "portCount": 4}},
        "metrics": {"detectionCount": 0}, "diagnostics": {"provider": "CPU"}}}
    detail = LogRecordDetail()
    detail.setEvent(record)
    root = detail.readable.invisibleRootItem()
    outputs = child(root, "输出摘要")
    assert child(outputs, "value").text(1) == "0"
    assert "uint8" in child(outputs, "image").text(1) and "3 × 5" in child(outputs, "image").text(1)
    assert "8 项" in child(outputs, "matches").text(1)
    assert child(outputs, "省略端口").text(1) == "1"
    assert child(child(root, "指标"), "detectionCount").text(1) == "0"
    assert child(child(root, "诊断"), "provider").text(1) == "CPU"
    assert json.loads(detail.rawTree.toPlainText()) == record

    inner = {"eventType": "node.skipped", "message": "skip", "payload": {
        "message": "缺少端口", "missingInputs": ["image"], "metrics": {}}}
    wrapper = {"type": "node.log", "generation": 2, "executionId": "one", "sequence": 6, "event": inner}
    detail.setDebugEvent(wrapper, "session")
    assert detail.title.text() == "节点跳过记录"
    assert child(root, "跳过原因").text(1) == "缺少端口"
    assert child(root, "输出摘要").text(1) == "本记录未提供"
    assert child(root, "指标").text(1) == "{} · 无附加指标"
    assert child(root, "诊断").text(1) == "本记录未提供"
    assert json.loads(detail.rawTree.toPlainText()) == wrapper


def testCompletedModelShowsRecordedBindingAndLoadedPathBeforePorts(designerApplication):
    digest = "42" * 32
    loaded = rf"C:\runtime\configuration-resources\station\{digest}.onnx"
    source = {"path": "assets/三标签.onnx", "resourceId": "labels", "sha256": digest,
              "configuredPath": "D:/models/previous.onnx"}
    record = {"eventType": "node.completed", "payload": {
        "diagnostics": {"modelPath": loaded, "modelSource": source, "provider": "CPU"}}}
    detail = LogRecordDetail()
    detail.setEvent(record)
    root = detail.readable.invisibleRootItem()
    model = child(root, "使用的模型")
    assert model.isExpanded() and model.text(1) == "三标签.onnx · 工程资源"
    assert root.indexOfChild(model) < root.indexOfChild(child(root, "输入摘要"))
    assert child(model, "模型来源路径").text(1) == source["path"]
    assert child(model, "实际加载路径").text(1) == loaded
    assert child(model, "填写路径（未生效）").text(1) == source["configuredPath"]
    assert child(model, "资源 ID").text(1) == "labels"
    assert child(model, "SHA-256").text(1) == digest
    assert child(root, "诊断").childCount() == 1 and child(child(root, "诊断"), "provider").text(1) == "CPU"
    assert json.loads(detail.rawTree.toPlainText()) == record

    detail.setEvent({"eventType": "node.completed", "payload": {"diagnostics": {
        "modelPath": loaded, "modelSource": {"path": "models/当前模型.onnx", "sha256": digest}}}})
    model = child(root, "使用的模型")
    assert model.text(1) == "当前模型.onnx · 节点选定文件"
    assert child(model, "节点选择路径").text(1) == "models/当前模型.onnx"
    assert child(model, "实际加载路径").text(1) == loaded
    assert child(model, "SHA-256").text(1) == digest

    # Historical records may identify only the Runtime copy. This event has no source filename.
    detail.setEvent({"eventType": "node.completed", "payload": {"diagnostics": {"modelPath": loaded}}})
    model = child(root, "使用的模型")
    assert model.text(1) == "来源未记录" and model.childCount() == 2
    assert child(model, "SHA-256").text(1) == digest
    assert child(model, "实际加载路径").text(1) == loaded

    # A digest-like name elsewhere is just a filename unless a fingerprint was recorded.
    direct = f"D:/models/{digest}.onnx"
    detail.setEvent({"eventType": "node.completed", "payload": {"diagnostics": {
        "modelPath": direct, "modelSource": {"path": direct}}}})
    model = child(root, "使用的模型")
    assert model.text(1).endswith(" · 直接文件") and model.childCount() == 1
    assert child(model, "实际加载路径").text(1) == direct


def testFrameworkPresentationTranslatesOnlyKnownFieldsAndCopiesOriginalValues(designerApplication, monkeypatch):
    copies = []
    monkeypatch.setattr(log_json_tree, "_copyText", copies.append)
    record = {"eventType": "loop.iteration.started", "message": "loop iteration 0", "iterationPath": [0]}
    entry = StructuredLogEntry.fromRuntimeEvent(record)
    assert entry.displayMessage() == "该轮开始" and entry.rawRecord() == record

    detail = LogRecordDetail()
    detail.setEvent(record)
    assert "该轮开始" in detail.title.text()
    assert json.loads(detail.rawTree.toPlainText()) == record
    custom = dict(record, message="loop iteration 0 (custom data)")
    assert StructuredLogEntry.fromRuntimeEvent(custom).displayMessage() == custom["message"]
    record = {"eventType": "node.log", "message": "loop iteration 0", "payload": {"data": {"status": "RUNNING"}}}
    detail.setEvent(record)
    root = detail.readable.invisibleRootItem()
    assert detail.message.text() == "loop iteration 0"
    assert child(child(root, "日志数据"), "status").text(1) == "RUNNING"

    result = {"executionId": "first", "status": "SUCCEEDED", "outputs": {"status": "FAILED"}}
    detail.setDebugResult(result, sessionId="session", generation=1)
    status = child(root, "状态")
    assert status.text(1) == "执行成功"
    assert child(child(root, "已返回输出"), "status").text(1) == "FAILED"
    detail.readable.setCurrentItem(status)
    detail.copyValueButton.click()
    detail.copyRecordButton.click()
    assert [json.loads(value) for value in copies] == ["SUCCEEDED", result]

    detail.setEvent({"eventType": "node.failed", "payload": {"status": "UNRECOGNIZED"}})
    assert child(root, "状态").text(1) == "UNRECOGNIZED"
    detail.setEvent({"eventType": "node.completed", "payload": {"status": "COMPLETED"}})
    assert child(root, "状态").text(1) == "已完成"
