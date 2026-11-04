"""Local software Runtime: exact Qt/external budget rejection, no detection."""
import json
from pathlib import Path
import sys
import tempfile

from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from tests.runtime.presentation.test_equivalent_image_capture import imageProject, imageRequests
from tests.runtime.presentation.test_external_output_requests import request


with tempfile.TemporaryDirectory(prefix='emo-web-quota-round3-') as directory:
    root = Path(directory)
    runtime = RuntimeService(dbPath=root / 'runtime.sqlite3', workspaceRoot=root / 'jobs')
    try:
        registry = runtime.pluginScanResult.activeOperators
        path, document = imageProject(root / 'project', registry)
        assert runtime.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
        equivalent = runtime.ValidateOutputRequests(request(runtime, imageRequests(document, registry)), None)
        rejected = runtime.ValidateOutputRequests(request(runtime, imageRequests(document, registry, third=True)), None)
        state = json.loads(rejected.state_json)
        report = json.dumps(dict(schemaVersion=document.schemaVersion,
            presentationSchemaVersion=document.presentation.schemaVersion,
            devicesSchemaVersion=document.devices.schemaVersion,
            equivalentTwoImagesAccepted=equivalent.ok,
            rejected=dict(ok=rejected.ok, code=rejected.code, message=rejected.message,
                          diagnostics=state.get('diagnostics', [])),
            savedSources=state['config']['sources'],
            boundary='local software Runtime direct RPC methods; no Start/Stop, browser, Qt UI, package install or device'),
            ensure_ascii=False, indent=2)
        if len(sys.argv) > 1:
            Path(sys.argv[1]).write_text(report + '\n', encoding='utf-8')
        else:
            print(report)
    finally:
        runtime.close()
