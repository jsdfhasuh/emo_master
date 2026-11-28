"""Diagnostic-only Path.open wrapper restricted to one armed Path.read_bytes call."""
from contextlib import contextmanager
import threading
import time

class FileSubphases:
    def __init__(self, limit=2048):
        self.limit = limit
        self.rows = []
        self.dropped = 0
        self.local = threading.local()
        self.lock = threading.Lock()

    @contextmanager
    def armed(self, path, call_id):
        # Identity is the exact Path object used by original Path.read_bytes.
        previous = getattr(self.local,"target",None)
        self.local.target = (path, call_id)
        try:
            yield
        finally:
            self.local.target = previous

    def run(self, call_id, stage, call, *args, **kwargs):
        w = time.perf_counter_ns()
        c = time.thread_time_ns()
        ok = False
        try:
            result = call(*args, **kwargs)
            ok = True
            return result
        finally:
            elapsed = time.perf_counter_ns()-w
            cpu = time.thread_time_ns()-c
            # Never expose path, bytes, or exception text.
            with self.lock:
                if len(self.rows) < self.limit:
                    self.rows.append({"call_id":call_id,"stage":stage,"elapsed_ns":elapsed,
                                      "thread_cpu_ns":cpu,"ok":ok})
                else:
                    self.dropped += 1

    def wrap_open(self, original):
        probe = self

        class ReadProxy:
            def __init__(self, file, call_id):
                self.file, self.call_id = file, call_id
            def __enter__(self):
                self.reader = probe.run(self.call_id,"enter",self.file.__enter__)
                return self
            def read(self,*args,**kwargs):
                return probe.run(self.call_id,"read",self.reader.read,*args,**kwargs)
            def __exit__(self,*exc):
                return probe.run(self.call_id,"close",self.file.__exit__,*exc)

        def open_file(path,*args,**kwargs):
            scope = getattr(probe.local,"target",None)
            mode = kwargs.get("mode",args[0] if args else "r")
            if scope is None or path is not scope[0] or mode != "rb":
                return original(path,*args,**kwargs)
            return ReadProxy(probe.run(scope[1],"open",original,path,*args,**kwargs),scope[1])
        return open_file
