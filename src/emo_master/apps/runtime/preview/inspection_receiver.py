"""Bounded image adoption off the ordered worker-event bridge."""
import logging
import multiprocessing
import queue
import threading


class InspectionReceiver:
    def __init__(self, jobId, publish, onError, *, capacity=8):
        self.credits = multiprocessing.get_context('spawn').BoundedSemaphore(capacity)
        self._pending = queue.Queue(maxsize=capacity)
        self._publish, self._onError = publish, onError
        self._lock = threading.Lock()
        self._closed = False
        self.thread = threading.Thread(target=self._run, name=f'inspection-receiver-{jobId}', daemon=True)
        self.thread.start()

    def submit(self, event):
        # A producer reserves a credit before sending on the existing channel.
        # Active + queued + in-transit messages therefore fit without waiting.
        with self._lock:
            try:
                if self._closed:
                    raise RuntimeError('inspection receiver is closed')
                self._pending.put_nowait(event)
            except Exception:
                self.credits.release()
                raise

    def _run(self):
        while True:
            event = self._pending.get()
            if event is None:
                return
            try:
                self._publish(event)
            except Exception as error:
                try:
                    self._onError(error)
                except Exception:
                    logging.getLogger(__name__).exception('Failed to report inspection adoption error')
            finally:
                self.credits.release()
            del event

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed = True
                self._pending.put(None)
        self.thread.join()
