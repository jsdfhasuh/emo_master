"""Bounded idle subscriptions, not a permanently occupied worker per stream.

Two independent consumers per Job; 32 displayed Jobs (the latest-cache address
budget). Local windows share a DisplaySession. FIFO work admission has two
workers and at most one outstanding operation per admitted subscription.
"""
import asyncio
from collections import deque
import time

import grpc


class DisplaySubscriptions:
    jobsLimit = 32
    perJobLimit = 2

    def __init__(self, owner, workers):
        self.owner = owner
        self.byJob = {}
        self.events = deque(maxlen=512)
        self.sequence = self.eventSequence = 0
        self.work = asyncio.Semaphore(workers)
        self.workActive = self.workPeak = 0

    def record(self, action, identity, job, reason=""):
        self.eventSequence += 1
        self.events.append(dict(sequence=self.eventSequence, time_ns=time.perf_counter_ns(),
            action=action, subscriptionId=identity, jobId=job,
            runtimeInstanceId=self.owner.presentation.runtimeInstanceId,
            active=self.owner.active["display"], byJob=dict(self.byJob), reason=reason))

    async def run(self, function, *args):
        # Callers shield this task. Cancellation never returns a worker credit
        # while synchronous work is running. Semaphore waiters are FIFO.
        async with self.work:
            self.workActive += 1
            self.workPeak = max(self.workPeak, self.workActive)
            try:
                return await self.owner.loop.run_in_executor(self.owner.pools["display"], function, *args)
            finally:
                self.workActive -= 1

    def handler(self, method):
        from emo_master.apps.runtime.grpc_server.aio_entry import RpcAbort, SyncContext, nextItem

        async def call(request, context):
            job = request.job_id
            self.sequence += 1
            identity = self.sequence
            if job not in self.owner.presentation.jobs:
                await context.abort(grpc.StatusCode.NOT_FOUND, "unknown display Job")
            reason = ("display per-Job subscriptions" if self.byJob.get(job, 0) >= self.perJobLimit
                      else "display Job subscriptions" if job not in self.byJob and len(self.byJob) >= self.jobsLimit
                      else "")
            if reason:
                self.record("rejected", identity, job, reason)
                await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, reason)
            self.byJob[job] = self.byJob.get(job, 0) + 1
            self.owner.active["display"] += 1
            self.owner.peak["display"] = max(self.owner.peak["display"], self.owner.active["display"])
            self.record("admitted", identity, job)
            completed = asyncio.Event()
            context.add_done_callback(lambda _: self.owner.loop.call_soon_threadsafe(completed.set))
            token = SyncContext()
            token.nonblockingDisplay = True
            iterator = pending = None
            constructing = True
            try:
                pending = self.owner.loop.create_task(self.run(method, request, token))
                iterator = await asyncio.shield(pending)
                constructing = False
                while not context.cancelled():
                    pending = self.owner.loop.create_task(self.run(nextItem, iterator))
                    valid, message = await asyncio.shield(pending)
                    if not valid:
                        return
                    if message is not None:
                        yield message  # Backpressure: no next snapshot until consumed.
                    await asyncio.sleep(.02)  # Idle waits own NO executor worker.
            except RpcAbort as error:
                await context.abort(error.code, error.details)
            finally:
                token.cancelled.set()
                # A callback may be the operation that unblocks next/construct.
                # Do not queue it behind the work it must cancel, nor behind
                # both busy display workers. Reuse the existing cleanup pool.
                with token.lock:
                    hasCallbacks = bool(token.callbacks)

                async def retire():
                    nonlocal iterator
                    callbacks = (self.owner.loop.run_in_executor(self.owner.cleanupPool, token.finishCallbacks)
                                 if hasCallbacks else None)
                    try:
                        if pending is not None:
                            try:
                                value = await pending
                                if constructing:
                                    iterator = value
                            except Exception as error:
                                self.owner.errors.append(repr(error))
                        try:
                            if callbacks is not None:
                                await callbacks
                        finally:
                            if iterator is not None and hasattr(iterator, "close"):
                                await self.run(iterator.close)
                    except Exception as error:
                        self.owner.errors.append(repr(error))
                    finally:
                        await completed.wait()
                        self.byJob[job] -= 1
                        if not self.byJob[job]:
                            del self.byJob[job]
                        self.owner.active["display"] -= 1
                        self.record("retired", identity, job)
                task = self.owner.loop.create_task(retire())
                self.owner.cleanups.add(task)
                task.add_done_callback(self.owner.cleanups.discard)
        return call
