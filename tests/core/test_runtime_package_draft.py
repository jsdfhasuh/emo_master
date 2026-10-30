"""Package a current in-memory draft against the original resource directory."""
import base64
import json
import zipfile

import pytest

from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.runtime_package import buildRuntimePackage, installRuntimePackage


def testAuthoritativeDraftIsCopiedBeforeMakingInputPathsPortable(tmp_path):
    root = tmp_path / 'engineering'
    document = ProjectDocument.model_validate(WorkflowStore().toPayload())
    saveProject(root, document.model_dump(mode='json'))
    original = (root / 'project.json').read_bytes()
    image = tmp_path / 'input.png'
    image.write_bytes(base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII='))
    payload = document.model_dump(mode='json')
    payload['workflows']['main']['nodes'].insert(1, dict(
        nodeId='reader', operatorId='vision.io.image_loader', params={'imagePath': str(image)}))
    draft = ProjectDocument.model_validate(payload)
    before = draft.model_dump(mode='json')
    package = buildRuntimePackage(root, tmp_path / 'packages', document=draft)
    assert draft.model_dump(mode='json') == before
    assert (root / 'project.json').read_bytes() == original
    with zipfile.ZipFile(package) as archive:
        exported = json.loads(archive.read('project.json'))
        reader = next(node for node in exported['workflows']['main']['nodes'] if node['nodeId'] == 'reader')
        assert reader['params']['imagePath'].startswith('runtime-assets/')
        assert archive.read(reader['params']['imagePath']) == image.read_bytes()
    installed = installRuntimePackage(package, tmp_path / 'installed')
    assert installed.schemaVersion == '2.8' and installed.presentation.schemaVersion == '1.1'
    assert installed.devices.schemaVersion == '1.0'
    with pytest.raises(ValueError, match='outside'):
        buildRuntimePackage(root, root / 'packages', document=draft)
