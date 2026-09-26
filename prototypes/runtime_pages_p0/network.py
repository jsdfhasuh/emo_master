"""Public grpc.aio API with separate bounded synchronous compatibility pools.

P0 server only. The production service, proto and four-thread entry stay intact.
Long-running next() calls never consume a control worker. No private grpc APIs.
"""
import asyncio
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

    def is_active(self):
        return not self.cancelled.is_set()

    def add_callback(self, callback):
        self.callbacks.append(callback)
        return True

    def cancel(self):
        self.cancelled.set()
        for callback in self.callbacks:
            callback()

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
        self.ready = threading.Event()
        self.accepted = []
        self.thread = threading.Thread(target=self._run, name="p0-aio")
        self.thread.start()
        if not self.ready.wait(10):
            raise TimeoutError("aio startup")

    async def _admit(self, category, context):
        if self.active[category] >= LIMITS[category]:
            await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, category)
        self.active[category] += 1
        self.peak[category] = max(self.peak[category], self.active[category])

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
                cancel.cancel()
                if future is not None and not future.done():
                    future.add_done_callback(lambda _: self._release(category))
                else:
                    self._release(category)
        return call

    def _release(self, category):
        self.active[category] -= 1

    def _stream(self, method, category):
        async def stream(request, context):
            await self._admit(category, context)
            cancel = CancelContext()
            iterator = method(request, cancel)
            pending = None

            def cleanup(_=None):
                iterator.close()
                self._release(category)

            try:
                while not context.cancelled():
                    pending = self.loop.run_in_executor(self.pools[category], _next, iterator)
                    valid, message = await asyncio.shield(pending)
                    if not valid:
                        return
                    yield message
            except RpcAbort as error:
                await context.abort(error.code, error.details)
            finally:
                cancel.cancel()
                # A running generator owns the slot until next() really exits.
                if pending is not None and not pending.done():
                    pending.add_done_callback(cleanup)
                else:
                    cleanup()
        return stream

    async def _start(self):
        self.server = grpc.aio.server(options=[("grpc.max_receive_message_length", 1024 * 1024)])
        adapter = rpc.RuntimeServiceServicer()
        for method in pb.DESCRIPTOR.services_by_name["RuntimeService"].methods:
            name = method.name
            if method.client_streaming:
                # Upload is deliberately not an implemented P0 capability.
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
        self.port = self.server.add_insecure_port("127.0.0.1:0")
        await self.server.start()
        self.ready.set()

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._start())
        self.loop.run_forever()
        self.loop.close()

    def close(self):
        async def stop():
            await self.server.stop(0)
            deadline = time.monotonic() + 4
            while any(self.active.values()) and time.monotonic() < deadline:
                await asyncio.sleep(.01)
            assert not any(self.active.values()), self.active
        asyncio.run_coroutine_threadsafe(stop(), self.loop).result(6)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(2)
        assert not self.thread.is_alive()
        for pool in self.pools.values():
            pool.shutdown(wait=True)


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
        cursor = request.get("cursor", -1)
        while context.is_active():
            snapshot = self.snapshot_json(request)
            if snapshot["cursor"] != cursor:
                snapshot["reset_required"] = cursor >= 0 and snapshot["cursor"] > cursor + 1
                cursor = snapshot["cursor"]
                yield snapshot
            context.cancelled.wait(.05)
