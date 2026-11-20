"""Replay the sample through the real Web editor, publication, and loopback Runtime."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.main import createRuntimeServer
from examples.advanced_appearance.build_example import byId
from examples.generic_page_composition.build_example import DATABASE, PANEL, PROJECT
from web_panel.frontend.tests.product_runtime_e2e import (
    accepted_start, read_json, start_gateway, stop_gateway, wait_until,
)


EDITOR = '/api/editor/configured'


def browserReady(page, base, gateway, username, password):
    def ready():
        assert gateway.poll() is None, 'Gateway exited'
        try:
            return page.request.get('/', timeout=1000).ok
        except Exception:
            return False

    wait_until(page, ready, 'gateway startup')
    page.goto(base)
    page.get_by_label('用户名', exact=True).fill(username)
    page.get_by_label('密码', exact=True).fill(password)
    with page.expect_response(lambda r: matches(r, '/api/session', 'POST')) as response:
        page.get_by_role('button', name='登录', exact=True).click()
    return {'X-CSRF-Token': read_json(response.value)['csrf']}


def matches(response, path, method):
    return urlsplit(response.url).path == path and response.request.method == method


def installPanel(page, path):
    settings = page.get_by_role('button', name='运行设置', exact=True)
    settings.click()
    page.get_by_text('页面包管理', exact=True).click()
    with page.expect_response(lambda r: matches(r, '/api/packages', 'POST')) as imported:
        page.get_by_label('导入页面包', exact=True).set_input_files(str(path))
    assert imported.value.ok, imported.value.text()
    package = page.locator('[data-package-id]')
    expect(package).to_have_count(1)
    packageId = package.get_attribute('data-package-id')
    page.once('dialog', lambda dialog: dialog.accept())
    with page.expect_response(lambda r: matches(r, f'/api/packages/{packageId}/activate', 'POST')) as activated:
        package.get_by_role('button').click()
    assert activated.value.ok, activated.value.text()


def seedDraft(page, project, document, headers):
    """Use the existing independent draft API and Runtime history references."""
    source = document.presentation.model_dump(mode='json', exclude_none=True)
    projectId = document.project.projectId
    resources = {}
    for name in ('logo', 'history-arrow'):
        resource = document.resources.items[name]
        response = page.request.post(EDITOR + '/assets', params={
            'projectId': projectId, 'name': Path(resource.path).name},
            data=(project.parent / resource.path).read_bytes(),
            headers={**headers, 'Content-Type': 'image/png'})
        resources[name] = read_json(response)['resourceId']
    source['shell']['logoResourceId'] = resources[source['shell']['logoResourceId']]
    for item in source['pages'].values():
        item['targets'] = ['web']
        for component in byId(item['components']).values():
            props = component['props']
            if 'iconResourceId' in props:
                props['iconResourceId'] = resources[props['iconResourceId']]
            imagePath = props.pop('imagePath', None)
            if props.pop('sqlite', None) is not None or imagePath is not None:
                history = {'componentId': component['componentId']}
                if imagePath and imagePath.get('componentId'):
                    history['sourceComponentId'] = imagePath['componentId']
                props['historySource'] = history
    # Begin with a different draft value so the edited rule can be checked on the canvas and after reload.
    branch = directionProps(source)['customStyle']['webCss']
    branch['code'] = branch['code'].replace('4px', '3px')
    response = page.request.put(EDITOR + '/draft', headers=headers, data={
        'projectId': projectId, 'expectedDraftRevision': '', 'presentation': source})
    return read_json(response)['presentation']


def componentPart(page, component, part):
    return page.locator(f'[data-component-id="{component}"][data-emo-part="{part}"], '
                        f'[data-component-id="{component}"] [data-emo-part="{part}"]').first


def directionProps(doc):
    return byId(doc['pages']['production']['components'])['result-direction']['props']


def author(page, base, source, targetCss, output):
    page.goto(base + '/editor')
    direction = page.locator('[data-editor-component="result-direction"]')
    direction.click()
    page.get_by_role('button', name='高级外观…', exact=True).click()
    dialog = page.locator('.editor-appearance-dialog')
    expect(dialog).to_be_visible()
    preview = dialog.locator('.editor-appearance-sample')
    previewRoot = preview.locator('[data-emo-part="root"]').first
    expect(previewRoot).to_have_css('border-left-width', '3px')
    section = dialog.get_by_text('自定义 CSS', exact=True)
    if section.locator('..').get_attribute('open') is None:
        section.click()
    css = dialog.get_by_role('textbox', name='CSS 代码', exact=True)
    enabled = dialog.get_by_label('启用自定义 CSS', exact=True)
    original = directionProps(source)['customStyle']
    expect(css).to_have_value(original['webCss']['code'].replace('\r\n', '\n'))
    enabled.uncheck()
    expect(previewRoot).to_have_css('border-left-width', '1px')
    enabled.check()
    expect(previewRoot).to_have_css('border-left-width', '3px')
    confirm = dialog.get_by_role('button', name='确定', exact=True)
    for invalid in ('[data-emo-part="root"] {', '@import "example.css";'):
        css.fill(invalid)
        expect(confirm).to_be_disabled()
        expect(dialog.get_by_role('alert')).to_contain_text('当前代码未应用')
        expect(previewRoot).to_have_css('border-left-width', '1px')
    css.fill(targetCss)
    expect(previewRoot).to_have_css('border-left-width', '4px')
    page.screenshot(path=output / 'web-editor-appearance.png')
    confirm.click()
    expect(dialog).not_to_be_visible()
    expect(componentPart(page, 'result-direction', 'root')).to_have_css('border-left-width', '4px')
    page.screenshot(path=output / 'web-editor-canvas.png')
    with page.expect_response(lambda r: matches(r, EDITOR + '/draft', 'PUT')) as savedResponse:
        page.get_by_role('button', name='保存草稿', exact=True).click()
    saved = read_json(savedResponse.value)
    style = directionProps(saved['presentation'])['customStyle']
    assert style == dict(qtQss=original['qtQss'], webCss={'enabled': True, 'code': targetCss})
    page.reload()
    expect(componentPart(page, 'result-direction', 'root')).to_have_css('border-left-width', '4px')
    page.get_by_role('button', name='示例预览', exact=True).click()
    panelPreview = page.get_by_role('dialog', name='示例数据预览')
    expect(componentPart(panelPreview, 'result-direction', 'root')).to_have_css('border-left-width', '4px')
    panelPreview.get_by_role('button', name='关闭预览', exact=True).click()
    with page.expect_response(lambda r: matches(r, EDITOR + '/publish', 'POST')) as publishedResponse:
        page.get_by_role('button', name='发布页面', exact=True).click()
    published = read_json(publishedResponse.value)
    assert published['state'] == 'PUBLISHED', published
    return dict(draftRevision=saved['draftRevision'], packageId=published['packageId'],
                retainedQss=True, normalAndDisabledCode=True, invalidRulesRejected=True)


def runAndCapture(page, base, database, output):
    page.goto(base)
    start = page.get_by_role('button', name='开始检测', exact=True)
    stop = page.get_by_role('button', name='停止检测', exact=True)
    page.get_by_role('button', name='运行设置', exact=True).click()
    page.get_by_label('启动工作流目标', exact=True).select_option('main')
    expect(start).to_be_enabled()
    expect(start).to_have_css('background-color', 'rgb(8, 127, 91)')
    expect(start).to_have_css('border-radius', '8px')
    expect(start).to_have_css('column-gap', '6px')
    expect(start).to_have_css('flex-direction', 'row-reverse')
    expect(start.locator('svg')).to_have_css('width', '12px')
    expect(stop).to_be_disabled()
    expect(stop).to_have_css('background-color', 'rgb(232, 237, 243)')
    page.screenshot(path=output / 'web-runtime-ready.png', full_page=True)
    start.hover()
    expect(start).to_have_css('background-color', 'rgb(6, 102, 74)')
    with page.expect_response(lambda r: matches(r, '/api/runs/start', 'POST')) as started:
        page.mouse.down()
        expect(start).to_have_css('background-color', 'rgb(4, 77, 56)')
        page.mouse.up()
    job = accepted_start(page, started.value)['runId']
    page.get_by_label('查看运行', exact=True).select_option(job)
    page.get_by_label('停止控制目标', exact=True).select_option(job)
    expect(stop).to_be_enabled()
    expect(stop).to_have_css('background-color', 'rgb(255, 247, 232)')
    expect(componentPart(page, 'main-value', 'value')).to_have_text(re.compile(r'^5\.0\s*°$'))
    expect(componentPart(page, 'main-value', 'value')).to_have_css('font-weight', '700')
    image = page.locator('[data-component-id="main-image"] img')
    image.wait_for()
    firstImage = image.get_attribute('src')
    expect(componentPart(page, 'main-image', 'viewport')).to_have_css('background-color', 'rgb(37, 57, 80)')
    expect(componentPart(page, 'main-value', 'value')).to_have_text(re.compile(r'^10\.0\s*°$'), timeout=60_000)
    expect(componentPart(page, 'result-direction', 'value')).to_have_text('符合')
    expect(componentPart(page, 'result-direction', 'root')).to_have_css('border-left-width', '4px')
    expect(componentPart(page, 'main-value', 'value')).to_have_css('font-size', '52px')
    expect(componentPart(page, 'main-value', 'value')).to_have_css('font-weight', '700')
    expect(componentPart(page, 'main-value', 'value')).to_have_css('color', 'rgb(33, 77, 133)')
    expect(componentPart(page, 'result-direction', 'marker')).to_be_visible()
    expect(componentPart(page, 'live-job', 'value')).to_have_css('font-weight', '700')
    expect(componentPart(page, 'production-hint', 'value')).to_have_css('font-weight', '700')
    expect(componentPart(page, 'production-results', 'root')).to_have_css('border-top-width', '3px')
    expect(componentPart(page, 'main-image', 'root')).to_have_css('border-bottom-width', '3px')
    expect(image).not_to_have_attribute('src', firstImage)
    expect(componentPart(page, 'main-image', 'viewport')).to_have_css('background-color', 'rgb(37, 57, 80)')
    page.wait_for_function('(image) => image.complete && image.naturalWidth > 0', arg=image.element_handle())
    page.screenshot(path=output / 'web-runtime-production.png', full_page=True)

    confirmation = []

    def confirm(dialog):
        confirmation.append(dialog.message)
        dialog.accept()

    page.once('dialog', confirm)
    with page.expect_response(lambda r: matches(r, f'/api/runs/{job}/stop', 'POST')) as stopped:
        stop.click()
    assert confirmation and job in confirmation[0]
    assert read_json(stopped.value)['state'] == 'accepted'

    def terminal():
        runs = read_json(page.request.get('/api/runtimes/configured/runs'))
        return next((item['status'] for item in runs if item['id'] == job
                     and item['status'] in ('STOPPED', 'ABORTED', 'COMPLETED', 'FAILED')), None)

    status = wait_until(page, terminal, 'controlled run terminates')
    assert status in ('STOPPED', 'ABORTED'), status
    expect(stop).to_be_disabled()
    expect(start).to_be_enabled()
    with closing(sqlite3.connect(database)) as connection:
        count = connection.execute('SELECT count(*) FROM records').fetchone()[0]
        assert count >= 4

    nav = componentPart(page, 'view-history', 'action')
    nav.hover()
    expect(nav).to_have_css('background-color', 'rgb(6, 102, 74)')
    nav.click()
    history = page.locator('[data-component-id="history-records"]')
    selected = history.locator('[data-emo-part="item"][aria-pressed="true"]').first
    expect(selected).to_have_css('background-color', 'rgb(217, 232, 250)')
    page.screenshot(path=output / 'web-runtime-history.png', full_page=True)
    nextRecord = history.locator('[data-emo-part="item"][aria-pressed="false"]').first
    nextRecordLabel = nextRecord.text_content()
    nextRecord.click()
    expect(selected).to_have_text(nextRecordLabel)
    expect(selected).to_have_css('background-color', 'rgb(217, 232, 250)')
    page.get_by_role('tab', name='控件对照', exact=True).first.click()
    expect(componentPart(page, 'coverage-count', 'value')).to_have_text(f'{count} 条')
    expect(componentPart(page, 'coverage-count', 'value')).to_have_css('font-weight', '700')
    expect(componentPart(page, 'coverage-title', 'title')).to_have_css('font-size', '24px')
    expect(componentPart(page, 'coverage-title', 'title')).to_have_css('font-weight', '700')
    current = page.locator('[data-component-id="coverage-navigation"] [aria-current="page"]')
    expect(current).to_have_css('background-color', 'rgb(217, 232, 250)')
    table = page.locator('[data-component-id="coverage-table"]')
    expect(table.locator('tbody tr').first).to_be_visible()
    expect(table.locator('thead th').first).to_have_css('background-color', 'rgb(217, 232, 250)')
    collection = page.locator('[data-component-id="coverage-collection"]')
    expect(collection.locator('tbody tr').first).to_be_visible()
    expect(collection.locator('thead th').first).to_have_css('background-color', 'rgb(217, 232, 250)')
    collection.get_by_role('button', name='下一页', exact=True).click()
    expect(collection.locator('tbody tr')).to_have_count(1)
    expect(collection.locator('thead th').first).to_have_css('background-color', 'rgb(217, 232, 250)')
    clock = componentPart(page, 'coverage-clock', 'value')
    initial = clock.inner_text()
    expect(clock).not_to_have_text(initial, timeout=3000)
    expect(clock).to_have_css('font-weight', '700')
    page.screenshot(path=output / 'web-runtime-coverage.png', full_page=True)
    return dict(jobId=job, terminal=status, records=count, valueRefresh=['5.0°', '10.0°'],
                imageRefreshed=True, historySelectionStyled=True, collectionPageStyled=True, clockRefreshed=True)


def verify(project, output):
    output.mkdir(parents=True, exist_ok=True)
    document = loadProjectDocument(project)
    report = dict(projectSha256=hashlib.sha256(project.read_bytes()).hexdigest(),
                  suppliedPackageSha256=hashlib.sha256((project.parent / PANEL).read_bytes()).hexdigest(),
                  continuousModeOnTemporaryCopy=True, browserErrors=[])
    with TemporaryDirectory(prefix='emo-advanced-appearance-') as directory, \
            (output / 'web-gateway.log').open('w', encoding='utf-8') as log:
        root = Path(directory)
        copy = root / 'project'
        shutil.copytree(project.parent / 'assets', copy / 'assets')
        (copy / DATABASE).parent.mkdir(parents=True)
        shutil.copyfile(project.parent / DATABASE, copy / DATABASE)
        payload = document.model_dump(mode='json')
        payload['production'].update(mode='continuous', cycleIntervalMs=1600)
        saveProject(copy / PROJECT, payload)
        runtime = RuntimeService(dbPath=root / 'runtime.db', workspaceRoot=root / 'jobs', productionMode=True)
        gateway = server = None
        try:
            loaded = runtime.LoadProject(pb.LoadProjectRequest(project_path=str(copy / PROJECT)), None)
            assert loaded.ok, loaded.message
            server, port, _ = createRuntimeServer(port=0, runtimeService=runtime)
            server.start()
            gatewayDirectory = root / 'gateway'
            gatewayDirectory.mkdir()
            gateway, base, username, password = start_gateway(gatewayDirectory, f'127.0.0.1:{port}', log)
            with sync_playwright() as playwright:
                executable = os.environ.get('CHROMIUM_PATH')
                browser = playwright.chromium.launch(headless=True,
                    **({'executable_path': executable} if executable else {}))
                report['browserVersion'] = browser.version
                page = browser.new_page(base_url=base, viewport={'width': 1600, 'height': 1000})
                page.set_default_timeout(20_000)
                page.on('pageerror', lambda error: report['browserErrors'].append(str(error)))
                headers = browserReady(page, base, gateway, username, password)
                installPanel(page, project.parent / PANEL)
                source = seedDraft(page, project, document, headers)
                targetCss = directionProps(document.presentation.model_dump(mode='json'))['customStyle']['webCss']['code']
                targetCss = targetCss.replace('\r\n', '\n')
                report['editor'] = author(page, base, source, targetCss, output)
                report['runtime'] = runAndCapture(page, base, copy / DATABASE, output)
                assert not report['browserErrors'], report['browserErrors']
        finally:
            if gateway:
                stop_gateway(gateway)
            if server:
                server.stop(0).wait()
            runtime.close()
            (output / 'web-verification.json').write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    verify(options.project.resolve(), options.output)
