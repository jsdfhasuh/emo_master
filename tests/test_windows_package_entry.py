from __future__ import annotations

import json
from pathlib import Path
import sys
from types import ModuleType

from emo_master.apps import windows_entry
from emo_master.apps.package_selftest import runSelfTest, runSelfTestCommand


def testPackageSelfTestValidatesBundledResourcesAndOnnxRuntime() -> None:
    result = runSelfTest()

    assert result["status"] == "ok"
    checks = result["checks"]
    assert checks["builtins"]["operatorCount"] >= 49
    assert checks["builtins"]["editorUiCount"] >= 3
    assert checks["onnxruntime"]["provider"] == "CPUExecutionProvider"
    assert checks["migrations"]["versions"] == [1, 2, 3, 4]
    assert checks["migrations"]["journalMode"] == "delete"


def testPackageSelfTestCommandWritesResultJson(tmp_path: Path) -> None:
    resultPath = tmp_path / "results" / "self-test.json"

    exitCode = runSelfTestCommand(["--self-test", "--result-json", str(resultPath)])

    assert exitCode == 0
    assert json.loads(resultPath.read_text(encoding="utf-8"))["status"] == "ok"


def testWindowsEntryCallsFreezeSupportBeforeImportingDesigner(monkeypatch) -> None:
    events: list[str] = []
    fakeDesignerMain = ModuleType("emo_master.apps.designer.main")

    def runDesigner() -> None:
        events.append("designer")

    fakeDesignerMain.runDesigner = runDesigner  # type: ignore[attr-defined]
    monkeypatch.setattr(
        windows_entry.multiprocessing,
        "freeze_support",
        lambda: events.append("freeze"),
    )
    monkeypatch.setitem(sys.modules, "emo_master.apps.designer.main", fakeDesignerMain)

    assert windows_entry.main([]) == 0
    assert events == ["freeze", "designer"]
