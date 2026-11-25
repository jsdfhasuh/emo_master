import json

from PySide2.QtCore import Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QDialog, QPushButton, QTableWidget

from emo_master.apps.designer.ui import log_json_tree, log_record_detail
from emo_master.apps.designer.ui.log_dialog import StructuredLogEntry
from emo_master.apps.designer.ui.log_json_tree import LogJsonTree
from emo_master.apps.designer.ui.log_record_detail import LogRecordDetail
from emo_master.plugins.builtins.annotate.operator import AnnotateOperator


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


def testJobAndDebugExplainOnlyMatchingOperatorFieldsAndKeepRawValues(designerApplication, monkeypatch):
    copies = []
    monkeypatch.setattr(log_json_tree, "_copyText", copies.append)
    definition = {"operatorId": "test.operator", "version": "1.2", "paramSchema": {"properties": {
        "options": {
            "properties": {"limit": {"title": "限制", "description": "限制说明", "xUnit": "px"}},
            "xFieldDescriptions": {"constant": {"title": "常量", "description": "显式提供的常量"}},
        }}},
        "resultSchema": {
            "inputs": {"amount": {"title": "输入数值", "description": "实际输入"}},
            "outputs": {"objects": {"title": "对象集合", "description": "实际结果集合",
                                    "items": {"title": "对象", "fields": {"score": {
                                        "title": "评分", "description": "对象得分", "unit": "分"}}}}},
            "metrics": {"latencyMs": {"title": "提交用时", "description": "只统计后台提交", "unit": "ms",
                                      "group": "duration"}},
            "diagnostics": {"effectiveLimit": {"title": "实际限制", "description": "归一化后的值",
                                                "group": "parameter"}},
        }}
    detail = LogRecordDetail(operatorLookup=lambda *_: definition)
    summary = [
        {"path": ["options", "limit"], "value": None, "stage": "call", "source": "prepared"},
        {"path": ["options", "limit"], "value": 0, "stage": "effective", "source": "input"},
    ]
    payload = {"operatorId": "test.operator", "operatorVersion": "1.2",
               "metrics": {"latencyMs": 0.25, "unknown": False}, "diagnostics": {"effectiveLimit": 0},
               "parameterSummary": summary}
    event = {"eventType": "node.completed", "payload": {**payload, "ioSummary": {
        "version": 1, "inputs": {"items": [{"port": "amount", "value": {"kind": "scalar", "value": 0}}],
                                 "portCount": 1},
        "outputs": {"items": [{"port": "objects", "value": {"kind": "collection", "count": 1}}],
                    "portCount": 1},
    }}}
    detail.setEvent(event, nodeElapsedMs=3)
    root = detail.readable.invisibleRootItem()
    assert child(root, "节点总耗时").text(1) == "3 ms · 节点起止事件"
    latency = child(child(root, "运行概览"), "提交用时（latencyMs）")
    assert latency.text(1) == "0.25 ms" and "只统计后台提交" in latency.toolTip(0)
    assert child(child(root, "输入摘要"), "输入数值（amount）").text(1) == "0"
    output = child(child(root, "输出摘要"), "对象集合（objects）")
    assert "1 项" in output.text(1) and "仅显示数量" in output.text(1)
    output.setExpanded(True)
    assert child(output, "count").text(1) == "1"
    assert child(child(root, "指标"), "unknown").text(1) == "false"
    param = "限制（options.limit）"
    assert child(child(root, "本次调用参数"), param).text(1) == "null · 准备后的节点参数"
    assert child(child(root, "本次生效参数"), param).text(1) == "0 px · 输入覆盖"
    assert child(child(root, "本次生效参数"), "实际限制（effectiveLimit）").text(1) == "0 · 诊断"
    detail.copyRecordButton.click()
    assert json.loads(copies[-1]) == event

    result = {"sourceIdentity": {"operatorId": "test.operator", "operatorVersion": "1.2", "generation": 1},
              "metrics": payload["metrics"], "diagnostics": payload["diagnostics"], "parameterSummary": summary,
              "elapsedMs": 1.5, "outputs": {"objects": [{"score": 0, "extra": ""}]},
              "effectiveParams": {"options": {"limit": 1, "constant": None}}}
    detail.setDebugResult(result, sessionId="session", generation=1)
    assert child(root, "调试执行耗时").text(1) == "1.5 ms"
    output = child(child(root, "已返回输出"), "对象集合（objects）")
    output.setExpanded(True)
    entry = child(output, "对象（0）")
    entry.setExpanded(True)
    score = child(entry, "评分（score）")
    assert score.text(1) == "0 分" and child(entry, "extra").text(1) == '""'
    prepared = child(root, "准备阶段参数")
    prepared.setExpanded(True)
    options = child(prepared, "options")
    options.setExpanded(True)
    preparedLimit = child(options, "限制（limit）")
    assert preparedLimit.text(1) == "1 px" and "限制说明" in preparedLimit.toolTip(0)
    assert child(options, "常量（constant）").text(1) == "null"
    detail.readable.setCurrentItem(score)
    detail.copyValueButton.click()
    assert json.loads(copies[-1]) == 0
    assert json.loads(detail.rawTree.toPlainText()) == result

    mismatched = {**result, "sourceIdentity": {**result["sourceIdentity"], "operatorVersion": "older"}}
    detail.setDebugResult(mismatched, sessionId="session", generation=1)
    assert child(child(root, "已返回输出"), "objects")
    assert child(child(root, "指标"), "latencyMs").text(1) == "0.25"
    assert child(child(root, "本次生效参数"), "options.limit").text(1) == "0 · 输入覆盖"
    assert json.loads(detail.rawTree.toPlainText()) == mismatched


def testAnnotateClassStylesExplainDynamicMembersAndCopyActualValues(designerApplication, monkeypatch):
    copies = []
    monkeypatch.setattr(log_json_tree, "_copyText", copies.append)
    schema = AnnotateOperator.meta.paramSchema["properties"]["classStyles"]
    style = {"displayLabel": "安全锥", "color": [0, 128, 255], "futureField": None}
    styles = {"safety-cone": style}
    tree = log_record_detail._ReadableTree()
    group = tree.field("classStyles", styles, semantics=schema, expand=True)
    member = child(group, "safety-cone")
    assert schema["additionalProperties"]["description"] in member.toolTip(0)
    member.setExpanded(True)
    label = child(member, "显示标签（displayLabel）")
    assert label.text(1) == "安全锥" and "不修改输入 label" in label.toolTip(0)
    color = child(member, "类别颜色（BGR）（color）")
    color.setExpanded(True)
    firstChannel = child(color, "颜色通道值（0）")
    assert (firstChannel.text(1), firstChannel.text(2)) == ("0", "number")
    assert "按 B、G、R 顺序" in firstChannel.toolTip(0)
    unknown = child(member, "futureField")
    assert (unknown.text(1), unknown.text(2), unknown.toolTip(0)) == ("null", "null", "futureField")

    for item in (group, member, color, firstChannel):
        tree.setCurrentItem(item)
        tree.copySelectedValue()
    assert [json.loads(value) for value in copies] == [styles, style, [0, 128, 255], 0]
