"""Export a fixed source commit and record this round's actual validation."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile


BASELINE = 'b86d0162dcde14b9c19ec19eee838dc114eeca98'
CHAIN = [
    'tests/runtime/test_tcp_device_integration.py',
    'tests/runtime/test_tcp_device_closure.py',
    'tests/runtime/test_device_contract_closure.py',
    'tests/runtime/test_device_feedback_closure.py',
    'tests/designer/test_tcp_closure_ui.py',
]
REGRESSION = [
    'tests/runtime/test_frame_dispatch.py',
    'tests/runtime/test_project_configuration.py',
    'tests/runtime/test_loop_cycle_configuration.py',
    'tests/runtime/test_operator_logging.py',
    'tests/runtime/test_device_runtime.py',
    'tests/runtime/test_device_ipc_retirement.py',
    'tests/runtime/test_configuration_owners.py',
    'tests/runtime/test_initial_project_configuration.py',
    'tests/designer/test_runtime_validation_feedback.py',
    'tests/designer/test_project_configuration_ui.py',
    'tests/designer/test_device_workspace.py',
    'tests/core/project/test_configuration.py',
]


def main():
    repo = Path(sys.argv[1]).resolve()
    evidence = Path(__file__).resolve().parent
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=repo, text=True).strip()
    source = git('rev-parse', 'HEAD')
    tree = git('rev-parse', 'HEAD^{tree}')
    temporary = Path(tempfile.mkdtemp(prefix='emo-r2-'))
    snapshot = temporary / 'source'
    archive = temporary / 'source.zip'
    subprocess.run(['git', 'archive', '--format=zip', '-o', str(archive), source], cwd=repo, check=True)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(snapshot)
    changed = git('diff', '--name-only', BASELINE, source).splitlines()
    python = [path for path in changed if path.endswith('.py')]
    exported = []
    for path in python:
        data = (snapshot/path).read_bytes()
        blob = subprocess.check_output(['git', 'hash-object', '--path='+path, '--stdin'],
                                      cwd=repo, input=data).decode().strip()
        assert blob == git('rev-parse', source+':'+path), path
        exported.append(dict(path=path, blob=blob, sha256=hashlib.sha256(data).hexdigest()))
    env = dict(os.environ, PYTHONPATH=str(snapshot/'src')+os.pathsep+str(snapshot),
               PYTHONIOENCODING='utf-8', QT_QPA_PLATFORM='windows', EMO_TEST_SOURCE_SHA=source)
    record = dict(baseline=BASELINE, sourceSha=source, sourceTree=tree,
        snapshot=str(snapshot), temporaryRoot=str(temporary), executable=sys.executable,
        platform=platform.platform(), exported=exported,
        environment={k: env[k] for k in ('PYTHONPATH', 'PYTHONIOENCODING', 'QT_QPA_PLATFORM', 'EMO_TEST_SOURCE_SHA')},
        commands=[])
    def run(label, args, cwd=snapshot):
        print('RUN '+label, flush=True)
        started = datetime.now().astimezone().isoformat()
        start = time.monotonic()
        with (evidence/(label+'.log')).open('wb') as output:
            result = subprocess.run(list(map(str, args)), cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT)
        record['commands'].append(dict(label=label, argv=list(map(str, args)), cwd=str(cwd),
            started=started, elapsedSeconds=round(time.monotonic()-start, 3), exitCode=result.returncode,
            log=label+'.log'))
        (evidence/'validation.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        print(label+': exit='+str(result.returncode), flush=True)
        return result.returncode
    failures = [run('environment', [sys.executable, '-c',
        "import emo_master,sys,json,PySide2; from PySide2.QtCore import qVersion; from pathlib import Path; "
        "print(json.dumps(dict(python=sys.version,PySide2=PySide2.__version__,Qt=qVersion(),importPath=emo_master.__file__),indent=2)); "
        "import os; assert os.path.samefile(Path(emo_master.__file__).parent.parent, Path.cwd()/'src'); "
        "assert sys.version_info[:2]==(3,10) and PySide2.__version__=='5.15.2.1'"])]
    for label, paths in [('chain', CHAIN), ('regression', REGRESSION)]:
        # Legacy 2.1 -> 2.6 migration is explicitly outside this round's scope.
        options = (['--deselect=tests/designer/test_runtime_validation_feedback.py::testUpgradedDraftResourceFailureCanBeFixedAndRevalidated']
                   if label == 'regression' else [])
        failures.append(run(label, [sys.executable, '-m', 'pytest', '-q', '-rA',
            '--basetemp='+str(temporary/label), '--junitxml='+str(evidence/(label+'.xml')), *options, *paths]))
    failures.append(run('ruff', [sys.executable, '-m', 'ruff', 'check', *python]))
    failures.append(run('mypy', [sys.executable, '-m', 'mypy', *[p for p in python if p.startswith('src/')]]))
    failures.append(run('proto', [sys.executable, 'scripts/gen_proto.py', '--check']))
    failures.append(run('diff-check', ['git', 'diff', '--check', BASELINE, source], cwd=repo))
    artifacts = []
    chainRoot = temporary/'chain/tcp-device-chain0'
    for root, target in [(chainRoot, 'acceptance')]:
        for path in root.rglob('*'):
            if path.is_file() and path.suffix.lower() in {'.emoproj', '.emodev', '.json', '.png', '.vxpkg'}:
                if any(part in {'runtime', 'jobs', 'configuration-resources', 'resource-uploads'} for part in path.relative_to(root).parts):
                    continue
                destination = evidence/target/path.relative_to(root)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, destination)
                artifacts.append(dict(source=str(path), retained=str(destination.relative_to(evidence))))
    for root in (temporary/'regression').glob('testConsumerDeviceLogsKeepReta*'):
        if root.is_dir() and not root.is_symlink():
            for path in [*root.rglob('runtime-*.jsonl'), root/'project.json']:
                if path.is_file():
                    destination = evidence/'dispatch'/root.name/path.relative_to(root)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, destination)
                    artifacts.append(dict(source=str(path), retained=str(destination.relative_to(evidence))))
    (evidence/'artifacts.json').write_text(json.dumps(artifacts, ensure_ascii=False, indent=2), encoding='utf-8')
    return int(any(failures))


if __name__ == '__main__':
    raise SystemExit(main())
