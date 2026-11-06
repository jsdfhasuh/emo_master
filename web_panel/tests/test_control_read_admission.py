"""Read-only admission recovery: real gRPC refusals and an exact budget clock.

The synthetic service rejects before executing a method. This is not a device
or production-load acceptance test.
"""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import grpc
from emo_panel_protocol.generated import runtime_pb2 as pb, runtime_pb2_grpc as rpc
from emo_web_panel import runtime_client as module
from emo_web_panel.runtime_client import GrpcRuntimeClient
from test_runtime_contract import Service


class AdmissionService(Service):
    def __init__(self):
        super().__init__()
        self.denials = {}
        self.calls = []
        self.code, self.detail = grpc.StatusCode.RESOURCE_EXHAUSTED, 'control'

    async def call(self, name, request, context):
        self.calls.append((name, request))
        if self.denials.get(name, 0):
            self.denials[name] -= 1
            await context.abort(self.code, self.detail)
        return await getattr(super(), name)(request, context)

    async def GetRuntimeInfo(self, request, context):
        return await self.call('GetRuntimeInfo', request, context)

    async def GetRunState(self, request, context):
        return await self.call('GetRunState', request, context)

    async def ListRuns(self, request, context):
        return await self.call('ListRuns', request, context)

    async def StartJob(self, request, context):
        return await self.call('StartJob', request, context)

    async def StopJob(self, request, context):
        return await self.call('StopJob', request, context)


class ControlReadAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = AdmissionService()
        self.server = grpc.aio.server()
        rpc.add_RuntimeServiceServicer_to_server(self.service, self.server)
        port = self.server.add_insecure_port('127.0.0.1:0')
        await self.server.start()
        self.client = GrpcRuntimeClient(f'127.0.0.1:{port}')
        await self.client.start()

    async def asyncTearDown(self):
        await self.client.close()
        await self.server.stop(None)

    async def test_info_recovers_one_explicit_control_admission_refusal(self):
        self.service.denials = {'GetRuntimeInfo': 1}
        info = await self.client.info()
        self.assertEqual(info['runtimeInstanceId'], 'runtime-1')
        self.assertEqual([name for name, _ in self.service.calls], ['GetRuntimeInfo'] * 2)

    async def test_exact_run_recovers_both_read_admissions_without_catalog_or_writes(self):
        self.service.denials = {'GetRuntimeInfo': 1, 'GetRunState': 1}
        run = await self.client.run_state('j1')
        self.assertEqual((run['id'], run['status'], run['ownsResources']), ('j1', 'COMPLETED', True))
        self.assertFalse(run['displayResourcesReleased'])
        self.assertEqual([name for name, _ in self.service.calls],
                         ['GetRuntimeInfo'] * 2 + ['GetRunState'] * 2)
        for name, request in self.service.calls:
            if name == 'GetRunState':
                self.assertEqual((request.runtime_instance_id, request.project_id, request.job_id),
                                 ('runtime-1', 'project-1', 'j1'))

    async def test_other_read_errors_are_not_retried(self):
        errors = [(grpc.StatusCode.RESOURCE_EXHAUSTED, 'asset'),
                  (grpc.StatusCode.RESOURCE_EXHAUSTED, 'asset integrity error'),
                  (grpc.StatusCode.UNAVAILABLE, 'control'),
                  (grpc.StatusCode.DEADLINE_EXCEEDED, 'control'),
                  (grpc.StatusCode.FAILED_PRECONDITION, 'RESET_REQUIRED')]
        for method in ('GetRuntimeInfo', 'GetRunState'):
            for code, detail in errors:
                with self.subTest(method=method, code=code, detail=detail):
                    self.service.calls.clear()
                    self.service.denials = {method: 1}
                    self.service.code, self.service.detail = code, detail
                    with self.assertRaises(grpc.aio.AioRpcError) as failure:
                        await (self.client.info() if method == 'GetRuntimeInfo' else self.client.run_state('j1'))
                    self.assertEqual((failure.exception.code(), failure.exception.details()), (code, detail))
                    self.assertEqual(sum(name == method for name, _ in self.service.calls), 1)

    async def test_start_and_stop_are_never_retried(self):
        body = {'runtimeInstanceId':'runtime-1', 'projectId':'project-1', 'configurationRevision':'7',
                'requestId':'stable-request-1', 'workflowId':'wf', 'inputs':{}, 'runGroupId':'group-1'}
        self.service.denials = {'StartJob': 1, 'StopJob': 1}
        result = await self.client.control('start', 'wf', body)
        self.assertEqual(result['outcome'], 'UNKNOWN')
        with self.assertRaises(grpc.aio.AioRpcError) as failure:
            await self.client.control('stop', 'j1', body)
        self.assertEqual(failure.exception.code(), grpc.StatusCode.RESOURCE_EXHAUSTED)
        self.assertEqual(sum(name == 'StartJob' for name, _ in self.service.calls), 1)
        self.assertEqual(sum(name == 'StopJob' for name, _ in self.service.calls), 1)


class Refused(grpc.RpcError):
    def code(self):
        return grpc.StatusCode.RESOURCE_EXHAUSTED

    def details(self):
        return 'control'


class ControlReadBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_info_and_run_share_one_timeout_and_persistent_refusal_stops(self):
        client = GrpcRuntimeClient('127.0.0.1:1', timeout=.05)
        client.pb = pb
        clock, calls, sleeps = [0.0], [], []
        failure = Refused()

        async def info(request, timeout):
            calls.append(('info', clock[0], timeout))
            clock[0] += .007
            if sum(name == 'info' for name, *_ in calls) == 1:
                raise failure
            return pb.RuntimeInfoReply(ok=True, runtime_instance_id='runtime', project_id='project',
                                       protocol_version='1.0', capabilities=['runtime_info_v1'])

        async def state(request, timeout):
            calls.append(('state', clock[0], timeout))
            clock[0] += .006
            raise failure

        async def sleep(delay):
            sleeps.append(delay)
            clock[0] += delay

        client.runtime = SimpleNamespace(GetRuntimeInfo=info, GetRunState=state)
        with patch.object(module, 'time', SimpleNamespace(monotonic=lambda: clock[0]), create=True), \
                patch.object(module, 'asyncio', SimpleNamespace(sleep=sleep), create=True):
            with self.assertRaises(Refused) as caught:
                await client.run_state('job')
        self.assertIs(caught.exception, failure)
        self.assertEqual([name for name, *_ in calls], ['info', 'info', 'state', 'state'])
        self.assertAlmostEqual(clock[0], client.timeout)
        self.assertTrue(sleeps)
        for _, started, timeout in calls:
            self.assertGreater(timeout, 0)
            self.assertAlmostEqual(started + timeout, client.timeout)

    async def test_async_cancellation_during_backoff_does_not_resubmit(self):
        client = GrpcRuntimeClient('127.0.0.1:1')
        client.pb = pb
        calls, entered = [], asyncio.Event()

        async def info(request, timeout):
            calls.append(request)
            raise Refused()

        async def sleep(_delay):
            entered.set()
            await asyncio.Future()

        client.runtime = SimpleNamespace(GetRuntimeInfo=info)
        with patch.object(module, 'asyncio', SimpleNamespace(sleep=sleep), create=True):
            task = asyncio.create_task(client.info())
            try:
                await asyncio.wait_for(entered.wait(), 1)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertEqual(len(calls), 1)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
