from __future__ import annotations

import hashlib
from typing import cast


_TRUSTED_BUILTIN_CONTROLLERS = frozenset(
    {
        "emo_master.plugins.builtins.huaray_camera.editor:HuarayCameraEditorController",
        "emo_master.plugins.builtins.roi.editor:RoiEditorController",
        "emo_master.plugins.builtins.histogram.editor:HistogramEditorController",
    }
)


def isBuiltinController(controllerEntry: str) -> bool:
    return controllerEntry in _TRUSTED_BUILTIN_CONTROLLERS


class EditorTrustStore:
    def __init__(self, settingsStore: object) -> None:
        self.settingsStore = settingsStore

    def ensureTrusted(
        self,
        *,
        operatorId: str,
        version: str,
        controllerEntry: str,
        uiHash: str,
        parent: object | None = None,
    ) -> bool:
        if isBuiltinController(controllerEntry):
            return True
        signature = "\0".join((operatorId, version, controllerEntry, uiHash))
        identity = hashlib.sha256(signature.encode("utf-8")).hexdigest()
        key = f"operatorEditors/trusted/{identity}"
        valueMethod = getattr(self.settingsStore, "value", None)
        if callable(valueMethod) and bool(valueMethod(key, False)):
            return True
        if not _askTrust(parent, operatorId, version, controllerEntry, uiHash):
            return False
        setValue = getattr(self.settingsStore, "setValue", None)
        if callable(setValue):
            setValue(key, True)
        return True


def _askTrust(
    parent: object | None,
    operatorId: str,
    version: str,
    controllerEntry: str,
    uiHash: str,
) -> bool:
    try:
        from PySide2.QtWidgets import QMessageBox, QWidget
    except Exception:  # pragma: no cover
        return False
    message = (
        "此算子编辑器将加载插件 Python Controller。\n\n"
        f"算子：{operatorId} {version}\n"
        f"Controller：{controllerEntry}\n"
        f"UI SHA-256：{uiHash}\n\n"
        "仅在信任该插件来源时继续。"
    )
    answer = QMessageBox.question(
        cast(QWidget, parent),
        "信任算子编辑器",
        message,
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    return answer == QMessageBox.Yes
