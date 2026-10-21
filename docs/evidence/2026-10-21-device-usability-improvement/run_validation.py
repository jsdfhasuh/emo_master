"""Validate a fixed usability source SHA using the existing archive/CHAIN runner."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--output-dir', type=Path, required=True)
    options, _ = parser.parse_known_args()
    previous = Path(__file__).resolve().parents[1] / '2026-10-21-tcp-device-final-closure/run_validation.py'
    spec = importlib.util.spec_from_file_location('device_usability_validation', previous)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    runner.BASELINE = '756e526c1d2c657b3ff793fed7abe904d38b8d48'
    runner.CHAIN += ['tests/runtime/test_device_usability_chain.py']
    runner.REGRESSION = list(dict.fromkeys([*runner.REGRESSION,
        'tests/designer/test_device_workspace_usability.py',
        'tests/designer/test_device_workspace_sizing.py',
        'tests/designer/test_device_usability_publication.py',
        'tests/designer/test_device_usability_drafts.py',
        'tests/designer/test_device_catalog.py',
        'tests/designer/test_device_project_open.py',
        'tests/designer/test_device_finish_ui.py',
        'tests/designer/test_device_plan_completion_ui.py',
        'tests/designer/test_device_package.py',
        'tests/designer/test_workflow_package_devices.py',
        'tests/designer/test_workflow_package_resources.py',
        'tests/designer/test_device_identity_reuse.py',
        'tests/designer/test_param_form.py',
        'tests/core/plugin/test_device_registry.py',
    ]))
    runner.COUNTEREXAMPLES = [
        'tests/designer/test_device_definition_feedback.py::testDisabledEmptyServerCanBeCreatedAndEnabledErrorFindsChannels',
        'tests/designer/test_device_definition_feedback.py::testEmptyTcpIpFindsExactCellAndGivesChineseFeedback',
        'tests/designer/test_device_usability_publication.py',
        'tests/designer/test_device_usability_drafts.py',
    ]
    runner.PRODUCT = [
        'src/emo_master/apps/designer/page_designer/coordinator.py',
        'src/emo_master/apps/designer/state/device_catalog.py',
        'src/emo_master/apps/designer/state/device_package.py',
        'src/emo_master/apps/designer/state/workflow_package.py',
        *['src/emo_master/apps/designer/ui/' + name + '.py' for name in (
            'device_wizard', 'device_workspace', 'main_window', 'param_form', 'project_configuration', 'tcp_device_form')],
        'src/emo_master/core/plugin/devices.py',
        'src/emo_master/apps/runtime/devices/tcp.py',
        'src/emo_master/plugins/builtins/_tcp_transport.py',
        *['src/emo_master/plugins/builtins/' + path for path in (
            'huaray_camera/device_types/imv.json', 'plc_slmp_read/device_types/slmp.json',
            'simulated_camera/device_types/camera.json', 'tcp_client/device_types/client_once.json',
            'tcp_receive_once/device_types/receive_once.json', 'tcp_session/device_types/server.json')],
    ]
    result = runner.main()
    evidence = options.output_dir.resolve()
    validation = json.loads((evidence/'validation.json').read_text(encoding='utf-8'))
    validation['entryPointSha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (evidence/'validation.json').write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding='utf-8')
    artifacts = json.loads((evidence/'artifacts.json').read_text(encoding='utf-8'))
    temporary = Path(validation['temporaryRoot'])
    for test in (temporary/'chain').glob('testC1ThroughC6PendingDraftPub*'):
        for path in test.rglob('*'):
            if (path.is_file() and not any(part in {'runtime', 'jobs'} for part in path.relative_to(test).parts)
                    and path.suffix in {'.json', '.emoproj', '.emodev', '.vxpkg', '.png'}):
                relative = Path('usability-chain')/test.name/path.relative_to(test)
                target = evidence/relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(path.read_bytes())
                artifacts.append(dict(source=str(path), retained=str(relative),
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    for test in (temporary/'chain').glob('testTcpOnceResultLogsKeepBound*'):
        for path in [*test.rglob('runtime-*.jsonl'), test/'once-preview.json']:
            if path.is_file():
                relative = Path('once-byte-logs')/test.name/path.relative_to(test)
                target = evidence/relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(path.read_bytes())
                artifacts.append(dict(source=str(path), retained=str(relative),
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    (evidence/'artifacts.json').write_text(json.dumps(artifacts, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    raise SystemExit(main())
