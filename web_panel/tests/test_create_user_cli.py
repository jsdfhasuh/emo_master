"""Password-file bootstrap uses the real CLI and normal authentication path."""
import contextlib
import io
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from emo_web_panel.app import create_app
from emo_web_panel.auth import AuthStore
from emo_web_panel.config import Config
from emo_web_panel.main import main, read_password_file


class CreateUserCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root / 'data'
        self.password_file = self.root / 'password.txt'
        # Public, disposable test fixture, never a deployed account credential.
        self.password = '  synthetic-test-password-123  '

    def write_password(self, content=None):
        with self.password_file.open('wb') as stream:
            os.chmod(self.password_file, 0o600)
            stream.write(self.password.encode('utf-8') if content is None else content)

    def cli(self, *extra):
        return subprocess.run(
            [sys.executable, '-m', 'emo_web_panel.main', '--data-dir', str(self.data),
             'create-user', 'admin', '--password-file', str(self.password_file), *extra],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10,
        )

    def test_file_bootstrap_then_normal_http_login(self):
        self.write_password((self.password + '\r\n').encode('utf-8'))
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        self.assertIn('User created.', result.stdout)
        self.assertNotIn(self.password, result.stdout + result.stderr)
        self.assertTrue(self.password_file.exists())
        self.assertEqual(self.password_file.read_bytes(), (self.password + '\r\n').encode())
        database = self.data / 'web.sqlite'
        with sqlite3.connect(database) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 0)
            username, hashed, role = db.execute('SELECT username,password,role FROM users').fetchone()
            self.assertEqual((username, role), ('admin', 'admin'))
            self.assertNotEqual(hashed, self.password)
        self.assertNotIn(self.password.encode(), database.read_bytes())
        app = create_app(Config(self.data))
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/me').status_code, 401)
            wrong = client.post('/api/session', json={'username': 'admin', 'password': self.password.strip()})
            self.assertEqual(wrong.status_code, 401)
            login = client.post('/api/session', json={'username': 'admin', 'password': self.password})
            self.assertEqual(login.status_code, 200)
            self.assertEqual(login.json()['role'], 'admin')
            self.assertEqual(client.get('/api/admin/users').status_code, 200)
        for log in (self.data / 'logs').glob('*'):
            self.assertNotIn(self.password.encode(), log.read_bytes())

    def test_utf8_bom_line_endings_and_character_limits(self):
        for text in ('a' * 12, '密' * 12, '🙂' * 1024, self.password):
            for bom in (b'', b'\xef\xbb\xbf'):
                for ending in (b'', b'\n', b'\r\n'):
                    with self.subTest(length=len(text), bom=bool(bom), ending=ending):
                        self.write_password(bom + text.encode('utf-8') + ending)
                        self.assertEqual(read_password_file(self.password_file), text)

    def test_invalid_contents_never_create_database_or_leak_content(self):
        for content in (b'', b'short', b'a' * 1025, b'x' * 5000, b'\xff' * 12,
                        b'first-password\nsecond-password', b'password-value\n\n',
                        b'password-value\r', b'password\x00value', b'\xef\xbb\xbf\n'):
            with self.subTest(length=len(content)):
                self.write_password(content)
                result = self.cli()
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
                self.assertNotIn('Traceback', result.stderr)
                if content:
                    self.assertNotIn(repr(content), result.stderr)
                self.assertFalse(self.data.exists())

    def test_missing_or_directory_file_fails_without_database(self):
        result = self.cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn('cannot read password file', result.stderr)
        self.password_file.mkdir()
        result = self.cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn('regular file', result.stderr)
        self.assertFalse(self.data.exists())

    @unittest.skipUnless(os.name == 'posix', 'POSIX ownership and permission checks')
    def test_public_permissions_rejected_and_read_only_private_file_accepted(self):
        self.write_password()
        for mode in (0o644, 0o640, 0o604, 0o606):
            with self.subTest(mode=oct(mode)):
                self.password_file.chmod(mode)
                result = self.cli()
                self.assertEqual(result.returncode, 2)
                self.assertIn('0600 or 0400', result.stderr)
                self.assertFalse(self.data.exists())
        self.password_file.chmod(0o400)
        self.assertEqual(read_password_file(self.password_file), self.password)

    @unittest.skipUnless(os.name == 'posix', 'POSIX ownership check')
    def test_different_file_owner_rejected(self):
        self.write_password()
        with patch('emo_web_panel.main.os.geteuid', return_value=os.geteuid() + 1):
            with self.assertRaisesRegex(ValueError, 'owned by the current user'):
                read_password_file(self.password_file)

    @unittest.skipUnless(os.name == 'posix', 'POSIX FIFO and symlink fixtures')
    def test_symlink_and_fifo_rejected_without_waiting(self):
        target = self.root / 'target.txt'
        target.write_text(self.password, encoding='utf-8')
        target.chmod(0o600)
        self.password_file.symlink_to(target)
        result = self.cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn('regular file', result.stderr)
        self.password_file.unlink()
        os.mkfifo(self.password_file, 0o600)
        result = self.cli()
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.data.exists())

    def test_duplicate_user_does_not_reset_password_or_role(self):
        self.write_password()
        self.assertEqual(self.cli('--role', 'viewer').returncode, 0)
        self.write_password(b'different-test-password')
        result = self.cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn('does not reset', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        auth = AuthStore(self.data / 'web.sqlite')
        self.assertEqual(auth.users(), [{'username': 'admin', 'role': 'viewer'}])
        self.assertIsNotNone(auth.login('admin', self.password))
        self.assertIsNone(auth.login('admin', 'different-test-password'))

    def test_password_file_never_prompts(self):
        self.write_password()
        argv = ['emo-web-panel', '--data-dir', str(self.data), 'create-user', 'admin',
                '--password-file', str(self.password_file)]
        with patch('sys.argv', argv), patch('emo_web_panel.main.getpass.getpass') as prompt:
            with contextlib.redirect_stdout(io.StringIO()):
                main()
        prompt.assert_not_called()

    def test_interactive_input_still_confirms(self):
        argv = ['emo-web-panel', '--data-dir', str(self.data), 'create-user', 'admin']
        with patch('sys.argv', argv), \
                patch('emo_web_panel.main.getpass.getpass', side_effect=[self.password] * 2) as prompt:
            with contextlib.redirect_stdout(io.StringIO()):
                main()
        self.assertEqual(prompt.call_count, 2)
        self.assertIsNotNone(AuthStore(self.data / 'web.sqlite').login('admin', self.password))

    def test_interactive_mismatch_still_fails_before_creating_database(self):
        argv = ['emo-web-panel', '--data-dir', str(self.data), 'create-user', 'admin']
        with patch('sys.argv', argv), \
                patch('emo_web_panel.main.getpass.getpass', side_effect=[self.password, 'not-matching']):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                main()
        self.assertEqual(error.exception.code, 2)
        self.assertFalse(self.data.exists())


if __name__ == '__main__':
    unittest.main()
