"""Build the editable, device-free page composition example and its Web package."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3

import cv2
import numpy as np
from onnx import TensorProto, checker, helper, numpy_helper

from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.runtime.business_sqlite.backend import initialize
from emo_master.core.contracts.port_types import normalizePortType
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.runtime_directory import prepareRuntimeProject
from emo_master.core.project.test_delivery import trustedRegistry
from emo_master.core.project.web_panel import buildWebPanel, readWebPanel
from emo_master.core.workflow.compiler import WorkflowCompiler


PROJECT = 'inspection.emoproj'
PANEL = 'inspection.emoweb'
DATABASE = 'archive/history.sqlite3'
HISTORY = 'history-records'
SCOPE = 'inspection-result'
WIDTH, HEIGHT = 756, 600
CLASSES = {
    'upper': {'displayLabel': '上端', 'color': [81, 209, 58]},
    'lower': {'displayLabel': '下端', 'color': [85, 103, 244]},
    'target': {'displayLabel': '定位点', 'color': [29, 207, 249]},
}
RESULT_STATES = {
    '1': dict(text='通过', color='green', background='soft'),
    '2': dict(text='需复核', color='amber', background='soft'),
    '3': dict(text='超出范围', color='red', background='soft'),
}
BOOLEAN_STATES = {
    'true': dict(text='符合', color='green', background='soft'),
    'false': dict(text='偏离', color='red', background='soft'),
}


def saveImage(path, image):
    success, buffer = cv2.imencode(path.suffix, image)
    if not success:
        raise ValueError(f'Cannot encode {path}')
    path.write_bytes(buffer.tobytes())


def syntheticImage(variant):
    """An artificial grayscale inspection target with a textured surface."""
    yy, xx = np.indices((HEIGHT, WIDTH))
    noise = np.random.default_rng(41 + variant).normal(0, 1.6, (HEIGHT, WIDTH))
    angle = np.deg2rad(24)
    x = (xx - 371) * np.cos(angle) + (yy - 294) * np.sin(angle)
    y = -(xx - 371) * np.sin(angle) + (yy - 294) * np.cos(angle)
    dist = (np.maximum(np.abs(x) - 92, 0) / 109) ** 2 + (y / (165 + 0.19 * x)) ** 2
    ring = np.abs(dist - 1.0)
    base = 24 + 14 * np.exp(-((xx - 360) ** 2 + (yy - 300) ** 2) / 113000) + noise
    base += 3.3 * np.sin(xx * 0.26 + yy * 0.008)
    polished = 96 + variant * 10 + 23 * np.cos((x + 68) / 114) + 9 * np.sin(y / 13)
    polished += 24 * np.exp(-((y + 78) / 36) ** 2) + 24 * np.exp(-ring * 32) + noise
    image = np.where(dist < 1, polished, base + 12 * np.exp(-ring * 35))
    for cx, cy in ((-138, -29), (83, 24), (116, 72)):
        feature = np.hypot(x - cx, (y - cy) * 1.16)
        image += (dist < 1) * (19 * np.exp(-((feature - 16) / 3) ** 2) - 40 * np.exp(-(feature / 12) ** 2))
    image = np.uint8(np.clip(image, 0, 255))
    cv2.putText(image, f'SAMPLE {variant}', (25, HEIGHT - 20), cv2.FONT_HERSHEY_SIMPLEX,
                0.7, 164, 1, cv2.LINE_AA)
    return image


def makeAssets(root):
    assets = root / 'assets'
    assets.mkdir(parents=True, exist_ok=True)
    saveImage(assets / 'sample-a.jpg', syntheticImage(1))
    saveImage(assets / 'sample-b.jpg', syntheticImage(2))

    # The fixture emits declared boxes in YOLO tensor form. It is never a trained model.
    predictions = np.array([
        [222, 206, 114, 88, .99, .01, .01],
        [402, 282, 142, 95, .01, .97, .01],
        [522, 357, 126, 105, .01, .01, .96],
    ], dtype=np.float32).T[None, ...]
    tensor = numpy_helper.from_array(predictions)
    constant = helper.make_node('Constant', [], ['predictions'], value=tensor)
    graph = helper.make_graph([constant], 'simulated_inspection',
        [helper.make_tensor_value_info('image', TensorProto.FLOAT, [1, 3, HEIGHT, WIDTH])],
        [helper.make_tensor_value_info('predictions', TensorProto.FLOAT, list(predictions.shape))])
    model = helper.make_model(graph, producer_name='emo_master sample fixture', ir_version=8,
                              opset_imports=[helper.make_opsetid('', 13)])
    helper.set_model_props(model, {'names': str(dict(enumerate(CLASSES))), 'task': 'detect'})
    checker.check_model(model)
    (assets / 'synthetic.onnx').write_bytes(model.SerializeToString())

    logo = np.zeros((96, 96, 4), np.uint8)
    cv2.rectangle(logo, (9, 9), (86, 86), (208, 97, 32, 255), -1, cv2.LINE_AA)
    cv2.circle(logo, (48, 48), 23, (255, 255, 255, 255), 4, cv2.LINE_AA)
    cv2.line(logo, (19, 48), (77, 48), (255, 255, 255, 255), 3, cv2.LINE_AA)
    cv2.circle(logo, (48, 48), 6, (255, 255, 255, 255), -1, cv2.LINE_AA)
    saveImage(assets / 'logo.png', logo)
    return {
        'sample-a': ('assets/sample-a.jpg', 'input_image', ('input-a', 'imagePath')),
        'sample-b': ('assets/sample-b.jpg', 'input_image', ('input-b', 'imagePath')),
        'synthetic-model': ('assets/synthetic.onnx', 'model', ('detect', 'modelPath')),
        'logo': ('assets/logo.png', 'icon', None),
    }


def mapping(column, storageType, node=None, port=None, *, context=None, constant=None):
    if node is not None:
        source = dict(kind='node_output', nodeId=node, port=port)
    elif context is not None:
        source = dict(kind='context', key=context)
    else:
        source = dict(kind='constant', value=constant)
    return dict(column=column, storageType=storageType, source=source)


def configureWorkflow(payload, registry, root):
    main = payload['workflows']['main']
    main['name'] = '软件模拟检测与归档'
    locations = main['layout']['nodePositions']
    used = {}

    def node(key, operatorId, title, x, y, **params):
        manifest = registry[operatorId].manifest
        used[operatorId] = manifest.version
        locations[key] = dict(x=x, y=y)
        main['nodes'].insert(-1, dict(nodeId=key, operatorId=operatorId, displayName=title, params=params,
            inputPorts={port: normalizePortType(spec) for port, spec in manifest.inputPorts.items()},
            outputPorts={port: normalizePortType(spec) for port, spec in manifest.outputPorts.items()},
            paramSchema=manifest.paramSchema))

    def edge(source, sourcePort, target, targetPort):
        main['edges'].append(dict(fromNode=source, fromPort=sourcePort, toNode=target, toPort=targetPort))

    def loader(key, asset, title, x, y):
        node(key, 'vision.io.image_loader', title, x, y, imagePath=asset, colorMode='grayscale')

    node('enable', 'vision.value.number', '模拟采样触发', 20, 20, value=1)
    node('trigger', 'vision.compare.number', '采样允许', 255, 20, operator='eq', rightValue=1)
    node('sequence', 'vision.state.counter', '采样序号', 490, 20, name='simulated-inspection-samples')
    node('first-batch', 'vision.compare.number', '首批样本', 725, 20, operator='lte', rightValue=3)
    node('in-range', 'vision.compare.number', '模拟方向判定', 725, -174, operator='lte', rightValue=5)
    loader('input-a', 'assets/sample-a.jpg', '模拟工件 A', 490, 200)
    loader('input-b', 'assets/sample-b.jpg', '模拟工件 B', 490, 374)
    node('sample', 'vision.flow.select_value', '选择模拟工件', 960, 202)
    node('detect', 'vision.inference.yolo', '模拟位置信息', 1200, 202,
         modelPath='assets/synthetic.onnx', drawOverlay=False, outputCrop=True, cropTargetIndex=2)
    for label, title, y in (('upper', '上端', 470), ('lower', '下端', 690)):
        node(f'{label}-filter', 'vision.collection.filter', f'筛选{title}类别', 1440, y,
             collectionType='detections', detectionLabelsEnabled=True, detectionLabels=[label])
        node(f'{label}-select', 'vision.collection.select', f'选择{title}目标', 1680, y,
             collectionType='detections', mode='first')
        node(f'{label}-bbox', 'vision.geometry.detection_bbox', f'提取{title}区域', 1920, y)
        node(f'{label}-center', 'vision.geometry.extract_points', f'提取{title}中心', 2160, y,
             inputType='bbox', feature='bboxCenter')
        edge('detect', 'detections', f'{label}-filter', 'detections')
        edge(f'{label}-filter', 'keptDetections', f'{label}-select', 'detections')
        edge(f'{label}-select', 'selectedDetections', f'{label}-bbox', 'detections')
        edge(f'{label}-bbox', 'bbox', f'{label}-center', 'bbox')
    node('line', 'vision.geometry.line_from_points', '连接本次两个中心', 2400, 470)
    node('annotate', 'vision.render.annotate', '标注检测证据', 2640, 202,
         classStyles=CLASSES, labelBackground=True, showConfidence=False, colorMode='bgr',
         lineColor=[255, 255, 255], lineStyle='dashed', endpointMarker='dot', thickness=3, fontScale=.67)
    node('class-count', 'vision.collection.count', '定位数量', 1440, 20, collectionType='detections')
    node('save-main', 'vision.io.image_saver', '归档主图', 2880, 202,
         outputPath='archive/images/main.png', namingMode='unique')
    node('save-local', 'vision.io.image_saver', '归档局部图', 2880, 392,
         outputPath='archive/images/detail.png', namingMode='unique')

    node('code-pass', 'vision.math.integer_add', '模拟结果 1', 880, -340, leftValue=1)
    node('code-review', 'vision.math.integer_add', '模拟结果 2', 880, -510, leftValue=2)
    node('code-out', 'vision.math.integer_add', '模拟结果 3', 880, -680, leftValue=3)
    node('early-code', 'vision.flow.select_value', '正常与复核', 1120, -340)
    node('choose-code', 'vision.flow.select_value', '选择模拟结果', 1360, -340)
    node('result-code', 'vision.math.exact_integer', '输出结果码', 1600, -340)
    node('angle-scale', 'vision.value.number', '模拟角度分母', 1600, -510, value=20)
    node('angle', 'vision.math.percentage', '模拟角度偏差', 1835, -340)
    node('write', 'vision.io.sqlite_writer', '保存本次检测记录', 3120, 202,
         configVersion=1, databasePath=DATABASE, table='records', failurePolicy='stop',
         fileReferencePathMode='project_relative', mappings=[
             mapping('sequence', 'INTEGER', 'sequence', 'count'),
             mapping('result_code', 'INTEGER', 'result-code', 'value'),
             mapping('angle', 'REAL', 'angle', 'value'),
             mapping('targets', 'INTEGER', 'class-count', 'count'),
             mapping('main_path', 'FILE_REFERENCE', 'save-main', 'result'),
             mapping('detail_path', 'FILE_REFERENCE', 'save-local', 'result'),
             mapping('sampled_at', 'UTC_TIME', context='timestampUtc'),
             mapping('workflow_run_id', 'TEXT', context='workflowRunId'),
             mapping('review_note', 'TEXT', constant=None),
             mapping('record_log', 'TEXT', constant='[模拟输入] 从受管样例读取图像。\n[标注] 按本次检测选择两个目标中心并绘制连接虚线。\n[归档] 主图和局部图均通过 ImageSaver 写入，随后提交本条记录。'),
         ])
    for connection in (
        ('enable', 'value', 'trigger', 'left'), ('trigger', 'result', 'sequence', 'increment'),
        ('sequence', 'count', 'first-batch', 'left'), ('sequence', 'count', 'in-range', 'left'),
        ('first-batch', 'result', 'sample', 'condition'), ('input-a', 'image', 'sample', 'trueValue'),
        ('input-b', 'image', 'sample', 'falseValue'), ('sample', 'value', 'detect', 'image'),
        ('sample', 'value', 'annotate', 'image'), ('detect', 'frame', 'annotate', 'frame'),
        ('detect', 'frame', 'line', 'frame'), ('upper-center', 'points', 'line', 'startPoints'),
        ('lower-center', 'points', 'line', 'endPoints'),
        ('detect', 'detections', 'annotate', 'detections'), ('line', 'lines', 'annotate', 'lines'),
        ('detect', 'detections', 'class-count', 'detections'), ('detect', 'croppedImage', 'save-local', 'image'),
        ('annotate', 'overlay', 'save-main', 'image'),
        ('first-batch', 'result', 'early-code', 'condition'), ('code-pass', 'value', 'early-code', 'trueValue'),
        ('code-review', 'value', 'early-code', 'falseValue'), ('in-range', 'result', 'choose-code', 'condition'),
        ('early-code', 'value', 'choose-code', 'trueValue'), ('code-out', 'value', 'choose-code', 'falseValue'),
        ('choose-code', 'value', 'result-code', 'value'), ('result-code', 'value', 'angle', 'numerator'),
        ('angle-scale', 'value', 'angle', 'denominator'),
    ):
        edge(*connection)
    locations['__workflow_input__'] = dict(x=-236, y=20)
    locations['__workflow_output__'] = dict(x=3355, y=202)
    payload['production'].update(mode='single', autoStart=False)
    payload['dependencies']['operators'] = [dict(operatorId=key, version=version) for key, version in sorted(used.items())]
    database = root / DATABASE
    database.parent.mkdir(parents=True, exist_ok=True)
    columns = [dict(name=row['column'], storageType=row['storageType'], nullable=row['column'] == 'review_note')
               for row in next(n['params']['mappings'] for n in main['nodes'] if n['nodeId'] == 'write')]
    initialize(database, 'records', columns)
    with closing(sqlite3.connect(database)) as connection:
        with connection:
            connection.execute('''CREATE VIEW IF NOT EXISTS history_records AS
                SELECT *, substr(sampled_at,1,10) AS sample_date, substr(sampled_at,12,8) AS sample_time
                FROM records''')


def typography(size=16, *, color='default', weight='normal', role='value'):
    return {role: dict(size=size, weight=weight, color=color)}


def padding(vertical, horizontal=None):
    if horizontal is None:
        horizontal = vertical
    return dict(top=vertical, bottom=vertical, left=horizontal, right=horizontal)


def component(key, kind, *, source=None, responsive=None, **props):
    result = dict(componentId=key, type=kind,
                  responsive=dict(minWidth=0, minHeight=0, preferredWidth=0, preferredHeight=0) | (responsive or {}),
                  props=props)
    if source:
        result['bindings'] = {'image' if kind == 'image' else 'value': source}
    return result


def text(key, value='', *, size=16, color='default', bold=False, compact=False, **props):
    return component(key, 'text', text=value, typography=typography(size, color=color, weight='bold' if bold else 'normal'),
                     responsive=dict(width='content') if compact else None, **props)


def button(key, title, action, *, style='secondary', **target):
    return component(key, 'navigation_button', title=title, buttonStyle=style,
                     responsive=dict(width='content', minHeight=36)) | dict(actions={'clicked': dict(type=action, **target)})


def group(key, children, *, direction='vertical', justify='start', alignment='stretch', space=12, card=False,
          inset=None, responsive=None, background=None, **props):
    surface = dict(kind='card' if card else 'none')
    if inset is not None:
        surface['padding'] = inset
    if background:
        surface['background'] = background
        surface['radius'] = 8
    return component(key, 'container', responsive=responsive, surface=surface, **props) | dict(
        children=children, grid=dict(type='responsive', direction=direction, justify=justify, alignment=alignment, spacing=space))


def field(fieldName):
    return dict(componentId=HISTORY, kind='field', field=fieldName)


def historicalImage(key, column, aspect):
    return component(key, 'image', imageView=dict(sizing='aspect', aspectRatio=aspect, background='dark'),
        imagePath=dict(kind='selected_row', componentId=HISTORY, field=column, baseDirectory='.'),
        emptyText='等待选定记录')


def productionPage():
    status = group('production-state', [
        component('live-job', 'runtime_status', statusField='job_state', title='作业状态',
                  responsive=dict(width='content', minHeight=28)),
        component('sample-no', 'number', source='sequence', title='本次采样', decimals=0,
                  valueLayout='horizontal', responsive=dict(width='content', minWidth=148)),
    ], direction='horizontal', justify='space-between', alignment='center', inset=padding(12, 16), background='subtle')
    actions = group('production-image-header', [
        text('production-image-title', '检测图像', size=20, bold=True, compact=True),
        group('production-image-controls', [
            button('freeze', '冻结画面', 'freeze', resultScopeId=SCOPE),
            button('resume', '恢复实时', 'resume_live', style='link', resultScopeId=SCOPE),
        ], direction='horizontal', alignment='center', responsive=dict(width='content'), space=8),
    ], direction='horizontal', justify='space-between', alignment='center', inset=padding(14, 20))
    legend = group('production-legend', [text('legend-upper', '● 上端', compact=True, size=14, color='green'),
        text('legend-lower', '● 下端', compact=True, size=14, color='red'),
        text('legend-target', '● 定位点', compact=True, size=14, color='amber')],
        direction='horizontal', alignment='center', space=12, responsive=dict(width='content'))
    image = group('production-image-card', [actions,
        component('main-image', 'image', source='overlay', imageView=dict(aspectRatio=1.45, background='dark'),
                  emptyText='启动模拟采样后显示检测图像'),
        group('production-image-footer', [legend, button('view-history', '查看历史记录', 'navigate', style='link', pageId='history')],
              direction='horizontal', alignment='center', justify='space-between', inset=padding(12, 20)),
    ], card=True, inset=padding(0), space=0, responsive=dict(minWidth=560, weight=2, alignSelf='start'))

    def indicator(key, title, source, states):
        return component(key, 'indicator', source=source, title=title, valueLayout='horizontal', indicatorView='row',
            titleFollowsState=True, markerPosition='title', showMarker=True,
            indicatorStates=states, typography=typography(20, weight='bold', role='title') | typography(16, weight='bold'),
            responsive=dict(minHeight=56), surface=dict(kind='none', background='subtle', radius=8, padding=padding(12)))

    result = group('production-results', [
        text('production-results-title', '检测结果', size=20, bold=True),
        indicator('result-direction', '方向判定', 'direction', BOOLEAN_STATES),
        indicator('result-batch', '样本分类', 'first-batch', {
            'true': dict(text='标准样本', color='blue', background='soft'),
            'false': dict(text='复核样本', color='amber', background='soft')}),
        indicator('result-overall', '综合判定', 'result-code', RESULT_STATES),
        component('main-value', 'number', source='angle', title='角度偏差', unit='°', decimals=1,
                  helperText='模拟测量值', valueLayout='horizontal',
                  typography=typography(53, color='primary', weight='bold') | typography(16, role='title')),
        component('production-write-status', 'indicator', source='archive-status', title='归档回执', indicatorView='row',
                  indicatorStates={'"COMMITTED"': dict(text='已写入', color='green', background='soft')}),
        text('production-request', source='request-id', title='本次请求', valueLayout='horizontal', size=12, color='neutral'),
        text('production-write-key', source='record-key', title='记录键', valueLayout='horizontal', size=12, color='neutral'),
        text('production-hint', '软件模拟 · 结果码及角度来自示例配置。', color='neutral', size=14,
             surface=dict(kind='none', background='subtle', radius=8, padding=padding(10))),
    ], card=True, inset=padding(18), responsive=dict(minWidth=320, weight=1, alignSelf='start'))
    return dict(name='生产监控', targets=['desktop', 'web'], layout=dict(type='responsive', spacing=16), components=[
        status, group('production-columns', [image, result], direction='horizontal', alignment='start', space=24),
        text('production-footer', '模拟工站   ·   每次启动写入一条独立检测记录', color='neutral', size=14),
    ])


def historyPage():
    historyActions = group('history-browse', [
        button('history-previous', '上一条', 'history_previous', componentId=HISTORY),
        button('history-next', '下一条', 'history_next', componentId=HISTORY),
        group('history-count', [
            component('history-position', 'number', recordValue=dict(componentId=HISTORY, kind='meta', meta='position'),
                      responsive=dict(width='content'), decimals=0),
            text('history-divider', '/', compact=True),
            component('history-total', 'number', recordValue=dict(componentId=HISTORY, kind='meta', meta='count'),
                      responsive=dict(width='content'), decimals=0),
        ], direction='horizontal', alignment='center', responsive=dict(width='content'), space=6),
        button('history-first', '回到最新记录', 'history_first', style='link', componentId=HISTORY),
    ], direction='wrap', alignment='center', inset=padding(10, 20), space=8)
    main = group('history-main-card', [
        group('history-main-heading', [text('history-main-title', '检测结果图', size=20, bold=True, compact=True),
              text('history-time', color='neutral', size=14, recordValue=field('sampled_at'), compact=True)],
              direction='horizontal', justify='space-between', alignment='center', inset=padding(14, 20)),
        historicalImage('history-main', 'main_path', 1.26), historyActions,
    ], card=True, inset=padding(0), space=0)
    left = group('history-left', [main,
        group('history-log', [text('history-log-content', size=14, recordValue=field('record_log'))],
              card=True, inset=padding(14, 20), title='原始详情与日志', collapse=dict(initiallyCollapsed=True)),
    ], space=20, responsive=dict(minWidth=520, weight=1.6, alignSelf='start'))

    columns = ['sample_time', 'sample_date', 'result_code', 'angle', 'targets', 'sequence',
               'review_note', 'sampled_at', 'write_id', 'record_log']
    records = component(HISTORY, 'sqlite_table', pageSize=3, responsive=dict(minHeight=180),
        sqlite=dict(databasePath=DATABASE, table='history_records', columns=columns, orderBy='id', descending=True,
                    recordKey='write_id', location='runtime'),
        historyView=dict(mode='list', titleField='sample_time', subtitleField='sample_date', trailingField='result_code',
                         showFilter=False, showSort=False, showPagination=False),
        indicatorStates=RESULT_STATES)
    listing = group('history-list-card', [text('history-list-title', '检测记录', size=20, bold=True),
        text('history-list-order', '采样时间倒序 · UTC', size=14, color='neutral'), records],
        card=True, inset=padding(14, 16), space=8)
    detail = group('history-detail-card', [
        group('history-detail-heading', [text('history-detail-title', '局部图与详情', size=20, bold=True)], inset=padding(12, 16)),
        historicalImage('history-detail', 'detail_path', 16 / 9),
        group('history-detail-fields', [
            component('history-result', 'indicator', title='综合判定', indicatorView='row',
                      indicatorStates=RESULT_STATES, recordValue=field('result_code')),
            component('history-angle', 'number', title='角度偏差', valueLayout='horizontal', unit='°', decimals=1,
                      recordValue=field('angle')),
            component('history-targets', 'number', title='定位数量', valueLayout='horizontal', unit=' 个', decimals=0,
                      recordValue=field('targets')),
            component('history-sequence', 'number', title='采样序号', valueLayout='horizontal', decimals=0,
                      recordValue=field('sequence')),
            text('history-note', title='复核备注', valueLayout='horizontal', size=14, recordValue=field('review_note')),
            text('history-key', title='记录键', valueLayout='horizontal', size=12, color='neutral', recordValue=field('write_id')),
        ], inset=padding(16), space=10),
    ], card=True, inset=padding(0), space=0)
    right = group('history-right', [listing, detail], space=20, responsive=dict(minWidth=320, weight=1, alignSelf='start'))
    return dict(name='历史回看', targets=['desktop', 'web'], layout=dict(type='responsive', spacing=14), components=[
        text('history-heading', '历史回看', size=24, bold=True),
        text('history-caption', '按采样时间倒序查看实际归档的检测图像与记录。', size=14, color='neutral'),
        group('history-columns', [left, right], direction='wrap', alignment='start', space=24),
    ])


def output(node, port, expectedType, *fieldPath):
    return dict(kind='node_output', resultScopeId=SCOPE, workflowId='main', nodeId=node, port=port,
                expectedType=expectedType, fieldPath=list(fieldPath))


def inspectionPayload(root, registry):
    """Create the synthetic inspection workflow, assets and pages from current defaults."""
    assets = makeAssets(root)
    payload = WorkflowStore().toPayload('工件视觉检测站 · 软件模拟')
    assert payload['schemaVersion'] == '2.10' and payload['presentation']['schemaVersion'] == '1.3'
    configureWorkflow(payload, registry, root)
    for key, (path, purpose, binding) in assets.items():
        data = (root / path).read_bytes()
        payload['resources']['items'][key] = dict(path=path, purpose=purpose, sha256=hashlib.sha256(data).hexdigest(), size=len(data))
        if binding:
            node, parameter = binding
            payload['resources']['parameterBindings'].append(dict(resourceId=key,
                target=dict(workflowId='main', nodeId=node, parameterPath=[parameter])))
    payload['presentation'].update(defaultPageId='production', pageOrder=['production', 'history'],
        appearance=dict(theme='light', contentMaxWidth=1200, pagePadding=24),
        shell=dict(title='工件视觉检测站', subtitle='生产监控与检测记录 · 软件模拟', logoResourceId='logo',
            logoWidth=64, logoHeight=64,
            headerSurface=dict(background='surface', border='none', padding=padding(16, 24)),
            headerTypography=typography(26, weight='bold', role='title') | typography(14, color='neutral', role='helper'),
            startControl=dict(label='开始检测', appearance=dict(style='primary', radius=8,
                padding=padding(10, 18), minHeight=44, showIcon=False)),
            stopControl=dict(label='停止检测', appearance=dict(style='secondary', radius=8,
                padding=padding(10, 18), minHeight=44, showIcon=False))),
        pages={'production': productionPage(), 'history': historyPage()},
        resultScopes={SCOPE: dict(entryWorkflowId='main', scopeWorkflowId='main')},
        dataSources={
            'sequence': output('sequence', 'count', 'integer'),
            'overlay': output('annotate', 'overlay', 'image'),
            'direction': output('in-range', 'result', 'boolean'),
            'first-batch': output('first-batch', 'result', 'boolean'),
            'result-code': output('result-code', 'value', 'integer'),
            'angle': output('angle', 'value', 'number'),
            'archive-status': output('write', 'receipt', 'string', 'status'),
            'record-key': output('write', 'receipt', 'string', 'writeId'),
            'request-id': output('write', 'receipt', 'string', 'execution', 'workflowRunId'),
        })
    return payload


def build(root):
    root = root.resolve()
    projectFile = root / PROJECT
    previous = loadProjectDocument(projectFile) if projectFile.is_file() else None
    registry = trustedRegistry()
    payload = inspectionPayload(root, registry)
    if previous:
        payload['project'].update(projectId=previous.project.projectId, createdAt=previous.project.createdAt)
    document = ProjectDocument.model_validate(payload)
    saveProject(projectFile, document.model_dump(mode='json'))
    reloaded = loadProjectDocument(projectFile)
    assert reloaded == document
    WorkflowCompiler(operatorRegistry=registry).compile(prepareRuntimeProject(reloaded, root, registry))
    package = buildWebPanel(reloaded, root, root / PANEL, manifests={key: item.manifest for key, item in registry.items()})
    readWebPanel(package)
    return projectFile, package


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    for artifact in build(parser.parse_args().directory):
        print(artifact)
