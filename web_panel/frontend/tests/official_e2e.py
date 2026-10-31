"""Actual preview CLI + supplied official package/SDK fixtures + Chromium.

This is a synthetic contract/browser test, not Windows Runtime or device proof.
Run after package_frontend.py; Chromium is provisioned separately by CI.
"""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
from urllib.parse import urlsplit
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT.parent
CONTRACT = PANEL / 'contracts' / 'panel-contract-v1'
EVIDENCE = ROOT / 'test-tmp'


def start_preview(directory, stderr):
    package = CONTRACT / 'sample-panels' / 'demo.emoweb'
    with zipfile.ZipFile(package) as archive:
        manifest = json.loads(archive.read('manifest.json'))
    context = {'previewContextVersion': '1', 'mode': 'fixtures', 'readOnly': True,
               **{key: manifest[key] for key in ('projectId', 'packageRevision', 'historyDefinitionRevision')}}
    context_path = directory / 'context.json'
    context_path.write_text(json.dumps(context), encoding='utf-8')
    env = dict(os.environ)
    inherited_paths = [str(Path(part).resolve()) for part in env.get('PYTHONPATH', '').split(os.pathsep) if part]
    env['PYTHONPATH'] = os.pathsep.join([str(PANEL / 'backend'), *inherited_paths])
    command = [sys.executable, '-m', 'emo_web_panel.main', 'preview',
               '--package', str(package), '--context', str(context_path),
               '--host', '127.0.0.1', '--port', '0', '--json', '--no-control']
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=stderr,
                               text=True, encoding='utf-8', env=env, cwd=PANEL)
    lines = queue.Queue()
    threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
    try:
        line = lines.get(timeout=30)
        if not line:
            raise RuntimeError('Preview exited before ready; inspect official-preview-stderr.log')
        ready = json.loads(line)
        assert set(ready) == {'event', 'protocolVersion', 'url'}, 'Unexpected preview ready shape'
        assert ready['event'] == 'ready' and ready['protocolVersion'] == '1'
        url = urlsplit(ready['url'])
        assert url.scheme == 'http' and url.hostname == '127.0.0.1' and url.port
        assert url.query.startswith('token=')
        return process, ready['url'], manifest
    except BaseException:
        stop_preview(process)
        raise


def stop_preview(process):
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    if process.stdout:
        process.stdout.close()


def loaded_image(page, selector):
    image = page.locator(selector)
    image.wait_for()
    page.wait_for_function('(image) => image.complete && image.naturalWidth > 0',
                           arg=image.element_handle())


def main():
    from playwright.sync_api import sync_playwright, expect
    if not (PANEL / 'backend' / 'emo_web_panel' / 'static' / 'index.html').is_file():
        raise RuntimeError('Build/package frontend first; this test never silently uses a stub UI')
    EVIDENCE.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='official-preview-') as temporary, \
            (EVIDENCE / 'official-preview-stderr.log').open('w', encoding='utf-8') as stderr:
        process, ready_url, manifest = start_preview(Path(temporary), stderr)
        try:
            with sync_playwright() as playwright:
                options = {'executable_path': os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}
                browser = playwright.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'], **options)
                base = urlsplit(ready_url)
                page = browser.new_page(base_url=f'{base.scheme}://{base.netloc}', viewport={'width': 1280, 'height': 900})
                page.set_default_timeout(15000)
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                try:
                    page.goto(ready_url)
                    page.get_by_text('Synthetic fixture data; no device connected', exact=True).wait_for()
                    assert 'token=' not in page.url, 'Bootstrap token must be removed from browser URL'
                    assert page.get_by_label('用户名', exact=True).count() == 0
                    me = page.request.get('/api/me').json()
                    assert me['role'] == 'viewer' and me['preview'] is True
                    panels = page.request.get('/api/panels').json()
                    assert len(panels) == 1 and panels[0]['packageRevision'] == manifest['packageRevision']
                    # Select explicitly; the same flow works if fixture auto-selection is removed.
                    page.get_by_label('查看运行', exact=True).select_option('demo-j1')
                    expect(page.locator('.number')).to_contain_text('12.00')
                    expect(page.locator('.indicator')).to_contain_text('OK')
                    loaded_image(page, 'img[src*="/api/assets/"]')
                    assert page.locator('time').count() == 1
                    assert page.get_by_role('button', name='启动工作流', exact=True).count() == 0
                    assert page.get_by_role('button', name='停止选中运行', exact=True).count() == 0
                    assert page.get_by_label('导入页面包', exact=True).count() == 0
                    page.get_by_role('button', name='冻结查看', exact=True).click()
                    page.get_by_text('已冻结查看，后台仍在运行', exact=True).wait_for()
                    page.get_by_role('button', name='恢复实时', exact=True).click()
                    page.screenshot(path=str(EVIDENCE / 'official-live-desktop.png'), full_page=True)
                    # Mutation denial must be server-side too, not just hidden controls.
                    headers = {'x-csrf-token': me['csrf']}
                    for path in ('/api/runs/start', '/api/runs/demo-j1/stop', '/api/packages',
                                 '/api/packages/' + panels[0]['id'] + '/activate'):
                        response = page.request.post(path, data={}, headers=headers)
                        assert response.status == 403, (path, response.status)
                    # A used URL cannot mint another preview session.
                    replay = page.request.post('/api/preview/session', data={'token':urlsplit(ready_url).query[6:]})
                    assert replay.status == 403
                    page.get_by_role('button', name='History', exact=True).click()
                    history = page.locator('section.panel-card').filter(has=page.get_by_label('records 筛选'))
                    expect(history.locator('tbody tr')).to_have_count(2)
                    expect(page.locator('section.panel-card > strong')).to_have_text('2')
                    assert history.get_by_role('columnheader').all_text_contents() == ['id', 'verdict']
                    loaded_image(page, 'img[src*="/history-images/fixed-image"]')
                    history.get_by_role('row').filter(has=history.get_by_role('cell', name='OK', exact=True)).click()
                    loaded_image(page, 'img[src*="/history-images/record-image"]')
                    history.get_by_role('button', name='刷新历史', exact=True).click()
                    expect(history.locator('tbody tr[aria-selected="true"]')).to_have_count(1)
                    loaded_image(page, 'img[src*="/history-images/record-image"]')
                    # EMPTY must retire the previously loaded selected-row image.
                    history.get_by_role('row').filter(has=history.get_by_role('cell', name='NG', exact=True)).click()
                    expect(page.locator('img[src*="/history-images/record-image"]')).to_have_count(0)
                    page.get_by_label('查看运行', exact=True).select_option('')
                    expect(history.locator('tbody tr')).to_have_count(2)
                    history.get_by_label('records 筛选列', exact=True).select_option('verdict')
                    history.get_by_label('records 筛选', exact=True).fill('NG')
                    expect(history.locator('tbody tr')).to_have_count(1)
                    history.get_by_label('records 筛选', exact=True).fill('')
                    expect(history.locator('tbody tr')).to_have_count(2)
                    page.screenshot(path=str(EVIDENCE / 'official-history-desktop.png'), full_page=True)
                    page.set_viewport_size({'width':390, 'height':844})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'History overflows mobile viewport'
                    page.screenshot(path=str(EVIDENCE / 'official-history-mobile.png'), full_page=True)
                    page.locator('.page-nav').get_by_role('button', name='Live demo', exact=True).click()
                    page.get_by_label('查看运行', exact=True).select_option('demo-j1')
                    expect(page.locator('.number')).to_contain_text('12.00')
                    loaded_image(page, 'img[src*="/api/assets/"]')
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Live page overflows mobile viewport'
                    page.screenshot(path=str(EVIDENCE / 'official-live-mobile.png'), full_page=True)
                    assert not errors, errors
                except BaseException:
                    page.screenshot(path=str(EVIDENCE / 'official-failure.png'), full_page=True)
                    (EVIDENCE / 'official-failure.html').write_text(page.content(), encoding='utf-8')
                    raise
                finally:
                    browser.close()
        finally:
            stop_preview(process)
    print('PASS: actual read-only preview CLI, official package, SDK fixtures, token login, results, static/history images, stable selection, no-Job history, mutation denial and desktop/mobile Chromium. Synthetic data only; no real Runtime/device proof.')


if __name__ == '__main__':
    main()
