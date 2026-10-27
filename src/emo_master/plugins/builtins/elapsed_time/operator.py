from time import monotonic

from emo_master.plugins.builtins.image_loader.operator import OperatorMeta


class ElapsedTimeOperator:
    meta = OperatorMeta(
        "vision.state.elapsed_time", "\u8fd0\u884c\u8ba1\u65f6", "1.0.0",
        {"after": {"type": "any", "required": True, "nullable": False}},
        {"seconds": {"type": "number", "required": True, "nullable": False},
         "text": {"type": "string", "required": True, "nullable": False}},
        {"type": "object", "properties": {}, "additionalProperties": False},
    )

    def __init__(self):
        self._started = None

    def initOperator(self, initContext):
        self._started = None

    def validateParams(self, params):
        return None

    def executeNode(self, inputs, params, runtimeContext):
        if inputs.get("after") is None:
            return {"status": "error", "error": {"code": "E_INPUT_MISSING", "message": "after is required"}}
        cancelled = runtimeContext.get("raiseIfCancellationRequested")
        if callable(cancelled):
            cancelled()
        now = monotonic()
        if self._started is None:
            self._started = now
        seconds = now - self._started
        hours, remainder = divmod(int(seconds), 3600)
        minutes, second = divmod(remainder, 60)
        return {"status": "ok", "outputs": {
            "seconds": seconds, "text": f"{hours:02d}:{minutes:02d}:{second:02d}"}}
