"""Reproduce unrelated failures with only this task's edits removed in a copy.

This deliberately retains the other uncommitted project changes. It neither
resets the live checkout nor substitutes the older committed tree for it.
"""
from pathlib import Path
import difflib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[3]
OUTPUT = Path(__file__).resolve().parent
snapshot = Path(tempfile.mkdtemp(prefix="emo-interface-baseline-"))
omitted = ("workflow_interface_dialog.py", "test_workflow_interface_dialog.py", "validate_workflow_interface.py")
for directory in ("src", "tests", "examples", "scripts", "proto"):
    shutil.copytree(ROOT / directory, snapshot / directory,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", *omitted))
for name in ("pyproject.toml", "mypy.ini", "requirements.txt", "requirements-dev.txt"):
    shutil.copy2(ROOT / name, snapshot / name)


def replaceOnce(text, before, after):
    assert text.count(before) == 1, before
    return text.replace(before, after, 1)


mainPath = Path("src/emo_master/apps/designer/ui/main_window.py")
originalMain = (snapshot / mainPath).read_text(encoding="utf-8")
main = replaceOnce(originalMain, "from emo_master.apps.designer.ui.workflow_interface_dialog import WorkflowInterfaceDialog\n", "")
main = replaceOnce(main, "from datetime import datetime\n", "from datetime import datetime\nimport json\n")
main = replaceOnce(main, "    defaultNodePosition,\n    portTypes,\n", "    defaultNodePosition,\n")
start = main.index("    @draftCommand\n    def editWorkflowInterfaceFor(")
end = main.index("    @draftCommand\n    def addSubflowNode(", start)
main = main[:start] + '''    @draftCommand
    def editWorkflowInterfaceFor(
        self,
        workflowId: str,
        inputs: dict[str, object] | None = None,
        outputs: dict[str, object] | None = None,
    ) -> None:
        try:
            workflow = self.workflowStore.get(workflowId)
        except KeyError:
            self.appendRuntimeLog("ERROR", f"工作流接口设置失败：{workflowId} 不存在")
            return
        if inputs is None:
            inputs = self._promptInterfaceMap("输入接口", workflow.inputs)
        if inputs is None:
            return
        if outputs is None:
            outputs = self._promptInterfaceMap("输出接口", workflow.outputs)
        if outputs is None:
            return
        try:
            refreshReport = self.workflowController.setWorkflowInterface(
                workflowId, inputs, outputs
            )
        except (TypeError, ValueError) as err:
            self.appendRuntimeLog("ERROR", f"工作流接口设置失败：{err}")
            return
        self.appendRuntimeLog("INFO", f"工作流接口已更新：{workflowId}")
        if refreshReport:
            message = "接口同步时发现以下变化：\\n" + "\\n".join(refreshReport)
            self.appendRuntimeLog("WARN", message.replace("\\n", " | "))
            QMessageBox.warning(self, "工作流引用已更新", message)
        self._refreshWorkflowTabs()

    def _promptInterfaceMap(
        self, label: str, current: dict[str, object]
    ) -> dict[str, object] | None:
        text, accepted = QInputDialog.getText(
            self,
            "设置工作流接口",
            f"{label} JSON",
            QLineEdit.Normal,
            json.dumps(current, ensure_ascii=True),
        )
        if not accepted:
            return None
        try:
            parsed = json.loads(str(text))
        except json.JSONDecodeError as err:
            self.appendRuntimeLog("ERROR", f"{label} JSON 无效：{err}")
            return None
        if not isinstance(parsed, dict):
            self.appendRuntimeLog("ERROR", f"{label} 必须是 JSON 对象")
            return None
        return parsed

''' + main[end:]
main = replaceOnce(main, '''        if node.kind in {"workflow_input", "workflow_output"}:
            self.editWorkflowInterfaceFor(
                self.activeWorkflowId,
                initialTab="inputs" if node.kind == "workflow_input" else "outputs",
            )
            return

''', "")
(snapshot / mainPath).write_text(main, encoding="utf-8")

scenePath = Path("src/emo_master/apps/designer/ui/flow_scene.py")
originalScene = (snapshot / scenePath).read_text(encoding="utf-8")
scene = replaceOnce(originalScene, '''            tooltip = model.title
            if model.kind in {"workflow_input", "workflow_output"}:
                side = "输入" if model.kind == "workflow_input" else "输出"
                tooltip += f"\\n双击配置工作流{side}接口"
            titleText.setToolTip(tooltip)
''', "            titleText.setToolTip(model.title)\n")
scene = replaceOnce(scene, "            self.setToolTip(tooltip)\n", "            self.setToolTip(model.title)\n")
scene = replaceOnce(scene, '''                if self.model.kind in {"workflow_input", "workflow_output"}:
                    # The modal interface editor may replace the current scene.
                    # Leave this native item event before opening it.
                    nodeId = self.model.nodeId
                    QTimer.singleShot(0, lambda: handleNodeDoubleClick(nodeId))
                else:
                    handleNodeDoubleClick(self.model.nodeId)
''', "                handleNodeDoubleClick(self.model.nodeId)\n")
(snapshot / scenePath).write_text(scene, encoding="utf-8")

patch = "".join(difflib.unified_diff(originalMain.splitlines(keepends=True), main.splitlines(keepends=True),
                                 fromfile=str(mainPath), tofile="baseline/" + str(mainPath)))
patch += "".join(difflib.unified_diff(originalScene.splitlines(keepends=True), scene.splitlines(keepends=True),
                                  fromfile=str(scenePath), tofile="baseline/" + str(scenePath)))
(OUTPUT / "baseline-restoration.patch").write_text(patch, encoding="utf-8")
record = {"snapshot": str(snapshot), "omittedTaskFiles": omitted,
          "transformed": {str(path): {"liveSha256": hashlib.sha256((ROOT / path).read_bytes()).hexdigest(),
                                      "baselineSha256": hashlib.sha256((snapshot / path).read_bytes()).hexdigest()}
                          for path in (mainPath, scenePath)},
          "tests": [
              "tests/designer/test_minimal_project_metadata.py::testEditorUsesLateCatalogWithoutMutatingSavedGraph",
              "tests/designer/test_minimal_project_metadata.py::testNormalizationPreservesSavedContractsAndOwnsFallbackSchema",
              "tests/runtime/test_image_batch_while.py::testRealWhileProcessesEveryImageAndResetsForNewRun[1]",
              "tests/runtime/test_image_batch_while.py::testRealWhileProcessesEveryImageAndResetsForNewRun[3]",
              "tests/runtime/test_image_batch_while.py::testDirectoryResolvedRelativeToProjectAndDraftUnchanged",
          ]}
(OUTPUT / "baseline-snapshot.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_SCALE_FACTOR": "1",
       "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
env.pop("PYTEST_ADDOPTS", None)
completed = subprocess.run([sys.executable, "-m", "pytest", "-q", *record["tests"]],
                           cwd=snapshot, env=env, capture_output=True, text=True, encoding="utf-8")
(OUTPUT / "baseline-designer-failures.txt").write_text(completed.stdout + completed.stderr, encoding="utf-8")
print(completed.stdout)
print(f"Baseline exit={completed.returncode}; snapshot={snapshot}")
raise SystemExit(completed.returncode)
