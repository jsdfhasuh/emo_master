from typing import Protocol


class OperatorProtocol(Protocol):
  def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
    ...

  def executeNode(
    self,
    inputs: dict[str, object],
    params: dict[str, object],
    runtimeContext: dict[str, object]
  ) -> dict[str, object]:
    ...
