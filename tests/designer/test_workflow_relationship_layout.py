from __future__ import annotations

from math import ceil
from copy import deepcopy
import json
from pathlib import Path

import emo_master  # noqa: F401 - preload Windows dependencies before Qt
import pytest
from PySide2.QtCore import Qt
from PySide2.QtGui import QTextDocument, QTextOption
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication, QTreeWidgetItem

from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.apps.designer.ui.workflow_relationship_tree import WorkflowRelationshipTree


class _RuntimeClientStub:
    def listOperators(self):
        return []


def _requiredHeight(tree, item):
    document = QTextDocument()
    document.setDocumentMargin(0)
    document.setDefaultFont(tree.font())
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
    document.setDefaultTextOption(option)
    document.setPlainText(item.text(0))
    document.setTextWidth(max(1, tree.visualItemRect(item).width() - 8))
    return ceil(document.size().height()) + 6


def testLongWorkflowNameFitsAfterOpeningHiddenRelations(designerApplication, tmp_path):
    window = MainWindow(_RuntimeClientStub())
    window.workflowStore.get("main").name = "Real image CPU detection"
    window.refreshWorkflowDependencyTree()
    window.resize(1100, 760)
    window.show()
    window.expandSidebar()
    window.floatingToolbox.tabs.setCurrentIndex(1)
    for _ in range(3):
        designerApplication.processEvents()
    tree = window.workflowDependencyTree
    item = tree.topLevelItem(0)
    tree.setCurrentItem(item)
    path = tmp_path / "long-workflow-name.png"
    assert window.floatingToolbox.grab().save(str(path))
    print(f"Relationship layout screenshot: {path}")
    assert tree.visualItemRect(item).height() >= _requiredHeight(tree, item)
    assert tree.horizontalScrollBar().maximum() == 0


@pytest.mark.parametrize("pointSize", [9, 12, 16])
def testNestedRowsReflowOnResizeAndKeepEveryLine(designerApplication, pointSize):
    tree = WorkflowRelationshipTree()
    tree.setHeaderHidden(True)
    font = tree.font()
    font.setPointSize(pointSize)
    tree.setFont(font)
    root = QTreeWidgetItem(["Real image CPU detection [入口 · 正在编辑]"])
    call = QTreeWidgetItem(["子工作流调用 → 图像检测与结果输出工作流"])
    data = QTreeWidgetItem(["输出数据 (1)"])
    route = QTreeWidgetItem([
        "检测结果 : list<object>\n检测工作流 / " + "long_unbroken_node_identifier_" * 4
        + ".result\n→ 主工作流 / 数据接收端口\n按轮汇总；循环结束后返回"
    ])
    following = QTreeWidgetItem(["下一个工作流"])
    tree.addTopLevelItem(root)
    tree.addTopLevelItem(following)
    root.addChild(call)
    call.addChild(data)
    data.addChild(route)
    tree.expandAll()
    tree.show()
    heights = []
    for width in (430, 240, 308, 430):
        tree.resize(width, 600)
        for _ in range(3):
            designerApplication.processEvents()
        items = (root, call, data, route, following)
        rectangles = [tree.visualItemRect(item) for item in items]
        for item, rect in zip(items, rectangles):
            assert rect.height() >= _requiredHeight(tree, item)
        for before, after in zip(rectangles, rectangles[1:]):
            assert before.bottom() < after.top()
        assert tree.horizontalScrollBar().maximum() == 0
        heights.append(tree.visualItemRect(route).height())
    assert heights[1] > heights[0]
    assert heights[-1] == heights[0]
    tree.collapseItem(root)
    designerApplication.processEvents()
    clicked = []
    tree.itemClicked.connect(lambda item, _column: clicked.append(item))
    QTest.mouseClick(tree.viewport(), Qt.LeftButton, pos=tree.visualItemRect(following).center())
    assert clicked == [following]
    tree.expandItem(root)
    designerApplication.processEvents()
    assert tree.visualItemRect(route).height() >= _requiredHeight(tree, route)


def testRowsRelayoutWhenFontAndTextChange(designerApplication):
    tree = WorkflowRelationshipTree()
    tree.setHeaderHidden(True)
    tree.resize(280, 400)
    item = QTreeWidgetItem(["Main"])
    tree.addTopLevelItem(item)
    tree.show()
    designerApplication.processEvents()
    initialHeight = tree.visualItemRect(item).height()
    item.setText(0, "Real image CPU detection [入口 · 正在编辑]")
    font = tree.font()
    font.setPointSize(18)
    tree.setFont(font)
    for _ in range(3):
        designerApplication.processEvents()
    assert tree.visualItemRect(item).height() >= _requiredHeight(tree, item)
    assert tree.visualItemRect(item).height() > initialHeight


def _whileWindow():
    window = MainWindow(_RuntimeClientStub())
    example = Path(__file__).resolve().parents[2] / "examples/image_batch_while_portable/image-batch-while.emoproj"
    window.workflowController.loadPayload(
        json.loads(example.read_text(encoding="utf-8")), preserveEdges=True,
    )
    window.activeWorkflowId = "main"
    window.refreshWorkflowDependencyTree()
    window.show()
    QApplication.instance().processEvents()
    window.resize(1280, 1000)
    window.expandSidebar()
    window.floatingToolbox.tabs.setCurrentIndex(1)
    return window


def testWhileShowsCallerBeforeConditionAndBody(designerApplication, tmp_path):
    window = _whileWindow()
    for _ in range(3):
        designerApplication.processEvents()
    tree = window.workflowDependencyTree
    root = tree.topLevelItem(0)
    assert root.childCount() == 1
    call = root.child(0)
    assert call.text(0) == "loop\nWhile 循环控制节点"
    assert "所属工作流：Main" in call.toolTip(0)
    assert call.childCount() == 3
    condition, body, exitItem = [call.child(i) for i in range(3)]
    assert condition.text(0) == "Continue While Images Remain\n条件来源 · 输出 continue:boolean"
    assert body.text(0) == "Load And Process One Image\n循环体 · 条件为真时执行"
    assert "条件为假 → 返回 Main" == exitItem.text(0)
    assert "调用方：Main / loop" in body.toolTip(0)
    assert root.isExpanded() and call.isExpanded()
    assert not condition.child(0).isExpanded()
    assert not body.child(0).isExpanded()
    assert "初始：Main /" in condition.child(0).child(0).child(0).text(0)
    screenshot = tmp_path / "while-call-hierarchy.png"
    assert window.floatingToolbox.grab().save(str(screenshot))
    print(f"While hierarchy screenshot: {screenshot}")
    assert tree.visualItemRect(exitItem).bottom() < tree.viewport().height()
    assert tree.horizontalScrollBar().maximum() == 0
    tree.setCurrentItem(body)
    assert window.floatingToolbox.grab().save(str(tmp_path / "while-selected.png"))


def testClickCallerLocatesLoopAndRefreshKeepsExpandedData(designerApplication):
    window = _whileWindow()
    designerApplication.processEvents()
    tree = window.workflowDependencyTree
    call = tree.topLevelItem(0).child(0)
    body = call.child(1)
    tree.setCurrentItem(body)
    QTest.mouseClick(tree.viewport(), Qt.LeftButton, pos=tree.visualItemRect(body).center())
    designerApplication.processEvents()
    assert window.getActiveWorkflowId() == "body"
    call = tree.topLevelItem(0).child(0)
    tree.scrollToItem(call)
    QTest.mouseClick(tree.viewport(), Qt.LeftButton, pos=tree.visualItemRect(call).center())
    designerApplication.processEvents()
    assert window.getActiveWorkflowId() == "main"
    assert window.flowModel.selectedNodeId == "while"
    assert window.flowScene.getSelectedNodeIds() == ["while"]
    call = tree.topLevelItem(0).child(0)
    data = call.child(1).child(0)
    data.setExpanded(True)
    tree.setCurrentItem(data)
    selectedText = data.text(0)
    before = deepcopy(window.workflowStore.toPayload())
    window.refreshWorkflowDependencyTree()
    assert tree.topLevelItem(0).child(0).child(1).child(0).isExpanded()
    assert tree.currentItem().text(0) == selectedText
    assert window.workflowStore.toPayload() == before
    tree.topLevelItem(0).child(0).setExpanded(False)
    window.refreshWorkflowDependencyTree()
    assert not tree.topLevelItem(0).child(0).isExpanded()


@pytest.mark.parametrize("width", [240, 308, 430])
def testActualWhileHierarchyFitsNarrowWidths(designerApplication, width, tmp_path):
    window = _whileWindow()
    tree = window.workflowDependencyTree
    tree.setParent(None)
    tree.resize(width, 900)
    tree.show()
    tree.expandAll()
    for _ in range(3):
        designerApplication.processEvents()
    previous = None
    for _key, item in tree._itemsWithKeys():
        rect = tree.visualItemRect(item)
        index = tree.indexFromItem(item)
        option = tree.viewOptions()
        delegate = tree.itemDelegate()
        delegate.initStyleOption(option, index)
        document = delegate._document(option, rect.width(), index)
        if (item.data(0, Qt.UserRole) or {}).get("itemType") in {"workflow", "call"}:
            assert document.toPlainText() == item.text(0)
        assert rect.height() >= ceil(document.size().height()) + 6
        if previous is not None:
            assert previous.bottom() < rect.top()
        previous = rect
    assert tree.horizontalScrollBar().maximum() == 0
    assert tree.grab().save(str(tmp_path / f"while-details-{width}.png"))
