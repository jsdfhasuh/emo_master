"""Non-publishing, bounded smoke acceptance of an extracted Windows product ZIP.

Run with Python 3.10 and the source revision used to build the ZIP installed
(`pip install -e .`), plus psutil and pywinauto. Every product process uses the
ZIP's executable, never a Python source entry point. Source is used only for
fixture construction and the RPC client. Requires an interactive Windows desktop.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import traceback
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def extract(archive, destination):
    """Reject traversal and ambiguous executable selection before executing."""
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            name = item.filename.replace('\\', '/')
            target = (destination / name).resolve()
            require(target.is_relative_to(destination.resolve()) and ':' not in name,
                    'unsafe ZIP entry: ' + name)
            require((item.external_attr >> 16) & 0o170000 != 0o120000, 'ZIP symlink')
        bundle.extractall(destination)
    executables = [p for p in destination.rglob('*') if p.name.lower() == 'emomaster.exe']
    require(len(executables) == 1, f'expected one EmoMaster.exe, got {len(executables)}')
    return executables[0].resolve()


def wait_for(predicate, seconds=60, process=None):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process is not None:
            require(process.poll() is None, f'product exited early: {process.returncode}')
        value = predicate()
        if value:
            return value
        time.sleep(.05)
    raise TimeoutError(f'condition not satisfied within {seconds}s')


def validate_icons(report):
    require(report.get('status') == 'ok', 'self-test did not pass')
    icons = report.get('checks', {}).get('guiIcons', {})
    require(icons.get('status') == 'ok' and len(icons.get('samples', [])) == 9,
            'nine real GUI icon samples required')
    pairs = set()
    for sample in icons['samples']:
        require(sample.get('renderSource') == 'custom' and sample.get('nonTransparentPixels', 0) >= 20
                and sample.get('accentPixels', 0) >= 8 and len(sample.get('sha256', '')) == 64,
                'custom icon rendering evidence missing')
        scale = sample.get('scale')
        require(scale in (1.0, 1.5, 2.0) and sample.get('pixelSize') == int(24 * scale),
                'icon scale evidence mismatch')
        pairs.add((sample.get('operatorId'), scale))
    require(len(pairs) == 9 and len({operator for operator, _ in pairs}) == 3,
            'expected three distinct icons at three scales')


def isolated_environment(root):
    env = os.environ.copy()
    for key in ('PYTHONPATH', 'PYTHONHOME', 'QT_QPA_PLATFORM', 'QT_PLUGIN_PATH',
                'QT_QPA_PLATFORM_PLUGIN_PATH', 'EMO_RUNTIME_TARGET',
                'EMO_RUNTIME_DB_PATH', 'EMO_MASTER_RUNTIME_DB_PATH', 'EMO_RUNTIME_LOG_DIR'):
        env.pop(key, None)
    for key in ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP',
                'EMO_RUNTIME_DATA_DIR', 'EMO_DESIGNER_CACHE_DIR'):
        path = root / key.lower()
        path.mkdir(parents=True, exist_ok=True)
        env[key] = str(path)
    env['QT_ACCESSIBILITY'] = '1'
    env['HUARAY_CAMERA_SMOKE'] = '0'
    return env


class Product:
    def __init__(self, executable, output):
        self.executable, self.output = executable, output
        self.env = isolated_environment(output / 'isolated-user')
        self.processes, self.logs, self.descendants = [], [], []

    def start(self, name, arguments, offscreen=False):
        env = dict(self.env)
        if offscreen:
            env['QT_QPA_PLATFORM'] = 'offscreen'
        log = (self.output / (name + '.log')).open('wb')
        self.logs.append(log)
        process = subprocess.Popen([str(self.executable), *map(str, arguments)],
            cwd=self.output, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        self.processes.append(process)
        return process

    def run(self, name, arguments, timeout=120, offscreen=False):
        process = self.start(name, arguments, offscreen)
        require(process.wait(timeout=timeout) == 0, f'{name} failed: {process.returncode}')
        return process

    def cleanup(self):
        import psutil
        forced = []
        for process in reversed(self.processes):
            if process.poll() is not None:
                continue
            try:
                owner = psutil.Process(process.pid)
                children = owner.children(recursive=True)
                for child in reversed(children):
                    try:
                        child.kill()
                        forced.append(child.pid)
                    except psutil.NoSuchProcess:
                        pass
                process.kill()
                forced.append(process.pid)
                process.wait(timeout=10)
                _, alive = psutil.wait_procs(children, timeout=10)
                require(not alive, 'owned processes survived forced cleanup')
            except psutil.NoSuchProcess:
                pass
        # Descendants captured while owned remain attributable even if their host crashes.
        for child in self.descendants:
            if child.is_running():
                try:
                    child.kill()
                    forced.append(child.pid)
                    child.wait(timeout=10)
                except psutil.NoSuchProcess:
                    pass
        for log in self.logs:
            log.close()
        return forced


def window_for(process, title):
    from pywinauto import Desktop
    matches = Desktop(backend='uia').windows(process=process.pid, title=title, visible_only=True)
    return matches[0] if matches else None


def designer(product, report):
    process = product.start('designer', [])
    window = wait_for(lambda: window_for(process, '项目入口'), process=process)
    require(window.is_visible(), 'Designer chooser not visible')
    window.capture_as_image().save(str(product.output / 'designer-startup.png'))
    # Cancel only this owned chooser. No project or user data is changed.
    window.close()
    require(process.wait(timeout=30) == 0, 'Designer startup cancellation did not exit cleanly')
    report.update(status='PASS', pid=process.pid, window_title='项目入口',
                  scope='native startup chooser visibility and clean cancellation; main editing not tested')


def runtime(product, report):
    import grpc
    import psutil
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
    from emo_master.core.project.delivery_store import DeliveryStore, DirectoryOwner
    from emo_master.core.project.package_builder import buildPageTestPackage
    from examples.runtime_pages_p2 import sampleProject

    draft = product.output / 'draft'
    draft.mkdir()
    doc = sampleProject(draft)
    package = buildPageTestPackage(doc, draft, product.output / 'fixture-packages')
    store = DeliveryStore(product.output / 'release-store')
    revision = store.importPackage(package)
    store.activate(revision)
    draft.rename(product.output / 'unavailable-draft')
    data = product.output / 'runtime-data'
    ready = data / 'ready.json'
    host = product.start('runtime', ['--runtime', '--store', store.root, '--data', data, '--ready-file', ready])
    wait_for(ready.exists, process=host)
    info = json.loads(ready.read_text(encoding='utf-8'))
    require(info['revision'] == revision and info['runtimeReady'] and info['projectReady'], 'host not ready')
    report.update(host_pid=host.pid, ready=info, fixture_sha256=digest(package))
    with grpc.insecure_channel(info['address']) as channel:
        display = rpc.DisplayServiceStub(channel)
        execution = rpc.RuntimeServiceStub(channel)
        require(display.Capabilities(pb.DisplayEmpty(), timeout=3).runtime_instance_id == info['runtimeInstanceId'],
                'Runtime identity mismatch')
        require(not display.ListJobs(pb.DisplayEmpty(), timeout=3).jobs, 'readiness automatically started a Job')
        started = display.Start(pb.DisplayStartRequest(prepared_id=info['preparedId']), timeout=30)
        require(bool(started.job_id), 'no job created')
        initial = execution.GetJobStatus(pb.GetJobStatusRequest(job_id=started.job_id), timeout=3)
        require(initial.ok and initial.pid > 0 and initial.pid != host.pid, 'spawned worker PID missing')
        worker = psutil.Process(initial.pid)
        require(worker.ppid() == host.pid, 'worker is not owned by frozen Runtime')
        require(Path(worker.exe()).resolve() == product.executable, 'worker did not execute the packaged binary')
        product.descendants.extend(psutil.Process(host.pid).children(recursive=True))
        report['worker_identity'] = {'pid': worker.pid, 'parent_pid': worker.ppid(),
            'executable': worker.exe(), 'created': worker.create_time()}

        request = pb.DisplayRequest(runtime_instance_id=info['runtimeInstanceId'], job_id=started.job_id)
        def completed_result():
            snapshot = display.Snapshot(request, timeout=3)
            return next((r for r in snapshot.results if r.status == 'COMPLETE'), None)
        result = wait_for(completed_result, process=host)
        require(result.identity.job_id == started.job_id and result.identity.mode == 'release', 'wrong result identity')
        count = next((s.value_json for s in result.sources if s.source_id == 'count'), None)
        require(count == '2', f'wrong actual image analysis count: {count}')
        images = [s.image for s in result.sources if s.HasField('image') and s.image.resource_id]
        require(len(images) == 1, 'image result missing')
        asset = display.ReadAsset(pb.DisplayAssetRequest(runtime_instance_id=info['runtimeInstanceId'],
            job_id=started.job_id, resource_id=images[0].resource_id), timeout=5)
        require(asset.content.startswith(b'\x89PNG') and len(asset.content) == images[0].byte_size,
                'not a complete PNG result')
        require(hashlib.sha256(asset.content).hexdigest() == images[0].sha256 == asset.sha256,
                'runtime image digest mismatch')
        (product.output / 'runtime-overlay.png').write_bytes(asset.content)
        def terminal():
            value = execution.GetJobStatus(pb.GetJobStatusRequest(job_id=started.job_id), timeout=3)
            return value if value.status in {'COMPLETED', 'FAILED', 'STOPPED'} else None
        status = wait_for(terminal, process=host)
        require(status.ok and status.status == 'COMPLETED' and status.pid > 0 and status.pid != host.pid,
                'real spawned worker did not complete')
        report.update(job_id=started.job_id, worker_pid=status.pid, job_status=status.status,
                      image_sha256=asset.sha256, count=count, result_key=result.identity.result_key)
        # A frozen viewer must connect to this exact Job and show its native shell.
        viewer = product.start('operator-view', ['--operator-view', '--ready-file', ready, '--job', started.job_id])
        window = wait_for(lambda: window_for(viewer, 'EmoMaster · 测试项目运行端'), process=viewer)
        def view_ready():
            texts = [c.window_text() for c in window.descendants()]
            require(not any('操作失败：' in t for t in texts), 'OperatorView reports operation failure')
            return texts if any('Runtime 就绪' in t and '已连接 Job ' + started.job_id in t for t in texts) else None
        texts = wait_for(view_ready, process=viewer)
        window.capture_as_image().save(str(product.output / 'operator-view.png'))
        window.close()
        require(viewer.wait(timeout=30) == 0, 'OperatorView did not close gracefully')
        require(host.poll() is None, 'viewer close stopped owned Runtime')
        require(len(display.ListJobs(pb.DisplayEmpty(), timeout=3).jobs) == 1, 'viewer changed job count')
        report['operator_view'] = {'status': 'PASS', 'pid': viewer.pid, 'texts': texts,
            'scope': 'native shell connected to exact Job; rendered image pixel fidelity not asserted',
            'close_preserved_runtime': True}
    try:
        with DirectoryOwner(data):
            raise AssertionError('live Runtime released its owner lock early')
    except RuntimeError as error:
        require('in use' in str(error), 'unexpected owner lock failure')
    product.run('runtime-stop', ['--runtime', '--stop', '--ready-file', ready], timeout=30)
    require(host.wait(timeout=45) == 0 and not ready.exists(), 'explicit frozen stop did not retire host')
    wait_for(lambda: not psutil.pid_exists(status.pid), seconds=15)
    with DirectoryOwner(data):
        pass
    store.activate(revision)
    report.update(status='PASS', explicit_stop=True, worker_retired=True, owner_locks_reacquired=True)


def verify_artifacts(directory, expected_source):
    files = sorted(p for p in directory.rglob('*') if p.is_file())
    manifests = [p for p in files if p.suffix.lower() == '.json']
    require(len(files) == 3 and len(manifests) == 1, 'expected exactly ZIP, setup EXE and manifest JSON')
    manifest = json.loads(manifests[0].read_text(encoding='utf-8-sig'))
    require(manifest.get('source_commit') == expected_source, 'manifest source commit mismatch')
    require(manifest.get('source_repo') == 'jsdfhasuh/emo_master', 'manifest source repository mismatch')
    assets = manifest.get('assets', {})
    verified = {}
    for kind, suffix in [('portable', '.zip'), ('setup', '.exe')]:
        entry = assets.get(kind, {})
        name = entry.get('name', '')
        require(bool(name) and Path(name).name == name and '/' not in name and '\\' not in name,
                'unsafe manifest asset name')
        matches = [p for p in files if p.name == name and p.suffix.lower() == suffix]
        require(len(matches) == 1, 'manifest asset missing or ambiguous: ' + kind)
        actual = digest(matches[0])
        require(actual == entry.get('sha256', '').lower(), 'manifest asset SHA256 mismatch: ' + kind)
        verified[kind] = {'path': str(matches[0]), 'sha256': actual}
    require(manifest.get('sha256', '').lower() == verified['portable']['sha256'], 'top-level archive SHA256 mismatch')
    require(manifest.get('url') == assets['portable'].get('url'), 'portable URL alias mismatch')
    return Path(verified['portable']['path']), {
        'manifest': str(manifests[0]), 'manifest_sha256': digest(manifests[0]),
        'source_commit': expected_source, 'assets': verified,
        'setup_scope': 'hash verified only; installer not executed'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact-directory', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--expected-source-sha', required=True, help='exact source commit that built the ZIP')
    args = parser.parse_args()
    evidence_path = args.output.resolve()
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    require(not evidence_path.exists(), 'existing evidence must not be overwritten')
    args.output = evidence_path.with_suffix('')
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'status': 'FAIL', 'started_utc': datetime.now(timezone.utc).isoformat(),
        'platform': platform.platform(), 'python': sys.version, 'checks': {},
        'not_tested': ['physical cameras', 'GPU execution', 'full production/field acceptance',
                       'Designer main editing', 'OperatorView image pixel fidelity']}
    product = None
    try:
        require(os.name == 'nt', 'real Windows desktop required; no source fallback permitted')
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        require(head == args.expected_source_sha, 'source fixture/protocol revision differs from packaged source')
        archive, provenance = verify_artifacts(args.artifact_directory.resolve(), head)
        report['provenance'] = provenance
        report.update(source=head, source_dirty=subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True),
                      archive=str(archive), archive_sha256=digest(archive))
        executable = extract(archive, args.output / 'product')
        report.update(executable=str(executable), executable_sha256=digest(executable))
        sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
        product = Product(executable, args.output)
        for name, action in [('icons', None), ('designer_startup', designer), ('runtime', runtime)]:
            check = report['checks'][name] = {'status': 'FAIL'}
            if action is None:
                path = args.output / 'icon-self-test.json'
                product.run('icons', ['--self-test', '--gui-icons', '--result-json', path], offscreen=True)
                icon_report = json.loads(path.read_text(encoding='utf-8'))
                validate_icons(icon_report)
                check.update(status='PASS', report=str(path), sha256=digest(path))
            else:
                action(product, check)
        require(len(report['checks']) == 3 and all(c['status'] == 'PASS' for c in report['checks'].values()),
                'non-vacuous required check set failed')
        report['status'] = 'PASS'
    except BaseException:
        report['error'] = traceback.format_exc()
    finally:
        if product is not None:
            try:
                report['forced_cleanup_pids'] = product.cleanup()
                if report['forced_cleanup_pids']:
                    report['status'] = 'FAIL'
            except BaseException:
                report['cleanup_error'] = traceback.format_exc()
                report['status'] = 'FAIL'
        for name in ('icons', 'designer_startup', 'runtime'):
            report['checks'].setdefault(name, {'status': 'NOT_RUN', 'reason': 'prior prerequisite failed'})
        report['ended_utc'] = datetime.now(timezone.utc).isoformat()
        evidence_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(report['status'] != 'PASS')


if __name__ == '__main__':
    raise SystemExit(main())
