"""Opt-in, bounded attribution of the unchanged formal ReadAsset path.

Install only inside one serial diagnostic trial, before server/session creation.
The generated stubs, protobufs, codecs, deadlines and ownership are unchanged.
One identity-only gRPC metadata token pairs the server call with its client call.
No image, path, exception message or encoded payload is retained by this helper.
"""
from collections import Counter
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from uuid import uuid4


METHOD = "/emo_master.runtime.DisplayService/ReadAsset"
HEADER = "x-emo-r3-asset-trace"
ROW_LIMIT = 6000
IDENTITY_LIMIT = 256
ASSOCIATION_LIMIT = 16
TRACE_BYTES = 24 * 1024 * 1024
REQUIRED_STAGES = (
    "client.asset_rpc_split", "server.dispatch_to_worker", "server.handler",
    "server.worker", "server.asset_read", "server.asset_lock_wait",
    "server.file_read", "server.sha256_bytes", "server.protobuf_serialize",
    "client.protobuf_deserialize",
)
SOURCE_FILES = (
    "scripts/r3_asset_split_trace.py", "scripts/r3_measure.py",
    "scripts/r3_policy_measure.py",
    "src/emo_master/apps/runtime/presentation/assets.py",
    "src/emo_master/apps/runtime/presentation/rpc.py",
    "src/emo_master/apps/runtime/presentation/exporter.py",
    "src/emo_master/apps/runtime/presentation/collector.py",
    "src/emo_master/apps/runtime/presentation/store.py",
    "src/emo_master/apps/runtime/grpc_server/aio_entry.py",
    "src/emo_master/apps/runtime/grpc_server/generated/runtime_pb2.py",
    "src/emo_master/apps/runtime/grpc_server/generated/runtime_pb2_grpc.py",
    "src/emo_master/clients/runtime/display_session.py",
)


def fingerprints(root):
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            if (root / name).is_file() else None for name in SOURCE_FILES}


def _cpu():
    clock = getattr(time, "thread_time_ns", None)
    return clock() if clock is not None else None


class AssetSplitTrace:
    """Identity-only associations and finite rows; no threads, queues or profiler."""
    def __init__(self, row_limit=ROW_LIMIT, association_limit=ASSOCIATION_LIMIT,
                 identity_limit=IDENTITY_LIMIT):
        self.row_limit = row_limit
        self.association_limit = association_limit
        self.identity_limit = identity_limit
        self.rows = []
        self.counters = Counter()
        self.identities = {}
        self.requests = {}
        self.replies = {}
        self.tokens = {}
        self.runtimes = set()
        self.local = threading.local()
        self.lock = threading.RLock()
        self.prefix = uuid4().hex
        self.sequence = 0
        self.channel_sequence = 0
        self.source_before = {}
        self.source_after = {}
        self.disabled = False
        self.diagnostic_errors = 0
        self.last_diagnostic_error = None

    def observe(self, operation, /, *args, default=None, **kwargs):
        """Only instrumentation callbacks go here, never the observed operation."""
        if self.disabled:
            return default
        try:
            return operation(*args, **kwargs)
        except Exception as error:
            self.disabled = True
            self.diagnostic_errors += 1
            self.last_diagnostic_error = type(error).__name__[:80]
            return default

    def count(self, name, amount=1):
        with self.lock:
            self.counters[name] += amount

    def associate(self, name, key, value):
        with self.lock:
            target = getattr(self, name)
            if key not in target and len(target) >= self.association_limit:
                self.counters[name + "_overflow"] += 1
                return False
            if key in target:
                self.counters[name + "_replaced"] += 1
            target[key] = value
            self.counters[name + "_peak"] = max(self.counters[name + "_peak"], len(target))
            return True

    def take(self, name, key):
        with self.lock:
            return getattr(self, name).pop(key, None)

    def register_result(self, result):
        identity = result.identity
        for source in result.sources:
            if source.image is None:
                continue
            key = (identity.runtimeInstanceId, identity.jobId, source.image.resourceId)
            value = {"result_key": identity.resultKey, "ordinal": identity.resultOrdinal,
                     "asset_bytes": source.image.byteSize}
            with self.lock:
                previous = self.identities.get(key)
                if previous is not None and previous != value:
                    self.counters["identity_conflicts"] += 1
                    self.identities[key] = {"conflicted": True}
                    continue
                if previous is None and len(self.identities) >= self.identity_limit:
                    self.counters["identity_overflow"] += 1
                    continue
                self.identities[key] = value

    def identity(self, request):
        key = (request.runtime_instance_id, request.job_id, request.resource_id)
        if any(not isinstance(value, str) or not 0 < len(value) <= 160 for value in key):
            self.count("invalid_identity")
            return None
        with self.lock:
            result = self.identities.get(key)
        if result is not None and result.get("conflicted"):
            result = None
        if result is None:
            self.count("unmatched_resource")
        return {"runtime_instance_id": key[0], "job_id": key[1], "resource_id": key[2],
                **(result or {}), "resource_match": result is not None}

    def new_call(self, request, channel):
        metadata = self.identity(request)
        if metadata is None:
            return None
        with self.lock:
            self.sequence += 1
            token = f"{self.prefix}:{self.sequence}"
        metadata = {**metadata, "call_id": token, "channel_id": channel,
                    "decoder_request": bool(getattr(self.local, "decoder", False)),
                    "runtime_matches_local_server": metadata["runtime_instance_id"] in self.runtimes}
        if not self.associate("tokens", token, metadata):
            return None
        self.count("client_calls_started")
        return metadata

    def record(self, stage, start, end, cpu_start, cpu_end, metadata, outcome="OK", **fields):
        row = {"stage": stage, "parent_stage": getattr(self.local, "stage", ""),
               "start_ns": start, "end_ns": end, "elapsed_ms": (end-start)/1e6,
               "thread_cpu_ns": cpu_end-cpu_start if cpu_start is not None and cpu_end is not None else None,
               "thread_id": threading.get_ident(), "thread": threading.current_thread().name,
               "outcome": outcome, **metadata, **fields}
        with self.lock:
            self.counters["spans_completed"] += 1
            if len(self.rows) < self.row_limit:
                self.rows.append(row)
            else:
                self.counters["dropped_rows"] += 1

    @contextmanager
    def span(self, stage, metadata):
        previous = getattr(self.local, "metadata", None)
        previous_stage = getattr(self.local, "stage", "")
        self.local.metadata, self.local.stage = metadata, stage
        start = self.observe(time.perf_counter_ns, default=0)
        cpu_start, outcome = self.observe(_cpu), "OK"
        try:
            yield
        except BaseException as error:
            outcome = type(error).__name__[:80]
            raise
        finally:
            end, cpu_end = self.observe(time.perf_counter_ns, default=start), self.observe(_cpu)
            self.local.metadata, self.local.stage = previous, previous_stage
            self.observe(self.record, stage, start, end, cpu_start, cpu_end, metadata, outcome)

    def call(self, stage, metadata, operation, /, *args, **kwargs):
        if self.disabled:
            return operation(*args, **kwargs)
        with self.span(stage, metadata):
            return operation(*args, **kwargs)

    def payload(self):
        with self.lock:
            counters = dict(self.counters)
            outstanding = {name: len(getattr(self, name)) for name in ("requests", "replies", "tokens")}
            rows = list(self.rows)
        coverage = {}
        missing = Counter()
        successful = failed = 0
        for row in rows:
            token = row.get("call_id")
            if token is None:
                continue
            item = coverage.setdefault(token, {"stages": Counter(), "rpc_outcome": None,
                                               "decoder_request": row.get("decoder_request", False)})
            item["stages"][row["stage"]] += 1
            if row["stage"] == "client.asset_rpc_split":
                item["rpc_outcome"] = row["outcome"]
        for item in coverage.values():
            if item["rpc_outcome"] == "OK":
                successful += 1
                expected = REQUIRED_STAGES + (("client.sha256_bytes", "client.png_decode_inclusive", "client.opencv_decode")
                                             if item["decoder_request"] else ())
                for stage in expected:
                    if not item["stages"][stage]:
                        missing[stage] += 1
                if item["stages"]["server.asset_lock_wait"] != 2:
                    missing["server.asset_lock_wait_expected_two"] += 1
            elif item["rpc_outcome"] is not None:
                failed += 1
        return {"role": "asset_split", "pid": os.getpid(), "row_limit": self.row_limit,
                "identity_limit": self.identity_limit, "association_limit": self.association_limit,
                "dropped_rows": counters.get("dropped_rows", 0), "counters": counters,
                "instrumentation_disabled": self.disabled, "diagnostic_errors": self.diagnostic_errors,
                "last_diagnostic_error_type": self.last_diagnostic_error,
                "outstanding_associations": outstanding, "rows": rows,
                "registered_runtime_ids": sorted(self.runtimes),
                "stage_coverage": {"observed_calls": len(coverage), "successful_rpc_calls": successful,
                                   "failed_rpc_calls": failed, "missing_success_stages": dict(missing),
                                   "interpretation": "Coverage is incomplete if rows were dropped; failed calls need not reach later stages."},
                "source_before": self.source_before, "source_after": self.source_after,
                "source_unchanged": bool(self.source_before) and self.source_before == self.source_after,
                "source_complete": bool(self.source_before) and all(self.source_before.values())
                    and all(self.source_after.values()),
                "clock": "same-process perf_counter_ns; synchronous spans also record current-thread CPU",
                "interpretation": "Inclusive nested spans; never sum stage P95s. Async handler CPU is null. "
                    "Dispatch includes admission/executor scheduling. RPC-minus-handler is not pure network time. "
                    "Hash spans cover SHA-256 construction from bytes, excluding hexdigest. "
                    "No GIL ownership is observed. Missing/ambiguous associations are not imputed.",
                "performance_verdict": "NOT_EVALUATED"}

    def save(self, destination):
        payload = self.payload()
        content = json.dumps(payload, separators=(",", ":")).encode()
        if len(content) > TRACE_BYTES:
            raise RuntimeError("asset split trace file budget exceeded")
        destination.write_bytes(content)
        counter_failures = {key: value for key, value in payload["counters"].items() if value and
            any(part in key for part in ("overflow", "unmatched", "ambiguous", "conflict", "invalid", "untraced", "dropped", "replaced"))}
        complete = (not self.disabled and not self.diagnostic_errors and not counter_failures
                    and payload["source_complete"] and payload["source_unchanged"]
                    and not any(payload["outstanding_associations"].values())
                    and not payload["stage_coverage"]["missing_success_stages"])
        return {"enabled": True, "file": destination.name, "complete": bool(complete),
                "source_complete": payload["source_complete"], "source_unchanged": payload["source_unchanged"],
                "diagnostic_errors": self.diagnostic_errors, "counter_failures": counter_failures,
                "client_calls_started": self.counters["client_calls_started"],
                "client_calls_finished": self.counters["client_calls_finished"],
                "stage_coverage": payload["stage_coverage"],
                "performance_verdict": "NOT_EVALUATED"}


class _Lock:
    def __init__(self, lock, trace):
        self.lock, self.trace = lock, trace

    def __getattr__(self, name):
        return getattr(self.lock, name)

    def __enter__(self):
        metadata = getattr(self.trace.local, "metadata", None)
        if not self.trace.disabled and metadata is not None and getattr(self.trace.local, "asset_read", False):
            phase = "admit" if not getattr(self.trace.local, "asset_lock_count", 0) else "retire"
            self.trace.local.asset_lock_count = getattr(self.trace.local, "asset_lock_count", 0) + 1
            self.trace.call("server.asset_lock_wait", {**metadata, "lock_phase": phase}, self.lock.acquire)
        else:
            self.lock.acquire()
        return self

    def __exit__(self, *_args):
        self.lock.release()


class _Unary:
    def __init__(self, inner, trace, channel, active):
        self.inner, self.trace, self.channel, self.active = inner, trace, channel, active

    def _start(self, request, kwargs):
        if getattr(self.trace.local, "decoder", False):
            self.trace.local.verify = self.trace.local.decode = None
        metadata = self.trace.new_call(request, self.channel)
        if metadata is None:
            with self.trace.lock:
                self.active[None] = None
            return None, kwargs
        supplied = tuple(kwargs.get("metadata") or ())
        if any(key == HEADER for key, _value in supplied):
            self.trace.take("tokens", metadata["call_id"])
            self.trace.count("reserved_header_conflict")
            with self.trace.lock:
                self.active[None] = None
            return None, kwargs
        with self.trace.lock:
            self.active[metadata["call_id"]] = metadata
        return metadata, {**kwargs, "metadata": supplied + ((HEADER, metadata["call_id"]),)}

    def _finish(self, metadata, success):
        with self.trace.lock:
            self.active.pop(metadata["call_id"], None)
        self.trace.take("tokens", metadata["call_id"])
        self.trace.count("client_calls_finished")
        if success and getattr(self.trace.local, "decoder", False):
            self.trace.local.verify = metadata
            self.trace.local.decode = metadata

    def _blocking(self, operation, request, args, kwargs):
        if self.trace.disabled:
            return operation(request, *args, **kwargs)
        # Public API permits positional timeout/metadata. Only the normal keyword
        # path is instrumented; don't alter positional call semantics.
        if len(args) > 1:
            self.trace.observe(self.trace.count, "positional_metadata_untraced")
            with self.trace.lock:
                self.active[None] = None
            return operation(request, *args, **kwargs)
        metadata, kwargs = self.trace.observe(self._start, request, kwargs, default=(None, kwargs))
        if metadata is None:
            return operation(request, *args, **kwargs)
        success = False
        try:
            value = self.trace.call("client.asset_rpc_split", metadata, operation, request, *args, **kwargs)
            success = True
            return value
        finally:
            self.trace.observe(self._finish, metadata, success)

    def __call__(self, request, *args, **kwargs):
        return self._blocking(self.inner, request, args, kwargs)

    def with_call(self, request, *args, **kwargs):
        return self._blocking(self.inner.with_call, request, args, kwargs)

    def future(self, request, *args, **kwargs):
        # The session contract is one synchronous read/decode worker. Preserve
        # other caller APIs but don't pretend their async attribution is known.
        self.trace.observe(self.trace.count, "future_untraced")
        with self.trace.lock:
            self.active[None] = None
        return self.inner.future(request, *args, **kwargs)


class _Channel:
    def __init__(self, inner, trace):
        self.inner, self.trace = inner, trace

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def __enter__(self):
        self.inner.__enter__()
        return self

    def __exit__(self, *args):
        return self.inner.__exit__(*args)

    def unary_unary(self, method, *args, **kwargs):
        if self.trace.disabled or method != METHOD or args or "response_deserializer" not in kwargs:
            return self.inner.unary_unary(method, *args, **kwargs)
        with self.trace.lock:
            self.trace.channel_sequence += 1
            channel = self.trace.channel_sequence
        active = {}
        deserialize = kwargs["response_deserializer"]

        def measured(content):
            def match():
                with self.trace.lock:
                    return next(iter(active.values())) if len(active) == 1 else None
            metadata = self.trace.observe(match)
            if metadata is None:
                self.trace.observe(self.trace.count, "client_deserialize_ambiguous_or_unmatched")
                return deserialize(content)
            return self.trace.call("client.protobuf_deserialize", {**metadata, "wire_bytes": len(content)}, deserialize, content)
        inner = self.inner.unary_unary(method, **{**kwargs, "response_deserializer": measured})
        return _Unary(inner, self.trace, channel, active)


@contextmanager
def installed(trace, patch, root):
    """Use r3_measure.patches()'s patch function; restore only after all owners close."""
    import grpc
    import cv2
    from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer
    from emo_master.apps.runtime.presentation.assets import AssetStore
    from emo_master.apps.runtime.presentation.rpc import DisplayRpc
    from emo_master.clients.runtime import display_session

    trace.source_before = trace.observe(fingerprints, root, default={})
    original_channel = grpc.insecure_channel

    def channel(*args, **kwargs):
        inner = original_channel(*args, **kwargs)
        return trace.observe(_Channel, inner, trace, default=inner)
    patch(grpc, "insecure_channel", channel)
    original_result = display_session.decodeResult

    def result(wire):
        value = original_result(wire)
        trace.observe(trace.register_result, value)
        return value
    patch(display_session, "decodeResult", result)
    original_decoder = display_session.DisplaySession._decode

    def decoder(self):
        trace.local.decoder = True
        try:
            return original_decoder(self)
        finally:
            trace.local.decoder = False
            trace.local.verify = trace.local.decode = None
    patch(display_session.DisplaySession, "_decode", decoder)
    original_png = display_session.decodePng

    def png(content):
        metadata = getattr(trace.local, "decode", None)
        trace.local.decode = trace.local.verify = None
        if trace.disabled or metadata is None:
            return original_png(content)
        return trace.call("client.png_decode_inclusive", metadata, original_png, content)
    patch(display_session, "decodePng", png)
    original_cv_decode = cv2.imdecode

    def cv_decode(*args, **kwargs):
        metadata = getattr(trace.local, "metadata", None)
        if trace.disabled or metadata is None or getattr(trace.local, "stage", "") != "client.png_decode_inclusive":
            return original_cv_decode(*args, **kwargs)
        return trace.call("client.opencv_decode", metadata, original_cv_decode, *args, **kwargs)
    patch(cv2, "imdecode", cv_decode)
    original_hash = hashlib.sha256

    def sha256(*args, **kwargs):
        if trace.disabled:
            return original_hash(*args, **kwargs)
        metadata = getattr(trace.local, "metadata", None)
        if getattr(trace.local, "asset_read", False) and metadata is not None:
            return trace.call("server.sha256_bytes", metadata, original_hash, *args, **kwargs)
        metadata = getattr(trace.local, "verify", None)
        trace.local.verify = None
        if metadata is not None:
            return trace.call("client.sha256_bytes", metadata, original_hash, *args, **kwargs)
        return original_hash(*args, **kwargs)
    patch(hashlib, "sha256", sha256)
    original_init, original_read = AssetStore.__init__, AssetStore.read

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.lock = trace.observe(_Lock, self.lock, trace, default=self.lock)

    def read(self, *args, **kwargs):
        metadata = getattr(trace.local, "metadata", None)
        if trace.disabled or metadata is None:
            return original_read(self, *args, **kwargs)
        trace.local.asset_read, trace.local.asset_lock_count = True, 0
        try:
            return trace.call("server.asset_read", metadata, original_read, self, *args, **kwargs)
        finally:
            trace.local.asset_read = False
    patch(AssetStore, "__init__", init)
    patch(AssetStore, "read", read)
    original_file_read = Path.read_bytes

    def file_read(path):
        metadata = getattr(trace.local, "metadata", None)
        if trace.disabled or metadata is None or not getattr(trace.local, "asset_read", False):
            return original_file_read(path)
        return trace.call("server.file_read", metadata, original_file_read, path)
    patch(Path, "read_bytes", file_read)
    original_unary = AioRuntimeServer._unary

    def unary(server, method, category):
        if trace.disabled or not isinstance(getattr(method, "__self__", None), DisplayRpc) or method.__name__ != "ReadAsset":
            return original_unary(server, method, category)
        runtime = server.presentation.runtimeInstanceId
        with trace.lock:
            if len(trace.runtimes) < 2 or runtime in trace.runtimes:
                trace.runtimes.add(runtime)
            else:
                trace.count("runtime_registry_overflow")

        def worker(request, context):
            entry = trace.observe(trace.take, "requests", id(request))
            if entry is None:
                trace.observe(trace.count, "server_worker_unmatched")
                reply = method(request, context)
                # IDs can be reused after a canceled reply is discarded. Every
                # untraced result invalidates any stale identity at that ID.
                if trace.observe(trace.take, "replies", id(reply)) is not None:
                    trace.observe(trace.count, "untraced_reply_cleared_stale_identity")
                return reply
            metadata, arrived = entry
            trace.observe(trace.record, "server.dispatch_to_worker", arrived, time.perf_counter_ns(), None, None, metadata)
            reply = trace.call("server.worker", metadata, method, request, context)
            trace.observe(trace.associate, "replies", id(reply), metadata)
            return reply
        inner = original_unary(server, worker, category)

        async def handler(request, context):
            def match():
                metadata = trace.identity(request)
                headers = [value for key, value in context.invocation_metadata() if key == HEADER]
                with trace.lock:
                    client = trace.tokens.get(headers[0]) if len(headers) == 1 else None
                if (metadata is None or client is None or request.runtime_instance_id != runtime
                        or any(client[key] != metadata[key] for key in ("runtime_instance_id", "job_id", "resource_id"))):
                    trace.count("server_identity_or_token_unmatched")
                    return None
                return dict(client)
            metadata = trace.observe(match)
            if metadata is None:
                return await inner(request, context)
            started, outcome = time.perf_counter_ns(), "OK"
            trace.observe(trace.associate, "requests", id(request), (metadata, started))
            try:
                return await inner(request, context)
            except BaseException as error:
                outcome = type(error).__name__[:80]
                raise
            finally:
                # On cancellation the actual worker can still be running. Its
                # identity was taken at entry; neither readers nor RPC quota are
                # released by instrumentation. An unstarted request stays finite.
                trace.observe(trace.record, "server.handler", started, time.perf_counter_ns(), None, None, metadata, outcome)
                if outcome != "CancelledError" and trace.observe(trace.take, "requests", id(request)) is not None:
                    trace.observe(trace.count, "server_worker_not_started")
        handler._r3_asset_trace = trace
        return handler
    patch(AioRuntimeServer, "_unary", unary)
    original_handler = grpc.unary_unary_rpc_method_handler

    def register(behavior, request_deserializer=None, response_serializer=None):
        if getattr(behavior, "_r3_asset_trace", None) is not trace:
            return original_handler(behavior, request_deserializer, response_serializer)

        def serialize(reply):
            metadata = trace.observe(trace.take, "replies", id(reply))
            if metadata is None:
                trace.observe(trace.count, "server_serialize_unmatched")
                return response_serializer(reply)
            details = dict(metadata)

            def encode():
                content = response_serializer(reply)
                trace.observe(details.__setitem__, "wire_bytes", len(content))
                return content
            return trace.call("server.protobuf_serialize", details, encode)
        return original_handler(behavior, request_deserializer, serialize)
    patch(grpc, "unary_unary_rpc_method_handler", register)
    try:
        yield trace
    finally:
        trace.source_after = trace.observe(fingerprints, root, default={})
