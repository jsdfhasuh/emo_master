"""Reuse the fixed-source TCP closure runner for four usability review fixes."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--output-dir', type=Path, required=True)
    options, _ = parser.parse_known_args()
    previous = Path(__file__).resolve().parents[1]/'2026-10-21-tcp-device-final-closure/run_validation.py'
    spec = importlib.util.spec_from_file_location('review_validation', previous)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    runner.BASELINE = 'eefb930481eda864cf03b0725407192c41eff17a'
    runner.CHAIN += ['tests/runtime/test_device_usability_chain.py']
    runner.REGRESSION = list(dict.fromkeys([*runner.REGRESSION,
        *['tests/designer/test_' + name + '.py' for name in (
            'device_workspace_usability', 'device_workspace_sizing', 'device_usability_publication',
            'device_usability_drafts', 'device_catalog', 'device_project_open', 'device_finish_ui',
            'device_plan_completion_ui', 'device_package', 'workflow_package_devices',
            'workflow_package_resources', 'device_identity_reuse', 'param_form')],
        'tests/core/plugin/test_device_registry.py',
    ]))
    runner.COUNTEREXAMPLES = [
        *['tests/designer/test_device_workspace_usability.py::test' + name for name in (
            'ConnectionReceiptBelongsToDeviceAndCandidate', 'DetailConnectionValidationIsChineseFocusedAndReadOnly',
            'DetailFieldFeedbackClearsOnlyCorrectedInputAndSurvivesState', 'DetailChannelFeedbackClearsOnlyEditedErrorCell',
            'DisabledEmptyDetailConnectionTestUsesCandidateWithoutApplying')],
        *['tests/designer/test_device_definition_feedback.py::test' + name for name in (
            'CreationConnectionReceiptBelongsToCandidate', 'MissingSlotFeedbackClearsOnlyWhenThatSlotIsSelected')],
        *['tests/designer/test_device_usability_drafts.py::test' + name for name in (
            'SelectorDefaultsAgreeBetweenDraftAndCompiledPlan', 'InvalidSelectorsRetainStrictLocation')],
        'tests/runtime/test_tcp_device_closure.py::testSelectorDefaultReachesRealSpawnedJobAndFrozenAdmission',
    ]
    runner.PRODUCT = [
        *['src/emo_master/apps/designer/ui/' + name + '.py' for name in (
            'device_workspace', 'device_wizard', 'tcp_device_form')],
        'src/emo_master/apps/designer/state/device_catalog.py',
        'src/emo_master/apps/runtime/devices/manager.py',
        'src/emo_master/apps/runtime/grpc_server/service.py',
        'src/emo_master/core/workflow/compiler.py',
    ]
    result = runner.main()
    evidence = options.output_dir.resolve()
    validation = json.loads((evidence/'validation.json').read_text(encoding='utf-8'))
    validation['entryPointSha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (evidence/'validation.json').write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding='utf-8')
    artifacts = json.loads((evidence/'artifacts.json').read_text(encoding='utf-8'))
    temporary = Path(validation['temporaryRoot'])
    for batch in ('counterexamples', 'chain', 'regression'):
        for pattern, target in [('testC1ThroughC6PendingDraftPub*', 'usability-chain'),
                                ('testSelectorDefaultReachesRea*', 'selector-job'),
                                ('testTcpOnceResultLogsKeepBound*', 'once-byte-logs')]:
            for test in (temporary/batch).glob(pattern):
                for path in test.rglob('*'):
                    relative = path.relative_to(test)
                    if (path.is_file() and not any(part in {'runtime', 'jobs'} for part in relative.parts)
                            and path.suffix in {'.json', '.emoproj', '.emodev', '.vxpkg', '.png', '.jsonl'}):
                        destination = Path(target)/batch/test.name/relative
                        output = evidence/destination
                        output.parent.mkdir(parents=True, exist_ok=True)
                        output.write_bytes(path.read_bytes())
                        artifacts.append(dict(source=str(path), retained=str(destination),
                            sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    (evidence/'artifacts.json').write_text(json.dumps(artifacts, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    raise SystemExit(main())
