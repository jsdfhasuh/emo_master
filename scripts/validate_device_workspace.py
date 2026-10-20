"""Native Qt software evidence for the device workspace; no hardware or Job start."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["HUARAY_CAMERA_SMOKE"] = "0"

from PySide2.QtCore import QSettings  # noqa: E402
from PySide2.QtWidgets import QApplication, QMessageBox  # noqa: E402
from emo_master.apps.designer.main import configureHighDpi, applyDesignerStyle  # noqa: E402
from emo_master.apps.designer.ui.main_window import MainWindow  # noqa: E402
from emo_master.apps.designer.ui.device_wizard import DeviceNodeDialog  # noqa: E402
from emo_master.apps.designer.ui.project_configuration import ProjectConfigurationDialog  # noqa: E402
from emo_master.apps.designer.ui.workflow_package_preview_dialog import WorkflowPackagePreviewDialog  # noqa: E402
from emo_master.apps.designer.ui.managed_plc_debug import ManagedPlcDebugDialog  # noqa: E402
from emo_master.apps.designer.ui.managed_camera_debug import ManagedCameraDebugDialog  # noqa: E402
from emo_master.apps.designer.state.workflow_package import buildWorkflowPackage, previewWorkflowPackageImport  # noqa: E402
from emo_master.apps.designer.state.project_store import saveProject  # noqa: E402
from emo_master.apps.designer.state.workflow_store import WorkflowStore  # noqa: E402
from emo_master.core.plugin.devices import DeviceType  # noqa: E402
from emo_master.core.project.migration import migrateProjectPayload  # noqa: E402
from emo_master.core.project.devices import DeviceDefinition  # noqa: E402


def wait(app, predicate=lambda: False, timeout=.3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=800)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    configureHighDpi()
    app = QApplication([])
    applyDesignerStyle(app)
    metadata = ROOT / "src/emo_master/plugins/builtins/simulated_camera/device_types/camera.json"
    declaration = DeviceType.model_validate_json(metadata.read_text(encoding="utf-8"))
    manifest = json.loads((metadata.parent.parent / "manifest.json").read_text(encoding="utf-8"))
    requirements = {manifest["operatorId"]: manifest["deviceRequirements"]}
    types = {declaration.deviceTypeId: declaration}
    plcMetadata = ROOT / "src/emo_master/plugins/builtins/plc_slmp_read/device_types/slmp.json"
    plc = DeviceType.model_validate_json(plcMetadata.read_text(encoding="utf-8"))
    plcManifest = json.loads((plcMetadata.parent.parent / "manifest.json").read_text(encoding="utf-8"))
    types[plc.deviceTypeId] = plc
    requirements[plcManifest["operatorId"]] = plcManifest["deviceRequirements"]
    imvMetadata = ROOT / "src/emo_master/plugins/builtins/huaray_camera/device_types/imv.json"
    imv = DeviceType.model_validate_json(imvMetadata.read_text(encoding="utf-8"))
    types[imv.deviceTypeId] = imv
    client = SimpleNamespace(listOperators=lambda: [], listDeviceTypes=lambda: (types, requirements, {}))
    with TemporaryDirectory(prefix="emo-device-visual-") as temporary:
        root = Path(temporary)
        os.environ["EMO_RUNTIME_DATA_DIR"] = temporary
        payload = migrateProjectPayload(WorkflowStore().toPayload(), enableDevices=True)
        payload["devices"]["definitions"]["camera-A"] = declaration.validateDefinition(
            DeviceDefinition(
                name="模拟相机 A", deviceTypeId=declaration.deviceTypeId, driverVersion=declaration.version,
                connection={"serial": "SIM-A"})).model_dump()
        plcDefinition = plc.validateDefinition(DeviceDefinition(name="PLC / 回环示例", deviceTypeId=plc.deviceTypeId,
                                                                driverVersion=plc.version, connection={"host": "127.0.0.1"}))
        payload["devices"]["definitions"]["plc"] = plcDefinition.model_dump()
        node = dict(nodeId="capture-A", operatorId=manifest["operatorId"], displayName="工位 A 采集",
                    deviceBindings={"camera": {"deviceId": "camera-A"}})
        payload["workflows"]["main"]["nodes"].append(node)
        path = root / "visual.emoproj"
        saveProject(path, payload)
        window = MainWindow(client, settingsStore=QSettings(str(root / "settings.ini"), QSettings.IniFormat))
        try:
            assert window.loadProjectDirectory(str(path), confirmed=True)
            window.resize(args.width, args.height)
            window.show()
            c = window.pageCoordinator
            c.showDevices()
            wait(app, lambda: c.devices.pending is None, 5)
            assert c.devices.deviceTypes
            c.devices.table.setCurrentCell(0, 0)
            wait(app)
            assert window.grab().save(str(args.output / "device-workspace.png"))
            c.devices.tabs.setCurrentIndex(2)
            wait(app)
            assert window.grab().save(str(args.output / "device-policies.png"))
            buttons = list(c.devices.buttons.values())
            assert all(not left.geometry().intersects(right.geometry())
                       for index, left in enumerate(buttons) for right in buttons[index + 1:])
            c.devices.tabs.setCurrentIndex(0)
            c.showFlow()
            dialog = DeviceNodeDialog(c, node, manifest["deviceRequirements"], window)
            dialog.show()
            wait(app, lambda: bool(dialog.slots), 5)
            dialog.tabs.setCurrentIndex(1)
            modes = dialog.sources["camera"].rows["exposureUs"][0]
            modes.setCurrentIndex(modes.findData("constant"))
            wait(app)
            assert dialog.grab().save(str(args.output / "device-binding.png"))
            dialog.reject()
            online = deepcopy(c.session.payload())
            online["devices"]["definitions"]["camera-A"]["defaults"]["exposureUs"] = 12000
            client.getOnlineProjectSnapshot = lambda projectId: dict(payload=online, runtimeInstanceId="visual-only",
                documentRevision=3, executionRevision=2)
            comparison = ProjectConfigurationDialog(c, window)
            comparison.resize(min(args.width, 1050), min(args.height, 650))
            comparison.show()
            wait(app, lambda: comparison.pending is None, 5)
            wait(app)
            assert comparison.grab().save(str(args.output / "online-configuration.png"))
            comparison.reject()
            package = buildWorkflowPackage(c.session.workflows, "main")
            preview = previewWorkflowPackageImport(c.session.workflows, package, [manifest["operatorId"]])
            packageDialog = WorkflowPackagePreviewDialog(preview, window)
            packageDialog.resize(min(args.width, 760), min(args.height, 680))
            packageDialog.show()
            wait(app)
            combo = packageDialog._deviceCombos["camera-A"]
            combo.setCurrentIndex(2)
            combo.parentWidget().ensurePolished()
            from PySide2.QtWidgets import QScrollArea
            packageDialog.findChild(QScrollArea).ensureWidgetVisible(combo)
            wait(app)
            assert packageDialog.grab().save(str(args.output / "workflow-device-mapping.png"))
            packageDialog.reject()
            plcDraft = dict(nodeId="plc-read", operatorId=plcManifest["operatorId"], params={"startAddress": 800},
                            paramSchema=plcManifest["paramSchema"], deviceBindings={"plc": {"deviceId": "plc"}})
            plcWizard = DeviceNodeDialog(c, plcDraft, plcManifest["deviceRequirements"], window)
            plcWizard.resize(min(args.width, 700), min(args.height, 650))
            plcWizard.show()
            wait(app, lambda: bool(plcWizard.slots), 5)
            wait(app)
            assert plcWizard.grab().save(str(args.output / "managed-plc-node.png"))
            plcWizard.reject()
            plcDialog = ManagedPlcDebugDialog(client, payload["project"]["projectId"], "plc", plcDefinition, window)
            plcDialog.resize(min(args.width, 960), min(args.height, 720))
            plcDialog.show()
            wait(app)
            assert plcDialog.grab().save(str(args.output / "managed-plc-diagnostics.png"))
            plcDialog.reject()
            imvDefinition = imv.validateDefinition(DeviceDefinition(name="IMV / 离线示例", deviceTypeId=imv.deviceTypeId,
                driverVersion=imv.version, connection={"cameraKey": "OFFLINE-ONLY"}))
            from emo_master.core.project.snapshots import revisionOf
            client.getDeviceStates = lambda project: ("visual-only", {"imv": dict(generation="offline",
                definitionRevision=revisionOf(imvDefinition.model_dump()))})
            import cv2
            import numpy as np
            sample = np.zeros((320, 480, 3), dtype=np.uint8)
            sample[:, :, 1] = np.arange(480, dtype=np.uint16) % 256
            sample[60:260, 100:380] = (180, 90, 45)
            ok, jpeg = cv2.imencode(".jpg", sample)
            assert ok
            def diagnostic(*values, **keywords):
                captured = values[4] == "capture"
                return dict(sessionId="offline", metadata=dict(cycleId="software-fixture", acquisition=dict(blockId=42,
                    actualExposureUs=10000)), preview=jpeg.tobytes() if captured else b"")
            client.cameraDiagnostic = diagnostic
            cameraDialog = ManagedCameraDebugDialog(client, payload["project"]["projectId"], "imv", imvDefinition, window)
            cameraDialog.resize(min(args.width, 800), min(args.height, 600))
            cameraDialog.show()
            wait(app, lambda: cameraDialog.pending is None, 5)
            originalQuestion = QMessageBox.question
            QMessageBox.question = lambda *args, **kwargs: QMessageBox.Yes
            try:
                cameraDialog.capture()
                wait(app, lambda: cameraDialog.pending is None, 5)
            finally:
                QMessageBox.question = originalQuestion
            wait(app)
            assert cameraDialog.preview.pixmap() is not None
            assert cameraDialog.grab().save(str(args.output / "managed-camera-diagnostics.png"))
            cameraDialog.reject()
            evidence = dict(platform=app.platformName(), requested=[args.width, args.height],
                            actual=[window.width(), window.height()], devicePixelRatio=window.devicePixelRatioF(),
                            scope="native Qt source rendering, stub metadata, no hardware, not manual acceptance")
            evidence["baseHead"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
            evidence["sourceSha256"] = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (ROOT / "src/emo_master/apps/designer/ui/device_workspace.py",
                             ROOT / "src/emo_master/apps/designer/ui/device_wizard.py",
                             ROOT / "src/emo_master/apps/designer/ui/managed_plc_debug.py",
                             ROOT / "src/emo_master/apps/runtime/devices/slmp.py",
                             ROOT / "src/emo_master/apps/runtime/devices/slmp_debug.py",
                             ROOT / "src/emo_master/apps/designer/ui/managed_camera_debug.py",
                             ROOT / "src/emo_master/apps/runtime/devices/imv_host.py",
                             ROOT / "src/emo_master/apps/runtime/devices/imv_capture.py",
                             ROOT / "src/emo_master/apps/runtime/devices/imv_debug.py",
                             ROOT / "src/emo_master/apps/designer/ui/project_configuration.py",
                             ROOT / "src/emo_master/apps/designer/ui/workflow_package_preview_dialog.py",
                             ROOT / "src/emo_master/apps/designer/state/workflow_package.py",
                             ROOT / "src/emo_master/apps/designer/controllers/workflow_controller.py",
                             ROOT / "src/emo_master/apps/designer/ui/main_window.py",
                             ROOT / "src/emo_master/apps/designer/page_designer/coordinator.py",
                             ROOT / "scripts/validate_device_workspace.py", metadata, plcMetadata, imvMetadata)}
            (args.output / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
            dialog.reject()
            c.session.markSaved()
        finally:
            QMessageBox.question = lambda *args, **kwargs: QMessageBox.Discard
            window.close()
            wait(app)


if __name__ == "__main__":
    main()
