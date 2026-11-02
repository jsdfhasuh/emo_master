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

class ConcurrentAdminTests(unittest.TestCase):
    def test_concurrent_admin_demotions_leave_one_admin(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as d:
            store=AuthStore(Path(d)/'a.sqlite')
            store.create_user('a','safe-test-password','admin');store.create_user('b','safe-test-password','admin')
            def demote(name):
                try:store.update_user(name,role='viewer');return True
                except ValueError:return False
            with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(demote,['a','b']))
            self.assertEqual(sum(results),1)
            self.assertEqual(sum(u['role']=='admin' for u in store.users()),1)

class LoginRaceTests(unittest.TestCase):
    def test_password_reset_during_hash_cannot_mint_old_session(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as d:
            auth=AuthStore(Path(d)/'auth.sqlite');auth.create_user('user','original-password','viewer')
            entered=threading.Event();resume=threading.Event();real=auth.hash_password
            def paused(password,salt):
                result=real(password,salt)
                if password=='original-password':entered.set();resume.wait(timeout=5)
                return result
            auth.hash_password=paused
            with ThreadPoolExecutor(max_workers=1) as pool:
                attempt=pool.submit(auth.login,'user','original-password')
                self.assertTrue(entered.wait(timeout=5))
                auth.update_user('user',password='replacement-password')
                resume.set();self.assertIsNone(attempt.result(timeout=5))
            self.assertIsNotNone(auth.login('user','replacement-password'))
