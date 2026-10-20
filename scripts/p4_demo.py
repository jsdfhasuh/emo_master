"""Open the existing Designer with the P4 development workspace and safe local flow."""
import argparse
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT)]
import emo_master  # noqa: E402,F401


def main():
    from PySide2.QtWidgets import QApplication
    from emo_master.apps.designer.main import applyDesignerStyle
    from emo_master.apps.designer.services.runtime_client import RuntimeClient
    from emo_master.apps.designer.ui.main_window import MainWindow
    from emo_master.apps.designer.state.project_store import saveProject
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from examples.runtime_pages_p2 import sampleProject
    from emo_master.core.contracts.port_types import normalizePortType
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, help='Persistent local project; omitted: temporary blank-page builtin flow')
    args = parser.parse_args()
    os.environ['EMO_PAGE_DESIGNER'] = '1'
    app = QApplication.instance() or QApplication([])
    applyDesignerStyle(app)
    with tempfile.TemporaryDirectory(prefix='emo-p4-designer-') as directory:
        root = Path(directory)
        runtime = RuntimeService(dbPath=root/'runtime.sqlite3', workspaceRoot=root/'jobs')
        project = args.project
        if project is None:
            project = root/'project'
            project.mkdir()
            payload = sampleProject(project).model_dump()
            payload['schemaVersion'] = '2.1'
            payload.pop('presentation')
            payload.pop('resources')
            payload['workflows']['main']['nodes'][1]['params']['imagePath'] = str(project/'input.png')
            for node in payload['workflows']['main']['nodes']:
                descriptor = runtime.pluginScanResult.activeOperators.get(node.get('operatorId'))
                if descriptor:
                    for field in ('inputPorts', 'outputPorts'):
                        node[field] = {key: normalizePortType(spec) for key, spec in getattr(descriptor.manifest, field).items()}
            saveProject(project, payload)
        client = RuntimeClient(runtime)
        window = MainWindow(client)
        try:
            if not window.loadProjectDirectory(str(project)):
                raise RuntimeError('project could not load')
            window.show()
            return app.exec_()
        finally:
            window.shutdownOperatorDisplay()
            client.close()
            runtime.close()


if __name__ == '__main__':
    multiprocessing.freeze_support()
    raise SystemExit(main())
