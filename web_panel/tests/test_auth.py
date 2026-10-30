import tempfile
import unittest
from pathlib import Path
from emo_web_panel.auth import AuthStore

class AuthTests(unittest.TestCase):
 def test_last_admin_and_revoked_sessions(self):
  with tempfile.TemporaryDirectory() as d:
   a=AuthStore(Path(d)/'state');a.create_user('admin','good-password-123','admin');a.create_user('u','good-password-123','operator')
   token=a.login('u','good-password-123');self.assertIsNotNone(a.session(token))
   a.update_user('u',role='viewer');self.assertIsNone(a.session(token))
   with self.assertRaises(ValueError):a.delete_user('admin')
   with self.assertRaises(ValueError):a.update_user('admin',role='viewer')
   a.delete_user('u');self.assertEqual(len(a.users()),1)
