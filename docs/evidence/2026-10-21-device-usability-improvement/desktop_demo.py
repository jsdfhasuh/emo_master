"""Prepare an isolated native desktop fixture; all walkthrough input uses sky."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import socket
import time

from PySide2.QtCore import QSettings
from PySide2.QtWidgets import QApplication

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.state.device_package import exportDevices, importDevices
from emo_master.apps.designer.state.schema_utils import applySchemaDefaults
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.apps.designer.ui.theme import configureTheme
from emo_master.apps.runtime.grpc_server.service import RuntimeService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--extra-copies', type=int, default=0)
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    app = QApplication([])
    configureTheme(app)
    runtime = RuntimeService(dbPath=root/'runtime/runtime.db', workspaceRoot=root/'runtime/jobs')
    client = RuntimeClient(runtimeService=runtime)
    types = runtime.pluginScanResult.deviceTypes
    def definition(typeId, name, **connection):
        decl = types[typeId]
        return dict(deviceTypeId=typeId, driverVersion=decl.version, name=name, enabled=True,
            connection=applySchemaDefaults(decl.connectionSchema, connection),
            defaults=applySchemaDefaults(decl.defaultsSchema, {}), policies=applySchemaDefaults(decl.policiesSchema, {}))
    online = WorkflowStore().toPayload()
    camera = definition('device.simulated.camera', '桌面模拟相机', serial='DESKTOP-SIM-ONLY')
    camera['defaults'].update(width=8, height=6, exposureUs=1000)
    with socket.socket() as endpoint:
        endpoint.bind(('127.0.0.1', 0))
        port = endpoint.getsockname()[1]
    server = definition('device.tcp.server', '待配置 TCP Server', port=port, channels=[])
    server['enabled'] = False
    online['devices']['definitions'] = dict(camera=camera, server=server)
    manifest = runtime.pluginScanResult.activeOperators['vision.io.simulated_camera'].manifest
    def ports(values):
        return {key: value if isinstance(value, str) else value['type'] for key, value in values.items()}
    capture = dict(nodeId='capture', operatorId=manifest.operatorId, displayName=manifest.displayName,
        deviceBindings={'camera': {'deviceId': 'camera'}},
        inputPorts=ports(manifest.inputPorts), outputPorts=ports(manifest.outputPorts), paramSchema=manifest.paramSchema)
    online['workflows']['main']['nodes'].append(capture)
    onlinePath = root/'online.emoproj'
    onlinePath.write_text(json.dumps(online, ensure_ascii=False), encoding='utf-8')
    loaded = client.loadProject(str(onlinePath))
    assert loaded.ok, loaded
    job = client.startJob(runtime.loadedProjectId, 'main')
    assert job.ok, job
    end = time.monotonic()+20
    while time.monotonic() < end:
        state = client.getJobStatus(job.job_id)
        if state.status in {'COMPLETED', 'FAILED', 'ABORTED'}:
            break
        time.sleep(.02)
    assert state.status == 'COMPLETED', state
    source = root/'desktop-draft.emoproj'
    source.write_text(json.dumps(online, ensure_ascii=False), encoding='utf-8')
    window = MainWindow(client, settingsStore=QSettings(str(root/'settings.ini'), QSettings.IniFormat))
    assert window.loadProjectDirectory(str(source), confirmed=True)
    c = window.pageCoordinator
    local = c.session.payload()
    pending = deepcopy(capture)
    pending.update(nodeId='pending-camera', displayName='稍后绑定的模拟相机', deviceBindings={})
    local['workflows']['main']['nodes'].append(pending)
    local['workflows']['main']['layout']['nodePositions']['pending-camera'] = dict(x=400., y=250.)
    with c.session.transaction():
        c.session._restore(local)
    package = exportDevices(c.session, ['camera'], root)
    importDevices(c.session, package, types, root)
    importDevices(c.session, package, types, root)
    for _ in range(args.extra_copies):
        importDevices(c.session, package, types, root)
    window.workflowController._renderActive()
    assert window.saveProjectToDirectory(str(source))
    title = '易用性验收 ' + args.source_sha[:7]
    window.setWindowTitle('EmoMaster · ' + title)
    window.resize(1280, 840)
    window.show()
    window.appendRuntimeLog('INFO', f'隔离模拟相机 Job 已完成：{job.job_id}；8×6 图像；无真实硬件')
    c.showDevices()
    c.devices.setWindowTitle('设备管理 · ' + title)
    c.devices.resize(1200, 760)
    import emo_master
    (root/'desktop-environment.json').write_text(json.dumps(dict(sourceSha=args.source_sha,
        importPath=emo_master.__file__, project=str(source), runtimeId=runtime.runtimeInstanceId,
        jobId=job.job_id, jobStatus=state.status), ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        return app.exec_()
    finally:
        client.close()
        runtime.close()


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
