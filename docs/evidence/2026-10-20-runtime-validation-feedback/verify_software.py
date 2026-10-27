"""Affected software checks only; no real devices, deployment or user projects."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import os
import re
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[3]
OUTPUT = Path(__file__).resolve().parent


def run(name, args):
    start = time.monotonic()
    with (OUTPUT / (name + '.log')).open('w', encoding='utf-8') as stream:
        result = subprocess.run(args, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, check=False)
    return dict(command=args, exitCode=result.returncode, seconds=round(time.monotonic() - start, 2))


def main():
    os.environ.update(QT_QPA_PLATFORM='offscreen', PYTHONIOENCODING='utf-8')
    previous = (ROOT / 'docs/testing/2026-10-20-runtime-menu-device-layout.md').read_text(encoding='utf-8')
    listing = re.search(r'\$files = @\((.*?)\n\)', previous, re.S).group(1)
    files = re.findall(r"'([^']+\.py)'", listing)
    assert len(files) == 37
    files += ['tests/designer/test_runtime_validation_feedback.py',
              'tests/core/test_runtime_directory.py', 'tests/core/test_global_variable_file_bindings.py',
              'tests/core/project/test_device_resources.py', 'tests/core/project/test_project_models.py',
              'tests/core/presentation/test_edit_session.py', 'tests/designer/test_device_catalog.py']
    old = json.loads((ROOT / 'docs/evidence/2026-10-20-runtime-menu/review-fixes/source-manifest.json').read_text(encoding='utf-8-sig'))
    paths = {f['path'] for f in old['files']} | set(files) | {
        'src/emo_master/apps/designer/ui/runtime_connection.py',
        'src/emo_master/apps/runtime/project_config/rpc.py',
        'src/emo_master/apps/runtime/project_config/coordinator.py',
        'src/emo_master/core/project/runtime_directory.py',
        'tests/designer/runtime_feedback_fixture.py'}
    def hashes():
        return [dict(path=p, sha256=hashlib.sha256((ROOT / p).read_bytes()).hexdigest()) for p in sorted(paths)]
    before = hashes()
    summary = dict(createdAt=datetime.now(timezone.utc).isoformat(),
                   head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                   python=sys.executable, files=files, sourceBefore=before)
    summary['regression'] = run('acceptance-regression', [sys.executable, '-u', '-m', 'pytest', *files, '-q', '--tb=short',
        '-o', 'faulthandler_timeout=90', '--junitxml=' + str(OUTPUT / 'acceptance-regression.xml')])
    summary['proto'] = run('proto-check', [sys.executable, 'scripts/gen_proto.py', '--check'])
    summary['ruff'] = run('ruff', [sys.executable, '-m', 'ruff', 'check',
        *sorted(p for p in paths if p.endswith('.py') and '/generated/' not in p)])
    summary['diff'] = run('diff-check', ['git', '-c', 'core.safecrlf=false', 'diff', '--check'])
    summary['sourceAfter'] = hashes()
    summary['sameSource'] = before == summary['sourceAfter']
    (OUTPUT / 'software-verification.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in summary.items() if k not in {'sourceBefore', 'sourceAfter', 'files'}}, ensure_ascii=False, indent=2))
    return 0 if summary['sameSource'] and all(summary[k]['exitCode'] == 0 for k in ('regression', 'proto', 'ruff', 'diff')) else 1


if __name__ == '__main__':
    raise SystemExit(main())
