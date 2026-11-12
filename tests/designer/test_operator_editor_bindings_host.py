from PySide2.QtWidgets import QScrollArea

from examples.image_preview_editor.demo import createWindow
from tests.runtime.test_global_variables import variable


def testNonBuiltinEditorOwnsBindingPlacementButPlatformOwnsValidation(designerApplication):
    window = createWindow()
    try:
        controller = window._controller
        assert window.context.operatorId == "example.image_preview"
        assert isinstance(controller.bindings.content, QScrollArea)
        assert controller.bindings.content is window._bindingScroll
        assert controller.root.isAncestorOf(window._bindingScroll)
        assert not controller.bindings.toggle.isChecked()
        assert not controller.source.pixmap().isNull() and not controller.result.pixmap().isNull()
        schema = {"type": "object", "properties": {"value": {"type": "integer", "default": 1}}}
        definitions = {"value": variable(3, "integer")}
        window.context.variableDefinitions = lambda: definitions
        window.refreshSchema(schema)
        assert controller.bindings.content is window._bindingScroll
        source = window._bindingPanel.fields[("value",)]
        source.setCurrentIndex(source.findData("value"))
        saved = []
        window.context.applyConfiguration = lambda _key, params, bindings: saved.append((params, bindings)) or True
        assert window.applyChanges()
        assert saved[-1][1] == [{"parameterPath": ["value"], "variableId": "value"}]
        definitions.clear()
        window.refreshSchema(schema)
        assert not window.applyChanges()
        assert len(saved) == 1
    finally:
        window.forceClose()
