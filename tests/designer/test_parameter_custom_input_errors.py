import pytest

from tests.designer.test_plc_editor import editor  # noqa: F401 - pure stub fixture


@pytest.mark.parametrize("action", ["validate", "tab", "reload"])
def testPlcMalformedJsonStaysEditableAndDoesNotReachDevice(editor, action):  # noqa: F811
    controller, context, _ = editor("plc_slmp_write")
    field = controller.paramForm._controls["values"]
    field.setText("[1,")
    if action == "validate":
        error = controller.validate()
        assert error["code"] == "E_PARAM_INVALID"
        assert "请输入数值" in error["message"]
    elif action == "tab":
        controller.tabs.setCurrentWidget(controller.runtimePage)
        assert "请输入数值" in controller.statusLabel.text()
    else:
        controller.reloadParameters()
        assert "请输入数值" in controller.statusLabel.text()
    assert field.text() == "[1,"
    assert context.calls == []
    field.setText("[1]")
    assert controller.validate() is None
