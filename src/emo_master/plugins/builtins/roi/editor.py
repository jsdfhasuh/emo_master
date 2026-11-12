from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

from PySide2.QtCore import QByteArray, Qt
from PySide2.QtGui import QColor, QImage, QPixmap
from PySide2.QtWidgets import (
    QAction,
    QAbstractSpinBox,
    QButtonGroup,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSlider,
    QSplitter,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from emo_master.plugins.builtins.roi.preview_result import PreviewImage, RoiPreviewSnapshot, atomicWrite
from emo_master.plugins.builtins.roi.canvas import GEOMETRY_FIELDS, RoiCanvas, regionPoints
from emo_master.plugins.builtins.roi.operator import RoiOperator
from emo_master.plugins.builtins.roi.point_editor import RoiPointEditor

from emo_master.apps.designer.ui.param_form import applyParameterLabel
from emo_master.apps.designer.ui.icon_map import icon
from emo_master.apps.designer.ui.image_view import ImageView
from emo_master.apps.designer.ui.widgets import CollapsibleSection, FormWheelGuard
from emo_master.plugins.builtins._editor_support import (
    PurePreviewControllerBase,
    requiredChild,
)


OUTPUT_TITLES = {
    "croppedImage": "裁剪图像",
    "maskedImage": "遮罩图像",
    "croppedMask": "裁剪掩膜",
    "fullMask": "原图掩膜",
}


class RoiEditorController(PurePreviewControllerBase):
    def __init__(self) -> None:
        super().__init__()
        self._loading = False
        self.canvas: RoiCanvas | None = None
        self._regions: list[dict[str, Any]] = []
        self._selected = -1
        self._regionsMode = False
        self._legacyParams: dict[str, Any] = {}
        self._history: list[tuple[list[dict[str, Any]], int, bool, dict[str, Any], int, str]] = []
        self._previewDraft = False
        self.controls: dict[str, Any] = {}
        self.outputViews: dict[str, ImageView] = {}
        self._previewSnapshot: RoiPreviewSnapshot | None = None
        self._resultViewers: list[QDialog] = []
        self._resultButtons: list[QPushButton | QAction] = []

    def bind(self, rootWidget: object, context: Any) -> None:
        rootWidget = cast(QWidget, rootWidget)
        for layout in rootWidget.findChildren(QLayout):
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)
        self.bindPreviewBase(rootWidget, context)
        cast(QPushButton, self.localImageButton).setIcon(icon("folder-open"))
        placeholder = requiredChild(rootWidget, QWidget, "canvasPlaceholder")
        self.canvas = RoiCanvas()
        self.canvas.setBackgroundBrush(self.root.palette().alternateBase())
        placeholder.layout().addWidget(self.canvas)
        sourceTools = requiredChild(rootWidget, QWidget, "sourceTools").layout()
        modes = QButtonGroup(rootWidget)
        self.toolButtons = {}
        for name, title, iconName, mode in (
            ("roiSelectButton", "选择与编辑区域", "mouse-pointer-2", "select"),
            ("roiPanButton", "移动图片", "hand", "pan"),
            ("roiDrawButton", "绘制矩形", "square", "bbox"),
            ("roiPolygonButton", "绘制多边形：点首点或 Enter 完成", "pentagon", "polygon"),
        ):
            button = QToolButton()
            button.setObjectName(name)
            button.setIcon(icon(iconName))
            button.setToolTip(title)
            button.setAccessibleName(title)
            button.setCheckable(True)
            modes.addButton(button)
            button.setChecked(mode == "select")
            button.clicked.connect(lambda _checked=False, value=mode: self._setTool(value))
            sourceTools.addWidget(button)
            self.toolButtons[mode] = button
        for name, title, iconName, callback in (
            ("roiUndoButton", "撤销；绘制时退回末点（Backspace）", "undo-2", self._undo),
            ("roiDeleteButton", "删除选中区域", "trash-2", self._deleteRegion),
        ):
            button = QToolButton()
            button.setObjectName(name)
            button.setIcon(icon(iconName))
            button.setToolTip(title)
            button.setAccessibleName(title)
            button.clicked.connect(callback)
            sourceTools.addWidget(button)
        self._viewTools(requiredChild(rootWidget, QWidget, "sourceViewTools").layout(), lambda: self.canvas, "roiSource")
        for name, sizes in (("roiImagesSplitter", [360, 360]), ("roiWorkspaceSplitter", [760, 220])):
            splitter = requiredChild(rootWidget, QSplitter, name)
            splitter.setSizes(sizes)
        requiredChild(rootWidget, QSplitter, "roiWorkspaceSplitter").setStretchFactor(0, 1)
        advanced = requiredChild(rootWidget, QWidget, "advancedContent")
        advancedSlot = requiredChild(rootWidget, QWidget, "advancedPlaceholder")
        advancedSlot.layout().removeWidget(advanced)
        self._addSection(advancedSlot, "高级设置", advanced, "roiAdvancedSection")
        parametersScroll = requiredChild(rootWidget, QScrollArea, "roiParametersScroll")
        guard = FormWheelGuard(parametersScroll)
        parametersScroll._parameterWheelGuard = guard
        for field in parametersScroll.findChildren(QWidget):
            if isinstance(field, (QAbstractSpinBox, QComboBox, QLineEdit)):
                field.installEventFilter(guard)
        pointsSlot = requiredChild(rootWidget, QWidget, "pointsPlaceholder")
        pointEditor = RoiPointEditor(pointsSlot)
        pointEditor.setObjectName("pointsEdit")
        pointsSlot.layout().addWidget(pointEditor)
        self.controls = {
            "roiType": requiredChild(rootWidget, QComboBox, "roiTypeCombo"),
            "x": requiredChild(rootWidget, QDoubleSpinBox, "xSpin"),
            "y": requiredChild(rootWidget, QDoubleSpinBox, "ySpin"),
            "width": requiredChild(rootWidget, QDoubleSpinBox, "widthSpin"),
            "height": requiredChild(rootWidget, QDoubleSpinBox, "heightSpin"),
            "centerX": requiredChild(rootWidget, QDoubleSpinBox, "centerXSpin"),
            "centerY": requiredChild(rootWidget, QDoubleSpinBox, "centerYSpin"),
            "angleDegrees": requiredChild(rootWidget, QDoubleSpinBox, "angleSpin"),
            "points": pointEditor,
            "padValue": requiredChild(rootWidget, QSpinBox, "padValueSpin"),
            "interpolation": requiredChild(rootWidget, QComboBox, "interpolationCombo"),
        }
        for name, choices in {
            "roiType": (("矩形", "bbox"), ("旋转矩形", "rotatedBox"), ("多边形", "polygon")),
            "interpolation": (("最近邻插值", "nearest"), ("双线性插值", "linear"), ("双三次插值", "cubic")),
        }.items():
            combo = self.controls[name]
            combo.clear()
            for title, value in choices:
                combo.addItem(title, value)
        labels = {
            "roiType": "roiTypeLabel", "x": "xLabel", "y": "yLabel",
            "width": "widthLabel", "height": "heightLabel",
            "centerX": "centerXLabel", "centerY": "centerYLabel",
            "angleDegrees": "angleLabel", "points": "pointsLabel",
            "padValue": "padLabel", "interpolation": "interpolationLabel",
        }
        self._parameterLabels = {name: requiredChild(rootWidget, QLabel, labelName)
                                 for name, labelName in labels.items()}
        for name, labelName in labels.items():
            applyParameterLabel(
                requiredChild(rootWidget, QLabel, labelName), self.controls[name],
                name, context.paramSchema,
            )
        for name in ("x", "y", "width", "height", "centerX", "centerY"):
            control = self.controls[name]
            control.setDecimals(3)
            control.setRange(.001 if name in ("width", "height") else -1_000_000_000.0, 1_000_000_000.0)
            control.valueChanged.connect(self._paramsChanged)
        self.controls["angleDegrees"].valueChanged.connect(self._paramsChanged)
        self.controls["padValue"].valueChanged.connect(self._paramsChanged)
        self.controls["roiType"].currentIndexChanged.connect(self._paramsChanged)
        self.controls["interpolation"].currentIndexChanged.connect(self._paramsChanged)
        self.controls["points"].pointsChanged.connect(self._paramsChanged)
        self.regionList = requiredChild(rootWidget, QListWidget, "roiRegionList")
        self.regionList.currentRowChanged.connect(self._selectRegion)
        self.regionList.itemChanged.connect(self._regionEnabledChanged)
        self.purposeCombo = requiredChild(rootWidget, QComboBox, "regionPurposeCombo")
        self.newPurposeCombo = requiredChild(rootWidget, QComboBox, "newPurposeCombo")
        for combo in (self.purposeCombo, self.newPurposeCombo):
            combo.addItem("保留 / 参与检测", "keep")
            combo.addItem("排除 / 不参与检测", "exclude")
        self.newPurposeCombo.setCurrentIndex(1)
        self.purposeCombo.currentIndexChanged.connect(self._purposeChanged)
        self.newPurposeCombo.currentIndexChanged.connect(lambda: self.canvas.setPurpose(self.newPurposeCombo.currentData()))
        self.drawingStatus = requiredChild(rootWidget, QLabel, "drawingStatus")
        self.draftLabel = requiredChild(rootWidget, QLabel, "draftRegionLabel")
        self.finishButton = requiredChild(rootWidget, QPushButton, "roiFinishButton")
        self.finishButton.setIcon(icon("check"))
        self.finishButton.setToolTip("自动连接首尾，不增加重复首点（Enter）")
        self.finishButton.clicked.connect(self.canvas.finishDrawing)
        self.cancelButton = requiredChild(rootWidget, QPushButton, "roiCancelButton")
        self.cancelButton.setIcon(icon("x"))
        self.cancelButton.setToolTip("取消当前绘制（Esc）")
        self.cancelButton.clicked.connect(lambda: self.canvas.setMode("select"))
        self.canvas.draftChanged.connect(self._draftChanged)
        self.canvas.regionCommitted.connect(self._appendRegion)
        self.canvas.regionSelected.connect(self._selectRegion)
        self.canvas.editStarted.connect(self._remember)
        self.canvas.regionEdited.connect(self._editRegion)
        self.canvas.editFinished.connect(self._editingFinished)
        self.canvas.modeChanged.connect(self._syncTools)
        opacity = requiredChild(rootWidget, QSlider, "regionOpacity")
        opacity.valueChanged.connect(self._opacityChanged)
        self.outputTabs = requiredChild(rootWidget, QTabWidget, "roiOutputTabs")
        self.outputTabs.tabBar().hide()
        self.outputModeButtons = {}
        resultModes = requiredChild(rootWidget, QWidget, "resultModesPlaceholder").layout()
        resultGroup = QButtonGroup(rootWidget)
        descriptions = {
            "croppedImage": "裁剪出的选区图像",
            "maskedImage": "保持原图尺寸；选区外使用填充值覆盖",
            "croppedMask": "与裁剪图像同尺寸；白色为有效区域，黑色为无效区域",
            "fullMask": "与原图同尺寸；白色为有效选区，黑色为选区外",
        }
        for port in OUTPUT_TITLES:
            page = requiredChild(rootWidget, QWidget, port + "Page")
            view = ImageView(minimumZoom=.01, maximumZoom=20)
            view.setObjectName(port + "View")
            view.setMinimumSize(240, 180)
            view.setBackgroundBrush(self.root.palette().alternateBase())
            view.emptyText = OUTPUT_TITLES[port] + "（等待预览）"
            page.layout().addWidget(view)
            self.outputViews[port] = view
            index = self.outputTabs.indexOf(page)
            self.outputTabs.setTabText(index, OUTPUT_TITLES[port])
            self.outputTabs.setTabToolTip(index, descriptions[port])
            self.outputTabs.tabBar().setTabData(index, port)
            view.setToolTip(descriptions[port])
            button = QPushButton(OUTPUT_TITLES[port])
            button.setObjectName(port + "ModeButton")
            button.setCheckable(True)
            button.setChecked(index == self.outputTabs.currentIndex())
            button.setToolTip(descriptions[port])
            resultGroup.addButton(button)
            button.clicked.connect(lambda _checked=False, pageIndex=index: self.outputTabs.setCurrentIndex(pageIndex))
            resultModes.addWidget(button, index // 2, index % 2)
            self.outputModeButtons[port] = button
        self.outputTabs.currentChanged.connect(self._resultTabChanged)
        self.viewImageButton = QPushButton()
        self.viewImageButton.setIcon(icon("maximize"))
        self.viewImageButton.setToolTip("按原始像素查看当前结果")
        self.viewImageButton.setAccessibleName("原尺寸查看")
        self.exportImageButton = QPushButton("导出图片")
        self.exportImageButton.setIcon(icon("download"))
        tools = QHBoxLayout()
        viewTools = QHBoxLayout()
        self._viewTools(viewTools, self._currentView, "roiResult")
        tools.addStretch(1)
        for button, name, callback in (
            (self.viewImageButton, "roiViewFullImageButton", self._viewFullImage),
            (self.exportImageButton, "roiExportImageButton", self._exportImage),
        ):
            button.setObjectName(name)
            button.clicked.connect(callback)
            button.setEnabled(False)
            tools.addWidget(button)
            self._resultButtons.append(button)
        more = QToolButton()
        more.setObjectName("roiMoreResultsButton")
        more.setIcon(icon("ellipsis"))
        more.setToolTip("预览详情与更多导出")
        more.setAccessibleName("预览详情与更多导出")
        more.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(more)
        self.viewResultAction = cast(QAction, menu.addAction("查看预览详情", self._viewResult))
        self.exportJsonAction = cast(QAction, menu.addAction("导出数据（JSON）", self._exportJson))
        self.exportArchiveAction = cast(QAction, menu.addAction("导出全部结果（ZIP）", self._exportArchive))
        for action in (self.viewResultAction, self.exportJsonAction, self.exportArchiveAction):
            action.setEnabled(False)
            self._resultButtons.append(action)
        more.setMenu(menu)
        tools.addWidget(more)
        self.resultIdentityLabel = QLabel("尚无本次预览结果")
        self.resultIdentityLabel.setObjectName("roiPreviewIdentityLabel")
        self.resultIdentityLabel.setWordWrap(True)
        self.exportArchiveAction.setToolTip("四幅原始 PNG、坐标数据及本次预览记录；不重新执行")
        self.exportJsonAction.setToolTip("结构化结果和预览记录，不包含图片文件")
        self.exportImageButton.setToolTip("将当前预览的结果保存为原始 PNG 图片")
        self.resultCaption = QLabel("等待预览")
        self.resultCaption.setObjectName("roiResultCaption")
        self.resultCaption.setWordWrap(True)
        resultLayout = requiredChild(rootWidget, QWidget, "roiResultPanel").layout()
        resultLayout.addWidget(self.resultCaption)
        resultLayout.addLayout(tools)
        resultLayout.addLayout(viewTools)
        self._addSection(requiredChild(rootWidget, QWidget, "roiDetailsPlaceholder"),
                         "预览记录", self.resultIdentityLabel, "roiPreviewDetails")

    def _viewTools(self, layout, getView, prefix):
        label = QLabel("适应窗口")
        label.setObjectName(prefix + "ZoomLabel")
        layout.addWidget(label)
        layout.addStretch(1)
        def updateZoom(*_):
            view = getView()
            label.setText(("适应 · " if view.fitting else "") + f"{view.zoom * 100:.0f}%")
        views = [self.canvas] if prefix == "roiSource" else list(self.outputViews.values())
        for view in views:
            view.zoomChanged.connect(updateZoom)
        if prefix == "roiResult":
            self.outputTabs.currentChanged.connect(updateZoom)
        for suffix, title, iconName, action in (
            ("FitButton", "适应窗口", "maximize", lambda view: view.fit()),
            ("ActualButton", "原始比例（100%）", "", lambda view: view.setZoom(1)),
        ):
            button = QToolButton()
            button.setObjectName(prefix + suffix)
            if iconName:
                button.setIcon(icon(iconName))
            else:
                button.setText("1:1")
            button.setToolTip(title)
            button.setAccessibleName(title)
            button.clicked.connect(lambda _checked=False, callback=action: callback(getView()))
            layout.addWidget(button)

    def _currentView(self):
        return self.outputViews[self.outputTabs.tabBar().tabData(self.outputTabs.currentIndex())]

    def _resultTabChanged(self):
        port = self.outputTabs.tabBar().tabData(self.outputTabs.currentIndex())
        self.outputModeButtons[port].setChecked(True)
        pixmap = self.outputViews[port].pixmap()
        if hasattr(self, "resultCaption"):
            self.resultCaption.setText(f"{OUTPUT_TITLES[port]} · {pixmap.width()} × {pixmap.height()}" if not pixmap.isNull() else "等待预览")

    def _addSection(self, placeholder, title, content, name):
        section = CollapsibleSection(title, content)
        section.setObjectName(name)
        section.toggle.setObjectName(name + "Toggle")
        placeholder.layout().addWidget(section)

    def mountVariableBindings(self, scroll):
        scroll.setMinimumHeight(150)
        scroll.setMaximumHeight(230)
        self._addSection(requiredChild(self.root, QWidget, "bindingsPlaceholder"),
                         "参数来源", scroll, "roiBindingsSection")

    def loadParams(self, params: dict[str, Any]) -> None:
        canvas = cast(RoiCanvas, self.canvas)
        self._loading = True
        try:
            canvas.setMode("select")
            self._history.clear()
            self._regionsMode = params.get("roiType") == "regions"
            if self._regionsMode:
                self._regions = deepcopy(params.get("regions", []))
            else:
                kind = params.get("roiType", "bbox")
                defaults = dict(x=0., y=0., width=1., height=1., centerX=0., centerY=0., angleDegrees=0., points=[])
                self._regions = [dict(name="保留区 1", roiType=kind, purpose="keep", enabled=True,
                                      **{name: deepcopy(params.get(name, defaults[name])) for name in GEOMETRY_FIELDS[kind]})]
            self._selected = 0 if self._regions else -1
            hasKeep = any(region.get("enabled", True) and region.get("purpose", "keep") == "keep"
                          and not (region.get("roiType") == "bbox" and region.get("width", 1) <= 1 and region.get("height", 1) <= 1)
                          for region in self._regions)
            self.newPurposeCombo.setCurrentIndex(1 if hasKeep else 0)
            canvas.purpose = self.newPurposeCombo.currentData()
            display = {**params, **(self._regions[0] if self._regionsMode and self._regions else {})}
            self.controls["roiType"].setCurrentIndex(self.controls["roiType"].findData(str(display.get("roiType", "bbox"))))
            for name in ("x", "y", "width", "height", "centerX", "centerY", "angleDegrees"):
                self.controls[name].setValue(float(display.get(name, 0.0 if name not in {"width", "height"} else 1.0)))
            self.controls["padValue"].setValue(int(params.get("padValue", 0)))
            self.controls["interpolation"].setCurrentIndex(self.controls["interpolation"].findData(str(params.get("interpolation", "linear"))))
            points = display.get("points", [])
            self.controls["points"].setPoints(points)
            self._legacyParams = self._fieldParams()
        finally:
            self._loading = False
        self._syncCanvas()
        self._refreshRegionList()
        self._showSelectedRegion()
        self.refreshParameterVisibility()

    def _fieldParams(self) -> dict[str, Any]:
        points = self.controls["points"].points()
        return {
            "roiType": self.controls["roiType"].currentData(),
            "x": float(self.controls["x"].value()),
            "y": float(self.controls["y"].value()),
            "width": float(self.controls["width"].value()),
            "height": float(self.controls["height"].value()),
            "centerX": float(self.controls["centerX"].value()),
            "centerY": float(self.controls["centerY"].value()),
            "angleDegrees": float(self.controls["angleDegrees"].value()),
            "points": points,
            "padValue": int(self.controls["padValue"].value()),
            "interpolation": self.controls["interpolation"].currentData(),
        }

    def collectParams(self) -> dict[str, Any]:
        if self._regionsMode:
            return dict(roiType="regions", regions=deepcopy(self._regions),
                        padValue=int(self.controls["padValue"].value()),
                        interpolation=self.controls["interpolation"].currentData())
        return {**deepcopy(self._legacyParams),
                "padValue": int(self.controls["padValue"].value()),
                "interpolation": self.controls["interpolation"].currentData()}

    def collectPreviewParams(self) -> dict[str, Any]:
        canvas = cast(RoiCanvas, self.canvas)
        params = self.collectParams()
        self._previewDraft = bool(canvas.draft is not None and not canvas.draftError())
        if self._previewDraft:
            params.update(roiType="regions", regions=[*deepcopy(self._regions), deepcopy(canvas.draft)])
        return params

    def collectParamsForSave(self):
        if self.canvas.draft is not None:
            self.context.setError("请先完成绘制或取消当前草稿，再应用参数")
            return None
        conflict = self._regionBindingConflict()
        if conflict:
            self.context.setError(conflict)
            return None
        return self.collectParams()

    def _regionBindingConflict(self):
        if not self._regionsMode and self.canvas.draft is None:
            return ""
        source = getattr(self, "parameterBindingsForDisplay", None)
        bindings = source() if callable(source) else getattr(self.context, "variableBindings", [])
        legacy = {"roiType", *set().union(*GEOMETRY_FIELDS.values())}
        if any(item.get("parameterPath", [None])[0] in legacy for item in bindings if item.get("parameterPath")):
            return "组合区域请使用“区域配置”参数来源；请先将旧单区域的类型、位置或顶点来源改为固定值"
        return ""

    def _startPreview(self):
        conflict = self._regionBindingConflict()
        if conflict:
            self.onPreviewInvalidated()
            self.context.setError(conflict)
            return
        super()._startPreview()

    def validate(self) -> object:
        if cast(RoiCanvas, self.canvas).draft is not None:
            return "请先完成绘制或取消当前草稿，再应用参数"
        error = RoiOperator().validateParams(self.collectParams())
        return error["message"] if error else None

    def onSourceImage(self, content: bytes, mimeType: str) -> None:
        _ = mimeType
        if self.canvas is not None:
            self.canvas.cancelDrawing()
            self.canvas.setImageBytes(content)

    def handlePreviewResult(self, outputs: dict[str, object], assets: dict[str, str]) -> None:
        self._clearRoiResult()
        if not outputs and not assets:
            return
        try:
            if self._previewMetadata is None:
                raise ValueError("缺少本次草稿预览身份，请重新预览")
            self._previewMetadata["editorState"] = {"draftIncluded": self._previewDraft}
            metadataJson = json.dumps(self._previewMetadata, ensure_ascii=False, allow_nan=False)
            outputsJson = json.dumps(outputs, ensure_ascii=False, allow_nan=False)
            images = []
            for port in self.outputViews:
                assetId = assets.get(port)
                if not assetId:
                    raise ValueError(f"预览缺少图像输出：{port}")
                # The restricted session bridge is the sole byte source. Never
                # read a Runtime cache path or save a scaled label pixmap.
                content, mime = self.context.downloadPreviewAsset(assetId)
                image = QImage.fromData(QByteArray(content))
                if mime != "image/png" or image.isNull() or not content.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise ValueError(f"预览输出不是有效的完整 PNG：{port}")
                images.append(PreviewImage(port, assetId, bytes(content), image.width(), image.height()))
            snapshot = RoiPreviewSnapshot(metadataJson, outputsJson, tuple(images))
            self._previewSnapshot = snapshot
            self._validateSnapshot(snapshot)
            for previewImage in snapshot.images:
                view = self.outputViews[previewImage.port]
                view.setImageBytes(previewImage.content, preserveView=True)
            self._resultTabChanged()
            for button in self._resultButtons:
                button.setEnabled(True)
            requiredChild(self.root, QLabel, "resultTitle").setText("结果预览 · 临时" if self._previewDraft else "结果预览")
            self.resultIdentityLabel.setText(
                f"{'临时预览（含未完成草稿）' if self._previewDraft else '本次纯预览'} {self._previewMetadata['requestId']} · 四幅完整图像及 frame/roi\n"
                "PNG 仅图像；ZIP 含本次身份。参数为请求值；Runtime 未返回实际解析参数。")
            self.context.setStatus("ROI 临时预览已刷新（含未完成草稿）" if self._previewDraft else "ROI 预览已刷新")
        except Exception as error:
            self._clearRoiResult()
            self.context.setError(str(error))

    def onPreviewInvalidated(self) -> None:
        super().onPreviewInvalidated()
        self._clearRoiResult()

    def _clearRoiResult(self) -> None:
        self._previewSnapshot = None
        for button in self._resultButtons:
            button.setEnabled(False)
        for view in self.outputViews.values():
            view.setImage(None, preserveView=True)
        for viewer in self._resultViewers:
            viewer.close()
            viewer.deleteLater()
        self._resultViewers.clear()
        if hasattr(self, "resultIdentityLabel"):
            self.resultIdentityLabel.setText("尚无本次预览结果；更改参数或图源后旧结果不可导出")
            requiredChild(self.root, QLabel, "resultTitle").setText("结果预览")
            self.resultCaption.setText("等待预览")

    def _validateSnapshot(self, snapshot: RoiPreviewSnapshot) -> None:
        try:
            metadata = json.loads(snapshot.metadataJson)
            if (self._disposed or self._previewSnapshot is not snapshot
                    or metadata["generation"] != self._generation):
                raise ValueError("预览已失效，请等待本次结果")
            self.context.validatePurePreviewResult()
            if self.context.purePreviewIdentity() != metadata["context"]:
                raise ValueError("预览会话或 Runtime 已变更，请重新预览")
        except Exception:
            self.onPreviewInvalidated()
            raise

    def _selectedImage(self, snapshot):
        port = self.outputTabs.tabBar().tabData(self.outputTabs.currentIndex())
        return next(image for image in snapshot.images if image.port == port)

    def _viewFullImage(self) -> None:
        snapshot = self._previewSnapshot
        if snapshot is None:
            return
        try:
            self._validateSnapshot(snapshot)
            image = self._selectedImage(snapshot)
            viewer = self._newViewer(f"{OUTPUT_TITLES[image.port]} · {image.width}×{image.height} · 原尺寸", snapshot)
            scroll = QScrollArea(viewer)
            label = QLabel()
            pixmap = QPixmap.fromImage(QImage.fromData(QByteArray(image.content)))
            label.setPixmap(pixmap)
            label.setFixedSize(pixmap.size())
            scroll.setWidget(label)
            viewer.layout().addWidget(scroll)
            viewer.show()
        except Exception as error:
            self.context.setError(str(error))

    def _viewResult(self) -> None:
        snapshot = self._previewSnapshot
        if snapshot is None:
            return
        try:
            self._validateSnapshot(snapshot)
            viewer = self._newViewer("预览详情", snapshot)
            text = QPlainTextEdit()
            text.setReadOnly(True)
            text.setPlainText(snapshot.resultJson().decode("utf-8"))
            viewer.layout().addWidget(text)
            viewer.show()
        except Exception as error:
            self.context.setError(str(error))

    def _newViewer(self, title, snapshot):
        viewer = QDialog(self.root)
        viewer.setWindowTitle(f"{title} · {json.loads(snapshot.metadataJson)['requestId']}")
        viewer.setLayout(QVBoxLayout())
        viewer.resize(800, 600)
        self._resultViewers.append(viewer)
        return viewer

    def _exportImage(self) -> None:
        snapshot = self._previewSnapshot
        if snapshot is not None:
            image = self._selectedImage(snapshot)
            self._saveSnapshot(snapshot, image.port + ".png", "PNG (*.png)", ".png",
                               lambda stream: stream.write(image.content))

    def _exportJson(self) -> None:
        snapshot = self._previewSnapshot
        if snapshot is not None:
            self._saveSnapshot(snapshot, "roi-result.json", "JSON (*.json)", ".json",
                               lambda stream: stream.write(snapshot.resultJson()))

    def _exportArchive(self) -> None:
        snapshot = self._previewSnapshot
        if snapshot is not None:
            self._saveSnapshot(snapshot, "roi-preview.zip", "ZIP (*.zip)", ".zip", snapshot.writeArchive)

    def _saveSnapshot(self, snapshot, filename, filters, suffix, writer):
        try:
            self._validateSnapshot(snapshot)
            selected, _ = QFileDialog.getSaveFileName(self.root, "导出本次 ROI 纯预览", filename, filters)
            if not selected:
                return
            path = Path(selected)
            # GTK/native pickers need not append the filter suffix.
            if path.suffix.lower() != suffix:
                path = path.with_name(path.name + suffix)
            atomicWrite(path, writer, lambda: self._validateSnapshot(snapshot))
            self.context.setStatus(f"已导出本次 ROI 预览：{path}")
        except Exception as error:
            # No partial file replaces an existing destination, and no stale
            # selection is silently re-exported after a nested modal event.
            if not self._disposed:
                self.context.setError(f"ROI 导出失败：{error}")

    def onClose(self) -> None:
        super().onClose()

    def dispose(self) -> None:
        self.disposePreviewBase()

    def _paramsChanged(self, *args) -> None:
        _ = args
        if self._loading:
            return
        canvas = cast(RoiCanvas, self.canvas)
        self._remember()
        fields = self._fieldParams()
        if canvas.draft is not None:
            self._legacyParams.update(padValue=fields["padValue"], interpolation=fields["interpolation"])
            self.schedulePreview(markDirty=True)
            return
        if self._selected >= 0:
            region = self._regions[self._selected]
            kind = fields["roiType"]
            oldKind = region["roiType"]
            if oldKind != kind and regionPoints(region):
                points = regionPoints(region)
                xs, ys = [p[0] for p in points], [p[1] for p in points]
                if kind == "polygon" and not fields["points"]:
                    fields["points"] = [dict(x=x, y=y) for x, y in points]
                elif kind == "bbox":
                    fields.update(x=min(xs), y=min(ys), width=max(xs)-min(xs), height=max(ys)-min(ys))
                elif kind == "rotatedBox":
                    fields.update(centerX=(min(xs)+max(xs))/2, centerY=(min(ys)+max(ys))/2,
                                  width=max(xs)-min(xs), height=max(ys)-min(ys))
            region["roiType"] = kind
            region.update({name: deepcopy(fields[name]) for name in GEOMETRY_FIELDS[kind]})
            if not self._regionsMode:
                self._legacyParams = fields
            if oldKind != kind:
                self._showSelectedRegion()
        canvas.angle = float(self.controls["angleDegrees"].value())
        self._legacyParams.update(padValue=fields["padValue"], interpolation=fields["interpolation"])
        self.refreshParameterVisibility()
        self._syncCanvas()
        self._refreshRegionList()
        self.schedulePreview(markDirty=True)

    def _syncCanvas(self) -> None:
        if self.canvas is None or not self.controls:
            return
        self.canvas.setRegions(self._regions, self._selected)
        self._syncTools()

    def refreshParameterVisibility(self, *_args, bindings=None) -> None:
        from emo_master.plugins.builtins._operator_modes import PARAMETER_CONDITIONS
        if bindings is None:
            source = getattr(self, "parameterBindingsForDisplay", None)
            bindings = source() if callable(source) else getattr(self.context, "variableBindings", [])
        dynamic = set(self.context.paramSchema.get("xDynamicParameters", []))
        dynamic.difference_update(item["parameterPath"][0] for item in getattr(self.context, "variableBindings", [])
                                  if item.get("parameterPath"))
        dynamic.update(item["parameterPath"][0] for item in bindings if item.get("parameterPath"))
        mode = self.controls["roiType"].currentData()
        for name, condition in PARAMETER_CONDITIONS["vision.preprocess.roi"].items():
            visible = "roiType" in dynamic or mode in condition["roiType"]
            self.controls[name].setVisible(visible)
            self.controls[name].setEnabled(visible)
            self._parameterLabels[name].setVisible(visible)

    def _showSelectedRegion(self):
        region = self._regions[self._selected] if 0 <= self._selected < len(self._regions) else None
        self._loading = True
        try:
            if region:
                self.controls["roiType"].setCurrentIndex(self.controls["roiType"].findData(region["roiType"]))
                self.purposeCombo.setCurrentIndex(self.purposeCombo.findData(region.get("purpose", "keep")))
                display = region if self._regionsMode else self._legacyParams
                fields = set().union(*GEOMETRY_FIELDS.values())
                for name in fields:
                    default = [] if name == "points" else 1. if name in {"width", "height"} else 0.
                    value = display.get(name, default)
                    if name == "points":
                        self.controls[name].setPoints(value)
                    else:
                        self.controls[name].setValue(float(value))
                self.canvas.angle = float(region.get("angleDegrees", 0))
            requiredChild(self.root, QLabel, "selectedRegionTitle").setText(region.get("name", f"区域 {self._selected+1}") if region else "未选择区域")
        finally:
            self._loading = False
        self.refreshParameterVisibility()
        for name, control in self.controls.items():
            if name not in {"padValue", "interpolation"}:
                control.setEnabled(region is not None)
        self.purposeCombo.setEnabled(region is not None)

    def _refreshRegionList(self):
        self.regionList.blockSignals(True)
        self.regionList.clear()
        for index, region in enumerate(self._regions):
            item = QListWidgetItem(region.get("name", f"区域 {index+1}"))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if region.get("enabled", True) else Qt.Unchecked)
            kind = dict(bbox="矩形", rotatedBox="旋转矩形", polygon="多边形").get(region.get("roiType"), "")
            purpose = "保留" if region.get("purpose", "keep") == "keep" else "排除"
            item.setToolTip(f"{kind} · {purpose}")
            item.setForeground(QColor("#209779" if purpose == "保留" else "#dc5969"))
            self.regionList.addItem(item)
        self.regionList.setCurrentRow(self._selected)
        self.regionList.blockSignals(False)

    def _selectRegion(self, index):
        if self._loading or not 0 <= index < len(self._regions):
            return
        self.canvas.setMode("select")
        self._selected = index
        self._showSelectedRegion()
        self._refreshRegionList()
        self._syncCanvas()

    def _remember(self):
        self._history.append(deepcopy((self._regions, self._selected, self._regionsMode, self._legacyParams,
                                       self._legacyParams.get("padValue", 0), self._legacyParams.get("interpolation", "linear"))))

    def _undo(self):
        if self.canvas.draft is not None:
            self.canvas.removeLastPoint()
            return
        if not self._history:
            return
        self._regions, self._selected, self._regionsMode, self._legacyParams, pad, interpolation = self._history.pop()
        self._loading = True
        try:
            self.controls["padValue"].setValue(pad)
            self.controls["interpolation"].setCurrentIndex(self.controls["interpolation"].findData(interpolation))
        finally:
            self._loading = False
        self.canvas.setMode("select")
        self._showSelectedRegion()
        self._refreshRegionList()
        self._syncCanvas()
        self.schedulePreview(markDirty=True)

    def _appendRegion(self, region):
        self._remember()
        self._regionsMode = True
        prefix = "保留区" if region.get("purpose", "keep") == "keep" else "排除区"
        used = {r.get("name") for r in self._regions}
        number = 1
        while f"{prefix} {number}" in used:
            number += 1
        region["name"] = f"{prefix} {number}"
        self._regions.append(deepcopy(region))
        self._selected = len(self._regions)-1
        self._showSelectedRegion()
        self._refreshRegionList()
        self._syncCanvas()
        self.schedulePreview(markDirty=True)

    def _deleteRegion(self):
        if self._selected < 0 or self.canvas.draft is not None:
            return
        self._remember()
        del self._regions[self._selected]
        self._regionsMode = True
        self._selected = min(self._selected, len(self._regions)-1)
        self._showSelectedRegion()
        self._refreshRegionList()
        self._syncCanvas()
        self.schedulePreview(markDirty=True)

    def _regionEnabledChanged(self, item):
        if self._loading:
            return
        self._remember()
        self._regionsMode = True
        self._regions[self.regionList.row(item)]["enabled"] = item.checkState() == Qt.Checked
        self._syncCanvas()
        self.schedulePreview(markDirty=True)

    def _purposeChanged(self):
        if self._loading or self._selected < 0:
            return
        self._remember()
        self._regionsMode = True
        region = self._regions[self._selected]
        region["purpose"] = self.purposeCombo.currentData()
        region["name"] = ("保留区 " if region["purpose"] == "keep" else "排除区 ") + str(self._selected+1)
        self._showSelectedRegion()
        self._refreshRegionList()
        self._syncCanvas()
        self.schedulePreview(markDirty=True)

    def _editRegion(self, index, region):
        self._regions[index] = deepcopy(region)
        if not self._regionsMode:
            self._legacyParams.update({name: deepcopy(region[name]) for name in GEOMETRY_FIELDS[region["roiType"]]})
        self._showSelectedRegion()
        self._syncCanvas()
        self.schedulePreview(markDirty=True)

    def _editingFinished(self):
        region = self._regions[self._selected]
        error = RoiOperator().validateParams(region)
        if error:
            self._undo()
            self.context.setError("区域调整无效，已恢复：" + error["message"])
        elif self._history and self._regions == self._history[-1][0]:
            self._history.pop()
            self._syncTools()

    def _setTool(self, mode):
        if mode == "bbox" and self.controls["roiType"].currentData() == "rotatedBox":
            mode = "rotatedBox"
        self.canvas.setMode(mode)
        self.canvas.setFocus()

    def _syncTools(self, *_):
        if not hasattr(self, "finishButton"):
            return
        mode = self.canvas.mode
        for key, button in self.toolButtons.items():
            button.setChecked(key == mode or key == "bbox" and mode == "rotatedBox")
        draft = self.canvas.draft
        self.finishButton.setEnabled(draft is not None and not self.canvas.draftError())
        self.cancelButton.setEnabled(mode in GEOMETRY_FIELDS or draft is not None)
        requiredChild(self.root, QToolButton, "roiUndoButton").setEnabled(bool(self._history) or draft is not None)
        requiredChild(self.root, QToolButton, "roiDeleteButton").setEnabled(self._selected >= 0 and draft is None)
        self.draftLabel.setVisible(draft is not None)
        if draft:
            count = len(regionPoints(draft))
            self.draftLabel.setText(f"{'保留' if draft.get('purpose') == 'keep' else '排除'}草稿 · {count} 个顶点")
            self.drawingStatus.setText(f"{count} 个顶点 · " + ("已自动闭合，待完成" if not self.canvas.draftError() else "尚未形成有效区域"))
        else:
            self.drawingStatus.setText("等待绘制" if mode in GEOMETRY_FIELDS else f"{len(self._regions)} 个已完成区域")

    def _draftChanged(self):
        if self._loading:
            return
        draft = self.canvas.draft
        if draft is not None:
            self._loading = True
            try:
                self.controls["roiType"].setCurrentIndex(self.controls["roiType"].findData(draft["roiType"]))
                for name in GEOMETRY_FIELDS[draft["roiType"]]:
                    if name == "points":
                        self.controls[name].setPoints(draft[name])
                    else:
                        self.controls[name].setValue(float(draft.get(name, 0)))
                self.purposeCombo.setCurrentIndex(self.purposeCombo.findData(draft["purpose"]))
                requiredChild(self.root, QLabel, "selectedRegionTitle").setText("绘制中 · 未完成草稿")
            finally:
                self._loading = False
            self.refreshParameterVisibility()
            for name, control in self.controls.items():
                if name not in {"padValue", "interpolation"}:
                    control.setEnabled(False)
            self.purposeCombo.setEnabled(False)
        else:
            self._showSelectedRegion()
        self._syncTools()
        self.schedulePreview(markDirty=self.canvas.draft is not None)

    def _opacityChanged(self, value):
        self.canvas.opacity = value / 100.
        requiredChild(self.root, QLabel, "opacityLabel").setText(f"选区透明度：{value}%")
        self.canvas.viewport().update()
