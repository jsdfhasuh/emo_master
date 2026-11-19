"""Capture the delivered Web pages through Chromium, HTTP and a software Runtime."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.main import createRuntimeServer

from build_example import DATABASE, PANEL, PROJECT


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'web_panel/frontend/tests'))
from product_runtime_e2e import accepted_start, read_json, start_gateway, stop_gateway, wait_until  # noqa: E402


def loaded_image(page, component):
    image = page.locator(f'[data-component-id="{component}"] img')
    image.wait_for()
    page.wait_for_function('(image) => image.complete && image.naturalWidth > 0', arg=image.element_handle())
    return image


def value(page, component):
    return page.locator(f'[data-component-id="{component}"] .panel-field-value').inner_text().strip()


def capture(project, output):
    from playwright.sync_api import sync_playwright, expect
    if not (ROOT / 'web_panel/backend/emo_web_panel/static/index.html').is_file():
        raise ValueError('Build frontend and run web_panel/scripts/package_frontend.py first')
    output.mkdir(parents=True, exist_ok=True)
    report = dict(source='Chromium → Web HTTP/WS → real repository software Runtime',
        physicalDevices=False, sdkVersion=importlib.metadata.version('emo_panel_protocol'),
        sourceSha=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        sourceDirty=bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True)),
        projectSha256=hashlib.sha256(project.read_bytes()).hexdigest(),
        packageSha256=hashlib.sha256((project.parent / PANEL).read_bytes()).hexdigest(),
        screenshots=[], runs=[], browserErrors=[])
    with TemporaryDirectory(prefix='emo-web-pages-') as directory, \
            (output / 'web-gateway.log').open('w', encoding='utf-8') as log:
        root = Path(directory)
        copy = root / 'project'
        (copy / DATABASE).parent.mkdir(parents=True)
        for name in (PROJECT, PANEL, DATABASE):
            shutil.copyfile(project.parent / name, copy / name)
        shutil.copytree(project.parent / 'assets', copy / 'assets')
        with closing(sqlite3.connect(copy / DATABASE)) as database:
            assert database.execute('SELECT COUNT(*) FROM records').fetchone() == (0,)
        runtime = RuntimeService(dbPath=root / 'runtime.db', workspaceRoot=root / 'jobs')
        server = gateway = None
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
                browser = playwright.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'],
                    **({'executable_path': executable} if executable else {}))
                page = browser.new_page(base_url=base, viewport={'width': 1920, 'height': 1080})
                page.set_default_timeout(20000)
                page.on('pageerror', lambda error: report['browserErrors'].append(str(error)))
                try:
                    def ready():
                        assert gateway.poll() is None, 'Gateway exited; inspect web-gateway.log'
                        try:
                            return page.request.get('/', timeout=1000).ok
                        except Exception:
                            return False
                    wait_until(page, ready, 'gateway startup')
                    page.goto(base)
                    page.get_by_label('用户名', exact=True).fill(username)
                    page.get_by_label('密码', exact=True).fill(password)
                    with page.expect_response(lambda reply: urlsplit(reply.url).path == '/api/session'
                                              and reply.request.method == 'POST') as login:
                        page.get_by_role('button', name='登录', exact=True).click()
                    signedIn = read_json(login.value)
                    assert signedIn['username'] == username and signedIn['role'] == 'admin'
                    expect(page.get_by_role('button', name='运行设置', exact=True)).to_be_visible()
                    report['health'] = read_json(page.request.get('/api/health'))
                    assert report['health']['mode'] == 'grpc'
                    page.get_by_role('button', name='运行设置', exact=True).click()
                    page.get_by_text('页面包管理', exact=True).click()
                    with page.expect_response(lambda reply: urlsplit(reply.url).path == '/api/packages'
                                              and reply.request.method == 'POST') as imported:
                        page.get_by_label('导入页面包', exact=True).set_input_files(str(copy / PANEL))
                    package = read_json(imported.value)
                    packageId = package['packageId']
                    report['packageId'] = packageId
                    page.once('dialog', lambda dialog: dialog.accept())
                    with page.expect_response(lambda reply: urlsplit(reply.url).path == f'/api/packages/{packageId}/activate'
                                              and reply.request.method == 'POST') as activation:
                        page.locator(f'[data-package-id="{packageId}"]').get_by_role('button').click()
                    read_json(activation.value)
                    expect(page.get_by_role('button', name='开始检测', exact=True)).to_be_visible()
                    page.get_by_role('button', name='运行设置', exact=True).click()
                    page.get_by_label('启动工作流目标', exact=True).select_option('main')
                    for sequence in range(1, 5):
                        expect(page.get_by_role('button', name='开始检测', exact=True)).to_be_enabled()
                        with page.expect_response(lambda reply: urlsplit(reply.url).path == '/api/runs/start'
                                                  and reply.request.method == 'POST') as started:
                            page.get_by_role('button', name='开始检测', exact=True).click()
                        accepted = accepted_start(page, started.value)
                        job = accepted['runId']
                        def terminal():
                            runs = read_json(page.request.get('/api/runtimes/configured/runs'))
                            return next((run for run in runs if run['id'] == job and run['state'] == 'COMPLETED'), None)
                        final = wait_until(page, terminal, f'job {sequence} completes')
                        expect(page.get_by_label('查看运行', exact=True).locator(f'option[value="{job}"]')).to_have_count(1)
                        page.get_by_label('查看运行', exact=True).select_option(job)
                        loaded_image(page, 'main-image')
                        wait_until(page, lambda: value(page, 'production-write-key') not in ('', '—'), 'current result key')
                        report['runs'].append(dict(sequence=sequence, jobId=job, state=final['state']))
                    with closing(sqlite3.connect(copy / DATABASE)) as database:
                        rows = database.execute('SELECT write_id,main_path,detail_path,sequence FROM records').fetchall()
                    assert len(rows) == 4
                    expected = {key: (main, detail, sequence) for key, main, detail, sequence in rows}

                    def navigate(pageId):
                        page.get_by_role('tab', name='生产监控' if pageId == 'production' else '历史回看', exact=True).click()

                    def record_ready():
                        loaded_image(page, 'history-main')
                        loaded_image(page, 'history-detail')
                        key = value(page, 'history-key')
                        return key if key in expected else None

                    navigate('history')
                    selected = wait_until(page, record_ready, 'selected archived record')
                    initial = selected
                    expect(page.locator('.history-record')).to_have_count(3)
                    assert page.locator('.history-filter,.history-sort,.history-paging').count() == 0
                    for position in range(2, 5):
                        page.get_by_role('button', name='下一条', exact=True).click()
                        expect(page.locator('[data-component-id="history-position"] .panel-field-value')).to_have_text(str(position))
                        selected = wait_until(page, record_ready, 'next archived record')
                        assert expected[selected][2] == 5 - position
                    assert page.locator('.history-record').count() == 1
                    page.get_by_role('button', name='回到最新记录', exact=True).click()
                    expect(page.locator('[data-component-id="history-position"] .panel-field-value')).to_have_text('1')
                    assert wait_until(page, record_ready, 'latest record') == initial
                    for component, index in (('history-main', 0), ('history-detail', 1)):
                        image = loaded_image(page, component)
                        response = page.request.get(image.get_attribute('src'))
                        assert response.ok
                        assert response.body() == (copy / expected[initial][index]).read_bytes()
                    report['history'] = dict(records=len(rows), hiddenToolbars=True, crossedPage=True,
                        returnedToLatest=True, selectedRecord=initial, selectedSequence=expected[initial][2],
                        mainAndDetailMatchSameArchiveRecord=True)
                    for width, height in ((1366, 768), (1920, 1080)):
                        page.set_viewport_size({'width': width, 'height': height})
                        for selectedPage in ('production', 'history'):
                            navigate(selectedPage)
                            if selectedPage == 'production':
                                loaded_image(page, 'main-image')
                            else:
                                wait_until(page, record_ready, 'history restored for screenshot')
                            page.evaluate('window.scrollTo(0,0)')
                            page.wait_for_timeout(150)
                            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                            name = f'web-{selectedPage}-{width}x{height}.png'
                            page.screenshot(path=str(output / name))
                            report['screenshots'].append(name)
                    navigate('production')
                    report['controls'] = page.locator('.operator-control').evaluate_all('(items)=>items.map(item=>({text:item.textContent,disabled:item.disabled,background:getComputedStyle(item).backgroundColor,color:getComputedStyle(item).color,height:item.getBoundingClientRect().height}))')
                    report['logo'] = page.locator('.operator-logo').evaluate('(item)=>({width:item.getBoundingClientRect().width,height:item.getBoundingClientRect().height,objectFit:getComputedStyle(item).objectFit,naturalWidth:item.naturalWidth,naturalHeight:item.naturalHeight})')
                    report['statusRows'] = page.locator('[data-component-id="result-direction"],[data-component-id="result-batch"],[data-component-id="result-overall"]').evaluate_all('(items)=>items.map(item=>({id:item.dataset.componentId,titleColor:getComputedStyle(item.querySelector(".panel-indicator-title")).color,valueColor:getComputedStyle(item.querySelector(".panel-indicator")).color,leftMarkers:item.querySelectorAll(".panel-indicator-title .panel-indicator-marker").length}))')
                    assert report['logo']['width'] == 64 and report['logo']['height'] == 64
                    assert all(row['leftMarkers'] == 1 and row['titleColor'] == row['valueColor'] for row in report['statusRows'])
                    assert not report['browserErrors'], report['browserErrors']
                    report['status'] = 'PASS'
                except BaseException:
                    page.screenshot(path=str(output / 'web-failure.png'), full_page=True)
                    (output / 'web-failure.html').write_text(page.content(), encoding='utf-8')
                    raise
                finally:
                    browser.close()
        finally:
            if gateway is not None:
                stop_gateway(gateway)
            if server is not None:
                server.stop(0).wait()
            runtime.close()
    (output / 'web-preview.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(capture(args.project.resolve(), args.output.resolve()), ensure_ascii=False, indent=2))
