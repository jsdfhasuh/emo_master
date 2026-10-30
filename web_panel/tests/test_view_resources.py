import asyncio
import hashlib
import unittest

from emo_web_panel.resource_cache import ResourceCache
from emo_web_panel.services import Views


def result(scope, key, instance='instance-1'):
    return {'runtimeInstanceId': instance, 'jobId': 'run', 'resultScopeId': scope,
            'resultKey': key, 'sources': {'score': {'state': 'available', 'value': key}}}


class Runtime:
    running = True
    def __init__(self):
        self.calls = 0
    async def snapshot(self, run):
        self.calls += 1
        return [result('a', str(self.calls)), result('b', str(self.calls))]


class ViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_multiscope_actual_display_freeze_isolation_and_latest_resume(self):
        views = Views(Runtime())
        a = views.create('alice', 'panel', 'run')
        b = views.create('bob', 'panel', 'run')
        for view in (a, b):
            for item in [result('a', 'old'), result('b', 'old'), result('a', 'new'), result('b', 'new')]:
                views._receive(view, item)
        views.freeze(a, {'a': 'old', 'b': 'new'})
        for view in (a, b):
            for item in [result('a', 'latest'), result('b', 'latest')]:
                views._receive(view, item)
        self.assertEqual(a['scopes']['a']['resultKey'], 'old')
        self.assertEqual(a['scopes']['b']['resultKey'], 'new')
        self.assertEqual(b['scopes']['a']['resultKey'], 'latest')
        views.resume(a)
        self.assertEqual(a['scopes']['a']['resultKey'], 'latest')
        self.assertFalse(a['frozenScopes'])
        with self.assertRaises(KeyError):
            views.get(a['id'], 'bob')
        with self.assertRaises(ValueError):
            views.freeze(a, 'latest')  # same key in two scopes is ambiguous
        await views.close()

    async def test_freeze_resume_drop_old_pending_and_do_not_extend_deadline(self):
        views=Views(Runtime())
        view=views.create('u','p','run')
        views._receive(view,result('a','displayed'))
        views._receive(view,result('a','not-yet-displayed'))
        frozen=views.freeze(view,{'a':'displayed'})
        deadline=view['freezeUntil']
        message=await views.next_message(view)
        self.assertEqual(message['result']['resultKey'],'displayed')
        self.assertEqual(message['viewGeneration'],frozen['viewGeneration'])
        views.freeze(view,{'a':'displayed'})
        self.assertEqual(view['freezeUntil'],deadline)
        views._receive(view,result('a','latest'))
        resumed=views.resume(view)
        message=await views.next_message(view)
        self.assertEqual(message['result']['resultKey'],'latest')
        self.assertEqual(message['viewGeneration'],resumed['viewGeneration'])
        self.assertNotEqual(resumed['viewGeneration'],frozen['viewGeneration'])
        await views.close()

    async def test_freeze_ttl_expires_as_missing_not_silently_latest(self):
        views = Views(Runtime(), freeze_ttl=.001)
        view = views.create('u', 'p', 'run')
        views._receive(view, result('a', 'seen'))
        views.freeze(view, {'a': 'seen'})
        await asyncio.sleep(.002)
        views._receive(view, result('a', 'new'))
        self.assertTrue(view['frozen'])
        self.assertEqual(view['last']['resultKey'], 'seen')
        self.assertEqual(view['last']['sources']['score']['reasonCode'], 'FREEZE_EXPIRED')
        self.assertEqual((await views.next_message(view))['type'], 'freeze_expired')
        await views.close()

    async def test_runtime_alias_streams_shared_and_cancelled_without_stopping(self):
        runtime = Runtime()
        other = Runtime()
        views = Views(runtime, poll_interval=.001)
        a = views.create('a', 'p', 'run', runtime_id='one')
        b = views.create('b', 'p', 'run', runtime_id='one')
        c = views.create('c', 'p', 'run', runtime_id='two', runtime=other)
        for view in (a, b, c):
            await views.attach(view)
        await asyncio.sleep(.01)
        self.assertEqual(len(views.streams), 2)
        self.assertEqual(a['latest'], b['latest'])
        await views.remove(a['id'])
        self.assertEqual(len(views.streams), 2)
        await views.remove(b['id'])
        self.assertEqual(len(views.streams), 1)
        await views.close()
        self.assertFalse(views.streams)
        self.assertTrue(runtime.running)

    async def test_notice_survives_snapshot_pressure_and_instance_reset(self):
        views = Views(Runtime(), max_scopes=2)
        view = views.create('u', 'p', 'run')
        views.publish_notice(view, {'type': 'package_updated', 'packageId': 'version-2'})
        for i in range(100):
            views._receive(view, result('a', str(i)))
        self.assertLessEqual(view['queue'].qsize(), 4)
        self.assertEqual((await views.next_message(view))['packageId'], 'version-2')
        old_epoch = view['epoch']
        views.freeze(view, {'a': '99'})
        views._receive(view, result('a', '1', 'restarted'))
        self.assertNotEqual(view['epoch'], old_epoch)
        self.assertFalse(view['frozen'])
        self.assertEqual((await views.next_message(view))['type'], 'reset_required')
        await views.close()

    async def test_readonly_snapshot_recovers_after_one_disconnect(self):
        class Flaky(Runtime):
            async def snapshot(self, run):
                self.calls += 1
                if self.calls == 1:raise ConnectionError('offline')
                return result('a', str(self.calls))
        runtime=Flaky();views=Views(runtime,poll_interval=.001)
        view=views.create('u','p','run');await views.attach(view)
        notice=await asyncio.wait_for(views.next_message(view),1)
        self.assertEqual(notice['type'],'reset_required')
        recovered=await asyncio.wait_for(views.next_message(view),1)
        self.assertEqual(recovered['type'],'result')
        self.assertTrue(recovered['state']['connected'])
        await views.close()

    async def test_bounded_short_stress_and_byte_budget(self):
        # Reproducible 16 viewers x 2 scopes x 200 updates, no unbounded queues/history.
        views = Views(Runtime(), max_views=16, max_scopes=2)
        users = [views.create(str(i), 'p', 'run') for i in range(16)]
        for n in range(200):
            for view in users:
                views._receive(view, result('a', str(n)))
                views._receive(view, result('b', str(n)))
        for view in users:
            self.assertLessEqual(view['queue'].qsize(), 4)
            self.assertEqual(sum(map(len, view['scopeRecent'].values())), 8)
        tiny = Views(Runtime(), byte_budget=64, max_scopes=1)
        with self.assertRaises(ValueError):
            tiny._receive(tiny.create('u','p','r'), result('a','too-large'))
        await views.close()


class CacheTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def key(data, resource='img'):
        return ResourceCache.key('instance', 'job', resource, hashlib.sha256(data).hexdigest())

    async def test_coalesces_waiters_cancellation_sha_and_ttl(self):
        cache = ResourceCache(ttl=.01)
        calls = 0
        gate = asyncio.Event()
        async def load():
            nonlocal calls
            calls += 1
            await gate.wait()
            return b'encoded-image'
        key = self.key(b'encoded-image')
        tasks = [asyncio.create_task(cache.get(key, load)) for _ in range(12)]
        await asyncio.sleep(.001)
        tasks[0].cancel()
        gate.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        self.assertIsInstance(results[0], asyncio.CancelledError)
        self.assertEqual(results[1:], [b'encoded-image'] * 11)
        self.assertEqual(calls, 1)
        await cache.get(key, load)
        self.assertEqual(calls, 1)
        await asyncio.sleep(.012)
        await cache.get(key, load)
        self.assertEqual(calls, 2)
        with self.assertRaises(ValueError):
            await cache.get(self.key(b'different'), load)
        await cache.close()
        self.assertEqual(cache.bytes, 0)

    async def test_concurrency_byte_eviction_and_shutdown(self):
        cache = ResourceCache(max_bytes=8, max_item_bytes=4, concurrency=2)
        active = peak = 0
        async def load():
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(.002)
            active -= 1
            return b'1234'
        await asyncio.gather(*(cache.get(self.key(b'1234',str(i)),load) for i in range(20)))
        self.assertEqual(peak,2)
        self.assertLessEqual(cache.bytes,8)
        self.assertLessEqual(len(cache.items),2)
        async def too_big():return b'12345'
        with self.assertRaises(ValueError):await cache.get(self.key(b'12345'),too_big)
        async def blocked():await asyncio.Event().wait()
        pending=asyncio.create_task(cache.get(self.key(b'x'),blocked))
        await asyncio.sleep(.001)
        await cache.close()
        with self.assertRaises(asyncio.CancelledError):await pending
        self.assertFalse(cache.pending)

    async def test_frozen_exact_bytes_pinned_until_expiry_or_owner_release(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        now=[100.0]
        with patch('emo_web_panel.resource_cache.time',SimpleNamespace(monotonic=lambda:now[0])):
            cache=ResourceCache(max_bytes=4,max_item_bytes=4,ttl=1,freeze_ttl=15,owner_bytes=4)
            async def load():return b'1234'
            key=self.key(b'1234');await cache.get(key,load)
            expiry=cache.pin(key,'view-a')
            self.assertEqual(cache.pin(key,'view-a'),expiry)
            now[0]+=3
            self.assertEqual(cache.peek(key),b'1234')
            async def second():return b'zzzz'
            with self.assertRaises(ValueError):await cache.get(self.key(b'zzzz'),second)
            cache.release_owner('view-a')
            with self.assertRaises(KeyError):cache.peek(key)
            await cache.get(key,load);cache.pin(key,'view-b',ttl=1)
            now[0]+=3
            with self.assertRaises(KeyError):cache.peek(key)
            await cache.close()
