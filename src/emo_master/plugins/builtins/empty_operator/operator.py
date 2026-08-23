from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OperatorMeta:
  operatorId: str
  displayName: str
  version: str
  inputPorts: dict[str, str]
  outputPorts: dict[str, str]
  paramSchema: dict[str, object]


class EmptyOperator:
  meta = OperatorMeta(
    operatorId="vision.demo.empty",
    displayName="Empty Operator",
    version="0.1.0",
    inputPorts={"image": "image"},
    outputPorts={"result": "json"},
    paramSchema={
      "type": "object",
      "properties": {
        "enabled": {"type": "boolean", "default": True}
      },
      "required": []
    }
  )

  def initOperator(self, initContext: dict[str, object]) -> None:
    _ = initContext

  def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
    enabled = params.get("enabled", True)
    if not isinstance(enabled, bool):
      return {"code": "E_PARAM_INVALID", "message": "enabled must be boolean"}
    return None

  def executeNode(
    self,
    inputs: dict[str, object],
    params: dict[str, object],
    runtimeContext: dict[str, object]
  ) -> dict[str, Any]:
    _ = runtimeContext
    paramError = self.validateParams(params)
    if paramError is not None:
      return {"status": "error", "error": paramError}

    if "image" not in inputs:
      return {
        "status": "error",
        "error": {"code": "E_INPUT_MISSING", "message": "input 'image' is required"}
      }

    return {
      "status": "ok",
      "outputs": {
        "result": {"ok": True}
      },
      "metrics": {
        "latencyMs": 0.0
      },
      "diagnostics": {
        "text": "Empty operator executed"
      }
    }

  def disposeOperator(self) -> None:
    return None
