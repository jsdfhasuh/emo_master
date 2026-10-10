"""Bounded shared editor work; cancellation never releases running admission."""
from concurrent.futures import ThreadPoolExecutor
import threading

from emo_master.apps.designer.services.display_calls import DisplayCallContext


class EditorRequests:
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='sqlite-editor')
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0

    def submit(self, operation):
        with self.lock:
            if self.active >= 2:
                raise RuntimeError('SQLite 编辑器管理请求额度为 2，请等待实际操作结束')
            self.active += 1
            self.peak = max(self.peak, self.active)
        token = DisplayCallContext()
        def work():
            try:
                return operation(token)
            finally:
                with self.lock:
                    self.active -= 1
        try:
            return self.pool.submit(work), token
        except BaseException:
            with self.lock:
                self.active -= 1
            raise


requests = EditorRequests()
