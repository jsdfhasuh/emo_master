"""Native Stop confirmation + real Web HTTP/WS + explicit synthetic Runtime.

The browser JS pauses in confirm while normal gateway results keep advancing.
On resume, the exact queued result can already be retired. Preserve that 404,
its reason and identity; do not revive its bytes or substitute a newer image.
This is browser automation with a synthetic Runtime, not visible/manual or
owning Runtime/device acceptance. Build/package the actual frontend first.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[3]
PANEL = ROOT / 'web_panel'
sys.path.insert(0, str(PANEL / 'backend'))

import uvicorn
from emo_web_panel.app import create_app
from emo_web_panel.config import Config
from emo_web_panel.mock_runtime import IMAGE, MockRuntime
from playwright.async_api import async_playwright


class AssetRuntime(MockRuntime):
    async def snapshot(self, run_id):
        result = await super().snapshot(run_id)
        # Match the real result's exact view/scope/key URL, not a static demo URL.
        result.update(projectId='demo-project', gatewayTransformed=True)
        return result

    async def read_asset(self, job_id, resource, *, runtime_instance_id):
        assert runtime_instance_id == self.instance_id and job_id == 'demo-run'
        assert resource['sha256'] == hashlib.sha256(IMAGE).hexdigest()
        return IMAGE, 'image/png'


async def wait_until(condition, description, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if asyncio.iscoroutine(value):
            value = await value
        if value:
            return value
        await asyncio.sleep(.05)
    raise AssertionError('Timed out: ' + description)


async def run(output, observe_only=False):
    output.mkdir(parents=True, exist_ok=True)
    report = {'scope': 'Chromium + real Web gateway + explicit synthetic Runtime',
              'physicalDevices': False, 'manualAcceptance': False, 'observeOnly': observe_only,
              'sourceSHA': os.environ.get('GITHUB_SHA'), 'status': 'FAILED', 'requests': [],
              'responses': [], 'gatewayAssets': [], 'frames': [], 'pageErrors': []}
    try:
        assert (PANEL / 'backend/emo_web_panel/static/index.html').is_file(), 'Build and package the frontend first'
        with tempfile.TemporaryDirectory(prefix='web-asset-retirement-') as directory, socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); sock.listen(128)
            port = sock.getsockname()[1]
            base = f'http://127.0.0.1:{port}'
            runtime = AssetRuntime()
            app = create_app(Config(Path(directory), port=port, local_no_auth=True), runtime=runtime)

            @app.middleware('http')
            async def observe_asset(request, call_next):
                if '/assets/' not in request.url.path or not request.url.path.startswith('/api/views/'):
                    return await call_next(request)
                view_id = request.url.path.split('/')[3]
                scope = request.query_params.get('scope')
                view = app.state.views.views.get(view_id) or {}
                observation = {'atMonotonic': time.monotonic(), 'viewId': view_id,
                    'query': dict(request.query_params), 'ordinal': runtime.ordinal,
                    'retainedKeys': list(view.get('scopeRecent', {}).get(scope, {}))}
                response = await call_next(request)
                observation['status'] = response.status_code
                report['gatewayAssets'].append(observation)
                return response

            server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port,
                proxy_headers=False, access_log=False, log_level='warning'))
            thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
            thread.start()
            await wait_until(lambda: server.started, 'gateway startup')
            try:
                async with async_playwright() as playwright:
                    options = {'executable_path': os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}
                    browser = await playwright.chromium.launch(headless=True,
                        args=['--no-sandbox', '--disable-dev-shm-usage'], **options)
                    report['browserVersion'] = browser.version
                    context = await browser.new_context(viewport={'width': 1280, 'height': 900})
                    page = await context.new_page()
                    page.on('pageerror', lambda error: report['pageErrors'].append(str(error)))
                    def receive(payload):
                        message = json.loads(payload)
                        raw = message.get('result') or {}
                        report['frames'].append({'atMonotonic': time.monotonic(),
                            'type': message.get('type') or message.get('event'),
                            'resultKey': raw.get('resultKey'), 'ordinal': raw.get('resultOrdinal')})
                    page.on('websocket', lambda ws: ws.on('framereceived', receive))
                    page.on('request', lambda request: report['requests'].append({
                        'atMonotonic': time.monotonic(), 'url': request.url, 'method': request.method})
                        if '/assets/' in request.url or request.url.endswith('/stop') else None)
                    tasks = set()
                    async def inspect_response(response):
                        if '/assets/' not in response.url and not response.url.endswith('/stop'):
                            return
                        item = {'atMonotonic': time.monotonic(), 'url': response.url, 'status': response.status}
                        if response.status >= 400:
                            try:item['body'] = await response.json()
                            except Exception as error:item['bodyUnavailable'] = type(error).__name__
                        report['responses'].append(item)
                    def response_handler(response):
                        task = asyncio.create_task(inspect_response(response))
                        tasks.add(task); task.add_done_callback(tasks.discard)
                    page.on('response', response_handler)
                    try:
                        await page.goto(base)
                        await page.get_by_label('查看运行', exact=True).select_option('demo-run')
                        await page.get_by_label('停止控制目标', exact=True).select_option('demo-run')
                        image = page.get_by_role('img', name='检测结果', exact=True)
                        await image.wait_for()
                        await page.wait_for_function('(image) => image.complete && image.naturalWidth > 0',
                                                     arg=await image.element_handle())
                        async def displayed_key_is_retained():
                            parts = (await page.locator('footer').inner_text()).split(' · ')
                            key = parts[2] if len(parts) > 2 else ''
                            return key if any(key in view['scopeRecent'].get('scope-1', {})
                                for view in app.state.views.views.values() if view['connected']) else None
                        report['visibleKeyBeforeModal'] = await wait_until(displayed_key_is_retained,
                            'visible result belongs to the currently retained scope')
                        report['beforeModalOrdinal'] = runtime.ordinal
                        dialog_wait = asyncio.create_task(page.wait_for_event('dialog'))
                        click = asyncio.create_task(page.get_by_role('button', name='停止选中运行', exact=True).click())
                        dialog = await dialog_wait
                        report['modalOpenedAt'] = time.monotonic()
                        report['modalOpenedOrdinal'] = runtime.ordinal
                        report['modalText'] = dialog.message
                        try:
                            # Actual state condition, not an assumed machine sleep.
                            await wait_until(lambda: runtime.ordinal >= report['modalOpenedOrdinal'] + 5,
                                             'five ordinary results while native confirmation remains open')
                            report['beforeAcceptOrdinal'] = runtime.ordinal
                            report['modalAcceptedAt'] = time.monotonic()
                            await dialog.accept()
                        except BaseException:
                            await dialog.dismiss()
                            raise
                        finally:
                            clicked = (await asyncio.gather(click, return_exceptions=True))[0]
                            report['clickCompleted'] = not isinstance(clicked, BaseException)
                            if isinstance(clicked, BaseException):
                                report['clickError'] = {'type':type(clicked).__name__, 'message':str(clicked)}
                        await wait_until(lambda: runtime.control_count == 1, 'one accepted Stop')
                        stopped_ordinal = runtime.ordinal
                        current_key = f'mock-{stopped_ordinal}'
                        try:
                            await wait_until(lambda: any(item['status'] == 404 for item in report['gatewayAssets']),
                                             'retired queued result request')
                        except AssertionError:
                            report['status'] = 'NO_REPRODUCTION'
                            raise
                        await wait_until(lambda: any(item['status'] == 200
                            and item['query'].get('resultKey') == current_key
                            for item in report['gatewayAssets']), 'current result image remains available')
                        await page.wait_for_function("key => document.querySelector('footer')?.textContent?.split(' · ').includes(key)",
                                                     arg=current_key)
                        await page.wait_for_function('(image) => image.complete && image.naturalWidth > 0',
                                                     arg=await image.element_handle())
                        assert runtime.ordinal == stopped_ordinal, 'No new result is generated after Stop'
                        if tasks:
                            await asyncio.gather(*list(tasks))
                        report['afterStopOrdinal'] = runtime.ordinal
                        report['controlCount'] = runtime.control_count
                        report['runtimeRunning'] = runtime.running
                        report['visibleImageAfterStop'] = await image.get_attribute('src')
                        report['visibleKeyAfterStop'] = current_key
                        retired = [item for item in report['responses'] if item['status'] == 404
                                   and item.get('body', {}).get('detail') == 'Exact advertised result retired or expired']
                        assert retired, 'Capture the actual 404 detail; status alone is insufficient'
                        assert all(item['query']['resultKey'] not in item['retainedKeys']
                                   for item in report['gatewayAssets'] if item['status'] == 404)
                        if not observe_only:
                            assert all(item['body']['code'] == 'RESULT_RETIRED' for item in retired)
                        assert report['controlCount'] == 1 and not report['runtimeRunning']
                        assert not report['pageErrors'], report['pageErrors']
                        report['status'] = 'PASSED'
                    finally:
                        await browser.close()
            finally:
                server.should_exit = True
                thread.join(timeout=10)
                assert not thread.is_alive(), 'Gateway did not stop'
                raw = Path(directory) / 'logs/gateway.jsonl'
                if raw.is_file():
                    (output / 'gateway.jsonl').write_bytes(raw.read_bytes())
    except BaseException:
        report['failure'] = traceback.format_exc()
        raise
    finally:
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({key: report.get(key) for key in ('status', 'scope', 'browserVersion',
            'beforeModalOrdinal', 'beforeAcceptOrdinal', 'afterStopOrdinal', 'controlCount')}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=PANEL / 'test-results/asset-retirement-browser')
    parser.add_argument('--observe-only', action='store_true', help='Record a baseline without requiring new error codes')
    args = parser.parse_args()
    asyncio.run(run(args.output, args.observe_only))
