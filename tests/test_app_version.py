from email.parser import Parser
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def versionProject(tmp_path: Path) -> Path:
    shutil.copyfile(ROOT / 'pyproject.toml', tmp_path / 'pyproject.toml')
    package = tmp_path / 'src/emo_master'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text(
        'raise RuntimeError("Version readers must not import the application")\n',
        encoding='utf-8',
    )
    (package / 'app_version.py').write_text('APP_VERSION = "8.7.6"\n', encoding='utf-8')
    return tmp_path


def testPackageMetadataReadsAppVersion(versionProject: Path) -> None:
    metadata = versionProject / 'metadata'
    metadata.mkdir()
    result = subprocess.run(
        [sys.executable, '-c',
         'from setuptools.build_meta import prepare_metadata_for_build_wheel; '
         'prepare_metadata_for_build_wheel("metadata")'],
        cwd=versionProject, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    files = list(metadata.glob('*.dist-info/METADATA'))
    assert len(files) == 1
    fields = Parser().parsestr(files[0].read_text(encoding='utf-8'))
    assert fields['Name'] == 'emo_master'
    assert fields['Version'] == '8.7.6'


def testFrozenWorkflowReadsAppVersionWithoutImport(versionProject: Path) -> None:
    workflow = yaml.safe_load(
        (ROOT / '.github/workflows/validate-windows-package.yml').read_text(encoding='utf-8')
    )
    step = next(step for step in workflow['jobs']['prepare']['steps']
                if step.get('id') == 'source')
    command = re.search(r'^\s*\$version = python -c "([^"]+)"', step['run'], re.MULTILINE)
    assert command is not None
    result = subprocess.run(
        [sys.executable, '-c', command.group(1)], cwd=versionProject,
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == '8.7.6'
