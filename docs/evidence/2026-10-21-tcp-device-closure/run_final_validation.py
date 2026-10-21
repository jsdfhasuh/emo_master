"""Validate an exported source commit; retain commands, paths and raw output."""
from datetime import datetime
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time


def main():
    tests_only = '--tests-only' in sys.argv[1:]
    snapshot, evidence, source_sha, source_tree = [arg for arg in sys.argv[1:] if arg != '--tests-only']
    snapshot, evidence = Path(snapshot).resolve(), Path(evidence).resolve()
    temporary_root = Path(tempfile.mkdtemp(prefix='emotcp-'))
    evidence.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONPATH=str(snapshot / 'src') + os.pathsep + str(snapshot),
               PYTHONIOENCODING='utf-8', QT_QPA_PLATFORM='windows', EMO_TEST_SOURCE_SHA=source_sha)
    records = dict(sourceSha=source_sha, sourceTree=source_tree, snapshot=str(snapshot),
                   executable=sys.executable, platform=platform.platform(), temporaryRoot=str(temporary_root),
                   environment={key: env[key] for key in ('PYTHONPATH', 'PYTHONIOENCODING',
                                'QT_QPA_PLATFORM', 'EMO_TEST_SOURCE_SHA')}, commands=[])

    def run(label, command, *, cwd=snapshot):
        started = datetime.now().astimezone().isoformat()
        start = time.monotonic()
        path = evidence / (label + '.log')
        print('RUN ' + label, flush=True)
        with path.open('wb') as stream:
            result = subprocess.run(command, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT)
        records['commands'].append(dict(label=label, argv=list(map(str, command)), cwd=str(cwd),
            started=started, ended=datetime.now().astimezone().isoformat(),
            elapsedSeconds=round(time.monotonic()-start, 3), exitCode=result.returncode, log=path.name))
        (evidence / 'final-validation.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
        print(label + ': exit=' + str(result.returncode), flush=True)
        return result.returncode

    failures = []
    failures.append(run('final-environment', [sys.executable, '-c',
        "import json,sys,PySide2,emo_master; from PySide2.QtCore import qVersion; "
        "from pathlib import Path; "
        "print(json.dumps(dict(python=sys.version,executable=sys.executable,PySide2=PySide2.__version__,"
        "Qt=qVersion(),importPath=emo_master.__file__),indent=2)); "
        "assert Path(emo_master.__file__).resolve().is_relative_to(Path.cwd()/'src'); "
        "assert sys.version_info[:2]==(3,10) and PySide2.__version__=='5.15.2.1'"]))

    chain = [
        'tests/runtime/test_tcp_device_integration.py',
        'tests/runtime/test_tcp_device_closure.py',
        'tests/runtime/test_device_contract_closure.py',
        'tests/runtime/test_device_feedback_closure.py',
        'tests/designer/test_tcp_closure_ui.py',
    ]
    regression = [
        'tests/core/project/test_configuration.py',
        'tests/core/project/test_devices.py',
        'tests/core/project/test_device_resources.py',
        'tests/core/test_runtime_directory.py',
        'tests/core/test_runtime_package.py',
        'tests/runtime/test_device_executor.py',
        'tests/runtime/test_device_manager.py',
        'tests/runtime/test_device_lifecycle.py',
        'tests/runtime/test_device_ipc_retirement.py',
        'tests/runtime/test_frame_dispatch.py',
        'tests/runtime/test_loop_cycle_configuration.py',
        'tests/runtime/test_operator_logging.py',
        'tests/runtime/test_initial_project_configuration.py',
        'tests/designer/test_project_configuration_ui.py',
        'tests/designer/test_device_definition_feedback.py',
        'tests/designer/test_device_workspace.py',
        'tests/designer/test_device_package.py',
        'tests/designer/test_workflow_package_devices.py',
    ]
    for label, paths in [('final-chain', chain), ('final-regression', regression)]:
        failures.append(run(label, [sys.executable, '-m', 'pytest', '-q', '-rA',
            '--basetemp=' + str(temporary_root / label),
            '--junitxml=' + str(evidence / (label + '.xml')), *paths]))

    if tests_only:
        return int(any(failures))

    changed = subprocess.check_output(['git', 'diff', '--name-only', 'd307b9f', source_sha],
                                     cwd=evidence.parents[2], text=True).splitlines()
    python_paths = [path for path in changed if path.endswith('.py') and path.startswith(('src/', 'tests/'))]
    source_paths = [path for path in python_paths if path.startswith('src/')]
    failures.append(run('final-ruff', [sys.executable, '-m', 'ruff', 'check', *python_paths]))
    failures.append(run('final-mypy', [sys.executable, '-m', 'mypy', *source_paths]))
    failures.append(run('final-proto', [sys.executable, 'scripts/gen_proto.py', '--check']))
    failures.append(run('final-diff-check', ['git', 'diff', '--check', 'd307b9f', source_sha], cwd=evidence.parents[2]))
    # A missing Linux environment is a separate NOT_RUN result, never a test pass.
    run('linux-wsl-inventory', ['wsl', '--list', '--quiet'], cwd=evidence.parents[2])
    run('linux-docker-inventory', ['docker', 'info', '--format', '{{.OSType}}'], cwd=evidence.parents[2])
    return int(any(failures))


if __name__ == '__main__':
    raise SystemExit(main())
