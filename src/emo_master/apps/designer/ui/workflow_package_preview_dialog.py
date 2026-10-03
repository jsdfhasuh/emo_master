from __future__ import annotations

from emo_master.apps.designer.state.workflow_package import (
    WorkflowPackageImportPreview,
)


_PATH_STATUS_LABELS = {
    "available": "输入文件存在",
    "missing": "输入文件不存在",
    "relative": "相对路径，导入后需核对",
    "output": "输出路径，导入后需核对",
}


def _workflowDisplayTexts(preview: WorkflowPackageImportPreview) -> list[str]:
    result: list[str] = []
    for workflow in preview.workflows:
        prefix = "[根] " if workflow.isRoot else "[依赖] "
        mapping = (
            workflow.sourceWorkflowId
            if workflow.sourceWorkflowId == workflow.workflowId
            else f"{workflow.sourceWorkflowId} → {workflow.workflowId}"
        )
        result.append(f"{prefix}{workflow.name} ({mapping})")
    return result


def _dependencyDisplayTexts(preview: WorkflowPackageImportPreview) -> list[str]:
    return [
        f"{dependency.relation}：{dependency.workflowId} → "
        f"{dependency.targetWorkflowId}"
        for dependency in preview.dependencies
    ]


def _conflictDisplayTexts(preview: WorkflowPackageImportPreview) -> list[str]:
    return [
        f"{conflict.sourceWorkflowId} → {conflict.workflowId}"
        for conflict in preview.conflicts
    ]


def _operatorDisplayTexts(preview: WorkflowPackageImportPreview) -> list[str]:
    missing = set(preview.missingOperators)
    return [
        f"{operatorId} [未加载]" if operatorId in missing else operatorId
        for operatorId in preview.requiredOperators
    ]


def _externalPathDisplayTexts(preview: WorkflowPackageImportPreview) -> list[str]:
    return [
        f"{item.workflowId}/{item.nodeId}.{item.paramName}：{item.path} "
        f"[{_PATH_STATUS_LABELS.get(item.status, item.status)}]"
        for item in preview.externalPaths
    ]


def _warningText(preview: WorkflowPackageImportPreview) -> str:
    warnings: list[str] = []
    if preview.conflicts:
        warnings.append(f"{len(preview.conflicts)} 个工作流 ID 将自动重命名")
    if preview.missingOperators:
        warnings.append(
            f"{len(preview.missingOperators)} 个算子尚未加载，导入后暂时无法运行"
        )
    pathWarnings = [
        item for item in preview.externalPaths if item.status != "available"
    ]
    if pathWarnings:
        warnings.append(f"{len(pathWarnings)} 个外部路径需要导入后核对")
    if not warnings:
        return "未发现冲突或缺失依赖。"
    return "注意：" + "；".join(warnings) + "。"


try:
    from PySide2.QtWidgets import (
        QCheckBox,
        QDialog,
        QHBoxLayout,
        QLabel,
        QListWidget,
        QPushButton,
        QTreeWidget,
        QTreeWidgetItem,
        QVBoxLayout,
        QWidget,
    )
    from emo_master.apps.designer.ui.widgets import WrapLabel, scrollContent
    from emo_master.apps.designer.ui.icon_map import icon

    class WorkflowPackagePreviewDialog(QDialog):
        def __init__(
            self, preview: WorkflowPackageImportPreview, parent=None
        ) -> None:
            super().__init__(parent)
            self.setModal(True)
            self.setWindowTitle("导入工作流包")
            self.resize(760, 680)
            self._preview = preview
            self._accepted = False
            self._insertSubflow = True
            self._workflowTexts = _workflowDisplayTexts(preview)
            self._dependencyTexts = _dependencyDisplayTexts(preview)
            self._conflictTexts = _conflictDisplayTexts(preview)
            self._operatorTexts = _operatorDisplayTexts(preview)
            self._externalPathTexts = _externalPathDisplayTexts(preview)

            rootLayout = QVBoxLayout()
            rootWorkflow = next(
                workflow for workflow in preview.workflows if workflow.isRoot
            )
            self._summaryLabel = WrapLabel(
                f"根工作流：{rootWorkflow.name} ({preview.rootWorkflowId}) | "
                f"包含 {len(preview.workflows)} 个工作流"
            )
            self._summaryLabel.setObjectName("workflowPackageSummary")
            rootLayout.addWidget(self._summaryLabel)

            self._warningLabel = WrapLabel(_warningText(preview))
            self._warningLabel.setObjectName("workflowPackageWarnings")
            setWordWrap = getattr(self._warningLabel, "setWordWrap", None)
            if callable(setWordWrap):
                setWordWrap(True)
            rootLayout.addWidget(self._warningLabel)

            rootLayout.addWidget(QLabel("工作流内容"))
            self._workflowTree = QTreeWidget()
            self._workflowTree.setHeaderHidden(True)
            self._workflowTree.setObjectName("workflowPackageWorkflowTree")
            for text in self._workflowTexts:
                self._workflowTree.addTopLevelItem(QTreeWidgetItem([text]))
            rootLayout.addWidget(self._workflowTree)

            rootLayout.addWidget(QLabel("依赖关系"))
            self._dependencyList = QListWidget()
            self._dependencyList.setObjectName("workflowPackageDependencyList")
            self._dependencyList.addItems(
                self._dependencyTexts or ["无内部工作流依赖"]
            )
            rootLayout.addWidget(self._dependencyList)

            rootLayout.addWidget(QLabel("ID 冲突"))
            self._conflictList = QListWidget()
            self._conflictList.setObjectName("workflowPackageConflictList")
            self._conflictList.addItems(self._conflictTexts or ["无 ID 冲突"])
            rootLayout.addWidget(self._conflictList)

            rootLayout.addWidget(QLabel("算子依赖"))
            self._operatorList = QListWidget()
            self._operatorList.setObjectName("workflowPackageOperatorList")
            self._operatorList.addItems(self._operatorTexts or ["无算子依赖"])
            rootLayout.addWidget(self._operatorList)

            rootLayout.addWidget(QLabel("外部文件路径"))
            self._externalPathList = QListWidget()
            self._externalPathList.setObjectName("workflowPackageExternalPathList")
            self._externalPathList.addItems(
                self._externalPathTexts or ["无外部文件路径"]
            )
            rootLayout.addWidget(self._externalPathList)

            for view in (self._workflowTree, self._dependencyList, self._conflictList,
                         self._operatorList, self._externalPathList):
                view.setMinimumHeight(self.fontMetrics().height() * 3 + 12)
                view.setMaximumHeight(self.fontMetrics().height() * 5 + 12)
                if isinstance(view, QListWidget):
                    for index in range(view.count()):
                        view.item(index).setToolTip(view.item(index).text())
            body = QWidget()
            body.setLayout(rootLayout)
            rootLayout = QVBoxLayout()
            rootLayout.addWidget(scrollContent(body), 1)

            self._insertSubflowCheckBox = QCheckBox(
                "导入后在目标工作流插入 Subflow 调用节点"
            )
            self._insertSubflowCheckBox.setChecked(True)
            self._insertSubflowCheckBox.setObjectName(
                "workflowPackageInsertSubflow"
            )
            rootLayout.addWidget(self._insertSubflowCheckBox)

            buttonRow = QHBoxLayout()
            self._importButton = QPushButton("导入")
            self._importButton.setIcon(icon("upload", "#ffffff"))
            self._cancelButton = QPushButton("取消")
            self._importButton.setObjectName("workflowPackageImportButton")
            self._cancelButton.setObjectName("workflowPackageCancelButton")
            buttonRow.addStretch(1)
            buttonRow.addWidget(self._importButton)
            buttonRow.addWidget(self._cancelButton)
            rootLayout.addLayout(buttonRow)
            self.setLayout(rootLayout)

            self._importButton.clicked.connect(self._onImportClicked)
            self._cancelButton.clicked.connect(self._onCancelClicked)

        def execSelection(self) -> tuple[bool, bool]:
            _ = self.exec_()
            return self.getSelection()

        def getSelection(self) -> tuple[bool, bool]:
            return self._accepted, self._insertSubflow

        def setInsertSubflowChecked(self, checked: bool) -> None:
            self._insertSubflowCheckBox.setChecked(checked)
            self._insertSubflow = checked

        def isInsertSubflowChecked(self) -> bool:
            return bool(self._insertSubflowCheckBox.isChecked())

        def getSummaryText(self) -> str:
            return self._summaryLabel.text()

        def getWarningText(self) -> str:
            return self._warningLabel.text()

        def getWorkflowDisplayTexts(self) -> list[str]:
            return list(self._workflowTexts)

        def getDependencyDisplayTexts(self) -> list[str]:
            return list(self._dependencyTexts)

        def getConflictDisplayTexts(self) -> list[str]:
            return list(self._conflictTexts)

        def getOperatorDisplayTexts(self) -> list[str]:
            return list(self._operatorTexts)

        def getExternalPathDisplayTexts(self) -> list[str]:
            return list(self._externalPathTexts)

        def _onImportClicked(self) -> None:
            self._accepted = True
            self._insertSubflow = bool(self._insertSubflowCheckBox.isChecked())
            self.accept()

        def _onCancelClicked(self) -> None:
            self._accepted = False
            self.reject()

except Exception:  # pragma: no cover

    class WorkflowPackagePreviewDialog:  # type: ignore[no-redef]
        def __init__(
            self, preview: WorkflowPackageImportPreview, parent=None
        ) -> None:
            _ = parent
            self._preview = preview
            self._accepted = False
            self._insertSubflow = True
            self._workflowTexts = _workflowDisplayTexts(preview)
            self._dependencyTexts = _dependencyDisplayTexts(preview)
            self._conflictTexts = _conflictDisplayTexts(preview)
            self._operatorTexts = _operatorDisplayTexts(preview)
            self._externalPathTexts = _externalPathDisplayTexts(preview)

        def execSelection(self) -> tuple[bool, bool]:
            return self.getSelection()

        def getSelection(self) -> tuple[bool, bool]:
            return self._accepted, self._insertSubflow

        def setInsertSubflowChecked(self, checked: bool) -> None:
            self._insertSubflow = checked

        def isInsertSubflowChecked(self) -> bool:
            return self._insertSubflow

        def getSummaryText(self) -> str:
            rootWorkflow = next(
                workflow for workflow in self._preview.workflows if workflow.isRoot
            )
            return (
                f"根工作流：{rootWorkflow.name} ({self._preview.rootWorkflowId}) | "
                f"包含 {len(self._preview.workflows)} 个工作流"
            )

        def getWarningText(self) -> str:
            return _warningText(self._preview)

        def getWorkflowDisplayTexts(self) -> list[str]:
            return list(self._workflowTexts)

        def getDependencyDisplayTexts(self) -> list[str]:
            return list(self._dependencyTexts)

        def getConflictDisplayTexts(self) -> list[str]:
            return list(self._conflictTexts)

        def getOperatorDisplayTexts(self) -> list[str]:
            return list(self._operatorTexts)

        def getExternalPathDisplayTexts(self) -> list[str]:
            return list(self._externalPathTexts)

        def simulateSelection(self, accepted: bool, insertSubflow: bool) -> None:
            self._accepted = accepted
            self._insertSubflow = insertSubflow
