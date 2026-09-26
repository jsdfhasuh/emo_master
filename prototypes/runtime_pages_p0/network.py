"""Public grpc.aio API with separate bounded synchronous compatibility pools.

P0 server only. The production service, proto and four-thread entry stay intact.
Long-running next() calls never consume a control worker. No private grpc APIs.
"""
import asyncio
from collections import deque
import tempfile
from dataclasses import asdict
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time

import grpc

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc


LIMITS = {"control": 4, "events": 2, "display": 2, "preview": 1, "asset": 2, "bulk": 2}


class CancelContext:
    def __init__(self):
        self.cancelled = threading.Event()
        self.callbacks = []
        self.lock = threading.Lock()

    def is_active(self):
        return not self.cancelled.is_set()

    def add_callback(self, callback):
        with self.lock:
            if self.cancelled.is_set():
                return False
            self.callbacks.append(callback)
            return True

    def cancel(self):
        self.cancelled.set()
        with self.lock:
            callbacks, self.callbacks = self.callbacks, []
        errors = []
        for callback in callbacks:
            try:
                callback()
            except Exception as error:
                errors.append(repr(error))
        if errors:
            raise RuntimeError(str(errors))

    def abort(self, code, details):
        raise RpcAbort(code, details)


class RpcAbort(Exception):
    def __init__(self, code, details):
        self.code, self.details = code, details


def _next(iterator):
    try:
        return True, next(iterator)
    except StopIteration:
        return False, None


class IsolatedServer:
    def __init__(self, service, display):
        self.service, self.display = service, display
        self.active = {name: 0 for name in LIMITS}
        self.peak = dict(self.active)
        self.pools = {name: ThreadPoolExecutor(max_workers=limit, thread_name_prefix=f"p0-{name}")
                      for name, limit in LIMITS.items()}
        self.cleanup_pool = ThreadPoolExecutor(max_workers=sum(LIMITS.values()), thread_name_prefix="p0-cancel")
        self.cleanups = set()
        self.errors = deque(maxlen=64)
        self.start_error = None
        self.ready = threading.Event()
        self.accepted = deque(maxlen=64)
        self.thread = threading.Thread(target=self._run, name="p0-aio")
        self.thread.start()
        if not self.ready.wait(10):
            raise TimeoutError("aio startup")
        if self.start_error:
            self.thread.join(2)
            self._shutdown_pools()
            raise RuntimeError("aio startup failed") from self.start_error

    async def _admit(self, category, context):
        if self.active[category] >= LIMITS[category]:
            await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, category)
        self.active[category] += 1
        self.peak[category] = max(self.peak[category], self.active[category])

    def _cleanup(self, category, cancel, pending, iterator=None, constructing=False, resource=None):
        # Only the Event is set on the loop. Arbitrary old callbacks run off-loop.
        cancel.cancelled.set()
        async def finish():
            nonlocal iterator
            callbacks = self.loop.run_in_executor(self.cleanup_pool, cancel.cancel)
            try:
                if pending is not None:
                    try:
                        value = await asyncio.shield(pending)
                        if constructing:
                            iterator = value
                    except Exception as error:
                        self.errors.append(repr(error))
                try:
                    await callbacks
                except Exception as error:
                    self.errors.append(repr(error))
                if iterator is not None and hasattr(iterator, "close"):
                    await self.loop.run_in_executor(self.pools[category], iterator.close)
            except Exception as error:
                self.errors.append(repr(error))
            finally:
                try:
                    if resource is not None:
                        await self.loop.run_in_executor(self.pools[category], resource.close)
                finally:
                    self.active[category] -= 1
        task = self.loop.create_task(finish())
        self.cleanups.add(task)
        task.add_done_callback(self.cleanups.discard)

    def _unary(self, name, category="control"):
        async def call(request, context):
            await self._admit(category, context)
            cancel = CancelContext()
            future = None
            try:
                self.accepted.append((name, time.perf_counter_ns()))
                future = self.loop.run_in_executor(self.pools[category], getattr(self.service, name), request, cancel)
                return await asyncio.shield(future)
            except RpcAbort as error:
                await context.abort(error.code, error.details)
            finally:
                self._cleanup(category, cancel, future)
        return call

    def _release(self, category):
        self.active[category] -= 1

    def _stream(self, method, category):
        async def stream(request, context):
            await self._admit(category, context)
            cancel = CancelContext()
            iterator = pending = None
            constructing = True
            try:
                pending = self.loop.run_in_executor(self.pools[category], method, request, cancel)
                iterator = await asyncio.shield(pending)
                constructing = False
                while not context.cancelled():
                    pending = self.loop.run_in_executor(self.pools[category], _next, iterator)
                    valid, message = await asyncio.shield(pending)
                    if not valid:
                        return
                    yield message
            except RpcAbort as error:
                await context.abort(error.code, error.details)
            finally:
                self._cleanup(category, cancel, pending, iterator, constructing)
        return stream

    def _upload(self):
        async def upload(requests, context):
            await self._admit("bulk", context)
            cancel = CancelContext()
            pending = None
            spool = None
            try:
                pending = self.loop.run_in_executor(self.pools["bulk"], tempfile.TemporaryFile)
                spool = await asyncio.shield(pending)
                total = 0
                count = 0
                spool_bytes = 0
                async for chunk in requests:
                    count += 1
                    total += len(chunk.content)
                    if total > 64 * 1024 * 1024:
                        return pb.PreviewAssetReply(ok=False, code="E_PREVIEW_ASSET_INVALID",
                                                    message="preview upload exceeds 64 MiB")
                    raw = chunk.SerializeToString()
                    spool_bytes += len(raw) + 4
                    if count > 1024 or spool_bytes > 68 * 1024 * 1024:
                        await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, "upload metadata/chunk count quota")
                    def write(data=raw):
                        spool.write(len(data).to_bytes(4, "little"))
                        spool.write(data)
                    pending = self.loop.run_in_executor(self.pools["bulk"], write)
                    await asyncio.shield(pending)
                def execute():
                    spool.seek(0)
                    def chunks():
                        while True:
                            if not cancel.is_active():
                                raise RuntimeError("upload cancelled")
                            size = spool.read(4)
                            if not size:
                                return
                            yield pb.PreviewUploadChunk.FromString(spool.read(int.from_bytes(size, "little")))
                    return self.service.UploadPreviewImage(chunks(), cancel)
                pending = self.loop.run_in_executor(self.pools["bulk"], execute)
                return await asyncio.shield(pending)
            finally:
                if spool is None and pending is not None:
                    # A cancelled TemporaryFile construction still owns a handle.
                    self._cleanup("bulk", cancel, pending, constructing=True)
                else:
                    self._cleanup("bulk", cancel, pending, resource=spool)
        return upload

    async def _start(self):
        self.server = grpc.aio.server(options=[("grpc.max_receive_message_length", 1024 * 1024)])
        adapter = rpc.RuntimeServiceServicer()
        for method in pb.DESCRIPTOR.services_by_name["RuntimeService"].methods:
            name = method.name
            if method.client_streaming:
                setattr(adapter, name, self._upload())
                continue
            category = "control"
            if name == "StreamJobEvents":
                category = "events"
            elif name == "StreamOperatorPreviewFrames":
                category = "preview"
            elif name == "StreamPreviewAsset":
                category = "asset"
            elif name in ("LoadProject", "RunOperatorPreview", "OpenOperatorPreviewSession"):
                category = "bulk"
            if method.server_streaming:
                handler = self._stream(getattr(self.service, name), category)
            else:
                handler = self._unary(name, category)
            setattr(adapter, name, handler)
        rpc.add_RuntimeServiceServicer_to_server(adapter, self.server)

        async def snapshot(request, context):
            await self._admit("control", context)
            try:
                return self.display.snapshot_json(request)
            finally:
                self._release("control")

        self.server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler("p0.Display", {
            "GetDisplaySnapshot": grpc.unary_unary_rpc_method_handler(
                snapshot, request_deserializer=json.loads, response_serializer=lambda v: json.dumps(v).encode()),
            "StreamDisplayUpdates": grpc.unary_stream_rpc_method_handler(
                self._stream(self.display.follow, "display"), request_deserializer=json.loads,
                response_serializer=lambda v: json.dumps(v).encode())}),))
        if hasattr(self.display, "assets"):
            async def pin(request, context):
                await self._admit("control", context)
                try:
                    return {"lease": self.display.assets.pin(request["asset_id"], request.get("seconds", 30))}
                finally:
                    self._release("control")
            async def unpin(request, context):
                self.display.assets.unpin(request["lease"])
                return {}
            self.server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler("p0.Display", {
                "GetDisplayAsset": grpc.unary_stream_rpc_method_handler(
                    self._stream(self.display.assets.read, "asset"), request_deserializer=json.loads,
                    response_serializer=lambda value: value),
                "Pin": grpc.unary_unary_rpc_method_handler(pin, request_deserializer=json.loads,
                    response_serializer=lambda value: json.dumps(value).encode()),
                "Unpin": grpc.unary_unary_rpc_method_handler(unpin, request_deserializer=json.loads,
                    response_serializer=lambda value: json.dumps(value).encode())}),))
        self.port = self.server.add_insecure_port("127.0.0.1:0")
        await self.server.start()
        self.ready.set()

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._start())
        except BaseException as error:
            self.start_error = error
            if hasattr(self, "server"):
                self.loop.run_until_complete(self.server.stop(0))
            self.ready.set()
        else:
            self.loop.run_forever()
        finally:
            self.loop.close()

    def _shutdown_pools(self):
        for pool in (*self.pools.values(), self.cleanup_pool):
            pool.shutdown(wait=True)

    def close(self, timeout=4):
        async def stop():
            await self.server.stop(0)
            deadline = time.monotonic() + timeout
            while (any(self.active.values()) or self.cleanups) and time.monotonic() < deadline:
                await asyncio.sleep(.01)
            if any(self.active.values()) or self.cleanups:
                # Keep loop/pools alive and quotas charged. Caller can release
                # the test gate and retry; outer watchdog owns a permanent hang.
                raise TimeoutError(f"unretired RPC work: {self.active}")
        asyncio.run_coroutine_threadsafe(stop(), self.loop).result(timeout + 2)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(2)
        assert not self.thread.is_alive()
        self._shutdown_pools()


class DisplayFeed:
    """Bounded latest-only subscription. Every gap triggers full snapshot reset.

    Polling also recovers a lost last notification with no subsequent product.
    """
    def __init__(self):
        self.lock = threading.Lock()
        self.latest = {}
        self.cursor = 0
        self.dropped = 0
        self.results = None

    def publish(self, job, payload):
        with self.lock:
            if job not in self.latest and len(self.latest) >= 2:
                raise ValueError("P0 feed job quota")
            self.cursor += 1
            self.latest[job] = dict(payload, job=job, cursor=self.cursor)

    def snapshot_json(self, request):
        if "identity" in request:
            def tuples(value):
                return tuple(tuples(v) for v in value) if isinstance(value, list) else value
            snapshot, cursor = self.results.snapshot(tuples(request["identity"]))
            return {"latest": asdict(snapshot) if snapshot else None, "cursor": cursor}
        with self.lock:
            return {"latest": self.latest.get(request["job"]), "cursor": self.cursor}

    def follow(self, request, context):
        if getattr(self, "follow_override", False):
            return self.test_method(request, context)
        return self._follow(request, context)

    def _follow(self, request, context):
        cursor = request.get("cursor", -1)
        while context.is_active():
            snapshot = self.snapshot_json(request)
            if snapshot["cursor"] != cursor:
                snapshot["reset_required"] = cursor >= 0 and snapshot["cursor"] > cursor + 1
                cursor = snapshot["cursor"]
                yield snapshot
            context.cancelled.wait(.05)
