from __future__ import annotations

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
    bodyItem = entryItem.child(0)
    assert "Subflow" in bodyItem.text(0)
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
