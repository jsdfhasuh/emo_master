from copy import deepcopy
from types import SimpleNamespace

import pytest

from emo_master.apps.designer.ui.debug_values import ValueTree
from emo_master.apps.designer.ui.operator_debug_window import OperatorDebugWindow
from emo_master.apps.designer.ui.plc_presentation import NUMBER_TYPES, plcLog, plcSchema
from emo_master.plugins.builtins._communication_operators import PLC_READ_PARAM_SCHEMA


def testDisplaySchemaDoesNotChangeProtocolOrDefaults():
    original = deepcopy(PLC_READ_PARAM_SCHEMA)
    display = plcSchema(original)
    assert original == PLC_READ_PARAM_SCHEMA
    for name, spec in original["properties"].items():
        assert display["properties"][name]["default"] == spec["default"]
        assert display["properties"][name].get("enum") == spec.get("enum")
    assert "ASCII" not in display["properties"]["dataType"]["enum"]


@pytest.mark.parametrize("device,dataType,values,addresses", [
    ("D", "uint32", [4294967295, 65541], ["D800", "D802"]),
    ("D", "int16", [-1, -32768], ["D800", "D801"]),
    ("D", "float32", [1.5, -2.25], ["D800", "D802"]),
    ("M", "uint16", [1, 2], ["M800", "M816"]),
    ("M", "bit", [True, False], ["M800", "M801"]),
])
def testReadableTableUsesCapturedAddresses(designerApplication, device, dataType, values, addresses):
    table = OperatorDebugWindow.resultTable(["地址", "数值", "数值类型"])
    tree = ValueTree()
    view = SimpleNamespace(plcResults=table, outputTree=tree)
    captured = dict(device=device, startAddress=800, dataType=dataType, values=values)
    try:
        OperatorDebugWindow.showPlcOutput(view, "values", captured, {"startAddress": 999})
        assert [table.item(i, 0).text() for i in range(2)] == addresses
        assert table.item(0, 2).text() == NUMBER_TYPES[dataType]
        assert table.item(0, 1).text() == ("开" if dataType == "bit" else str(values[0]))
    finally:
        table.close()
        tree.close()


def testReceiptDoesNotClaimReadback(designerApplication):
    table = OperatorDebugWindow.resultTable(["地址", "数值", "数值类型"])
    tree = ValueTree()
    try:
        OperatorDebugWindow.showPlcOutput(SimpleNamespace(plcResults=table, outputTree=tree), "receipt",
            dict(device="D", startAddress=796, dataType="uint16", valueCount=1, wordCount=1, attempts=1), {})
        assert "未回读" in table.item(0, 1).text()
        assert table.item(1, 1).text() == "D796"
    finally:
        table.close()
        tree.close()


def testReadableLogsPreserveErrorsAndUnknownEvents():
    event = dict(type="node.log", event=dict(level="ERROR", code="E_TIMEOUT", message="SLMP write failed"))
    assert "E_TIMEOUT" in plcLog(event)
    assert "结果需另行确认" in plcLog(event)
    unknown = dict(type="future.error", code="E_NEW", message="new diagnostic")
    assert "E_NEW" in plcLog(unknown)
    assert "new diagnostic" in plcLog(unknown)
