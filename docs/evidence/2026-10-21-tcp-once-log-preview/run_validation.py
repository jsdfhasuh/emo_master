"""Reuse the final-closure snapshot runner without changing its historical evidence."""
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
    spec = importlib.util.spec_from_file_location('tcp_final_closure_validation', previous)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    runner.BASELINE = '4550d46e34e508cbb2774f7bcda8863d293541e6'
    runner.COUNTEREXAMPLES = [
        'tests/designer/test_tcp_closure_ui.py::testTcpOnceResultLogsKeepBoundedPreview',
        'tests/runtime/test_device_feedback_closure.py::testCompactResultUsesExactOriginalByteLength',
        'tests/runtime/test_device_feedback_closure.py::testFoldedByteResultRetainsOnlyBoundedPreviewAndCounts',
    ]
    runner.PRODUCT = [
        'src/emo_master/apps/runtime/devices/diagnostics.py',
        'src/emo_master/ui/communication_panel.py',
    ]
    result = runner.main()
    evidence = options.output_dir.resolve()
    validation = json.loads((evidence/'validation.json').read_text(encoding='utf-8'))
    validation['entryPointSha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (evidence/'validation.json').write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding='utf-8')
    artifacts = json.loads((evidence/'artifacts.json').read_text(encoding='utf-8'))
    for batch in ('counterexamples', 'chain'):
        for test in (Path(validation['temporaryRoot'])/batch).glob('testTcpOnceResultLogsKeepBound*'):
            if test.is_dir() and not test.is_symlink():
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
