from __future__ import annotations

from pathlib import Path
from typing import Callable
from uuid import uuid4

from emo_master.apps.designer.state.project_store import (
    createProjectSkeleton,
    loadProject,
    saveProject,
)
from emo_master.apps.designer.state.workflow_store import defaultNodePosition
from emo_master.apps.designer.ui.flow_scene import FlowEdgeViewModel, FlowNodeViewModel
from emo_master.core.project.migration import utc_now_iso
from emo_master.core.project.files import projectFileForSave, resolveProjectFile
from emo_master.apps.designer.ui.project_entry_dialog import ProjectEntryDialog


class ProjectController:
    def __init__(
        self,
        runtimeClient,
        flowModel,
        flowScene,
        settingsStore,
        appendLog: Callable[[str, str], None],
        refreshSidebarNodeList: Callable[[], None],
        focusGraphContent: Callable[[], None],
        refreshRuntimePanelView: Callable[[], None],
        updateToolbarState: Callable[[], None],
        updateRuntimeJobState: Callable[[str, str], None],
        workflowController=None,
    ) -> None:
        self.runtimeClient = runtimeClient
        self.flowModel = flowModel
        self.flowScene = flowScene
        self.settingsStore = settingsStore
        self.appendLog = appendLog
        self.refreshSidebarNodeList = refreshSidebarNodeList
        self.focusGraphContent = focusGraphContent
        self.refreshRuntimePanelView = refreshRuntimePanelView
        self.updateToolbarState = updateToolbarState
        self.updateRuntimeJobState = updateRuntimeJobState
        self.workflowController = workflowController
        self.editCoordinator = None
        self.currentProjectFile: Path | None = None
        self._fallbackProjectMetadata: dict[str, object] = {
            "projectId": str(uuid4()),
            "name": "project",
            "revision": 1,
            "createdAt": utc_now_iso(),
            "updatedAt": utc_now_iso(),
        }

    def getRecentProjects(self) -> list[dict[str, str]]:
        valueMethod = getattr(self.settingsStore, "value", None)
        if not callable(valueMethod):
            return []
        stored = valueMethod("ui/recent_projects", [])
        if not isinstance(stored, list):
            return []
        result: list[dict[str, str]] = []
        for item in stored:
            projectPath = ""
            projectName = ""
            if isinstance(item, dict):
                rawPath = item.get("projectPath", "")
                rawName = item.get("projectName", "")
                if isinstance(rawPath, str):
                    projectPath = rawPath
                if isinstance(rawName, str):
                    projectName = rawName
            elif isinstance(item, str):
                projectPath = item
            if projectPath == "":
                continue
            pathObj = Path(projectPath)
            if pathObj.exists() and not pathObj.is_file():
                continue
            if projectName == "":
                projectName = pathObj.stem if pathObj.suffix.lower() == ".emoproj" else (pathObj.parent.name or "project")
            result.append(
                {"projectName": projectName, "projectPath": pathObj.as_posix()}
            )
        setValue = getattr(self.settingsStore, "setValue", None)
        if callable(setValue):
            setValue("ui/recent_projects", [dict(item) for item in result])
        return result

    def recordRecentProject(self, projectJsonPath: str) -> None:
        if projectJsonPath == "":
            return
        normalized = Path(projectJsonPath).as_posix()
        current = [
            item
            for item in self.getRecentProjects()
            if item.get("projectPath") != normalized
        ]
        pathObj = Path(normalized)
        projectName = pathObj.stem if pathObj.suffix.lower() == ".emoproj" else (pathObj.parent.name or "project")
        updated = [{"projectName": projectName, "projectPath": normalized}, *current][
            :10
        ]
        setValue = getattr(self.settingsStore, "setValue", None)
        if callable(setValue):
            setValue("ui/recent_projects", updated)

    def removeRecentProject(self, projectJsonPath: str) -> None:
        normalized = Path(projectJsonPath).as_posix()
        updated = [
            item
            for item in self.getRecentProjects()
            if item.get("projectPath") != normalized
        ]
        setValue = getattr(self.settingsStore, "setValue", None)
        if callable(setValue):
            setValue("ui/recent_projects", updated)

    def clearRecentProjects(self) -> None:
        setValue = getattr(self.settingsStore, "setValue", None)
        if callable(setValue):
            setValue("ui/recent_projects", [])

    def loadProjectFromPath(
        self,
        projectPath: str,
        successMessagePrefix: str,
        failedMessagePrefix: str,
    ) -> tuple[bool, str | None]:
        reply = self.runtimeClient.loadProject(projectPath)
        if getattr(reply, "ok", False):
            self.appendLog("INFO", f"{successMessagePrefix}：{projectPath}")
            self.updateRuntimeJobState("READY", f"当前项目：{projectPath}")
            self.refreshRuntimePanelView()
            self.updateToolbarState()
            return True, projectPath
        self.appendLog(
            "ERROR", f"{failedMessagePrefix}：{getattr(reply, 'message', '未知错误')}"
        )
        return False, None

    def loadProjectDirectory(
        self, projectDirPath: str
    ) -> tuple[bool, str | None, Path | None]:
        if self.editCoordinator is not None and not self.editCoordinator.confirmLeave():
            return False, None, None
        try:
            projectFile = resolveProjectFile(Path(projectDirPath))
            projectDir = projectFile.parent
            payload = loadProject(projectFile)
        except (OSError, ValueError) as err:
            self.appendLog("ERROR", f"加载项目失败：{err}")
            return False, None, None
        if self.editCoordinator is not None and payload.get('schemaVersion') in {'2.2', '2.3', '2.4'}:
            # Opening an editable draft must not compile unresolved resource paths
            # through the legacy execution API. P2 preparation happens on explicit start.
            loaded, runtimePath = True, str(projectFile)
            self.appendLog('INFO', '2.2 草稿已打开；明确启动本地调试时物化资源和编译')
        else:
            loaded, runtimePath = self.loadProjectFromPath(
                str(projectFile),
                successMessagePrefix="项目已加载",
                failedMessagePrefix="加载项目失败",
            )
        if not loaded:
            return False, None, None
        if self.editCoordinator is not None:
            # Minimal projects of any version may omit registered operator metadata.
            payload = self.editCoordinator.normalizeDraft(payload)
        self._captureFallbackProjectMetadata(payload)
        if self.workflowController is not None:
            if self.editCoordinator is not None:
                self.workflowController.loadPayload(payload, preserveEdges=True)
            else:
                self.workflowController.loadPayload(payload)
        else:
            self._restoreProjectPayload(payload)
        if self.editCoordinator is not None:
            self.editCoordinator.loaded(projectDir)
        self.currentProjectFile = projectFile
        self.recordRecentProject(str(projectFile))
        self.appendLog("INFO", f"项目已加载：{projectFile}")
        return True, runtimePath, projectDir

    def saveProjectToDirectory(
        self, projectDirPath: str, projectName: str, loadedProjectPath: str | None
    ) -> tuple[bool, Path | None]:
        try:
            selected = Path(projectDirPath)
            if self.currentProjectFile is not None and selected == self.currentProjectFile.parent:
                selected = self.currentProjectFile
            projectFile = projectFileForSave(selected)
            projectDir = projectFile.parent
            if self.editCoordinator is not None:
                self.editCoordinator.sync()
                self.editCoordinator.copyResources(projectDir)
            createProjectSkeleton(projectFile, projectName)
            payload = self._buildProjectPayload(projectName, loadedProjectPath)
            saveProject(projectFile, payload)
        except Exception as err:
            self.appendLog("ERROR", f"项目保存失败：{err}")
            return False, None
        if self.workflowController is not None:
            self.workflowController.commitSavedPayload(payload)
        else:
            self._captureFallbackProjectMetadata(payload)
        if self.editCoordinator is not None:
            self.editCoordinator.saved(projectDir)
        self.currentProjectFile = projectFile
        self.recordRecentProject(str(projectFile))
        self.appendLog("INFO", f"项目已保存：{projectFile}")
        return True, projectDir

    def resolveProjectDirectory(self, selectedPath: str) -> Path | None:
        try:
            return resolveProjectFile(Path(selectedPath)).parent
        except (OSError, ValueError):
            return None

    def handleStartupProjectEntry(
        self,
        parent,
        chooseProjectDirectory: Callable[[str], str],
        dialogFactory: Callable[[], object] | None = None,
    ) -> tuple[bool, str | None, Path | None]:
        dialogObj = (
            dialogFactory() if callable(dialogFactory) else ProjectEntryDialog(parent)
        )
        setRecentProjects = getattr(dialogObj, "setRecentProjects", None)
        if callable(setRecentProjects):
            setRecentProjects(self.getRecentProjects())
        clearRecentProjects = getattr(dialogObj, "shouldClearRecentProjects", None)
        removedRecentProjectPath = getattr(
            dialogObj, "getRemovedRecentProjectPath", None
        )
        execSelection = getattr(dialogObj, "execSelection", None)
        if not callable(execSelection):
            return False, None, None
        selectionResult = execSelection()
        if callable(clearRecentProjects) and clearRecentProjects():
            self.clearRecentProjects()
        if callable(removedRecentProjectPath):
            removedPath = removedRecentProjectPath()
            if isinstance(removedPath, str) and removedPath != "":
                self.removeRecentProject(removedPath)
        action = "cancel"
        selectedPath = ""
        if (
            isinstance(selectionResult, tuple)
            and len(selectionResult) == 2
            and isinstance(selectionResult[0], str)
            and isinstance(selectionResult[1], str)
        ):
            action = selectionResult[0]
            selectedPath = selectionResult[1]
        if action == "open_project" and selectedPath != "":
            projectDir = self.resolveProjectDirectory(selectedPath)
            if projectDir is None:
                return False, None, None
            return self.loadProjectDirectory(selectedPath)
        if action == "new_blank":
            selectedDir = chooseProjectDirectory("新建项目")
            if selectedDir != "":
                selected = Path(selectedDir)
                projectName = selected.stem if selected.suffix.lower() == ".emoproj" else selected.name
                saved, savedProjectDir = self.saveProjectToDirectory(
                    selectedDir, projectName or "project", None
                )
                if not saved or savedProjectDir is None:
                    return False, None, None
                self.appendLog("INFO", f"空白项目已初始化：{selectedDir}")
                loaded, runtimePath = self.loadProjectFromPath(
                    str(self.currentProjectFile or savedProjectDir),
                    successMessagePrefix="空白项目已加载",
                    failedMessagePrefix="加载空白项目失败",
                )
                if not loaded:
                    return False, None, None
                return True, runtimePath, savedProjectDir
        return False, None, None

    def _buildProjectPayload(
        self, projectName: str, loadedProjectPath: str | None
    ) -> dict[str, object]:
        if self.workflowController is not None:
            return self.workflowController.buildPayload(projectName)
        graphPayload = self.flowModel.toProjectGraph()
        nodePositions = self.flowScene.getNodePositions()

        rawNodes = graphPayload.get("nodes", [])
        nodes = rawNodes if isinstance(rawNodes, list) else []
        patchedNodes: list[dict[str, object]] = []
        for rawNode in nodes:
            if not isinstance(rawNode, dict):
                continue
            patchedNodes.append(dict(rawNode))

        edgesRaw = graphPayload.get("edges", [])
        edges = edgesRaw if isinstance(edgesRaw, list) else []

        project = dict(self._fallbackProjectMetadata)
        project["name"] = projectName
        project["updatedAt"] = utc_now_iso()
        revision = project.get("revision", 1)
        project["revision"] = (revision if isinstance(revision, int) else 1) + 1

        return {
            "schemaVersion": "2.1",
            "project": project,
            "entryWorkflowId": "main",
            "workflowOrder": ["main"],
            "workflows": {
                "main": {
                    "name": projectName,
                    "inputs": {},
                    "outputs": {},
                    "nodes": patchedNodes,
                    "edges": edges,
                    "layout": {
                        "nodePositions": {
                            nodeId: {"x": float(position[0]), "y": float(position[1])}
                            for nodeId, position in nodePositions.items()
                        }
                    },
                }
            },
            "runtime": {},
            "dependencies": {"operators": []},
            "devices": {"bindings": {}},
        }

    def _captureFallbackProjectMetadata(self, payload: dict[str, object]) -> None:
        project = payload.get("project")
        if isinstance(project, dict):
            self._fallbackProjectMetadata = dict(project)

    def _restoreProjectPayload(self, payload: dict[str, object]) -> str | None:
        workflowsRaw = payload.get("workflows")
        entryWorkflowId = payload.get("entryWorkflowId", "main")
        workflow = (
            workflowsRaw.get(entryWorkflowId)
            if isinstance(workflowsRaw, dict) and isinstance(entryWorkflowId, str)
            else None
        )
        if isinstance(workflow, dict):
            graphPayload = {
                "nodes": workflow.get("nodes", []),
                "edges": workflow.get("edges", []),
            }
            layoutRaw = workflow.get("layout")
            layout = layoutRaw if isinstance(layoutRaw, dict) else {}
            positionsRaw = layout.get("nodePositions", {})
            positions = positionsRaw if isinstance(positionsRaw, dict) else {}
        else:
            designerRaw = payload.get("designer", {})
            designer = designerRaw if isinstance(designerRaw, dict) else {}
            graphPayload = {
                "nodes": designer.get("nodes", []),
                "edges": designer.get("edges", []),
            }
            positions = {}
        graphPayload = {
            "nodes": graphPayload.get("nodes", []),
            "edges": graphPayload.get("edges", []),
        }
        self.flowModel.loadProjectGraph(graphPayload)
        self.flowScene.clearGraph()

        for node in self.flowModel.nodes.values():
            defaultX, defaultY = defaultNodePosition(node.kind)
            xValue = defaultX
            yValue = defaultY
            for rawNode in graphPayload.get("nodes", []):
                if not isinstance(rawNode, dict):
                    continue
                rawNodeId = rawNode.get("nodeId")
                if not isinstance(rawNodeId, str) or rawNodeId != node.nodeId:
                    continue
                xRaw = rawNode.get("x", defaultX)
                yRaw = rawNode.get("y", defaultY)
                layoutPosition = positions.get(node.nodeId)
                if isinstance(layoutPosition, dict):
                    xRaw = layoutPosition.get("x", xRaw)
                    yRaw = layoutPosition.get("y", yRaw)
                if isinstance(xRaw, (int, float)):
                    xValue = float(xRaw)
                if isinstance(yRaw, (int, float)):
                    yValue = float(yRaw)
                break

            self.flowScene.addFlowNode(
                FlowNodeViewModel(
                    nodeId=node.nodeId,
                    title=node.displayName,
                    x=xValue,
                    y=yValue,
                    inputPorts=node.inputPorts,
                    outputPorts=node.outputPorts,
                    operatorId=node.operatorId,
                    kind=node.kind,
                )
            )

        for edge in self.flowModel.edges:
            self.flowScene.renderEdge(
                FlowEdgeViewModel(
                    fromNodeId=edge.fromNode,
                    fromPort=edge.fromPort,
                    toNodeId=edge.toNode,
                    toPort=edge.toPort,
                )
            )
        self.refreshSidebarNodeList()
        self.focusGraphContent()
        self.updateToolbarState()
        return None
