import tempfile
import unittest
from pathlib import Path
from emo_web_panel.registry import RuntimeRegistry, RegistryDenied, RegistryError

ADMIN = {'username': 'admin', 'role': 'admin'}
OP = {'username': 'op', 'role': 'operator'}
VIEWER = {'username': 'v', 'role': 'viewer'}


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = RuntimeRegistry(Path(self.tmp.name) / 'registry.sqlite')
        self.store.create(ADMIN, 'demo', 'Demo', 'mock')

    def test_crud_persistence(self):
        r = self.store.create(ADMIN, 'production', '生产', 'grpc', 'LOCALHOST:05051')
        self.assertEqual(r['address'], 'localhost:5051')
        self.store.update(ADMIN, 'production', alias='新的名称')
        other = RuntimeRegistry(self.store.path)
        self.assertEqual(other.get(ADMIN, 'production')['alias'], '新的名称')
        self.store.delete(ADMIN, 'production')
        self.assertEqual(len(other.list(ADMIN)), 1)
        with self.assertRaises(RegistryDenied):
            other.get(ADMIN, 'production')

    def test_explicit_acl(self):
        self.assertEqual(self.store.list(OP), [])
        with self.assertRaises(RegistryDenied):
            self.store.require(OP, 'demo', 'control')
        self.store.set_acl(ADMIN, 'demo', 'op', True)
        self.assertEqual(self.store.get(OP, 'demo')['permissions'], ['view'])
        self.assertNotIn('address', self.store.get(OP, 'demo'))
        with self.assertRaises(RegistryDenied):
            self.store.require(OP, 'demo', 'control')
        self.store.set_acl(ADMIN, 'demo', 'op', True, True)
        self.assertIn('control', self.store.require(OP, 'demo', 'control')['permissions'])
        self.store.set_acl(ADMIN, 'demo', 'op', False)
        self.assertEqual(self.store.list(OP), [])

    def test_role_ceiling(self):
        self.store.set_acl(ADMIN, 'demo', 'v', True, True)
        self.assertEqual(self.store.get(VIEWER, 'demo')['permissions'], ['view'])
        with self.assertRaises(RegistryDenied):
            self.store.require(VIEWER, 'demo', 'control')
        self.assertEqual(self.store.get(ADMIN, 'demo')['permissions'], ['view', 'control'])

    def test_admin_only_mutation(self):
        for actor in (OP, VIEWER, {}, None, {'username': 'x', 'role': []}):
            for operation in (
                lambda: self.store.create(actor, 'bad', 'Bad', 'mock'),
                lambda: self.store.update(actor, 'demo', alias='Bad'),
                lambda: self.store.delete(actor, 'demo'),
                lambda: self.store.set_acl(actor, 'demo', 'op', True, True),
                lambda: self.store.list_acl(actor, 'demo'),
                lambda: self.store.revoke_user(actor, 'op'),
            ):
                with self.subTest(actor=actor):
                    with self.assertRaises(RegistryDenied):
                        operation()

    def test_acl_references_delete(self):
        self.store.set_acl(ADMIN, 'demo', 'op', True, True)
        with self.assertRaises(RegistryError):
            self.store.delete(ADMIN, 'demo')
        with self.assertRaises(RegistryError):
            self.store.delete(ADMIN, 'demo', revoke_acl=True, in_use=True)
        self.store.delete(ADMIN, 'demo', revoke_acl=True)
        self.store.create(ADMIN, 'demo', 'Recreated', 'mock')
        self.assertEqual(self.store.list(OP), [])

    def test_revoke_deleted_user(self):
        self.store.set_acl(ADMIN, 'demo', 'op', True, True)
        self.store.revoke_user(ADMIN, 'op')
        self.assertEqual(self.store.list(OP), [])
        self.assertEqual(self.store.list_acl(ADMIN, 'demo'), [])

    def test_invalid_addresses(self):
        for address in ('https://host:443', 'user:pass@host:123', 'host:123/path', 'host:123?x', 'host:123#x', 'host', 'host:0', 'host:65536', 'host: 123', '-host:123', 'a..b:123', '127.1:1', '999.0.0.1:1', '[bad::xyz]:1', 'host:12\n', 'unix:/tmp/socket', 'dns:///host:1'):
            with self.subTest(address=address):
                with self.assertRaises(RegistryError):
                    self.store.create(ADMIN, 'bad', 'Bad', 'grpc', address)
        self.store.create(ADMIN, 'v6', 'IPv6', 'grpc', '[::1]:50051')
        self.assertEqual(self.store.get(ADMIN, 'v6')['address'], '[::1]:50051')

    def test_malformed_configuration(self):
        for kwargs in ({'runtime_id': '../bad'}, {'alias': ''}, {'alias': '\n'}, {'mode': 'http'}, {'mode': []}, {'address': 'localhost:1'}):
            args = dict(runtime_id='new', alias='New', mode='mock', address='')
            args.update(kwargs)
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(RegistryError):
                    self.store.create(ADMIN, **args)
        with self.assertRaises(RegistryError):
            self.store.create(ADMIN, 'demo', 'Duplicate', 'mock')
        with self.assertRaises(RegistryError):
            self.store.update(ADMIN, 'absent', alias='A')

    def test_acl_validation(self):
        for view, control in ((False, True), (1, False), (True, []), ('yes', False)):
            with self.assertRaises(RegistryError):
                self.store.set_acl(ADMIN, 'demo', 'op', view, control)
        with self.assertRaises(RegistryError):
            self.store.set_acl(ADMIN, 'absent', 'op', True)
        with self.assertRaises(RegistryError):
            self.store.require(ADMIN, 'demo', 'raw_rpc')

    def test_no_cross_target_leak(self):
        self.store.create(ADMIN, 'private', 'Secret', 'grpc', 'secret.local:50051')
        self.store.set_acl(ADMIN, 'demo', 'op', True)
        self.assertEqual([r['id'] for r in self.store.list(OP)], ['demo'])
        for target in ('private', 'not-existing'):
            with self.assertRaisesRegex(RegistryDenied, '^Target unavailable or unauthorized$'):
                self.store.get(OP, target)


if __name__ == '__main__':
    unittest.main()
