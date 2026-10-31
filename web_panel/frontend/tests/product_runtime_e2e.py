"""Chromium -> real Web HTTP/WS -> owning software Runtime; synthetic data only.

Run after building/package_frontend.py, in CI with a separately provisioned
Runtime interpreter and Chromium. Never substitutes MockRuntime/FixtureRuntime,
intercepts RPC replies, installs dependencies, or touches a real device.
EMO_WEB_REQUIRE_OFFICIAL_UNGROUPED=1 additionally requires SDK >=1.1.1 and the
ordinary Job's empty group ID to arrive in a valid official BrowserJobState.
"""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[3]
PANEL = ROOT / 'web_panel'
CONTRACT = PANEL / 'contracts/panel-contract-v1'
EVIDENCE = PANEL / 'test-results/product-browser'
sys.path.insert(0, str(PANEL / 'backend'))


def wait_until(page, condition, description, *, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = condition()
        if result:
            return result
        page.wait_for_timeout(100)
    raise AssertionError('Timed out: ' + description)


def read_json(response):
    if not response.ok:
        raise AssertionError(f'{response.request.method} {response.url}: {response.status} {response.text()}')
    return response.json()


def start_runtime(directory, log):
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'), PYTHONDONTWRITEBYTECODE='1',
               QT_QPA_PLATFORM='offscreen', PYTHONUTF8='1')
    process = subprocess.Popen([os.environ.get('EMO_WEB_PRODUCT_PYTHON', sys.executable),
        str(PANEL / 'tests/test_product_runtime.py'), '--runtime-server-continuous', str(directory)],
        cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=log,
        text=True, encoding='utf-8')
    ready_lines = queue.Queue()
    threading.Thread(target=lambda: ready_lines.put(process.stdout.readline()), daemon=True).start()
    try:
        ready = json.loads(ready_lines.get(timeout=45))
        assert ready['event'] == 'ready'
        assert ready['address'].startswith('127.0.0.1:')
    except BaseException:
        process.kill(); process.wait(timeout=10); process.stdout.close()
        raise
    stdout = []
    def drain():
        for line in process.stdout:
            stdout.append(line)
            if len(stdout) > 1000:
                del stdout[0]
    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    return process, ready['address'], stdout, reader


def stop_runtime(process, directory, reader):
    if process.poll() is None:
        (directory / 'stop-requested').touch()
        try:
            process.wait(timeout=25)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=10)
    reader.join(timeout=2)
    process.stdout.close()


def start_gateway(directory, runtime_address, log):
    from emo_web_panel.auth import AuthStore
    username, password = 'browser-operator', 'Synthetic-browser-fixture-2026!'
    AuthStore(directory / 'web.sqlite').create_user(username, password, 'admin')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    env = dict(os.environ, PYTHONPATH=str(PANEL / 'backend'), PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
    process = subprocess.Popen([sys.executable, '-m', 'emo_web_panel.main', '--data-dir', str(directory),
        'serve', '--mode', 'grpc', '--runtime-address', runtime_address, '--host', '127.0.0.1', '--port', str(port)],
        cwd=PANEL, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
    return process, f'http://127.0.0.1:{port}', username, password


def stop_gateway(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=5)


def save_runtime_evidence(directory):
    total = 0
    events = []
    for path in sorted(directory.rglob('*')):
        if not path.is_file() or path.suffix not in {'.jsonl', '.log', '.json'}:
            continue
        size = path.stat().st_size
        if size > 2 * 1024 * 1024 or total + size > 8 * 1024 * 1024:
            continue
        target = EVIDENCE / 'runtime' / path.relative_to(directory)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        total += size
        if path.suffix == '.jsonl':
            for line in path.read_text(encoding='utf-8').splitlines():
                if line.strip():
                    events.append(json.loads(line))
    return events


def main():
    from playwright.sync_api import sync_playwright, expect
    from emo_panel_protocol.messages import BrowserMessage
    sdk_version = importlib.metadata.version('emo_panel_protocol')
    require_official = os.environ.get('EMO_WEB_REQUIRE_OFFICIAL_UNGROUPED') == '1'
    if require_official and tuple(map(int, sdk_version.split('.')[:3])) < (1, 1, 1):
        raise AssertionError('Final empty-group acceptance requires the delivered official SDK >=1.1.1')
    if not (PANEL / 'backend/emo_web_panel/static/index.html').is_file():
        raise AssertionError('Build and package the real frontend before this acceptance test')
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    requests, responses, messages, errors = [], [], [], []
    manifest = json.loads((CONTRACT / 'contract-manifest.json').read_text(encoding='utf-8'))
    wheel = CONTRACT / manifest['sdkWheels'][0]
    report = {'webSourceSha':os.environ.get('GITHUB_SHA'), 'sdkSourceSha':manifest['sourceSha'],
        'sdkSourceDirty':manifest['sourceDirty'], 'sdkWheelSha256':hashlib.sha256(wheel.read_bytes()).hexdigest(),
        'verification':'Chromium -> Web HTTP/WS -> repository software Runtime',
        'syntheticOnly':True, 'physicalDevices':False, 'sdkVersion':sdk_version,
        'officialUngroupedRequired':require_official, 'officialUngroupedVerified':False,
        'emptyGroupAcceptance':'PENDING_SDK_1_1_1', 'status':'FAILED'}
    runtime = gateway = reader = None
    with tempfile.TemporaryDirectory(prefix='web-product-browser-') as temporary, \
            (EVIDENCE / 'runtime-stderr.log').open('w', encoding='utf-8') as runtime_log, \
            (EVIDENCE / 'gateway.log').open('w', encoding='utf-8') as gateway_log:
        temp = Path(temporary)
        runtime_dir, gateway_dir = temp / 'runtime', temp / 'gateway'
        runtime_dir.mkdir(); gateway_dir.mkdir()
        stdout = []
        try:
            runtime, address, stdout, reader = start_runtime(runtime_dir, runtime_log)
            gateway, base, username, password = start_gateway(gateway_dir, address, gateway_log)
            with sync_playwright() as playwright:
                options = {'executable_path':os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}
                browser = playwright.chromium.launch(headless=True,
                    args=['--no-sandbox','--disable-dev-shm-usage'], **options)
                page = browser.new_page(base_url=base, viewport={'width':1280,'height':900})
                page.set_default_timeout(20000)
                page.on('pageerror', lambda error: errors.append(str(error)))
                def is_control(request):
                    path = urlsplit(request.url).path
                    return request.method == 'POST' and (path == '/api/runs/start' or path.endswith('/stop'))
                def observe_request(request):
                    if is_control(request):
                        requests.append({'path':urlsplit(request.url).path, 'body':request.post_data_json})
                def observe_response(response):
                    if is_control(response.request):
                        responses.append({'path':urlsplit(response.url).path, 'status':response.status,
                                          'body':response.json()})
                def receive(payload):
                    if isinstance(payload, bytes):
                        payload = payload.decode('utf-8')
                    message = json.loads(payload)
                    messages.append(message)
                    if len(messages) > 500:
                        del messages[0]
                page.on('request', observe_request)
                page.on('response', observe_response)
                page.on('websocket', lambda ws: ws.on('framereceived', receive))
                try:
                    def gateway_ready():
                        if gateway.poll() is not None:
                            raise AssertionError('Gateway exited before ready; inspect gateway.log')
                        try:
                            return page.request.get('/', timeout=1000).ok
                        except Exception:
                            return False
                    wait_until(page, gateway_ready, 'gateway startup')
                    page.goto(base)
                    page.get_by_label('用户名', exact=True).fill(username)
                    page.get_by_label('密码', exact=True).fill(password)
                    page.get_by_role('button', name='登录', exact=True).click()
                    page.get_by_text('真实 Runtime 模式 · 能力以合同为准', exact=True).wait_for()
                    me = read_json(page.request.get('/api/me'))
                    assert me['mode'] == 'grpc' and not me.get('preview'), me
                    initial = read_json(page.request.get('/api/runtimes/configured/status'))['runtime']
                    assert initial['projectId'] == 'panel-contract-demo', initial
                    assert initial['productionExecutionMode'] == 'continuous', initial
                    assert read_json(page.request.get('/api/runtimes/configured/runs')) == [], 'Fixture must not autostart'
                    page.get_by_text('页面包管理', exact=True).click()
                    with page.expect_response(lambda r:urlsplit(r.url).path == '/api/packages' and r.request.method == 'POST') as uploaded:
                        page.get_by_label('导入页面包', exact=True).set_input_files(str(CONTRACT / 'sample-panels/demo.emoweb'))
                    # Chromium may evict multipart POST response bodies from
                    # its inspector cache. Verify persistence via an independent
                    # authenticated catalog read, not that transient cache.
                    assert uploaded.value.ok, uploaded.value.status
                    sample_path = CONTRACT / 'sample-panels/demo.emoweb'
                    package_id = hashlib.sha256(sample_path.read_bytes()).hexdigest()
                    with zipfile.ZipFile(sample_path) as sample:
                        expected_manifest = json.loads(sample.read('manifest.json'))
                    installed = read_json(page.request.get(
                        '/api/packages?runtimeId=configured&projectId=panel-contract-demo'))
                    matching = [item for item in installed if item['packageId'] == package_id]
                    assert len(matching) == 1, installed
                    assert matching[0]['manifest']['packageRevision'] == expected_manifest['packageRevision']
                    assert matching[0]['manifest']['projectId'] == initial['projectId']
                    page.once('dialog', lambda dialog: dialog.accept())
                    with page.expect_response(lambda r:urlsplit(r.url).path == f'/api/packages/{package_id}/activate') as activated:
                        page.locator(f'[data-package-id="{package_id}"]').get_by_role('button',
                            name='对当前目标启用 / 切回此版本', exact=True).click()
                    read_json(activated.value)
                    page.get_by_role('heading', name='Live demo', exact=True).wait_for()
                    page.get_by_label('启动工作流目标', exact=True).select_option('main')
                    with page.expect_response(lambda r:urlsplit(r.url).path == '/api/runs/start' and r.request.method == 'POST') as started:
                        page.get_by_role('button', name='启动工作流', exact=True).click()
                    accepted = read_json(started.value)
                    assert accepted['state'] == 'accepted' and accepted['runId'], accepted
                    job = accepted['runId']
                    report.update(jobId=job, runtimeInstanceId=initial['runtimeInstanceId'], projectId=initial['projectId'],
                                  startReply=accepted, packageId=package_id)
                    # Do not dismiss tracking, click a query, or bypass the UI with
                    # an API Stop. The normal accepted transition must unblock it.
                    expect(page.get_by_role('button', name='结束跟踪（不重发）', exact=True)).to_have_count(0)
                    expect(page.get_by_role('button', name='查询操作结果', exact=True)).to_have_count(0)
                    expect(page.get_by_label('查看运行', exact=True).locator(f'option[value="{job}"]')).to_have_count(1)
                    page.get_by_label('查看运行', exact=True).select_option(job)
                    page.get_by_label('停止控制目标', exact=True).select_option(job)
                    def matching_result():
                        return next((m for m in messages if m.get('event') == 'result' and
                            m.get('result',{}).get('identity',{}).get('jobId') == job), None)
                    notification = wait_until(page, matching_result, 'official captured result over WebSocket')
                    BrowserMessage.model_validate_json(json.dumps(notification))
                    assert notification['runtimeInstanceId'] == initial['runtimeInstanceId']
                    assert notification['projectId'] == initial['projectId']
                    expect(page.locator('.number')).to_contain_text('12.00')
                    expect(page.locator('.indicator')).to_contain_text('OK')
                    current = read_json(page.request.get(f'/api/runs/{job}?runtimeId=configured'))
                    assert current['active'] and current['runGroupId'] == '', current
                    assert current['runtimeInstanceId'] == initial['runtimeInstanceId']
                    report['beforeStop'] = current
                    page.screenshot(path=str(EVIDENCE / 'running.png'), full_page=True)
                    page.once('dialog', lambda dialog: dialog.accept())
                    with page.expect_response(lambda r:urlsplit(r.url).path == f'/api/runs/{job}/stop' and r.request.method == 'POST') as stopped:
                        page.get_by_role('button', name='停止选中运行', exact=True).click()
                    stop_reply = read_json(stopped.value)
                    assert stop_reply['state'] == 'accepted', stop_reply
                    report['stopReply'] = stop_reply
                    expect(page.get_by_role('button', name='结束跟踪（不重发）', exact=True)).to_have_count(0)
                    expect(page.get_by_text('停止已受理：', exact=False)).to_be_visible()
                    def retired():
                        state = read_json(page.request.get(f'/api/runs/{job}?runtimeId=configured'))
                        report['afterStop'] = state
                        assert (state['runtimeInstanceId'],state['projectId'],state['id'],state['runGroupId']) == (
                            initial['runtimeInstanceId'],initial['projectId'],job,''), state
                        return state if state['status'] == 'ABORTED' and not state['active'] and not state['ownsResources'] else None
                    terminal = wait_until(page, retired, 'original Job ABORTED with resources retired', timeout=40)
                    # Verify the browser's authoritative status too, not just a
                    # successful out-of-band read while the screen stays stale.
                    page.get_by_role('button', name='刷新页面包和状态', exact=True).click()
                    original_status = page.get_by_label('原控制运行状态', exact=True)
                    expect(original_status).to_contain_text(job)
                    expect(original_status).to_contain_text('权威状态：ABORTED')
                    expect(original_status).to_contain_text('持有资源：否')
                    expect(original_status).to_contain_text('展示资源已释放：' + ('是' if terminal['displayResourcesReleased'] else '否'))
                    expect(page.get_by_role('button', name='查询操作结果', exact=True)).to_have_count(0)
                    expect(page.get_by_role('button', name='结束跟踪（不重发）', exact=True)).to_have_count(0)
                    report['renderedTerminalStatus'] = original_status.inner_text()
                    published_status = page.locator('section.panel-card p').filter(has_text='运行：')
                    expect(published_status).to_have_count(1)
                    expect(published_status).to_contain_text('运行：ABORTED')
                    report['renderedPublishedRuntimeStatus'] = published_status.inner_text()
                    assert len(read_json(page.request.get('/api/runtimes/configured/runs'))) == 1
                    official_state = next((m for m in messages if m.get('event') == 'job_state'
                        and m.get('jobId') == job and m.get('runGroupId') == ''), None)
                    if require_official:
                        official_state = wait_until(page, lambda:next((m for m in messages if m.get('event') == 'job_state'
                            and m.get('jobId') == job and m.get('runGroupId') == ''), None),
                            'official empty-group JobState after SDK repair')
                    if official_state is not None:
                        BrowserMessage.model_validate_json(json.dumps(official_state))
                        report['officialUngroupedVerified'] = True
                        report['emptyGroupAcceptance'] = 'PASS'
                    starts = [r for r in requests if r['path'] == '/api/runs/start']
                    stops = [r for r in requests if r['path'].endswith('/stop')]
                    assert len(starts) == 1 and len(stops) == 1, requests
                    assert stops[0]['path'] == f'/api/runs/{job}/stop'
                    assert stops[0]['body']['runGroupId'] == '', stops[0]
                    assert stops[0]['body']['runtimeInstanceId'] == initial['runtimeInstanceId']
                    assert stops[0]['body']['projectId'] == initial['projectId']
                    assert starts[0]['body']['requestId'] != stops[0]['body']['requestId'], 'Distinct user intents need distinct request IDs'
                    report.update(startHttpCount=len(starts), stopHttpCount=len(stops), renderedCount='12.00',
                                  websocketResultIdentity=notification['result']['identity'])
                    page.screenshot(path=str(EVIDENCE / 'stopped.png'), full_page=True)
                    assert not errors, errors
                    report['status'] = 'PASS'
                except BaseException as error:
                    report['error'] = repr(error)
                    try:
                        page.screenshot(path=str(EVIDENCE / 'failure.png'), full_page=True)
                        (EVIDENCE / 'failure.html').write_text(page.content(), encoding='utf-8')
                    except Exception:
                        pass
                    raise
                finally:
                    browser.close()
        finally:
            if gateway is not None:
                stop_gateway(gateway)
            if runtime is not None:
                stop_runtime(runtime, runtime_dir, reader)
            events = save_runtime_evidence(runtime_dir)
            accepted_jobs = [event for event in events if event.get('eventType') == 'job.accepted']
            report['runtimeAcceptedJobCount'] = len(accepted_jobs)
            report['runtimeAcceptedJobIds'] = [event.get('jobId') for event in accepted_jobs]
            (EVIDENCE / 'runtime-stdout.log').write_text(''.join(stdout), encoding='utf-8')
            (EVIDENCE / 'http-controls.json').write_text(json.dumps({'requests':requests,'responses':responses}, indent=2), encoding='utf-8')
            (EVIDENCE / 'websocket.json').write_text(json.dumps(messages, indent=2), encoding='utf-8')
            (EVIDENCE / 'browser-errors.json').write_text(json.dumps(errors, indent=2), encoding='utf-8')
            if report['status'] == 'PASS' and (len(accepted_jobs) != 1 or accepted_jobs[0]['jobId'] != report['jobId']):
                report['status'] = 'FAILED'
                report['error'] = 'Runtime acceptance log did not confirm exactly the one browser-started Job'
            (EVIDENCE / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    assert report['status'] == 'PASS', report
    if not report['officialUngroupedVerified']:
        print('PENDING: official empty-group BrowserMessage acceptance requires delivered SDK1.1.1; this run establishes the real browser/HTTP/WS/software Runtime chain only.')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
