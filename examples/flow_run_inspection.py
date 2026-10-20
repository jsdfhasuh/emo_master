"""Isolated local-image workflow for node inspection; no device or page session."""
from pathlib import Path

import cv2
import numpy as np

from emo_master.core.project.models import ProjectDocument
from emo_master.core.contracts.port_types import normalizePortType
from emo_master.plugins.builtins.blob_analysis.operator import BlobAnalysisOperator
from emo_master.plugins.builtins.collection_count.operator import CollectionCountOperator
from emo_master.plugins.builtins.image_loader.operator import ImageLoaderOperator
from emo_master.plugins.builtins.image_saver.operator import ImageSaverOperator
from emo_master.plugins.builtins.number_compare.operator import NumberCompareOperator


def sampleProject(root: Path) -> ProjectDocument:
    root.mkdir(parents=True, exist_ok=True)
    pixels = np.zeros((240, 360, 3), np.uint8)
    pixels[40:100, 40:120] = 255
    pixels[140:210, 220:300] = 255
    # NumPy owns Windows Unicode-path I/O; only this temporary fixture is written.
    ok, image = cv2.imencode('.png', pixels)
    if not ok:
        raise RuntimeError('test image encoding failed')
    image.tofile(root / 'input.png')
    document = ProjectDocument.model_validate({
        'schemaVersion': '2.1',
        'project': {'projectId': 'flow-inspection-local-fixture', 'name': '流程运行检查 · 本地图像',
                    'createdAt': '2026-10-04T00:00:00Z', 'updatedAt': '2026-10-04T00:00:00Z'},
        'entryWorkflowId': 'main', 'workflowOrder': ['main'],
        'workflows': {'main': {'name': '本地图像检测', 'nodes': [
            {'nodeId': 'input', 'kind': 'workflow_input'},
            {'nodeId': 'load', 'operatorId': 'vision.io.image_loader',
             'params': {'imagePath': str(root / 'input.png')}},
            {'nodeId': 'blob', 'operatorId': 'vision.analysis.blob',
             'params': {'drawOverlay': True, 'includeContour': False}},
            {'nodeId': 'count', 'operatorId': 'vision.collection.count'},
            {'nodeId': 'presence', 'operatorId': 'vision.compare.number',
             'params': {'operator': 'gt', 'rightValue': 0.0}},
            {'nodeId': 'save', 'operatorId': 'vision.io.image_saver',
             'params': {'outputPath': str(root / 'result.png')}},
            {'nodeId': 'output', 'kind': 'workflow_output'},
        ], 'edges': [
            {'fromNode': 'load', 'fromPort': 'image', 'toNode': 'blob', 'toPort': 'image'},
            {'fromNode': 'load', 'fromPort': 'frame', 'toNode': 'blob', 'toPort': 'frame'},
            {'fromNode': 'blob', 'fromPort': 'blobs', 'toNode': 'count', 'toPort': 'blobs'},
            {'fromNode': 'count', 'fromPort': 'count', 'toNode': 'presence', 'toPort': 'left'},
            {'fromNode': 'blob', 'fromPort': 'overlay', 'toNode': 'save', 'toPort': 'image'},
        ]}},
    })
    registry = {kind.meta.operatorId: kind.meta for kind in (ImageLoaderOperator, BlobAnalysisOperator,
        CollectionCountOperator, NumberCompareOperator, ImageSaverOperator)}
    labels = {'load': '图像输入', 'blob': 'Blob 分析', 'count': '集合计数',
              'presence': '数量判定', 'save': '保存结果图'}
    for node in document.workflows['main'].nodes:
        if node.operatorId:
            meta = registry[node.operatorId]
            node.displayName = labels[node.nodeId]
            node.inputPorts = {name: normalizePortType(spec) for name, spec in meta.inputPorts.items()}
            node.outputPorts = {name: normalizePortType(spec) for name, spec in meta.outputPorts.items()}
            node.paramSchema = meta.paramSchema
    return document
