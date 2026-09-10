from __future__ import annotations

from copy import deepcopy
import json

from emo_master.apps.designer.ui import main_window as mainWindowModule
from emo_master.apps.designer.state.workflow_package import (
    buildWorkflowPackage,
    writeWorkflowPackage,
)
from emo_master.apps.designer.ui.main_window import MainWindow


class _RuntimeClientStub:
    def listOperators(self):
        return []

    def loadProject(self, projectPath: str):
        _ = projectPath
        return type("Reply", (), {"ok": True, "message": "ok"})()


class _WorkflowPackagePreviewDialogStub:
    def __init__(
        self,
        preview,
        accepted: bool,
        insertSubflow: bool,
    ) -> None:
        self.preview = preview
        self.accepted = accepted
        self.insertSubflow = insertSubflow

    def execSelection(self) -> tuple[bool, bool]:
        return self.accepted, self.insertSubflow


def _previewDialogFactory(
    accepted: bool,
    insertSubflow: bool,
    captured: list[object] | None = None,
):
    def create(preview):
        if captured is not None:
            captured.append(preview)
        return _WorkflowPackagePreviewDialogStub(
            preview,
            accepted=accepted,
            insertSubflow=insertSubflow,
        )

    return create


def _dialogReturning(text: str, accepted: bool):
    class DialogStub:
        @staticmethod
        def getText(parent, title: str, label: str, echo=0, current: str = ""):
            _ = parent
            _ = title
            _ = label
            _ = echo
            _ = current
            return text, accepted

    return DialogStub


def _menuActionsByText(menu) -> dict[str, object]:
    return {
        action.text(): action
        for action in menu.actions()
        if callable(getattr(action, "text", None)) and action.text() != ""
    }


def testWorkflowTabsUseTabBarMetadataAndSwitchWithoutCreating() -> None:
    window = MainWindow(_RuntimeClientStub())
    bodyWorkflowId = window.createWorkflow("Body")

    assert window.workflowTabs.count() == 3
    assert not hasattr(window.workflowTabs, "setTabData")
    tabBar = window.workflowTabs.tabBar()
    assert tabBar.tabData(0) == "main"
    assert tabBar.tabData(1) == bodyWorkflowId
    assert tabBar.tabData(2) is None

    workflowCount = len(window.getWorkflowTabs())
    window.workflowTabs.setCurrentIndex(0)
    window.workflowTabs.tabBarClicked.emit(0)

    assert window.getActiveWorkflowId() == "main"
    assert len(window.getWorkflowTabs()) == workflowCount


def testWorkflowTabContextMenuSetsSelectedTabAsEntry() -> None:
    window = MainWindow(_RuntimeClientStub())
    bodyWorkflowId = window.createWorkflow("Body")
    window.workflowTabs.setCurrentIndex(0)
    tabBar = window.workflowTabs.tabBar()

    assert tabBar.contextMenuPolicy() == mainWindowModule.Qt.CustomContextMenu
    menu = window._buildWorkflowTabContextMenu(1)
    assert menu is not None
    actions = _menuActionsByText(menu)
    assert set(actions) == {
        "打开工作流",
        "设为入口工作流",
        "重命名工作流",
        "设置工作流接口",
        "导出工作流包",
        "导入为子工作流",
        "删除工作流",
    }

    actions["设为入口工作流"].trigger()

    assert window.workflowStore.entryWorkflowId == bodyWorkflowId
    assert window.getActiveWorkflowId() == "main"
    assert window.workflowTabs.tabText(0) == "Main"
    assert window.workflowTabs.tabText(1) == "Body [入口]"
    assert window._buildWorkflowTabContextMenu(2) is None
    entryMenu = window._buildWorkflowTabContextMenu(1)
    assert entryMenu is not None
    entryAction = _menuActionsByText(entryMenu)["当前入口工作流"]
    assert entryAction.isEnabled() is False


def testWorkflowTabContextMenuActionsTargetClickedWorkflow(monkeypatch) -> None:
    window = MainWindow(_RuntimeClientStub())
    bodyWorkflowId = window.createWorkflow("Body")
    window.workflowTabs.setCurrentIndex(0)
    monkeypatch.setattr(
        mainWindowModule,
        "QInputDialog",
        _dialogReturning("Renamed Body", True),
    )

    menu = window._buildWorkflowTabContextMenu(1)
    assert menu is not None
    _menuActionsByText(menu)["重命名工作流"].trigger()

    assert window.workflowStore.get(bodyWorkflowId).name == "Renamed Body"
    assert window.getActiveWorkflowId() == "main"

    interfaceResponses = iter(
        [
            ('{"image":"image"}', True),
            ('{"edges":"image"}', True),
        ]
    )

    class InterfaceDialogStub:
        @staticmethod
        def getText(parent, title: str, label: str, echo=0, current: str = ""):
            _ = parent
            _ = title
            _ = label
            _ = echo
            _ = current
            return next(interfaceResponses)

    monkeypatch.setattr(mainWindowModule, "QInputDialog", InterfaceDialogStub)
    menu = window._buildWorkflowTabContextMenu(1)
    assert menu is not None
    _menuActionsByText(menu)["设置工作流接口"].trigger()
    assert window.workflowStore.get(bodyWorkflowId).inputs == {"image": "image"}
    assert window.workflowStore.get(bodyWorkflowId).outputs == {"edges": "image"}
    assert window.getActiveWorkflowId() == "main"

    menu = window._buildWorkflowTabContextMenu(1)
    assert menu is not None
    _menuActionsByText(menu)["打开工作流"].trigger()
    assert window.getActiveWorkflowId() == bodyWorkflowId
    activeMenu = window._buildWorkflowTabContextMenu(1)
    assert activeMenu is not None
    activeAction = _menuActionsByText(activeMenu)["当前已打开"]
    assert activeAction.isEnabled() is False

    window.workflowTabs.setCurrentIndex(0)
    menu = window._buildWorkflowTabContextMenu(1)
    assert menu is not None
    _menuActionsByText(menu)["删除工作流"].trigger()
    assert bodyWorkflowId not in window.workflowStore.workflows
    assert window.getActiveWorkflowId() == "main"


def testPlusTabPromptsForNameAndCancelRestoresActiveTab(monkeypatch) -> None:
    window = MainWindow(_RuntimeClientStub())
    plusIndex = window.workflowTabs.count() - 1
    monkeypatch.setattr(
        mainWindowModule,
        "QInputDialog",
        _dialogReturning("ignored", False),
    )

    window.workflowTabs.setCurrentIndex(plusIndex)
    window.workflowTabs.tabBarClicked.emit(plusIndex)

    assert [item["name"] for item in window.getWorkflowTabs()] == ["Main"]
    assert window.workflowTabs.currentIndex() == 0

    monkeypatch.setattr(
        mainWindowModule,
        "QInputDialog",
        _dialogReturning("Body", True),
    )
    plusIndex = window.workflowTabs.count() - 1
    window.workflowTabs.setCurrentIndex(plusIndex)
    window.workflowTabs.tabBarClicked.emit(plusIndex)

    assert [item["name"] for item in window.getWorkflowTabs()] == ["Main", "Body"]
    assert window.getActiveWorkflowId() == "body"


def testReferencedWorkflowDeleteFailureShowsReasonAndPreservesState(
    monkeypatch,
) -> None:
    window = MainWindow(_RuntimeClientStub())
    bodyWorkflowId = window.createWorkflow("Body")
    window.workflowTabs.setCurrentIndex(0)
    subflowNodeId = window.addSubflowNode(bodyWorkflowId)
    assert subflowNodeId is not None
    window.workflowTabs.setCurrentIndex(1)
    previousTabs = window.getWorkflowTabs()
    previousNodeIds = set(window.flowModel.nodes)
    warning: dict[str, str] = {}

    class MessageBoxStub:
        @staticmethod
        def warning(parent, title: str, message: str):
            _ = parent
            warning.update({"title": title, "message": message})
            return None

    monkeypatch.setattr(mainWindowModule, "QMessageBox", MessageBoxStub)

    menu = window._buildWorkflowTabContextMenu(1)
    assert menu is not None
    _menuActionsByText(menu)["删除工作流"].trigger()

    assert window.getWorkflowTabs() == previousTabs
    assert window.getActiveWorkflowId() == bodyWorkflowId
    assert window.workflowTabs.currentIndex() == 1
    assert set(window.flowModel.nodes) == previousNodeIds
    assert warning["title"] == "无法删除工作流"
    assert "Body" in warning["message"]
    assert "Main" in warning["message"]
    assert "正被以下工作流引用" in warning["message"]
    assert warning["message"] in window.logBuffer[-1]


def testWorkflowPackageExportDialogWritesDedicatedPackage(
    monkeypatch, tmp_path
) -> None:
    window = MainWindow(_RuntimeClientStub())
    bodyWorkflowId = window.createWorkflow("Body")
    selectedPath = tmp_path / "body.json"
    monkeypatch.setattr(
        mainWindowModule.QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *args, **kwargs: (str(selectedPath), "")),
    )

    exportedPath = window.exportWorkflowPackageFor(bodyWorkflowId)

    expectedPath = tmp_path / "body.emowf.json"
    assert exportedPath == str(expectedPath)
    assert expectedPath.is_file()
    payload = json.loads(expectedPath.read_text(encoding="utf-8"))
    assert payload["rootWorkflowId"] == bodyWorkflowId
    assert payload["workflowOrder"] == [bodyWorkflowId]
    assert "工作流已导出" in window.logBuffer[-1]
    assert "包含 1 个工作流" in window.logBuffer[-1]


def testWorkflowPackageImportDialogPreservesEntryAndOpensImportedRoot(
    monkeypatch, tmp_path
) -> None:
    source = MainWindow(_RuntimeClientStub())
    bodyWorkflowId = source.createWorkflow("Body")
    packagePath = writeWorkflowPackage(
        tmp_path / "body",
        buildWorkflowPackage(source.workflowStore, bodyWorkflowId),
    )
    window = MainWindow(
        _RuntimeClientStub(),
        workflowPackagePreviewDialogFactory=_previewDialogFactory(
            accepted=True,
            insertSubflow=False,
        ),
    )
    monkeypatch.setattr(
        mainWindowModule.QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *args, **kwargs: (str(packagePath), "")),
    )

    from tests.designer.qt_wait import waitForCatalog
    waitForCatalog(window)
    importedRootId = window.importWorkflowPackageAction()

    assert importedRootId == bodyWorkflowId
    assert window.workflowStore.entryWorkflowId == "main"
    assert window.getActiveWorkflowId() == bodyWorkflowId
    assert window.workflowTabs.currentIndex() == 1
    assert [item["name"] for item in window.getWorkflowTabs()] == ["Main", "Body"]
    assert "工作流已导入" in window.logBuffer[-1]
    assert "请保存项目以持久化更改" in window.logBuffer[-1]


def testWorkflowPackagePreviewCancelDoesNotCaptureOrImport(
    monkeypatch, tmp_path
) -> None:
    source = MainWindow(_RuntimeClientStub())
    packagePath = writeWorkflowPackage(
        tmp_path / "main",
        buildWorkflowPackage(source.workflowStore, "main"),
    )
    captured: list[object] = []
    window = MainWindow(
        _RuntimeClientStub(),
        workflowPackagePreviewDialogFactory=_previewDialogFactory(
            accepted=False,
            insertSubflow=True,
            captured=captured,
        ),
    )
    unsavedNodeId = window.flowModel.addNode(
        "vision.unsaved",
        "Unsaved",
        {},
        {},
    )
    before = deepcopy(window.workflowStore.workflows)
    monkeypatch.setattr(
        mainWindowModule.QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *args, **kwargs: (str(packagePath), "")),
    )

    from tests.designer.qt_wait import waitForCatalog
    waitForCatalog(window)
    assert window.importWorkflowPackageAction() is None

    assert len(captured) == 1
    assert captured[0].rootWorkflowId == "main-2"
    assert window.workflowStore.workflows == before
    assert all(
        node.get("nodeId") != unsavedNodeId
        for node in window.workflowStore.get("main").nodes
    )
    assert "预览中取消" in window.logBuffer[-1]


def testWorkflowPackageImportCanInsertMappedSubflowIntoCurrentWorkflow(
    monkeypatch, tmp_path
) -> None:
    source = MainWindow(_RuntimeClientStub())
    packagePath = writeWorkflowPackage(
        tmp_path / "main",
        buildWorkflowPackage(source.workflowStore, "main"),
    )
    window = MainWindow(
        _RuntimeClientStub(),
        workflowPackagePreviewDialogFactory=_previewDialogFactory(
            accepted=True,
            insertSubflow=True,
        ),
    )
    parentWorkflowId = window.createWorkflow("Parent")
    window.activateWorkflow("main")
    monkeypatch.setattr(
        mainWindowModule.QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *args, **kwargs: (str(packagePath), "")),
    )

    from tests.designer.qt_wait import waitForCatalog
    waitForCatalog(window)
    importedRootId = window.importWorkflowPackageAction(parentWorkflowId)

    assert importedRootId == "main-2"
    assert window.workflowStore.entryWorkflowId == "main"
    assert window.getActiveWorkflowId() == parentWorkflowId
    subflow = next(
        node
        for node in window.workflowStore.get(parentWorkflowId).nodes
        if node.get("kind") == "subflow"
    )
    assert subflow["targetWorkflowId"] == "main-2"
    assert window.flowModel.selectedNodeId == subflow["nodeId"]
    assert f"已在 {parentWorkflowId} 插入 Subflow" in window.logBuffer[-1]


def testWorkflowPackageImportFailureWarnsWithoutChangingProject(
    monkeypatch, tmp_path
) -> None:
    invalidPath = tmp_path / "invalid.emowf.json"
    invalidPath.write_text('{"schemaVersion":"1.0"}', encoding="utf-8")
    window = MainWindow(_RuntimeClientStub())
    unsavedNodeId = window.flowModel.addNode(
        "vision.unsaved",
        "Unsaved",
        {},
        {},
    )
    before = {
        "entryWorkflowId": window.workflowStore.entryWorkflowId,
        "activeWorkflowId": window.workflowStore.activeWorkflowId,
        "workflowOrder": deepcopy(window.workflowStore.workflowOrder),
        "workflows": deepcopy(window.workflowStore.workflows),
        "dependencies": deepcopy(window.workflowStore.dependencies),
    }
    warning: dict[str, str] = {}

    class MessageBoxStub:
        @staticmethod
        def warning(parent, title: str, message: str):
            _ = parent
            warning.update({"title": title, "message": message})
            return None

    monkeypatch.setattr(mainWindowModule, "QMessageBox", MessageBoxStub)
    monkeypatch.setattr(
        mainWindowModule.QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *args, **kwargs: (str(invalidPath), "")),
    )

    from tests.designer.qt_wait import waitForCatalog
    waitForCatalog(window)
    assert window.importWorkflowPackageAction() is None

    after = {
        "entryWorkflowId": window.workflowStore.entryWorkflowId,
        "activeWorkflowId": window.workflowStore.activeWorkflowId,
        "workflowOrder": window.workflowStore.workflowOrder,
        "workflows": window.workflowStore.workflows,
        "dependencies": window.workflowStore.dependencies,
    }
    assert after == before
    assert all(
        node.get("nodeId") != unsavedNodeId
        for node in window.workflowStore.get("main").nodes
    )
    assert warning["title"] == "导入工作流失败"
    assert warning["message"] in window.logBuffer[-1]
