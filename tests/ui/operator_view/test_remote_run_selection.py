"""Native operator actions observe and stop explicit remote Runtime runs."""
import grpc

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.operator_runtime.main import OperatorWindow
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from tests.runtime.current_production_fixture import currentProductionProject
from tests.runtime.test_initial_project_configuration import waitForWrite
from tests.ui.operator_view.test_production_window import closeWindow, preferences
from tests.ui.operator_view.test_view import waitFor


def testRemoteFirstImportAppearsWithoutAutostart(qtApp, tmp_path):
    path, document = currentProductionProject(tmp_path / 'engineering', declared=True, autoStart=True)
    window = OperatorWindow(dataRoot=tmp_path / 'runtime', preferences=preferences(tmp_path))
    window.show()
    channel = client = None
    try:
        waitFor(lambda: window.controller is not None and not window.busy)
        assert window.controller.document is None
        address = window.controller.address
        channel = grpc.insecure_channel(address)
        grpc.channel_ready_future(channel).result(timeout=5)
        client = RuntimeClient(rpc.RuntimeServiceStub(channel), runtimeTarget='192.0.2.20:50051')
        payload = document.model_dump(mode='json')
        staged = client.prepareProjectImport(payload, 'first-project')
        uploaded = client.uploadInitialProjectResources(payload, path.parent)
        assert not uploaded['errors']
        checked = client.initializeProjectConfiguration(payload, None, staged['importId'], validate=True)
        client.initializeProjectConfiguration(payload, None, checked['importId'],
            runtimeInstanceId=checked['runtimeInstanceId'])
        assert waitForWrite(window.controller.runtime, client, checked['importId'])['state'] == 'COMMITTED'
        waitFor(lambda: window.pages is not None and window.projectLabel.text() == document.project.name)
        assert window.selectedWorkflowIds == ['main'] and window.startButton.isEnabled()
        assert not window.controller.runtime.jobRepository.all()
    finally:
        if client:
            client.close()
        if channel:
            channel.close()
        closeWindow(window)
