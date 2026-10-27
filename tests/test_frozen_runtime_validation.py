"""Portable harness guard tests; these do not substitute for frozen Windows runs."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
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
            self.assertTrue(Path(env['EMO_DESIGNER_SETTINGS_PATH']).is_relative_to(Path(root)))
            self.assertFalse(Path(env['EMO_DESIGNER_SETTINGS_PATH']).exists())
            self.assertEqual(env['QT_ACCESSIBILITY'], '1')

    def testWaitCannotPassAnExitedProduct(self):
        process = type('Exited', (), {'returncode': 1, 'poll': lambda self: 1})()
        with self.assertRaises(AssertionError):
            harness.wait_for(lambda: True, seconds=1, process=process)

    def testDesignerRootSelectionUsesAccessibleControlsWithoutStarting(self):
        process = SimpleNamespace(pid=123)
        wrapper = SimpleNamespace(handle=456)
        settings = MagicMock(spec_set=['child_window'])
        localButton = MagicMock(spec_set=['click_input'])
        combo = MagicMock(spec_set=['child_window'])
        edit = MagicMock(spec_set=['set_edit_text'])
        combo.child_window.return_value = edit
        controls = {('本机运行', 'CheckBox'): localButton, ('运行数据目录', 'ComboBox'): combo}
        settings.child_window.side_effect = lambda **criteria: controls[(criteria['title'], criteria['control_type'])]
        desktop = MagicMock(spec_set=['windows', 'window'])
        desktop.windows.return_value = [wrapper]
        desktop.window.return_value = settings
        factory = MagicMock(return_value=desktop)
        root = Path("test-only-runtime")
        with patch.dict('sys.modules', {'pywinauto': SimpleNamespace(Desktop=factory)}), patch.object(
                harness, "wait_for", side_effect=lambda action, **_kwargs: action()):
            self.assertIs(harness.choose_designer_root(process, root), settings)
        factory.assert_called_once_with(backend='uia')
        desktop.windows.assert_called_once_with(process=123, title='Designer 启动设置', visible_only=True)
        desktop.window.assert_called_once_with(handle=456)
        settings.child_window.assert_any_call(title="本机运行", control_type="CheckBox")
        settings.child_window.assert_any_call(title="运行数据目录", control_type="ComboBox")
        localButton.click_input.assert_called_once_with()
        combo.child_window.assert_called_once_with(control_type='Edit')
        edit.set_edit_text.assert_called_once_with(str(root))
        self.assertNotIn(unittest.mock.call(title="启动", control_type="Button"), settings.child_window.call_args_list)

    def testMissingVisibleWindowDoesNotCreateASpecification(self):
        desktop = MagicMock(spec_set=['windows', 'window'])
        desktop.windows.return_value = []
        with patch.dict('sys.modules', {'pywinauto': SimpleNamespace(Desktop=lambda **_: desktop)}):
            self.assertIsNone(harness.window_for(SimpleNamespace(pid=123), 'Designer 启动设置'))
        desktop.windows.assert_called_once_with(process=123, title='Designer 启动设置', visible_only=True)
        desktop.window.assert_not_called()

    def testDesignerStartWaitsForProjectEntryAndCloseRequiresCleanExit(self):
        settings = MagicMock(spec_set=['child_window'])
        startButton = MagicMock(spec_set=['click_input'])
        settings.child_window.return_value = startButton
        entry = MagicMock(spec_set=['close'])
        process = MagicMock(spec_set=['wait'])
        process.wait.return_value = 0
        with patch.object(harness, "window_for", return_value=entry) as window, patch.object(
                harness, "wait_for", side_effect=lambda action, **_kwargs: action()):
            self.assertIs(harness.start_designer_selection(process, settings), entry)
        settings.child_window.assert_called_once_with(title="启动", control_type="Button")
        settings.child_window.return_value.click_input.assert_called_once()
        window.assert_called_once_with(process, "项目入口")
        harness.close_designer_entry(process, entry)
        entry.close.assert_called_once()
        process.wait.assert_called_once_with(timeout=30)
        process.wait.return_value = 2
        with self.assertRaises(AssertionError):
            harness.close_designer_entry(process, entry)

    def testBusyChooserWaitsForErrorRatherThanTheInitialSettingsWindow(self):
        settings, process = MagicMock(), MagicMock()
        settings.descendants.return_value = []
        with patch.object(harness, "window_for", return_value=settings):
            settings.texts.return_value = ["Designer 启动设置", "运行数据目录"]
            self.assertIsNone(harness.busy_designer_settings(process, Path("first")))
            settings.texts.return_value = ["first", "不要删除锁文件或数据库"]
            self.assertIs(harness.busy_designer_settings(process, Path("first")), settings)
            self.assertIsNone(harness.busy_designer_settings(process, Path("other")))

    def testDesignerScenarioExercisesOwnedDoubleStartReselectCloseAndReopen(self):
        with tempfile.TemporaryDirectory() as directory:
            root, processes, choices, starts = Path(directory), [], [], []
            def launch(name, _arguments):
                process = SimpleNamespace(pid=100 + len(processes), done=False, name=name)
                process.poll = lambda: 0 if process.done else None
                process.wait = lambda timeout: 0 if process.done else 2
                processes.append(process)
                return process
            def choose(process, path):
                choices.append((process.name, path))
                chooser = MagicMock()
                chooser.root = path
                chooser.close.side_effect = lambda: setattr(process, "done", True)
                return chooser
            def start(process, chooser):
                starts.append((process.name, chooser.root))
                chooser.root.mkdir(parents=True, exist_ok=True)
                (chooser.root / "emo_master.db").touch()
                entry = MagicMock()
                entry.close.side_effect = lambda: setattr(process, "done", True)
                entry.is_visible.return_value = True
                return entry
            report = {}
            product = SimpleNamespace(output=root, start=launch)
            with patch.object(harness, "choose_designer_root", side_effect=choose), patch.object(
                    harness, "start_designer_selection", side_effect=start), patch.object(
                    harness, "capture_window"), patch.object(harness, "busy_designer_settings", return_value=MagicMock()), patch.object(
                    harness, "wait_for", side_effect=lambda action, **_kwargs: action()):
                harness.designer(product, report)
            self.assertEqual(report["status"], "PASS")
            self.assertEqual([path for _, path in starts], [root / "designer-first", root / "designer-second", root / "designer-first"])
            self.assertEqual(len(choices), 5)
            self.assertEqual(choices[2][1], root / "designer-first")
            self.assertEqual(choices[3][1], root / "designer-second")
            self.assertTrue(all(process.done for process in processes))
            self.assertFalse((root / "designer-cancelled").exists())


if __name__ == '__main__':
    unittest.main()
