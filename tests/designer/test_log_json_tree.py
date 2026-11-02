import json

from PySide2.QtCore import Qt
from PySide2.QtTest import QTest

from emo_master.apps.designer.ui import log_json_tree
from emo_master.apps.designer.ui.log_json_tree import LogJsonTree


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
