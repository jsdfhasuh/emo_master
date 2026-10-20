"""P5 supervised evidence; preserve every attempt with source identity."""
import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import platform
import sys

from p1_validate import git, run
from p4_validate import identity

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--suite', choices=['export', 'import', 'runtime', 'image-io', 'affected', 'ci', 'visual'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=300)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    tests = {
        'image-io': ['tests/plugins/test_image_loader_operator.py', 'tests/plugins/test_image_saver_operator.py',
                     'tests/plugins/test_unicode_image_io.py'],
        'export': ['tests/core/project/test_page_delivery.py', 'tests/core/project/test_package_builder.py',
                   'tests/core/presentation'],
        'import': ['tests/core/project/test_page_delivery.py', 'tests/core/project/test_delivery_store.py'],
        'runtime': ['tests/runtime/test_release_host.py', 'tests/ui/operator_view', 'tests/test_windows_package_entry.py',
                    'tests/test_p5_windows_dispatch.py'],
        'affected': ['tests/core/presentation', 'tests/runtime/presentation', 'tests/ui/presentation',
                     'tests/ui/page_designer', 'tests/core/project', 'tests/test_windows_package_entry.py'],
    }
    commands = ([[sys.executable, '-m', 'pytest', '-q', *tests[args.suite], '-rs'],
                 [sys.executable, '-m', 'ruff', 'check', 'src', 'tests'],
                 [sys.executable, '-m', 'mypy', '--config-file', 'mypy.ini', 'src']]
                if args.suite in tests else
                [[sys.executable, 'scripts/ci_check.py']] if args.suite == 'ci' else
                [[sys.executable, 'scripts/p5_visual_check.py', '--output', str(args.output/'screens')]])
    report = {'started_utc': datetime.now(timezone.utc).isoformat(), 'head': git('rev-parse', 'HEAD'),
              'dirty': git('status', '--short'), 'python': sys.version, 'os': platform.platform(),
              'executable': sys.executable, 'versions': {n: importlib.metadata.version(n)
                for n in ['PySide2', 'grpcio', 'protobuf', 'numpy', 'opencv-python']},
              'code_before': identity(), 'results': []}
    for index, command in enumerate(commands):
        result = run(command, ROOT, args.output/f'{index+1}.log', args.timeout)
        report['results'].append(result)
        print(result, flush=True)
    report['code_after'] = identity()
    report['code_stable'] = report['code_before'] == report['code_after']
    (args.output/'evidence.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return int(not report['code_stable'] or any(r['status'] != 'PASS' for r in report['results']))


if __name__ == '__main__':
    raise SystemExit(main())
