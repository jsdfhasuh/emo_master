from emo_master.plugins.builtins.global_counter.operator import OperatorMeta
from emo_master.plugins.builtins.variable_read.operator import ReadVariableOperator, SCHEMA


class WriteVariableOperator(ReadVariableOperator):
    meta = OperatorMeta("vision.state.variable_write", "写入全局变量", "1.0.0",
                        {"value": {"type": "any", "required": True, "nullable": False}},
                        {"value": {"type": "any", "required": True, "nullable": False}}, SCHEMA)

    def executeNode(self, inputs, params, runtimeContext):
        if "value" not in inputs:
            return {"status": "error", "error": {"code": "E_INPUT_MISSING", "message": "value is required"}}
        variables = runtimeContext.get("globalVariables")
        if variables is None:
            return {"status": "error", "error": {"code": "E_RUNTIME_STATE_UNAVAILABLE", "message": "global variable service unavailable"}}
        try:
            value = variables.set(params["variableId"], inputs["value"])
            return {"status": "ok", "outputs": {"value": value}}
        except Exception as error:
            return {"status": "error", "error": {"code": getattr(error, "code", "E_VARIABLE_WRITE"), "message": str(error)}}
