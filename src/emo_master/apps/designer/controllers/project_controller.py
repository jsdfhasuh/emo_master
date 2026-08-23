from __future__ import annotations

from pathlib import Path
from typing import Callable

from emo_master.apps.designer.state.project_store import (
    createProjectSkeleton,
    loadProject,
    saveProject,
)
from emo_master.apps.designer.ui.flow_scene import FlowEdgeViewModel, FlowNodeViewModel
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
                projectName = (
                    pathObj.parent.name if pathObj.parent.name != "" else "project"
                )
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
        projectName = (
            Path(normalized).parent.name
            if Path(normalized).parent.name != ""
            else "project"
        )
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
        projectDir = Path(projectDirPath)
        try:
            payload = loadProject(projectDir)
        except ValueError as err:
            self.appendLog("ERROR", f"加载项目失败：{err}")
            return False, None, None
        self._restoreProjectPayload(payload)
        loaded, runtimePath = self.loadProjectFromPath(
            str(projectDir),
            successMessagePrefix="项目已加载",
            failedMessagePrefix="加载项目失败",
        )
        if not loaded:
            return False, None, None
        self.recordRecentProject(str(projectDir / "project.json"))
        self.appendLog("INFO", f"项目已加载：{projectDir / 'project.json'}")
        return True, runtimePath, projectDir

    def saveProjectToDirectory(
        self, projectDirPath: str, projectName: str, loadedProjectPath: str | None
    ) -> tuple[bool, Path | None]:
        projectDir = Path(projectDirPath)
        createProjectSkeleton(projectDir, projectName)
        payload = self._buildProjectPayload(projectName, loadedProjectPath)
        saveProject(projectDir, payload)
        self.appendLog("INFO", f"项目已保存：{projectDir / 'project.json'}")
        return True, projectDir

    def resolveProjectDirectory(self, selectedPath: str) -> Path | None:
        pathObj = Path(selectedPath)
        if pathObj.is_dir() and (pathObj / "project.json").exists():
            return pathObj
        if pathObj.is_file() and pathObj.name.lower() == "project.json":
            return pathObj.parent
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
            return self.loadProjectDirectory(str(projectDir))
        if action == "new_blank":
            selectedDir = chooseProjectDirectory("为新建空白项目选择文件夹")
            if selectedDir != "":
                projectDir = Path(selectedDir)
                self.saveProjectToDirectory(
                    selectedDir, projectDir.name or "project", None
                )
                self.appendLog("INFO", f"空白项目已初始化：{selectedDir}")
                return (
                    self.loadProjectFromPath(
                        str(projectDir),
                        successMessagePrefix="空白项目已加载",
                        failedMessagePrefix="加载空白项目失败",
                    )[0],
                    str(projectDir),
                    projectDir,
                )
        return False, None, None

    def _buildProjectPayload(
        self, projectName: str, loadedProjectPath: str | None
    ) -> dict[str, object]:
        graphPayload = self.flowModel.toProjectGraph()
        nodePositions = self.flowScene.getNodePositions()

        rawNodes = graphPayload.get("nodes", [])
        nodes = rawNodes if isinstance(rawNodes, list) else []
        patchedNodes: list[dict[str, object]] = []
        for rawNode in nodes:
            if not isinstance(rawNode, dict):
                continue
            node = dict(rawNode)
            nodeIdRaw = node.get("nodeId", "")
            nodeId = nodeIdRaw if isinstance(nodeIdRaw, str) else ""
            if nodeId in nodePositions:
                posX, posY = nodePositions[nodeId]
                node["x"] = float(posX)
                node["y"] = float(posY)
            else:
                node["x"] = float(node.get("x", 20.0))
                node["y"] = float(node.get("y", 20.0))
            patchedNodes.append(node)

        edgesRaw = graphPayload.get("edges", [])
        edges = edgesRaw if isinstance(edgesRaw, list) else []

        return {
            "version": "1.0",
            "meta": {
                "name": projectName,
            },
            "runtime": {
                "sourceImagePath": ""
                if loadedProjectPath is None
                else loadedProjectPath,
            },
            "designer": {
                "nodes": patchedNodes,
                "edges": edges,
            },
        }

    def _restoreProjectPayload(self, payload: dict[str, object]) -> str | None:
        designerRaw = payload.get("designer", {})
        designer = designerRaw if isinstance(designerRaw, dict) else {}
        graphPayload = {
            "nodes": designer.get("nodes", []),
            "edges": designer.get("edges", []),
        }
        self.flowModel.loadProjectGraph(graphPayload)
        self.flowScene.clearGraph()

        for node in self.flowModel.nodes.values():
            xValue = 20.0
            yValue = 20.0
            for rawNode in graphPayload.get("nodes", []):
                if not isinstance(rawNode, dict):
                    continue
                rawNodeId = rawNode.get("nodeId")
                if not isinstance(rawNodeId, str) or rawNodeId != node.nodeId:
                    continue
                xRaw = rawNode.get("x", 20.0)
                yRaw = rawNode.get("y", 20.0)
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
