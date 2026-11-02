"""Current-schema local-image projects; no hardware or legacy templates."""
import hashlib

import cv2
import numpy as np

from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.runtime_directory import prepareRuntimeProject
from emo_master.core.project.test_delivery import trustedRegistry
from emo_master.core.workflow.compiler import WorkflowCompiler


def imageBytes(value):
    ok, encoded = cv2.imencode('.png', np.full((24, 32, 3), value, np.uint8))
    assert ok
    return encoded.tobytes()


def currentProductionProject(root, *, mode='single', autoStart=False, projectId=None, value=37, declared=False):
    root.mkdir(parents=True, exist_ok=True)
    data = imageBytes(value)
    (root / 'input.png').write_bytes(data)
    payload = WorkflowStore().toPayload(root.name)
    assert payload['schemaVersion'] == '2.9'
    if projectId is not None:
        payload['project']['projectId'] = projectId
    payload['production'].update(mode=mode, autoStart=autoStart)
    payload['workflows']['main']['nodes'][1:1] = [
        dict(nodeId='load', operatorId='vision.io.image_loader', params=dict(imagePath='input.png')),
        dict(nodeId='save', operatorId='vision.io.image_saver', params=dict(outputPath='outputs/result.png')),
    ]
    payload['workflows']['main']['edges'] = [
        dict(fromNode='load', fromPort='image', toNode='save', toPort='image')]
    registry = trustedRegistry()
    payload['dependencies']['operators'] = [dict(operatorId=key, version=registry[key].manifest.version)
        for key in ('vision.io.image_loader', 'vision.io.image_saver')]
    payload['presentation'].update(defaultPageId='main', pageOrder=['main'],
        resultScopes={'root': dict(entryWorkflowId='main', scopeWorkflowId='main')},
        dataSources={'image': dict(kind='node_output', resultScopeId='root', workflowId='main',
                                  nodeId='load', port='image', expectedType='image')},
        pages={'main': dict(name='Image', components=[
            dict(componentId='image', type='image', bindings={'image': 'image'})])})
    if declared:
        payload['resources']['items']['input'] = dict(path='input.png', sha256=hashlib.sha256(data).hexdigest(),
                                                     size=len(data), purpose='input_image')
        payload['resources']['parameterBindings'] = [dict(resourceId='input', target=dict(
            workflowId='main', nodeId='load', parameterPath=['imagePath']))]
    document = ProjectDocument.model_validate(payload)
    path = root / 'inspection.emoproj'
    saveProject(path, document.model_dump(mode='json'))
    assert loadProjectDocument(path) == document
    WorkflowCompiler(registry).compile(prepareRuntimeProject(document, root, registry))
    return path, document


def node(document, nodeId):
    return next(item for item in document.workflows['main'].nodes if item.nodeId == nodeId)


def savedPixels(path):
    return cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
