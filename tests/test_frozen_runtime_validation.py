"""Portable harness guard tests; these do not substitute for frozen Windows runs."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SPEC = importlib.util.spec_from_file_location('frozen_validation', Path(__file__).resolve().parents[1] / 'scripts/verify_frozen_runtime.py')
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)


class FrozenHarnessTests(unittest.TestCase):
    def testConsoleReportSupportsWindowsLegacyEncoding(self):
        report = {'status': 'PASS', 'window_title': '项目入口'}
        rendered = harness.console_json(report)
        rendered.encode('cp1252')
        self.assertEqual(json.loads(rendered), report)

    def testAbortedJobIsTerminal(self):
        self.assertIn('ABORTED', harness.TERMINAL_JOB_STATUSES)
        self.assertNotIn('STOPPED', harness.TERMINAL_JOB_STATUSES)

    def testRejectsVacuousIcons(self):
        for data in [{}, {'status': 'ok'}, {'status': 'ok', 'checks': {'guiIcons': {'status': 'ok', 'samples': []}}}]:
            with self.assertRaises(AssertionError):
                harness.validate_icons(data)

    def testRequiresAllNineIconSamples(self):
        samples = [{'operatorId': name, 'scale': scale, 'renderSource': 'custom', 'pixelSize': int(24 * scale),
                    'nonTransparentPixels': 21, 'accentPixels': 9, 'sha256': 'a' * 64}
                   for name in ['one', 'two', 'three'] for scale in [1, 1.5, 2]]
        report = {'status': 'ok', 'checks': {'guiIcons': {'status': 'ok', 'samples': samples}}}
        harness.validate_icons(report)
        report['checks']['guiIcons']['samples'] = [samples[0]] * 9
        with self.assertRaises(AssertionError):
            harness.validate_icons(report)

    def testRejectsTraversalBeforeExecution(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            for index, name in enumerate(['../EmoMaster.exe', 'C:/EmoMaster.exe', '..\\EmoMaster.exe']):
                archive = root / f'{index}.zip'
                with zipfile.ZipFile(archive, 'w') as bundle:
                    bundle.writestr(name, b'not executed')
                with self.assertRaises(AssertionError):
                    harness.extract(archive, root / 'product')

    def testRejectsDuplicateExecutables(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            archive = root / 'test.zip'
            with zipfile.ZipFile(archive, 'w') as bundle:
                for name in ['a/EmoMaster.exe', 'b/emomaster.exe']:
                    bundle.writestr(name, b'not executed')
            with self.assertRaises(AssertionError):
                harness.extract(archive, root / 'product')

    def testSourceAndArtifactHashesAreBound(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root / 'product.zip').write_bytes(b'zip')
            (root / 'setup.exe').write_bytes(b'installer')
            assets = {kind: {'name': name, 'sha256': harness.digest(root / name), 'url': 'https://example.test/' + name}
                      for kind, name in [('portable', 'product.zip'), ('setup', 'setup.exe')]}
            manifest = {'source_repo': 'jsdfhasuh/emo_master', 'source_commit': 'a' * 40,
                        'sha256': assets['portable']['sha256'], 'url': assets['portable']['url'], 'assets': assets}
            (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            archive, _ = harness.verify_artifacts(root, 'a' * 40)
            self.assertEqual(archive, root / 'product.zip')
            with self.assertRaises(AssertionError):
                harness.verify_artifacts(root, 'b' * 40)
            (root / 'setup.exe').write_bytes(b'tampered')
            with self.assertRaises(AssertionError):
                harness.verify_artifacts(root, 'a' * 40)

    def testEnvironmentIsIsolatedAndNative(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {
                'PYTHONPATH': 'source', 'QT_QPA_PLATFORM': 'offscreen', 'EMO_RUNTIME_TARGET': 'external',
                'EMO_RUNTIME_DB_PATH': 'outside'}):
            env = harness.isolated_environment(Path(root))
            for name in ['PYTHONPATH', 'QT_QPA_PLATFORM', 'EMO_RUNTIME_TARGET', 'EMO_RUNTIME_DB_PATH']:
                self.assertNotIn(name, env)
            self.assertTrue(Path(env['EMO_RUNTIME_DATA_DIR']).is_relative_to(Path(root)))
            self.assertEqual(env['QT_ACCESSIBILITY'], '1')

    def testWaitCannotPassAnExitedProduct(self):
        process = type('Exited', (), {'returncode': 1, 'poll': lambda self: 1})()
        with self.assertRaises(AssertionError):
            harness.wait_for(lambda: True, seconds=1, process=process)


if __name__ == '__main__':
    unittest.main()
