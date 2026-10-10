"""Presentation-only layout adaptation for the three bundled editor UIs."""

from PySide2.QtCore import Qt
from PySide2.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton, QSplitter, QVBoxLayout, QWidget

from emo_master.apps.designer.ui.icon_map import icon
from emo_master.apps.designer.ui.widgets import PreviewLabel, WrapLabel, scrollContent


def _take(layout):
    items = []
    while layout.count():
        items.append(layout.takeAt(0))
    return items


def _replaceLabel(root, name, preview=False):
    old = root.findChild(QLabel, name)
    if old is None:
        return
    parent = old.parentWidget()
    label = PreviewLabel(old.text(), parent) if preview else WrapLabel(old.text(), parent)
    label.setParent(old.parentWidget())
    label.setObjectName(name)
    parent.layout().replaceWidget(old, label)
    old.setParent(None)
    old.deleteLater()


def _panel(layout):
    widget = QWidget()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    widget.setLayout(layout)
    return widget


def prepareBuiltinLayout(root, operatorId):
    name = root.objectName()
    builtinIds = {"HuarayCameraEditor": "vision.io.huaray_camera",
                  "RoiEditor": "vision.preprocess.roi", "HistogramEditor": "vision.analysis.histogram"}
    if builtinIds.get(name) != operatorId:
        return
    previews = {
        "HuarayCameraEditor": ("previewLabel",),
        "RoiEditor": ("croppedImageLabel", "croppedMaskLabel", "fullMaskLabel"),
        "HistogramEditor": ("sourceImageLabel",),
    }[name]
    for labelName in previews:
        _replaceLabel(root, labelName, preview=True)
    for labelName in ("connectionStatusLabel", "blockIdLabel", "timestampLabel", "exposureLabel", "pixelCountLabel"):
        _replaceLabel(root, labelName)
    for buttonName, iconName in (("connectButton", "camera"), ("singleFrameButton", "scan-line"),
                                 ("stopButton", "square"), ("localImageButton", "folder-open")):
        button = root.findChild(QPushButton, buttonName)
        if button is not None:
            button.setIcon(icon(iconName))
            button.setToolTip(button.text())
    layout = root.layout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    items = _take(layout)
    if name == "HuarayCameraEditor":
        preview, tools, metadata, params = items
        status = root.findChild(QLabel, "connectionStatusLabel")
        tools.layout().removeWidget(status)
        layout.addLayout(tools.layout())
        imageLayout = QVBoxLayout()
        imageLayout.addWidget(preview.widget(), 1)
        imageLayout.addWidget(status)
        rows = QGridLayout()
        for row, item in enumerate(_take(metadata.layout())):
            rows.addWidget(item.widget(), row, 0)
        imageLayout.addLayout(rows)
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(_panel(imageLayout))
        params.widget().setMinimumWidth(260)
        splitter.addWidget(params.widget())
        splitter.setSizes([600, 330])
        splitter.setChildrenCollapsible(False)
        layout.addWidget(splitter, 1)
    elif name == "RoiEditor":
        source, canvas, parameters, outputs = items
        canvas.widget().setMinimumSize(240, 180)
        source.layout().setStretch(1, 1)
        layout.addLayout(source.layout())
        fields = _take(parameters.layout())
        form = QVBoxLayout()
        for index in range(0, len(fields), 2):
            label = fields[index].widget()
            label.setWordWrap(True)
            form.addWidget(label)
            form.addWidget(fields[index + 1].widget())
        form.addStretch(1)
        parametersScroll = scrollContent(_panel(form))
        parametersScroll.setMinimumWidth(240)
        imageLayout = QVBoxLayout()
        imageLayout.addWidget(canvas.widget(), 4)
        for labelName in previews:
            root.findChild(QLabel, labelName).setMinimumSize(80, 70)
        imageLayout.addLayout(outputs.layout(), 1)
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(_panel(imageLayout))
        splitter.addWidget(parametersScroll)
        splitter.setSizes([640, 260])
        splitter.setChildrenCollapsible(False)
        layout.addWidget(splitter, 1)
    else:
        source, image, chart, count = items
        controls = _take(source.layout())
        for item in controls[:3]:
            source.layout().addItem(item)
        source.layout().setStretch(1, 1)
        layout.addLayout(source.layout())
        options = QHBoxLayout()
        for item in controls[3:]:
            options.addItem(item)
        options.addStretch(1)
        layout.addLayout(options)
        chart.widget().setMinimumSize(240, 160)
        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(image.widget())
        splitter.addWidget(chart.widget())
        splitter.setSizes([220, 300])
        splitter.setChildrenCollapsible(False)
        layout.addWidget(splitter, 1)
        layout.addWidget(count.widget())
