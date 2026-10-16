from emo_master.plugins.builtins.global_counter.operator import OperatorMeta


SCHEMA: dict[str, object] = {"type": "object", "properties": {
    "variableId": {"type": "string", "title": "全局变量", "minLength": 1}}, "required": ["variableId"]}


class ReadVariableOperator:
    meta = OperatorMeta("vision.state.variable_read", "读取全局变量", "1.0.0", {},
                        {"value": {"type": "any", "required": True, "nullable": False}}, SCHEMA)

    def validateParams(self, params):
        if not isinstance(params.get("variableId"), str) or not params["variableId"]:
            return {"code": "E_VARIABLE_UNKNOWN", "message": "variable ID is required"}
        return None

    def executeNode(self, inputs, params, runtimeContext):
        variables = runtimeContext.get("globalVariables")
        if variables is None:
            return {"status": "error", "error": {"code": "E_RUNTIME_STATE_UNAVAILABLE", "message": "global variable service unavailable"}}
        try:
            return {"status": "ok", "outputs": {"value": variables.get(params["variableId"])}}
        except Exception as error:
            return {"status": "error", "error": {"code": getattr(error, "code", "E_VARIABLE_READ"), "message": str(error)}}
