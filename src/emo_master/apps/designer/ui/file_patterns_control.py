from __future__ import annotations

from copy import deepcopy
from typing import Any

from PySide2.QtCore import Qt, Signal
from PySide2.QtWidgets import (
    QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QPlainTextEdit,
    QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from emo_master.apps.designer.ui.widgets import WrapLabel


class FilePatternsControl(QWidget):
    """Friendly filename selection; the persisted value is still a string array."""

    valueChanged: Any = Signal()
    FORMATS = {
        "video": ("mp4", "avi", "mkv", "mov", "wmv", "m4v", "webm", "mpg", "mpeg"),
        "image": ("png", "jpg", "jpeg", "bmp", "tif", "tiff", "webp"),
    }

    def __init__(self, value: object) -> None:
        super().__init__()
        self._original = deepcopy(value)
        self._edited = False
        patterns = value if isinstance(value, list) else []
        normalized = [item.strip().casefold() for item in patterns if isinstance(item, str)]
        self._mode = "custom"
        if normalized == ["*"]:
            self._mode = "all"
        elif normalized and len(normalized) == len(patterns):
            for mode, extensions in self.FORMATS.items():
                if all(item in {f"*.{ext}" for ext in extensions} for item in normalized):
                    self._mode = mode
                    break
        self._customVisited = self._mode == "custom"

        self.modeCombo = QComboBox()
        self.modeCombo.setAccessibleName("文件类型")
        for title, mode in (("全部文件（不筛选）", "all"), ("视频文件", "video"),
                            ("图片文件", "image"), ("自定义文件名（高级）", "custom")):
            self.modeCombo.addItem(title, mode)
        self.modeCombo.setCurrentIndex(self.modeCombo.findData(self._mode))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.modeCombo)
        self.hintLabel = WrapLabel()
        layout.addWidget(self.hintLabel)
        self.formatPanels: dict[str, QWidget] = {}
        self.formatChecks: dict[str, QCheckBox] = {}
        for mode, extensions in self.FORMATS.items():
            panel = QWidget()
            grid = QGridLayout(panel)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(12)
            grid.setVerticalSpacing(6)
            for index, ext in enumerate(extensions):
                check = QCheckBox(ext.upper())
                check.setToolTip(f"选择 .{ext} 文件（不区分大小写）")
                check.setChecked(self._mode != mode or f"*.{ext}" in normalized)
                check.toggled.connect(self._changed)
                self.formatChecks[ext] = check
                grid.addWidget(check, index // 3, index % 3)
            self.formatPanels[mode] = panel
            layout.addWidget(panel)

        self.selectionActions = QWidget()
        actions = QHBoxLayout(self.selectionActions)
        actions.setContentsMargins(0, 0, 0, 0)
        self.selectAllButton = QPushButton("全选")
        self.clearButton = QPushButton("清空")
        for button in (self.selectAllButton, self.clearButton):
            button.setAutoDefault(False)
            actions.addWidget(button)
        actions.addStretch(1)
        self.selectAllButton.clicked.connect(lambda: self._selectFormats(True))
        self.clearButton.clicked.connect(lambda: self._selectFormats(False))
        layout.addWidget(self.selectionActions)

        self.customEdit = QPlainTextEdit()
        self.customEdit.setAccessibleName("自定义文件名规则，每行一条")
        self.customEdit.setPlaceholderText("*.csv\ncamera_*.mp4")
        self.customEdit.setPlainText("\n".join(item for item in patterns if isinstance(item, str)))
        self.customEdit.setFixedHeight(104)
        self.customEdit.textChanged.connect(self._changed)
        layout.addWidget(self.customEdit)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        layout.setAlignment(Qt.AlignTop)
        self.setFocusProxy(self.modeCombo)
        self._updateVisibility()
        self.modeCombo.currentIndexChanged.connect(self._modeChanged)

    def _selectedPatterns(self) -> list[str]:
        if self._mode == "all":
            return ["*"]
        if self._mode == "custom":
            return [line.strip() for line in self.customEdit.toPlainText().splitlines() if line.strip()]
        return [f"*.{ext}" for ext in self.FORMATS[self._mode] if self.formatChecks[ext].isChecked()]

    def value(self) -> object:
        # Opening/applying an unchanged editor must retain case, order and duplicates.
        value = self._selectedPatterns() if self._edited else deepcopy(self._original)
        if not isinstance(value, list) or not value:
            message = "请至少填写一条文件名规则" if self._mode == "custom" else "请至少勾选一种文件格式"
            raise ValueError(f"文件类型 (patterns)：{message}；不筛选时请选择“全部文件”")
        if any(not isinstance(item, str) or not item.strip() or "/" in item or "\\" in item for item in value):
            raise ValueError("文件类型 (patterns)：每行填写一条非空文件名规则，不要填写文件夹路径")
        return value

    def _changed(self, *_args: object) -> None:
        self._edited = True
        self.valueChanged.emit()

    def _modeChanged(self, _index: int) -> None:
        mode = self.modeCombo.currentData()
        if mode == "custom" and not self._customVisited:
            self.customEdit.blockSignals(True)
            self.customEdit.setPlainText("\n".join(self._selectedPatterns()))
            self.customEdit.blockSignals(False)
            self._customVisited = True
        self._mode = mode
        self._updateVisibility()
        self._changed()

    def _updateVisibility(self) -> None:
        for mode, panel in self.formatPanels.items():
            panel.setVisible(mode == self._mode)
        self.selectionActions.setVisible(self._mode in self.FORMATS)
        self.customEdit.setVisible(self._mode == "custom")
        self.hintLabel.setText({
            "all": "列出文件夹中的所有文件，不限制类型。",
            "video": "勾选需要处理的视频格式，可多选。",
            "image": "勾选需要处理的图片格式，可多选。",
            "custom": "每行一条，不用引号或括号。例如 *.csv 或 camera_*.mp4；* 表示任意文字。",
        }[self._mode])

    def _selectFormats(self, checked: bool) -> None:
        for ext in self.FORMATS.get(self._mode, ()):
            check = self.formatChecks[ext]
            check.blockSignals(True)
            check.setChecked(checked)
            check.blockSignals(False)
        self._changed()
