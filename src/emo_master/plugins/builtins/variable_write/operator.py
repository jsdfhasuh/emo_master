from emo_master.plugins.builtins.global_counter.operator import OperatorMeta
from emo_master.plugins.builtins.variable_read.operator import ReadVariableOperator
from emo_master.core.project.global_variables import VariableError, variableWriteOperation


SCHEMA: dict[str, object] = {"type": "object", "properties": {
    "variableId": {
        "description": "目标变量稳定ID，常量不允许写；定义决定输出类型与job/persistent作用域。",
        "type": "string", "title": "全局变量", "minLength": 1
    },
    "operation": {"description": "set赋值；increment原子加delta，仅integer变量；reset恢复定义initialValue而不是一律归零。", "type": "string", "title": "变量操作", "enum": ["set", "increment", "reset"],
                  "default": "set", "xGlobalVariableBindingDisabled": True,
                  "xOptionLabels": {"set": "赋值", "increment": "原子自增", "reset": "恢复初始值"}},
    "delta": {"description": "仅increment使用的有符号64位整数增量，允许负值；set/reset不使用。", "type": "integer", "title": "整数增量", "default": 1,
              "minimum": -(2**63), "maximum": 2**63 - 1,
              "xEnabledWhen": {"operation": ["increment"]}},
}, "required": ["variableId"]}


class WriteVariableOperator(ReadVariableOperator):
    meta = OperatorMeta("vision.state.variable_write", "写入全局变量", "1.1.0",
                        {"value": {"type": "any", "required": False, "nullable": False},
                         "after": {"type": "any", "required": False, "nullable": False}},
                        {"value": {"type": "any", "required": True, "nullable": False}}, SCHEMA)

    def validateParams(self, params):
        error = super().validateParams(params)
        if error is not None:
            return error
        try:
            variableWriteOperation(params)
        except VariableError as error:
            return {"code": error.code, "message": str(error)}
        return None

    def executeNode(self, inputs, params, runtimeContext):
        error = self.validateParams(params)
        if error is not None:
            return {"status": "error", "error": error}
        operation = variableWriteOperation(params)
        required = "value" if operation == "set" else "after"
        if required not in inputs or (operation != "set" and inputs[required] is None):
            return {"status": "error", "error": {"code": "E_INPUT_MISSING", "message": f"{required} is required"}}
        if operation != "set" and "value" in inputs:
            return {"status": "error", "error": {"code": "E_INPUT_SHAPE", "message": f"value is not used by {operation}"}}
        variables = runtimeContext.get("globalVariables")
        if variables is None:
            return {"status": "error", "error": {"code": "E_RUNTIME_STATE_UNAVAILABLE", "message": "global variable service unavailable"}}
        effective = [{"path": ["operation"], "value": operation, "source": "normalized"}]
        try:
            if operation == "set":
                value = variables.set(params["variableId"], inputs["value"])
            elif operation == "increment":
                delta = params.get("delta", 1)
                value = variables.increment(params["variableId"], delta)
                effective.append({"path": ["delta"], "value": delta, "source": "normalized"})
            else:
                value = variables.reset(params["variableId"])
            return {"status": "ok", "outputs": {"value": value},
                    "diagnostics": {"effectiveParameters": effective}}
        except Exception as error:
            return {"status": "error", "error": {"code": getattr(error, "code", "E_VARIABLE_WRITE"), "message": str(error)}}
