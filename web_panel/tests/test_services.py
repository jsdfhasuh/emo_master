import asyncio
import tempfile
import unittest
from pathlib import Path
from emo_web_panel.services import Views, Operations
from emo_web_panel.mock_runtime import MockRuntime

class ServicesTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_subscription_independent_freeze_and_cleanup(self):
        rt=MockRuntime();v=Views(rt)
        a=v.create('a','p','demo-run');b=v.create('b','p','demo-run')
        await v.attach(a);await v.attach(b)
        self.assertEqual(len(v.streams),1)
        first=await asyncio.wait_for(a['queue'].get(),2);await b['queue'].get()
        a['frozen']=True;a['freezeUntil']=1e20
        second=await asyncio.wait_for(a['queue'].get(),2);other=await b['queue'].get()
        self.assertEqual(first['result']['resultKey'],second['result']['resultKey'])
        self.assertNotEqual(second['result']['resultKey'],other['result']['resultKey'])
        await v.remove(a['id']);self.assertEqual(len(v.streams),1)
        await v.remove(b['id']);self.assertFalse(v.streams);self.assertTrue(rt.running)
    async def test_persisted_intent_not_replayed(self):
        with tempfile.TemporaryDirectory() as t:
            rt=MockRuntime();path=Path(t)/'state.db';a=Operations(path)
            body={'configurationRevision':'1','runtimeInstanceId':'mock-instance','projectId':'demo-project','workflowId':'demo-workflow'}
            x=await a.execute('u','stable-1234','start','',body,rt)
            b=Operations(path);y=await b.execute('u','stable-1234','start','',body,rt)
            self.assertEqual(x,y);self.assertEqual(rt.control_count,1)
