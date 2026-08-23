from emo_master.apps.designer.ui.param_form import SchemaParamForm


def ensureQApp() -> None:
    try:
        from PySide2.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            _ = QApplication([])
    except Exception:
        pass


def testSchemaParamFormFileOpenPickerUpdatesValue() -> None:
    ensureQApp()
    form = SchemaParamForm()
    schema = {
        "type": "object",
        "properties": {
            "imagePath": {"type": "string", "xWidget": "file", "xFileMode": "open"}
        },
    }
    form.setSchema(schema, {"imagePath": ""})

    controls = getattr(form, "_controls", {})
    fileControl = controls.get("imagePath") if isinstance(controls, dict) else None
    assert fileControl is not None

    from PySide2.QtWidgets import QPushButton

    browseButtons = fileControl.findChildren(QPushButton)
    assert len(browseButtons) >= 1

    from emo_master.apps.designer.ui import param_form as paramFormModule

    fileDialog = getattr(paramFormModule, "QFileDialog", None)
    assert fileDialog is not None
    original = fileDialog.getOpenFileName
    fileDialog.getOpenFileName = staticmethod(
        lambda *args, **kwargs: ("C:/tmp/open.png", "")
    )
    try:
        browseButtons[0].click()
    finally:
        fileDialog.getOpenFileName = original

    values = form.getValues()
    assert values.get("imagePath") == "C:/tmp/open.png"


def testSchemaParamFormFileSavePickerUpdatesValue() -> None:
    ensureQApp()
    form = SchemaParamForm()
    schema = {
        "type": "object",
        "properties": {
            "outputPath": {"type": "string", "xWidget": "file", "xFileMode": "save"}
        },
    }
    form.setSchema(schema, {"outputPath": ""})

    controls = getattr(form, "_controls", {})
    fileControl = controls.get("outputPath") if isinstance(controls, dict) else None
    assert fileControl is not None

    from PySide2.QtWidgets import QPushButton

    browseButtons = fileControl.findChildren(QPushButton)
    assert len(browseButtons) >= 1

    from emo_master.apps.designer.ui import param_form as paramFormModule

    fileDialog = getattr(paramFormModule, "QFileDialog", None)
    assert fileDialog is not None
    original = fileDialog.getSaveFileName
    fileDialog.getSaveFileName = staticmethod(
        lambda *args, **kwargs: ("C:/tmp/save.png", "")
    )
    try:
        browseButtons[0].click()
    finally:
        fileDialog.getSaveFileName = original

    values = form.getValues()
    assert values.get("outputPath") == "C:/tmp/save.png"
