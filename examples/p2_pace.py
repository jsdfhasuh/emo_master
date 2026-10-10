"""Controlled test scheduler only, never registered by a production entry."""
import time


class Pace:
    class Meta:
        inputPorts = {}
        outputPorts = {"tick": "boolean"}
    meta = Meta()

    def validateParams(self, params):
        return None

    def initOperator(self, context):
        self.index = 0

    def executeNode(self, inputs, params, runtimeContext):
        if not hasattr(self, "origin"):
            self.origin = time.perf_counter() + params.get("initialDelay", 1.0)
        due = self.origin + self.index / 5
        time.sleep(max(0, due - time.perf_counter()))
        actual = time.perf_counter()
        self.index += 1
        return {"status": "ok", "outputs": {"tick": True},
                "metrics": {"due": due, "actual": actual, "index": self.index}}
