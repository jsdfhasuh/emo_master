"""Opt-in bounded timing only; never logs file paths or payload contents."""
import atexit
import json
import os
from pathlib import Path
import threading
import time

LIMIT = 256
ROWS = []
TLS = threading.local()
LOCK = threading.Lock()


def now():
    return time.perf_counter_ns()


def record(kind, asset_id):
    with LOCK:
        if len(ROWS) >= LIMIT:
            return None
        row = dict(kind=kind, asset_id=asset_id, rpc_id=len(ROWS), start_ns=now())
        ROWS.append(row)
        return row


def maximum(row, key, elapsed, offset=0):
    if elapsed > row.get(key, -1):
        row[key] = elapsed
        row[key + '_offset'] = offset
        row[key + '_end_ns'] = now()


class ReadHandle:
    def __init__(self, handle, row):
        self.handle, self.row, self.offset = handle, row, 0

    def __getattr__(self, name):
        return getattr(self.handle, name)

    def __enter__(self):
        self.handle.__enter__()
        return self

    def __exit__(self, *args):
        return self.handle.__exit__(*args)

    def read(self, size=-1):
        start = now()
        try:
            value = self.handle.read(size)
        finally:
            maximum(self.row, 'read_max_ns', now() - start, self.offset)
        self.offset += len(value)
        self.row['read_bytes'] = self.offset
        self.row['read_end_ns'] = now()
        return value


class TaggedIterator:
    def __init__(self, iterator, row):
        self.iterator, self.row = iter(iterator), row

    def __iter__(self):
        return self

    def __next__(self):
        previous = getattr(TLS, 'row', None)
        TLS.row = self.row
        start = now()
        try:
            value = next(self.iterator)
            self.row['batches'] = self.row.get('batches', 0) + 1
            return value
        except BaseException as error:
            self.row['iterator_terminal'] = type(error).__name__
            raise
        finally:
            maximum(self.row, 'iterator_max_ns', now() - start,
                    self.row.get('read_bytes', 0))
            TLS.row = previous

    def close(self):
        close = getattr(self.iterator, 'close', None)
        if close:
            return close()


class ClientCall:
    def __init__(self, call, row):
        self.call, self.row, self.previous = call, row, now()
        self.iterator = iter(call)

    def __getattr__(self, name):
        return getattr(self.call, name)

    def __iter__(self):
        return self

    def __next__(self):
        value = next(self.iterator)
        stamp = now()
        maximum(self.row, 'client_chunk_gap_max_ns', stamp - self.previous,
                self.row.get('received_bytes', 0))
        self.previous = stamp
        self.row['received_bytes'] = self.row.get('received_bytes', 0) + len(value)
        self.row['last_chunk_ns'] = stamp
        return value


def install():
    from prototypes.runtime_pages_p0 import network, continuous
    native_open = Path.open

    def opened(*args, **kwargs):
        handle = native_open(*args, **kwargs)
        row = getattr(TLS, 'row', None)
        return ReadHandle(handle, row) if row is not None else handle
    Path.open = opened

    native_start = network.IsolatedServer._start

    async def start(server):
        native_dispatch = server.loop.run_in_executor

        def dispatch(executor, function, *args):
            row = args[0].row if function is network._next and args and isinstance(args[0], TaggedIterator) else None
            if row is None:
                return native_dispatch(executor, function, *args)
            submitted = now()
            row['executor_submitted_ns'] = submitted

            def execute():
                row['executor_started_ns'] = now()
                maximum(row, 'executor_queue_max_ns', now() - submitted,
                        row.get('read_bytes', 0))
                try:
                    return function(*args)
                finally:
                    row['executor_completed_ns'] = now()
            return native_dispatch(executor, execute)
        server.loop.run_in_executor = dispatch
        return await native_start(server)
    network.IsolatedServer._start = start

    native_stream = network.IsolatedServer._stream

    def stream_factory(server, method, category, **kwargs):
        if category != 'asset' or 'batched' not in kwargs:
            return native_stream(server, method, category, **kwargs)

        async def observed(request, context):
            row = record('server', request.get('asset_id', ''))
            if row is None:
                async for value in native_stream(server, method, category, **kwargs)(request, context):
                    yield value
                return

            def construct(*args):
                return TaggedIterator(method(*args), row)
            delegate = native_stream(server, construct, category, **kwargs)(request, context)
            seen_batch = 0
            try:
                async for value in delegate:
                    stamp = now()
                    if row.get('batches', 0) != seen_batch:
                        seen_batch = row.get('batches', 0)
                        maximum(row, 'executor_completion_to_yield_max_ns',
                                stamp - row['executor_completed_ns'], row.get('read_bytes', 0))
                    row['wire_bytes_yielded'] = row.get('wire_bytes_yielded', 0) + len(value)
                    row['last_yield_ns'] = stamp
                    try:
                        yield value
                    finally:
                        # Includes gRPC transport/backpressure and scheduling;
                        # this is NOT file-read duration.
                        maximum(row, 'yield_resume_max_ns', now() - stamp,
                                row['wire_bytes_yielded'])
            except BaseException as error:
                row['stream_terminal'] = type(error).__name__
                raise
            finally:
                await delegate.aclose()
                row['finished_ns'] = now()
        return observed
    network.IsolatedServer._stream = stream_factory

    native_decode = continuous.NetworkConsumer._decode

    def decode(consumer, snapshot, values, received, diagnostics=None):
        row = record('client', values['image'][1]['asset']['asset_id'])
        if row is None:
            return native_decode(consumer, snapshot, values, received, diagnostics)
        row['ordinal'] = snapshot['ordinal']
        native_assets = consumer.assets
        consumer.assets = lambda *args, **kwargs: ClientCall(native_assets(*args, **kwargs), row)
        details = {} if diagnostics is None else diagnostics
        try:
            return native_decode(consumer, snapshot, values, received, details)
        except BaseException as error:
            row['terminal'] = type(error).__name__
            raise
        finally:
            consumer.assets = native_assets
            row['phases'] = details.get('read_phases', {})
            row['finished_ns'] = now()
    continuous.NetworkConsumer._decode = decode


def save():
    destination = os.environ.get('P0_TRANSFER_DIAG_DIR')
    if not destination:
        return
    path = Path(destination)
    path.mkdir(parents=True, exist_ok=True)
    (path / ('transfer-%s.json' % os.getpid())).write_text(json.dumps({
        'limit': LIMIT, 'limit_reached': len(ROWS) == LIMIT,
        'yield_resume_meaning': 'gRPC transport/backpressure plus scheduling, not pure disk read',
        'rows': ROWS}, indent=2), encoding='utf-8')


def main():
    import runpy
    import sys
    install()
    atexit.register(save)
    sys.argv[0] = 'prototypes.runtime_pages_p0.scenarios'
    runpy.run_module('prototypes.runtime_pages_p0.scenarios', run_name='__main__')


if __name__ == '__main__':
    main()
