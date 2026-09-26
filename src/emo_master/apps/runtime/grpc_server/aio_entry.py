"""Explicit development entry: public grpc.aio with bounded legacy adapters.

Synchronous construction, next, close and cancellation callbacks all execute off
the event loop. Admission is returned only after every piece of work finishes.
"""
import asyncio
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import tempfile
import threading
import time

import grpc

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from emo_master.apps.runtime.presentation.rpc import DisplayRpc


LIMITS = {"control": 4, "events": 2, "display": 2, "camera": 1, "asset": 2, "bulk": 2}


class RpcAbort(Exception):
    def __init__(self, code, details):
        self.code, self.details = code, details


class SyncContext:
    def __init__(self):
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.callbacks = []

    def is_active(self):
        return not self.cancelled.is_set()

    def add_callback(self, callback):
        with self.lock:
            if not self.is_active():
                return False
            self.callbacks.append(callback)
            return True

    def abort(self, code, details):
        raise RpcAbort(code, details)

    def finishCallbacks(self):
        with self.lock:
            callbacks, self.callbacks = self.callbacks, []
        failures = []
        for callback in callbacks:
            try:
                callback()
            except Exception as error:
                failures.append(repr(error))
        if failures:
            raise RuntimeError(str(failures))


def nextItem(iterator):
    try:
        return True, next(iterator)
    except StopIteration:
        return False, None


class AioRuntimeServer:
    def __init__(self, runtime, presentation, port=0):
        self.runtime, self.presentation = runtime, presentation
        self.port = port
        self.pools = {key: ThreadPoolExecutor(max_workers=value, thread_name_prefix=f"rpc-{key}") for key, value in LIMITS.items()}
        self.cleanupPool = ThreadPoolExecutor(max_workers=sum(LIMITS.values()), thread_name_prefix="rpc-cleanup")
        self.active = {key: 0 for key in LIMITS}
        self.peak = dict(self.active)
        self.cleanups = set()
        self.errors = deque(maxlen=64)
        self.ready = threading.Event()
        self.startError = None
        self.thread = threading.Thread(target=self._run, name="display-grpc-aio")
        self.thread.start()
        if not self.ready.wait(10):
            raise TimeoutError("aio start deadline")
        if self.startError:
            self.thread.join(2)
            self._shutdownPools()
            raise RuntimeError("aio startup failed") from self.startError

    async def _admit(self, category, context):
        if self.active[category] >= LIMITS[category]:
            await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, category)
        self.active[category] += 1
        self.peak[category] = max(self.peak[category], self.active[category])
        completed = asyncio.Event()
        context.add_done_callback(lambda _context: self.loop.call_soon_threadsafe(completed.set))
        return completed

    def _retire(self, category, token, pending, resource=None, constructing=False, completed=None):
        token.cancelled.set()

        async def finish():
            nonlocal resource
            callbacks = self.loop.run_in_executor(self.cleanupPool, token.finishCallbacks)
            try:
                if pending is not None:
                    try:
                        value = await asyncio.shield(pending)
                        if constructing:
                            resource = value
                    except Exception as error:
                        self.errors.append(repr(error))
                try:
                    await callbacks
                except Exception as error:
                    self.errors.append(repr(error))
                if resource is not None and hasattr(resource, "close"):
                    await self.loop.run_in_executor(self.pools[category], resource.close)
            except Exception as error:
                self.errors.append(repr(error))
            finally:
                # Also retain response/serialization ownership until gRPC has
                # finished the RPC, rather than only the synchronous method.
                if completed is not None:
                    await completed.wait()
                self.active[category] -= 1

        task = self.loop.create_task(finish())
        self.cleanups.add(task)
        task.add_done_callback(self.cleanups.discard)

    def _unary(self, method, category):
        async def call(request, context):
            completed = await self._admit(category, context)
            token = SyncContext()
            pending = None
            try:
                pending = self.loop.run_in_executor(self.pools[category], method, request, token)
                return await asyncio.shield(pending)
            except RpcAbort as error:
                await context.abort(error.code, error.details)
            finally:
                self._retire(category, token, pending, completed=completed)
        return call

    def _stream(self, method, category):
        async def call(request, context):
            completed = await self._admit(category, context)
            token = SyncContext()
            iterator = pending = None
            constructing = True
            try:
                pending = self.loop.run_in_executor(self.pools[category], method, request, token)
                iterator = await asyncio.shield(pending)
                constructing = False
                while not context.cancelled():
                    pending = self.loop.run_in_executor(self.pools[category], nextItem, iterator)
                    valid, message = await asyncio.shield(pending)
                    if not valid:
                        return
                    yield message
            except RpcAbort as error:
                await context.abort(error.code, error.details)
            finally:
                self._retire(category, token, pending, iterator, constructing, completed)
        return call

    def _upload(self, method):
        async def call(requests, context):
            completed = await self._admit("bulk", context)
            token = SyncContext()
            spool = pending = None
            try:
                pending = self.loop.run_in_executor(self.pools["bulk"], tempfile.TemporaryFile)
                spool = await asyncio.shield(pending)
                size = count = totalWire = 0
                async for chunk in requests:
                    size += len(chunk.content)
                    count += 1
                    if size > 64 * 1024 * 1024:
                        return pb.PreviewAssetReply(ok=False, code="E_PREVIEW_ASSET_INVALID", message="preview upload exceeds 64 MiB")
                    raw = chunk.SerializeToString()
                    totalWire += 4 + len(raw)
                    if count > 1024 or totalWire > 68 * 1024 * 1024:
                        await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, "upload framing quota")

                    def write(data=raw):
                        spool.write(len(data).to_bytes(4, "little"))
                        spool.write(data)
                    pending = self.loop.run_in_executor(self.pools["bulk"], write)
                    await asyncio.shield(pending)

                def execute():
                    spool.seek(0)

                    def chunks():
                        while token.is_active():
                            countBytes = spool.read(4)
                            if not countBytes:
                                return
                            yield pb.PreviewUploadChunk.FromString(spool.read(int.from_bytes(countBytes, "little")))
                    reply = method(chunks(), token)
                    if not token.is_active() and reply.ok:
                        self.runtime.previewAssetStore.removeTransient(reply.asset_id)
                    return reply
                pending = self.loop.run_in_executor(self.pools["bulk"], execute)
                return await asyncio.shield(pending)
            finally:
                self._retire("bulk", token, pending, spool, constructing=spool is None, completed=completed)
        return call

    def _adapter(self, serviceName, implementation, adapter):
        for method in pb.DESCRIPTOR.services_by_name[serviceName].methods:
            name = method.name
            category = {"StreamJobEvents": "events", "Subscribe": "display",
                        "StreamOperatorPreviewFrames": "camera", "StreamPreviewAsset": "asset", "ReadAsset": "asset",
                        "Prepare": "bulk", "LoadProject": "bulk", "RunOperatorPreview": "bulk",
                        "OpenOperatorPreviewSession": "bulk"}.get(name, "control")
            handler = getattr(implementation, name)
            if method.client_streaming:
                wrapper = self._upload(handler)
            elif method.server_streaming:
                wrapper = self._stream(handler, category)
            else:
                wrapper = self._unary(handler, category)
            setattr(adapter, name, wrapper)
        return adapter

    async def _start(self):
        self.server = grpc.aio.server(options=[("grpc.max_receive_message_length", 1024 * 1024),
                                              ("grpc.max_send_message_length", 9 * 1024 * 1024)])
        rpc.add_RuntimeServiceServicer_to_server(self._adapter("RuntimeService", self.runtime, rpc.RuntimeServiceServicer()), self.server)
        rpc.add_DisplayServiceServicer_to_server(self._adapter("DisplayService", DisplayRpc(self.presentation), rpc.DisplayServiceServicer()), self.server)
        self.port = self.server.add_insecure_port(f"127.0.0.1:{self.port}")
        if not self.port:
            raise RuntimeError("loopback port unavailable")
        await self.server.start()

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._start())
        except BaseException as error:
            self.startError = error
            if hasattr(self, "server"):
                self.loop.run_until_complete(self.server.stop(0))
            self.ready.set()
        else:
            self.ready.set()
            self.loop.run_forever()
        finally:
            self.loop.close()

    def _shutdownPools(self):
        for pool in (*self.pools.values(), self.cleanupPool):
            pool.shutdown(wait=True)

    def close(self, timeout=4):
        async def stop():
            await self.server.stop(0)
            deadline = time.monotonic() + timeout
            while (any(self.active.values()) or self.cleanups) and time.monotonic() < deadline:
                await asyncio.sleep(.01)
            if any(self.active.values()) or self.cleanups:
                raise TimeoutError(f"RPC work still owns quota: {self.active}")
        asyncio.run_coroutine_threadsafe(stop(), self.loop).result(timeout + 2)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(2)
        if self.thread.is_alive():
            raise RuntimeError("aio loop has not stopped")
        self._shutdownPools()
