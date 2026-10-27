"""Keep this closure's first failures and fixed-source validation in separate directories."""
import argparse
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


BASELINE = '31b5de4a604e4facdf636ad39795da0869d908d4'
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
    'tests/runtime/test_device_plan_completion.py',
    'tests/designer/test_device_definition_feedback.py',
]
COUNTEREXAMPLES = [
    'tests/designer/test_tcp_closure_ui.py::testTcpByteLogsKeepChannelAndExactBoundedPreview',
    'tests/runtime/test_device_plan_completion.py::testTcpDraftEndpointTestReusesRenamedServerWithoutPublishing',
    'tests/runtime/test_device_plan_completion.py::testNonTcpDraftConnectionSettingsRemainProtected',
    'tests/runtime/test_device_plan_completion.py::testConnectionTestDoesNotPublishAndReusesExistingOwner',
    'tests/designer/test_device_definition_feedback.py::testTcpOuterValidationFocusesExactConnectionFieldAndRetainsDraft',
]
PRODUCT = [
    'src/emo_master/apps/runtime/devices/diagnostics.py',
    'src/emo_master/apps/runtime/devices/manager.py',
    'src/emo_master/apps/runtime/devices/tcp.py',
    'src/emo_master/apps/designer/ui/tcp_device_form.py',
    'src/emo_master/ui/communication_panel.py',
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--working-tree', action='store_true', help='Run only direct counterexamples before/after repairs.')
    options = parser.parse_args()
    repo, evidence = options.repo.resolve(), options.output_dir.resolve()
    evidence.mkdir(parents=True, exist_ok=False)

    def git(*args):
        return subprocess.check_output(['git', *args], cwd=repo, text=True).strip()

    source = git('rev-parse', options.source_sha+'^{commit}')
    temporary = Path(tempfile.mkdtemp(prefix='emo-f-'))
    if options.working_tree:
        snapshot = repo
        changed = git('diff', '--name-only', BASELINE).splitlines()
    else:
        snapshot = temporary/'source'
        archive = temporary/'source.zip'
        subprocess.run(['git', 'archive', '--format=zip', '-o', str(archive), source], cwd=repo, check=True)
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(snapshot)
        changed = git('diff', '--name-only', BASELINE, source).splitlines()
    python = [path for path in changed if path.endswith('.py')]
    exported = []
    for path in sorted(set(python+PRODUCT)):
        data = (snapshot/path).read_bytes()
        blob = subprocess.check_output(['git', 'hash-object', '--path='+path, '--stdin'],
                                      cwd=repo, input=data).decode().strip()
        committed = git('rev-parse', source+':'+path)
        if not options.working_tree:
            assert blob == committed, path
        exported.append(dict(path=path, blob=blob, committedBlob=committed,
                             matchesSourceCommit=blob == committed, sha256=hashlib.sha256(data).hexdigest()))
    env = dict(os.environ, PYTHONPATH=str(snapshot/'src')+os.pathsep+str(snapshot),
               PYTHONIOENCODING='utf-8', QT_QPA_PLATFORM='windows', EMO_TEST_SOURCE_SHA=source)
    record = dict(baseline=BASELINE, sourceSha=source, sourceTree=git('rev-parse', source+'^{tree}'),
        workingTree=options.working_tree, workingTreeStatus=git('status', '--short'),
        snapshot=str(snapshot), temporaryRoot=str(temporary), executable=sys.executable,
        platform=platform.platform(), exported=exported,
        runnerSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        environment={key: env[key] for key in ('PYTHONPATH', 'PYTHONIOENCODING', 'QT_QPA_PLATFORM', 'EMO_TEST_SOURCE_SHA')},
        commands=[])

    def run(label, args, cwd=snapshot):
        print('RUN '+label, flush=True)
        start, started = time.monotonic(), datetime.now().astimezone().isoformat()
        with (evidence/(label+'.log')).open('wb') as output:
            result = subprocess.run(list(map(str, args)), cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT)
        record['commands'].append(dict(label=label, argv=list(map(str, args)), cwd=str(cwd), started=started,
            elapsedSeconds=round(time.monotonic()-start, 3), exitCode=result.returncode, log=label+'.log'))
        (evidence/'validation.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        print(label+': exit='+str(result.returncode), flush=True)
        return result.returncode

    failures = [run('environment', [sys.executable, '-c',
        "import emo_master,sys,json,PySide2; from PySide2.QtCore import qVersion; from pathlib import Path; "
        "print(json.dumps(dict(python=sys.version,PySide2=PySide2.__version__,Qt=qVersion(),importPath=emo_master.__file__),indent=2)); "
        "import os; assert os.path.samefile(Path(emo_master.__file__).parent.parent, Path.cwd()/'src'); "
        "assert sys.version_info[:2]==(3,10) and PySide2.__version__=='5.15.2.1'"])]
    batches = [('counterexamples', COUNTEREXAMPLES)] if options.working_tree else [('chain', CHAIN), ('regression', REGRESSION)]
    for label, paths in batches:
        # Exactly the same two legacy migration branches excluded in round two.
        exclusions = (['--deselect=tests/designer/test_runtime_validation_feedback.py::testUpgradedDraftResourceFailureCanBeFixedAndRevalidated']
                      if label == 'regression' else [])
        failures.append(run(label, [sys.executable, '-m', 'pytest', '-q', '-rA',
            '--basetemp='+str(temporary/label), '--junitxml='+str(evidence/(label+'.xml')), *exclusions, *paths]))
    if not options.working_tree:
        failures.append(run('ruff', [sys.executable, '-m', 'ruff', 'check', *python]))
        failures.append(run('mypy', [sys.executable, '-m', 'mypy', *[p for p in python if p.startswith('src/')]]))
        failures.append(run('proto', [sys.executable, 'scripts/gen_proto.py', '--check']))
        failures.append(run('diff-check', ['git', 'diff', '--check', BASELINE, source], cwd=repo))
    artifacts = []

    def retain(path, relative):
        destination = evidence/relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        artifacts.append(dict(source=str(path), retained=str(relative), sha256=hashlib.sha256(path.read_bytes()).hexdigest()))

    chainRoot = temporary/'chain/tcp-device-chain0'
    for path in chainRoot.rglob('*'):
        if path.is_file() and path.suffix.lower() in {'.emoproj', '.emodev', '.json', '.png', '.vxpkg'}:
            relative = path.relative_to(chainRoot)
            if not any(part in {'runtime', 'jobs', 'configuration-resources', 'resource-uploads'} for part in relative.parts):
                retain(path, Path('acceptance')/relative)
    for batch in ('chain', 'regression', 'counterexamples'):
        batchRoot = temporary/batch
        for pattern, target in [('testConsumerDeviceLogsKeepReta*', 'dispatch'),
                                ('testTcpByteLogsKeepChannelAndE*', 'byte-logs')]:
            for testRoot in batchRoot.glob(pattern):
                if testRoot.is_dir() and not testRoot.is_symlink():
                    for path in [*testRoot.rglob('runtime-*.jsonl'), testRoot/'project.json']:
                        if path.is_file():
                            retain(path, Path(target)/testRoot.name/path.relative_to(testRoot))
    (evidence/'artifacts.json').write_text(json.dumps(artifacts, ensure_ascii=False, indent=2), encoding='utf-8')
    return int(any(failures))


if __name__ == '__main__':
    raise SystemExit(main())
