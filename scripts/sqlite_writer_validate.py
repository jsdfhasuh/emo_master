"""Preserve exact candidate identity and raw output; never overwrite evidence."""
from pathlib import Path
import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import sqlite3
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parent.parent
SUITES = {
    'review-paths': ['tests/sqlite_writer/test_review_paths.py', 'tests/sqlite_writer/test_dependencies.py',
                     'tests/sqlite_writer/test_backend.py'],
    'review-isolation': ['tests/sqlite_writer/test_review_isolation.py', 'tests/sqlite_writer/test_backend.py',
                         'tests/sqlite_writer/test_rpc.py', 'tests/sqlite_writer/test_faults.py'],
    'review-receipts': ['tests/sqlite_writer/test_review_receipts.py', 'tests/sqlite_writer/test_inspection.py',
                        'tests/runtime/test_runner_io_summary.py', 'tests/designer/test_node_run_inspection.py'],
    'a': ['tests/sqlite_writer/test_dependencies.py', 'tests/runtime/test_runtime_workflow_architecture.py',
          'tests/runtime/test_workflow_subflow_contracts.py', 'tests/runtime/test_workflow_loop_contracts_v2.py',
          'tests/designer/test_workflow_package.py', 'tests/runtime/test_operator_registration.py'],
    'b': ['tests/sqlite_writer', 'tests/runtime/test_runtime_grpc_network.py',
          'tests/runtime/test_runtime_project_execution.py', 'tests/runtime/test_runner_io_summary.py'],
    'c': ['tests/sqlite_writer', 'tests/designer/test_operator_editor_manager.py',
          'tests/designer/test_builtin_operator_editor_ui.py', 'tests/ui/page_designer/test_user_path.py'],
    'c-lifecycle': ['tests/sqlite_writer/test_editor.py::testProjectCloseDuringModalRejectsCallbacksAndInitialization'],
    'c-regression': ['tests/designer/test_flow_scene_interactions.py', 'tests/designer/test_flow_layout_qt.py',
        'tests/designer/test_flow_layout_routing.py', 'tests/designer/test_node_run_inspection.py',
        'tests/designer/test_node_result_panel.py', 'tests/designer/test_operator_editor_manager.py',
        'tests/designer/test_builtin_operator_editor_ui.py', 'tests/designer/test_workflow_package.py',
        'tests/core/project/test_package_builder.py', 'tests/runtime/test_runtime_grpc_network.py',
        'tests/runtime/test_runtime_project_execution.py', 'tests/runtime/test_runner_io_summary.py'],
    'ci-compat': ['tests/designer/test_main_window_project_save_load.py',
        'tests/runtime/presentation/test_normal_capture_status_validation.py',
        'tests/runtime/test_builtin_communication_operator_workflows.py',
        'tests/runtime/test_builtin_coordinate_operator_workflow.py',
        'tests/runtime/test_builtin_vision_operator_workflows.py', 'tests/runtime/test_real_onnx_result_pipeline.py',
        'tests/sqlite_writer/test_dependencies.py::testBuiltinSqlitePreservesActualCoreCapabilityGate'],
}


def candidate():
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=ROOT).decode('utf-8').strip()
    files = {str(path.relative_to(ROOT)).replace('\\', '/'): hashlib.sha256(path.read_bytes()).hexdigest()
             for folder in ('src', 'tests', 'proto', 'scripts') for path in (ROOT / folder).rglob('*')
             if path.is_file() and path.suffix in {'.py', '.proto', '.json', '.ui'} and '__pycache__' not in path.parts}
    return {'head': git('rev-parse', 'HEAD'), 'branch': git('branch', '--show-current'),
            'dirty': git('status', '--porcelain=v1', '-uall').splitlines(), 'sourceHashes': files,
            'sourceDigest': hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
            'python': sys.version, 'executable': sys.executable, 'os': platform.platform(),
            'sqlite': sqlite3.sqlite_version, 'dependencies': {key: importlib.metadata.version(key)
                for key in ('PySide2', 'grpcio', 'protobuf', 'numpy', 'opencv-python')}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=[*SUITES, 'ci'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    report = candidate()
    report['phase'] = args.phase
    (args.output / 'candidate.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    env = {**os.environ, 'PYTHONUTF8': '1', 'QT_QPA_PLATFORM': 'offscreen', 'HUARAY_CAMERA_SMOKE': '0',
           'PYTHONPATH': str(ROOT / 'src'), 'PYTEST_ADDOPTS': '--ignore=manual_test_workspace'}
    env.pop('PYTHONIOENCODING', None)
    command = [sys.executable, 'scripts/ci_check.py'] if args.phase == 'ci' else [sys.executable, '-m', 'pytest', '-q', *SUITES[args.phase]]
    begin = time.monotonic()
    with (args.output / 'raw.log').open('w', encoding='utf-8') as output:
        result = subprocess.run(command, cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT)
    report = {'command': command, 'exitCode': result.returncode, 'seconds': time.monotonic() - begin}
    (args.output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    print((args.output / 'raw.log').read_text(encoding='utf-8')[-7000:])
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
