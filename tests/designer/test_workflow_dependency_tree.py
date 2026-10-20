from __future__ import annotations

from copy import deepcopy

import pytest

from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.designer.ui.main_window import MainWindow


class _RuntimeClientStub:
    def listOperators(self):
        return []


def _addWorkflow(store: WorkflowStore, workflowId: str, name: str) -> None:
    store.addWorkflow(name, workflowId=workflowId)


def testDependencyTreeShowsAllReferenceKindsAndUnusedWorkflows() -> None:
    store = WorkflowStore()
    for workflowId, name in [
        ("subflow-body", "Subflow Body"),
        ("repeat-body", "Repeat Body"),
        ("foreach-body", "ForEach Body"),
        ("while-body", "While Body"),
        ("while-condition", "While Condition"),
        ("unused", "Unused"),
    ]:
        _addWorkflow(store, workflowId, name)
    store.get("main").nodes.extend(
        [
            {
                "nodeId": "subflow-node",
                "kind": "subflow",
                "targetWorkflowId": "subflow-body",
            },
            {
                "nodeId": "repeat-node",
                "kind": "loop",
                "loop": {
                    "mode": "repeat",
                    "bodyWorkflowId": "repeat-body",
                },
            },
            {
                "nodeId": "foreach-node",
                "kind": "loop",
                "loop": {
                    "mode": "foreach",
                    "bodyWorkflowId": "foreach-body",
                },
            },
            {
                "nodeId": "while-node",
                "kind": "loop",
                "loop": {
                    "mode": "while",
                    "bodyWorkflowId": "while-body",
                    "conditionWorkflowId": "while-condition",
                },
            },
        ]
    )

    references = store.getWorkflowDependencyReferences()
    assert [(item["relation"], item["targetWorkflowId"]) for item in references] == [
        ("subflow", "subflow-body"),
        ("repeat-body", "repeat-body"),
        ("foreach-body", "foreach-body"),
        ("while-body", "while-body"),
        ("while-condition", "while-condition"),
    ]

    tree = store.getWorkflowDependencyTree()
    entry = tree[0]
    assert entry["workflowId"] == "main"
    assert entry["isEntry"] is True
    assert [child["relation"] for child in entry["children"]] == [
        "subflow",
        "repeat-body",
        "foreach-body",
        "while-body",
        "while-condition",
    ]
    unusedGroup = tree[1]
    assert unusedGroup["itemType"] == "group"
    assert unusedGroup["workflowIds"] == ["unused"]
    unused = unusedGroup["children"][0]
    assert unused["workflowId"] == "unused"
    assert unused["isReachable"] is False
    assert unused["isUnreferenced"] is True


def testDependencyTreeTerminatesCyclesAndKeepsMissingTargetsVisible() -> None:
    store = WorkflowStore()
    _addWorkflow(store, "child", "Child")
    store.get("main").nodes.extend(
        [
            {
                "nodeId": "to-child",
                "kind": "subflow",
                "targetWorkflowId": "child",
            },
            {
                "nodeId": "to-missing",
                "kind": "loop",
                "loop": {
                    "mode": "repeat",
                    "bodyWorkflowId": "missing-body",
                },
            },
        ]
    )
    store.get("child").nodes.append(
        {
            "nodeId": "back-to-main",
            "kind": "subflow",
            "targetWorkflowId": "main",
        }
    )

    entry = store.getWorkflowDependencyTree()[0]
    child = entry["children"][0]
    cycle = child["children"][0]
    missing = entry["children"][1]

    assert cycle["workflowId"] == "main"
    assert cycle["status"] == "cycle"
    assert cycle["children"] == []
    assert missing["workflowId"] == "missing-body"
    assert missing["status"] == "missing"
    assert missing["exists"] is False


def testDependencyTreeClickSwitchesTheActiveWorkflow() -> None:
    window = MainWindow(_RuntimeClientStub())
    bodyWorkflowId = window.createWorkflow("Body")
    window.activateWorkflow("main")
    assert window.addSubflowNode(bodyWorkflowId) is not None

    assert window.workflowDependencyTree.topLevelItemCount() == 1
    entryItem = window.workflowDependencyTree.topLevelItem(0)
    assert "Main" in entryItem.text(0)
    assert entryItem.childCount() == 1
    callItem = entryItem.child(0)
    assert "子工作流调用节点" in callItem.text(0)
    bodyItem = callItem.child(0)
    assert "Body" in bodyItem.text(0)

    window.workflowDependencyTree.itemClicked.emit(bodyItem, 0)

    assert window.getActiveWorkflowId() == bodyWorkflowId
    assert window.workflowTabs.currentIndex() == 1


def testDependencyTreeRefreshesAfterRenameAndEntryChange() -> None:
    window = MainWindow(_RuntimeClientStub())
    bodyWorkflowId = window.createWorkflow("Body")

    window.renameWorkflow(bodyWorkflowId, "Renamed Body")
    tree = window.getWorkflowDependencyEntries()
    unused = tree[1]["children"][0]
    assert unused["name"] == "Renamed Body"

    window.setEntryWorkflow(bodyWorkflowId)

    tree = window.getWorkflowDependencyEntries()
    assert tree[0]["workflowId"] == bodyWorkflowId
    assert tree[0]["isEntry"] is True
    assert tree[1]["workflowIds"] == ["main"]
    entryItem = window.workflowDependencyTree.topLevelItem(0)
    assert "Renamed Body" in entryItem.text(0)
    assert "入口" in entryItem.text(0)


def _transferStore() -> WorkflowStore:
    store = WorkflowStore()
    store.addWorkflow("Detect", workflowId="detect")
    child = store.get("detect")
    child.inputs = {"image": "image", "threshold": {"type": "number", "required": False}}
    child.outputs = {"result": "object", "unused": "string"}
    main = store.get("main")
    main.inputs = {"image": "image"}
    main.outputs = {"result": "object"}
    store.ensureBoundaryNodes()
    main.nodes.extend([
        {"nodeId": "call-1", "displayName": "Detect A", "kind": "subflow",
         "targetWorkflowId": "detect", "inputPorts": {"image": "image", "threshold": "number"},
         "outputPorts": child.outputs},
        {"nodeId": "call-2", "displayName": "Detect B", "kind": "subflow",
         "targetWorkflowId": "detect", "inputPorts": {"image": "image", "threshold": "number"},
         "outputPorts": child.outputs},
    ])
    inputId = next(n["nodeId"] for n in main.nodes if n["kind"] == "workflow_input")
    outputId = next(n["nodeId"] for n in main.nodes if n["kind"] == "workflow_output")
    main.edges = [
        {"fromNode": inputId, "fromPort": "image", "toNode": "call-1", "toPort": "image"},
        {"fromNode": "call-1", "fromPort": "result", "toNode": outputId, "toPort": "result"},
    ]
    return store


def testTransferRoutesArePerCallSiteAndDoNotMutateProject() -> None:
    store = _transferStore()
    before = deepcopy(store.workflows)
    first, second = store.getWorkflowDependencyTree()[0]["children"]
    rows = first["dataTransfers"]
    assert first["sourceNodeName"] == "Detect A"
    assert rows[0] == {"direction": "input", "port": "image", "portType": "image",
                       "source": "Main / 输入.image", "target": "Detect / 输入.image", "note": ""}
    assert rows[1]["source"] == "未连接"
    assert rows[1]["note"] == "可选输入"
    assert rows[2]["target"] == "Main / 输出.result"
    assert rows[3]["note"] == "无下游连接"
    assert second["dataTransfers"][0]["source"] == "未连接"
    assert second["dataTransfers"][0]["note"] == "必需输入"
    assert store.workflows == before


def testTransfersKeepFanoutAndSqliteFieldConsumers() -> None:
    store = _transferStore()
    main = store.get("main")
    main.edges.append({"fromNode": "call-1", "fromPort": "result",
                       "toNode": "call-2", "toPort": "extra"})
    main.nodes.append({"nodeId": "db", "kind": "operator", "operatorId": "vision.io.sqlite_writer",
                       "params": {"mappings": [{"column": "payload", "source": {
                           "kind": "node_output", "nodeId": "call-1", "port": "result"}}]}})
    rows = store.getWorkflowDependencyTree()[0]["children"][0]["dataTransfers"]
    targets = [row["target"] for row in rows if row["direction"] == "output" and row["port"] == "result"]
    assert targets == ["Main / 输出.result", "Main / Detect B (Detect).extra", "Main / db.payload"]


@pytest.mark.parametrize("version", [1, 2])
def testForEachTransferRoutesFollowContract(version) -> None:
    store = WorkflowStore()
    store.addWorkflow("Body", workflowId="body")
    body = store.get("body")
    item, index = ("item", "index") if version == 1 else ("frame", "position")
    body.inputs = {item: "image", index: "integer", "threshold": "number"}
    body.outputs = {"result": "object"}
    main = store.get("main")
    main.nodes.append({"nodeId": "loop", "kind": "loop", "loop": {
        "mode": "foreach", "contractVersion": version, "bodyWorkflowId": "body",
        "itemInputPort": item, "indexInputPort": index}})
    main.edges = [{"fromNode": "images", "fromPort": "frames", "toNode": "loop", "toPort": "items"}]
    rows = store.getWorkflowDependencyTree()[0]["children"][0]["dataTransfers"]
    assert rows[0]["source"] == "Main / images.frames"
    assert rows[0]["note"] == "逐项传入 items[i]"
    assert rows[1]["source"] == "循环迭代序号"
    assert rows[-1]["target"].endswith(".results" if version == 1 else ".result")
    assert ("results[i].result" if version == 1 else "list<object>") in rows[-1]["note"]


@pytest.mark.parametrize("version", [1, 2])
def testWhileTransfersSeparateStateFromConditionControl(version) -> None:
    store = WorkflowStore()
    store.addWorkflow("Body", workflowId="body")
    store.addWorkflow("Condition", workflowId="condition")
    port, portType = ("state", "object") if version == 1 else ("count", "integer")
    body, condition = store.get("body"), store.get("condition")
    body.inputs = body.outputs = {port: portType}
    condition.inputs = {port: portType}
    condition.outputs = {"continue": "boolean"}
    main = store.get("main")
    main.nodes.append({"nodeId": "loop", "kind": "loop", "loop": {
        "mode": "while", "contractVersion": version, "bodyWorkflowId": "body",
        "conditionWorkflowId": "condition"}})
    main.edges = [{"fromNode": "seed", "fromPort": "value", "toNode": "loop", "toPort": port}]
    bodyEntry, conditionEntry = store.getWorkflowDependencyTree()[0]["children"]
    rows = conditionEntry["dataTransfers"]
    assert rows[0]["source"] == "初始：Main / seed.value"
    assert rows[1]["source"] == f"后续：Body / 输出.{port}"
    assert rows[2]["target"] == "Main / loop / 循环判断"
    assert "false：结束循环" in rows[2]["note"]
    assert "回传下一轮状态" in bodyEntry["dataTransfers"][-1]["note"]


def testRepeatUsesOriginalInputsAndReturnsLastIteration() -> None:
    store = _transferStore()
    node = store.get("main").nodes[-2]
    node["kind"] = "loop"
    node["loop"] = {"mode": "repeat", "bodyWorkflowId": "detect"}
    rows = store.getWorkflowDependencyTree()[0]["children"][0]["dataTransfers"]
    assert "每轮使用相同输入" in rows[0]["note"]
    assert "最后一轮输出" in rows[2]["note"]


def _itemTexts(item):
    return [item.text(0), *[text for i in range(item.childCount())
                           for text in _itemTexts(item.child(i))]]


def testTransferTreeRendersAndRefreshesConnections(designerApplication, tmp_path, monkeypatch) -> None:
    window = MainWindow(_RuntimeClientStub())
    store = _transferStore()
    window.workflowController.loadPayload(store.toPayload())
    window.activeWorkflowId = "main"
    window.refreshWorkflowDependencyTree()
    tree = window.workflowDependencyTree
    root = tree.topLevelItem(0)
    assert "数据传递" in window.dependencyTreeTitle.text()
    assert "工作流 ID：main" in root.toolTip(0)
    assert "输入数据 (2)" in _itemTexts(root.child(0))
    assert "Main / 输入.image\n→ Detect / 输入.image" in "\n".join(_itemTexts(root.child(0)))
    inputId = next(n["nodeId"] for n in store.get("main").nodes if n["kind"] == "workflow_input")
    assert window.connectPorts(inputId, "image", "call-2", "image") is not None
    assert "Main / 输入.image\n→ Detect / 输入.image" in "\n".join(_itemTexts(tree.topLevelItem(0).child(1)))
    dataItem = tree.topLevelItem(0).child(1).child(0).child(0).child(0).child(0)
    tree.itemClicked.emit(dataItem, 0)
    assert window.getActiveWorkflowId() == "main"
    window.resize(1280, 800)
    window.show()
    window.expandSidebar()
    window.floatingToolbox.tabs.setCurrentIndex(1)
    designerApplication.processEvents()
    assert window.floatingToolbox.tabs.tabText(1) == "关系"
    assert tree.horizontalScrollBar().maximum() == 0
    image = window.floatingToolbox.grab()
    assert not image.isNull()
    path = tmp_path / "workflow-transfers.png"
    assert image.save(str(path))
    print(f"Relationship screenshot: {path}")
    monkeypatch.setattr(window.flowScene, "getSelectedEdgeKeys",
                        lambda: [(inputId, "image", "call-2", "image")])
    monkeypatch.setattr(window.flowScene, "getSelectedNodeIds", lambda: [])
    window.deleteSelectedElements()
    text = "\n".join(_itemTexts(tree.topLevelItem(0).child(1)))
    assert "未连接\n→ Detect / 输入.image" in text


def testTransfersRemainVisibleForUnreachableAndCyclicCalls() -> None:
    store = _transferStore()
    store.setEntryWorkflow("detect")
    entries = store.getWorkflowDependencyTree()
    assert entries[1]["label"] == "入口未调用的工作流"
    assert entries[1]["children"][0]["children"][0]["dataTransfers"]
    store.get("detect").nodes.append({"nodeId": "back", "kind": "subflow", "targetWorkflowId": "main"})
    cycle = store.getWorkflowDependencyTree()[0]["children"][0]["children"][0]
    assert cycle["status"] == "cycle"
    assert cycle["children"] == []
    assert cycle["dataTransfers"]


def testMissingTargetDoesNotInventDataTransfers() -> None:
    store = _transferStore()
    del store.workflows["detect"]
    missing = store.getWorkflowDependencyTree()[0]["children"][0]
    assert missing["status"] == "missing"
    assert missing["sourceNodeName"] == "Detect A"
    assert "dataTransfers" not in missing


def testWhileIterationInputComesFromLoopNotState() -> None:
    store = WorkflowStore()
    store.addWorkflow("Body", workflowId="body")
    store.get("body").inputs = {"state": "object", "__iteration__": "integer"}
    store.get("body").outputs = {"state": "object"}
    store.get("main").nodes.append({"nodeId": "loop", "kind": "loop", "loop": {
        "mode": "while", "bodyWorkflowId": "body", "conditionWorkflowId": "missing"}})
    rows = store.getWorkflowDependencyTree()[0]["children"][0]["dataTransfers"]
    iteration = next(row for row in rows if row["port"] == "__iteration__")
    assert iteration["source"] == "循环迭代序号"


def testNonWhileConditionReferenceDoesNotClaimACall() -> None:
    store = _transferStore()
    store.get("main").nodes.append({"nodeId": "loop", "kind": "loop", "loop": {
        "mode": "repeat", "bodyWorkflowId": "detect", "conditionWorkflowId": "detect"}})
    entry = store.getWorkflowDependencyTree()[0]["children"][-1]
    assert entry["relation"] == "repeat-body"
    assert not any(ref["relation"] == "loop-condition" for ref in store.getWorkflowDependencyReferences())
    assert store.get("main").nodes[-1]["loop"]["conditionWorkflowId"] == "detect"


def testIdenticallyNamedCallersKeepSeparateTargetsAndData() -> None:
    window = MainWindow(_RuntimeClientStub())
    store = _transferStore()
    for node in store.get("main").nodes:
        if node.get("kind") == "subflow":
            node["displayName"] = "Same caller"
    root = window._buildWorkflowDependencyTreeItem(store.getWorkflowDependencyTree()[0])
    assert root.childCount() == 2
    first, second = root.child(0), root.child(1)
    assert first.text(0) == second.text(0)
    assert first.child(0).text(0) == second.child(0).text(0)
    assert "Main / 输入.image" in "\n".join(_itemTexts(first))
    assert "未连接\n→ Detect / 输入.image" in "\n".join(_itemTexts(second))


@pytest.mark.parametrize("mode,label", [("repeat", "Repeat 计次"), ("foreach", "ForEach 逐项")])
def testOtherLoopModesOnlyShowTheirExecutableBody(mode, label) -> None:
    window = MainWindow(_RuntimeClientStub())
    store = _transferStore()
    node = store.get("main").nodes[-2]
    node["kind"] = "loop"
    node["loop"] = {"mode": mode, "bodyWorkflowId": "detect", "conditionWorkflowId": "detect"}
    root = window._buildWorkflowDependencyTreeItem(store.getWorkflowDependencyTree()[0])
    call = root.child(0)
    assert label in call.text(0)
    assert call.childCount() == 1
    assert "循环体" in call.child(0).text(0)
    assert node["loop"]["conditionWorkflowId"] == "detect"
