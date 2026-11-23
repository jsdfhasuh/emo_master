"""Build the advanced appearance sample through the normal project and Web export APIs."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
from pathlib import Path

from PIL import Image, ImageDraw

from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.runtime_directory import prepareRuntimeProject
from emo_master.core.project.test_delivery import trustedRegistry
from emo_master.core.project.web_panel import buildWebPanel, readWebPanel
from emo_master.core.workflow.compiler import WorkflowCompiler
from examples.generic_page_composition.build_example import (
    DATABASE, PANEL, PROJECT, component, group, inspectionPayload, output, padding, typography,
)


STYLES = Path(__file__).resolve().parents[2] / 'openspec/changes/archive/2026-11-23-add-generic-control-advanced-appearance/examples'


def native(qss, css):
    return dict(qtQss=dict(enabled=True, code=qss), webCss=dict(enabled=True, code=css))


def fileStyle(name):
    qss = (STYLES / (name + '.qss')).read_text(encoding='utf-8')
    css = (STYLES / (name + '.css')).read_text(encoding='utf-8')
    return native(qss, css)


def roleStyle(part, declarations='font-weight: bold;'):
    return native(f'*[emoPart="{part}"] {{ {declarations} }}',
                  f'[data-emo-part="{part}"] {{ {declarations} }}')


def surface(background, borderColor, **options):
    return dict(kind='none', background=background, borderColor=borderColor,
                borderWidth=1, borderStyle='solid', radius=8) | options


def hostAppearance(label, name, colors):
    states = {
        key: dict(zip(('background', 'color', 'borderColor'), row))
        for key, row in zip(('default', 'hover', 'pressed', 'disabled'), colors)
    }
    return dict(label=label, appearance=dict(
        style='primary' if name == 'start_button' else 'secondary',
        text=dict(size=14, weight='normal'), radius=8, padding=padding(8, 12),
        minWidth=100, minHeight=44, showIcon=True, borderWidth=1, borderStyle='solid',
        iconPosition='right', iconSize=12, iconGap=6, buttonStates=states, customStyle=fileStyle(name),
    ))


def byId(components):
    result = {}
    for item in components:
        result[item['componentId']] = item
        result.update(byId(item.get('children', [])))
    return result


def addNavigationIcon(root, payload):
    relative = 'assets/history-arrow.png'
    image = Image.new('RGBA', (32, 32))
    draw = ImageDraw.Draw(image)
    draw.line([(8, 16), (24, 16)], fill='#214D85', width=3)
    draw.line([(17, 9), (24, 16), (17, 23)], fill='#214D85', width=3)
    image.save(root / relative)
    data = (root / relative).read_bytes()
    payload['resources']['items']['history-arrow'] = dict(
        path=relative, purpose='icon', size=len(data), sha256=hashlib.sha256(data).hexdigest())


def configureLive(presentation):
    shell = presentation['shell']
    disabled = ('#E8EDF3', '#8190A3', '#D2DCE6')
    shell['startControl'] = hostAppearance('开始检测', 'start_button', (
        ('#087F5B', '#FFFFFF', '#06664A'), ('#06664A', '#FFFFFF', '#045038'),
        ('#044D38', '#FFFFFF', '#033B2A'), disabled))
    shell['stopControl'] = hostAppearance('停止检测', 'stop_button', (
        ('#FFF7E8', '#9B580A', '#D8B57B'), ('#FFF2E1', '#9B580A', '#C88A2F'),
        ('#F8E0B5', '#9B580A', '#995305'), disabled))

    live = byId(presentation['pages']['production']['components'])
    direction = live['result-direction']['props']
    direction['surface'] = surface('subtle', '#D2DCE6', padding=padding(12))
    direction['typography']['value']['weight'] = 'normal'
    direction['indicatorStates']['true'] = dict(
        text='符合', color='#11734C', background='#F0F9F5',
        borderColor='#72B39C', borderWidth=1, borderStyle='solid')
    direction['customStyle'] = fileStyle('direction')
    for key, state, color, background, border in (
        ('result-batch', 'false', '#9B580A', '#FFF7E8', '#D8B57B'),
        ('result-overall', '2', '#995305', '#FFF2E1', '#C88A2F'),
    ):
        props = live[key]['props']
        props['indicatorStates'][state].update(
            color=color, background=background, borderColor=border, borderWidth=2, borderStyle='solid')
    live['main-value']['props'].update(
        surface=surface('#F0F5FC', '#B9CCE4', padding=padding(10, 12)),
        typography=typography(52, color='#214D85') | typography(16, role='title') | typography(12, role='helper'),
        customStyle=fileStyle('number'))
    live['main-image']['props'].update(
        title='检测结果图', surface=surface('#F0F5FC', '#B9CCE4'),
        customStyle=roleStyle('root', 'border-bottom-width: 3px;'))
    live['main-image']['props']['imageView']['background'] = '#253950'
    live['live-job']['props'].update(
        typography=typography(16, color='#214D85'), customStyle=roleStyle('value'))
    live['view-history']['props'].update(
        iconResourceId='history-arrow', iconPosition='right', iconSize=12, iconGap=6,
        typography=typography(14, color='#214D85'), customStyle=fileStyle('navigation_button'))
    live['production-results']['props'].update(
        surface=surface('#FFFFFF', '#B9CCE4', padding=padding(18)),
        customStyle=roleStyle('root', 'border-top-width: 3px;'))
    live['production-hint']['props'].update(
        typography=typography(14, color='#214D85'), customStyle=roleStyle('value'))


def configureCoverage(presentation):
    history = byId(presentation['pages']['history']['components'])
    history['history-records']['props'].update(
        surface=surface('#F0F5FC', '#B9CCE4'),
        customStyle=native('QTableView[emoPart="table"] { selection-color: #214D85; }\n'
                           'QTableView[emoPart="table"]::item:selected { background: #D9E8FA; }',
                           '[data-emo-part="item"][aria-pressed="true"] { background: #D9E8FA; color: #214D85; }'))
    headerRule = native('QHeaderView[emoPart="header"]::section { background: #D9E8FA; color: #214D85; }',
                        '[data-emo-part="header"] th { background: #D9E8FA; color: #214D85; }')
    title = component('coverage-title', 'page_title',
                      typography=typography(24, color='#214D85', role='title'), customStyle=roleStyle('title'))
    navigation = component('coverage-navigation', 'page_navigation',
                           surface=surface('#F0F5FC', '#B9CCE4', padding=padding(6)),
                           customStyle=native(
                               'QTabBar[emoPart="tab"]::tab:selected { background: #D9E8FA; color: #214D85; }\n'
                               'QPushButton[emoPart="action"]:checked { background: #D9E8FA; color: #214D85; }',
                               '[data-emo-part="tab"][aria-current="page"],'
                               '[data-emo-part="action"][aria-current="page"] { background: #D9E8FA; color: #214D85; }'))
    clock = component('coverage-clock', 'clock', title='当前时间',
                      typography=typography(19, color='#214D85'), customStyle=roleStyle('value'))
    collection = component('coverage-collection', 'table', title='本次结果明细',
                           responsive=dict(minHeight=200), pageSize=2,
                           surface=surface('#F0F5FC', '#B9CCE4'), customStyle=headerRule,
                           columns=[dict(title='类别', fieldPath=['label'])])
    collection['bindings'] = {'rows': 'detections'}
    count = component('coverage-count', 'sqlite_value', title='累计记录数', decimals=0, unit=' 条',
                      sqlite=dict(location='runtime', databasePath=DATABASE, table='records', aggregate='count'),
                      typography=typography(26, color='#214D85'), customStyle=roleStyle('value'))
    records = component('coverage-table', 'sqlite_table', title='已归档记录',
                        responsive=dict(minHeight=240),
                        sqlite=dict(location='runtime', databasePath=DATABASE, table='history_records',
                                    columns=['sequence', 'result_code', 'angle'], orderBy='id', recordKey='write_id'),
                        surface=surface('#F0F5FC', '#B9CCE4'), customStyle=deepcopy(headerRule))
    sampleText = component('coverage-text', 'text', title='嵌套文字',
                           text='文字沿用自身的基础外观。\n改变容器字体或颜色后再比较这两行。')
    nested = group('coverage-nested', [sampleText], title='容器与子组件', inset=padding(10),
                   background='#F0F5FC', customStyle=native(
                       'QFrame[emoPart="root"] { color: #995305; font-size: 24px; border: 2px dashed #B9CCE4; }',
                       '[data-emo-part="root"] { color: #995305; font-size: 24px; border: 2px dashed #B9CCE4; }'))
    presentation['dataSources']['detections'] = output('detect', 'detections', 'collection')
    presentation['pageOrder'].append('coverage')
    presentation['pages']['coverage'] = dict(name='控件对照', targets=['desktop', 'web'],
        layout=dict(type='responsive', spacing=16), components=[
            title, navigation, group('coverage-summary', [clock, count], direction='horizontal', alignment='center'),
            group('coverage-data', [collection, records], direction='wrap', alignment='start'),
            nested, component('coverage-spacer', 'spacer', responsive=dict(minHeight=12, preferredHeight=12)),
        ])


def build(root):
    root = root.resolve()
    registry = trustedRegistry()
    payload = inspectionPayload(root, registry)
    payload['project'].update(projectId='advanced-appearance-demo', name='通用页面控件高级外观')
    addNavigationIcon(root, payload)
    configureLive(payload['presentation'])
    configureCoverage(payload['presentation'])
    project = root / PROJECT
    document = ProjectDocument.model_validate(payload)
    saveProject(project, document.model_dump(mode='json'))
    reloaded = loadProjectDocument(project)
    assert reloaded == document
    WorkflowCompiler(operatorRegistry=registry).compile(prepareRuntimeProject(reloaded, root, registry))
    package = buildWebPanel(reloaded, root, root / PANEL, manifests={key: item.manifest for key, item in registry.items()})
    _, snapshot = readWebPanel(package)
    projected = byId(snapshot['pages']['production']['components'])
    original = byId(payload['presentation']['pages']['production']['components'])
    assert projected['result-direction']['props']['customStyle'] == original['result-direction']['props']['customStyle']
    assert snapshot['shell']['startControl']['appearance']['customStyle'] == (
        payload['presentation']['shell']['startControl']['appearance']['customStyle'])
    return project, package


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    for artifact in build(parser.parse_args().directory):
        print(artifact)
