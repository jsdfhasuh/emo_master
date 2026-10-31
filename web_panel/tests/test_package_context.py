"""Per-Runtime/project activation, pinned versions, ACLs and one-time migration."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import test_app
from test_packages import bundle
from emo_web_panel.packages import PackageStore


class PackageContextTests(unittest.TestCase):
    setUp = test_app.AppTests.setUp
    tearDown = test_app.AppTests.tearDown
    login = test_app.AppTests.login

    def install(self, headers, revision, project='demo-project'):
        response = self.client.post('/api/packages', headers=headers,
                                    content=bundle(revision, changes={'projectId': project}))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['packageId']

    def activate(self, headers, pid, rid='configured', **extra):
        return self.client.post('/api/packages/' + pid + '/activate', headers=headers,
                                json={'runtimeId': rid, **extra})

    def add_b(self, headers):
        self.assertEqual(self.client.post('/api/admin/runtimes', headers=headers,
                         json={'id':'B','alias':'Line B','mode':'mock','address':''}).status_code, 200)

    def test_a_b_activation_rollback_pinned_view_and_scoped_notices(self):
        headers = self.login(); self.add_b(headers)
        a1 = self.install(headers, 'a1'); a2 = self.install(headers, 'a2'); b1 = self.install(headers, 'b1')
        self.assertEqual(self.activate(headers, a1).status_code, 200)
        self.assertEqual(self.activate(headers, b1, 'B').status_code, 200)
        def view(pid, rid):
            response = self.client.post('/api/views', headers=headers,
                json={'runtimeId':rid,'projectId':'demo-project','panelId':pid})
            self.assertEqual(response.status_code, 200, response.text)
            return self.app.state.views.views[response.json()['id']]
        av, bv = view(a1, 'configured'), view(b1, 'B')
        self.assertEqual(self.activate(headers, a2).status_code, 200)
        self.assertEqual(self.client.get('/api/panels?runtimeId=configured').json()[0]['id'], a2)
        self.assertEqual(self.client.get('/api/panels?runtimeId=B').json()[0]['id'], b1)
        self.assertEqual(av['revision'], 'a1')
        self.assertEqual(av['notices']['package_updated']['packageId'], a2)
        self.assertNotIn('package_updated', bv['notices'])
        self.assertEqual(self.client.get('/api/assets/' + a1 + '/sample').status_code, 200)
        self.assertEqual(self.client.post('/api/views', headers=headers,
                         json={'runtimeId':'configured','panelId':a1}).status_code, 404)
        flags = {x['packageId']:x['active'] for x in self.client.get('/api/packages?runtimeId=B').json()}
        self.assertTrue(flags[b1]); self.assertFalse(flags[a2])
        self.assertEqual(self.activate(headers, a1).status_code, 200)
        self.assertEqual(self.client.get('/api/panels?runtimeId=B').json()[0]['id'], b1)
        self.assertEqual(self.app.state.runtime.control_count, 0)

    def test_shared_package_update_never_revokes_other_target_acl(self):
        headers=self.login(); self.add_b(headers)
        shared=self.install(headers,'shared'); replacement=self.install(headers,'replacement')
        self.assertEqual(self.client.post('/api/packages/'+shared+'/activate',headers=headers,
                         json={'runtimeIds':['configured','B']}).status_code,200)
        self.activate(headers,replacement)
        self.activate(headers,shared)
        row=next(x for x in self.client.get('/api/packages?runtimeId=B').json() if x['packageId']==shared)
        self.assertEqual(set(row['runtimeIds']),{'configured','B'})
        self.client.put('/api/admin/runtimes/B/grants',headers=headers,
                        json={'username':'viewer','view':True,'control':False})
        vh=self.login('viewer')
        self.assertEqual(self.client.get('/api/panels?runtimeId=B').json()[0]['id'],shared)
        self.assertEqual(self.client.get('/api/panels?runtimeId=configured').status_code,403)
        self.assertEqual(self.client.get('/api/panels/'+shared+'?runtimeId=configured').status_code,403)
        self.assertEqual(self.client.get('/api/panels/'+shared+'?runtimeId=B').status_code,200)
        self.assertEqual(self.client.post('/api/views',headers=vh,
                         json={'panelId':shared,'runtimeId':'configured'}).status_code,403)
        self.assertEqual(self.client.get('/api/assets/'+shared+'/sample').status_code,200)

    def test_formal_project_mismatch_leaves_activation_and_associations(self):
        headers=self.login()
        old=self.install(headers,'old'); wrong=self.install(headers,'wrong','another-project')
        self.activate(headers,old)
        response=self.activate(headers,wrong)
        self.assertEqual(response.status_code,409)
        self.assertIn('formal project',response.json()['detail'])
        self.assertEqual(self.client.get('/api/panels').json()[0]['id'],old)
        with sqlite3.connect(Path(self.tmp.name)/'web.sqlite') as db:
            self.assertEqual(db.execute('SELECT runtime_id FROM package_targets WHERE package_id=?',(wrong,)).fetchall(),[])
        self.assertEqual(self.client.get('/api/panels?runtimeId=configured&projectId=another-project').status_code,409)

    def test_project_change_does_not_reuse_prior_project_activation(self):
        headers=self.login(); first=self.install(headers,'first')
        second=self.install(headers,'second','next-project')
        self.activate(headers,first)
        original=self.app.state.runtime.info
        async def changed():
            return {**await original(),'projectId':'next-project'}
        self.app.state.runtime.info=changed
        self.assertEqual(self.client.get('/api/panels').json(),[])
        self.assertEqual(self.activate(headers,second).status_code,200)
        self.assertEqual(self.client.get('/api/panels').json()[0]['id'],second)
        self.app.state.runtime.info=original
        self.assertEqual(self.client.get('/api/panels').json()[0]['id'],first)


class PackageMigrationTests(unittest.TestCase):
    def test_legacy_migration_explicit_known_targets_only_and_once(self):
        for with_links in (False,True):
            with self.subTest(with_links=with_links), tempfile.TemporaryDirectory() as directory:
                root=Path(directory)/'packages'; dbpath=Path(directory)/'web.sqlite'
                store=PackageStore(root, database=dbpath)
                item=store.install(bundle('old')); pid=item['packageId']
                (root/'active').write_text(pid)
                with sqlite3.connect(dbpath) as db:
                    db.execute('CREATE TABLE runtimes (id TEXT PRIMARY KEY)')
                    db.executemany('INSERT INTO runtimes VALUES (?)',[('A',),('B',)])
                    if with_links:
                        db.executemany('INSERT INTO package_targets VALUES (?,?)',[(pid,'A'),(pid,'gone')])
                    db.execute("DELETE FROM package_migrations WHERE name='context-active-v1'")
                migrated=PackageStore(root,database=dbpath)
                self.assertEqual(migrated.activations(),[{'runtimeId':'A','projectId':'demo-project','packageId':pid}] if with_links else [])
                self.assertIsNone(migrated.active('B','demo-project'))
                self.assertTrue((root/'active').is_file()); self.assertTrue((root/pid/'metadata.json').is_file())
                later=migrated.install(bundle('later'))
                migrated.activate(later['packageId'],runtime_id='A')
                restarted=PackageStore(root,database=dbpath)
                self.assertEqual(restarted.active('A','demo-project')['packageId'],later['packageId'])
                self.assertIsNone(restarted.active('B','demo-project'))


if __name__ == '__main__':unittest.main()
