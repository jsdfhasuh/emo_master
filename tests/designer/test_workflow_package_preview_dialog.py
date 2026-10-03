from emo_master.apps.designer.state.workflow_package import (
    WorkflowPackageDependencyPreview,
    WorkflowPackageExternalPathPreview,
    WorkflowPackageIdConflict,
    WorkflowPackageImportPreview,
    WorkflowPackageWorkflowPreview,
)
from emo_master.apps.designer.ui.workflow_package_preview_dialog import (
    WorkflowPackagePreviewDialog,
)


def _preview() -> WorkflowPackageImportPreview:
    return WorkflowPackageImportPreview(
        sourceRootWorkflowId="main",
        rootWorkflowId="main-2",
        workflows=(
            WorkflowPackageWorkflowPreview(
                sourceWorkflowId="main",
                workflowId="main-2",
                name="Imported Main",
                isRoot=True,
            ),
            WorkflowPackageWorkflowPreview(
                sourceWorkflowId="body",
                workflowId="body",
                name="Body",
                isRoot=False,
            ),
        ),
        workflowIdMap={"main": "main-2", "body": "body"},
        conflicts=(
            WorkflowPackageIdConflict(
                sourceWorkflowId="main",
                workflowId="main-2",
            ),
        ),
        dependencies=(
            WorkflowPackageDependencyPreview(
                sourceWorkflowId="main",
                workflowId="main-2",
                targetSourceWorkflowId="body",
                targetWorkflowId="body",
                relation="Repeat · Body",
            ),
        ),
        requiredOperators=("vision.test.echo",),
        missingOperators=("vision.test.echo",),
        externalPaths=(
            WorkflowPackageExternalPathPreview(
                workflowId="body",
                nodeId="loader",
                paramName="imagePath",
                path="C:/missing.png",
                fileMode="open",
                status="missing",
            ),
        ),
    )


def testWorkflowPackagePreviewDialogExposesImportImpact() -> None:
    dialog = WorkflowPackagePreviewDialog(_preview())

    assert "Imported Main" in dialog.getSummaryText()
    assert "2 个工作流" in dialog.getSummaryText()
    assert dialog.getWorkflowDisplayTexts() == [
        "[根] Imported Main (main → main-2)",
        "[依赖] Body (body)",
    ]
    assert dialog.getDependencyDisplayTexts() == [
        "Repeat · Body：main-2 → body"
    ]
    assert dialog.getConflictDisplayTexts() == ["main → main-2"]
    assert dialog.getOperatorDisplayTexts() == ["vision.test.echo [未加载]"]
    assert "输入文件不存在" in dialog.getExternalPathDisplayTexts()[0]
    assert "自动重命名" in dialog.getWarningText()
    assert "算子尚未加载" in dialog.getWarningText()
    assert dialog.isInsertSubflowChecked() is True


def testWorkflowPackagePreviewDialogReturnsSelectedImportMode() -> None:
    dialog = WorkflowPackagePreviewDialog(_preview())
    dialog.setInsertSubflowChecked(False)

    dialog._onImportClicked()

    assert dialog.getSelection() == (True, False)


def testWorkflowPackagePreviewDialogCancelRejectsImport() -> None:
    dialog = WorkflowPackagePreviewDialog(_preview())

    dialog._onCancelClicked()

    assert dialog.getSelection()[0] is False
