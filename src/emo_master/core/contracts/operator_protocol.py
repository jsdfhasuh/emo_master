from typing import Protocol

from emo_master.core.contracts.operator_logging import OperatorLoggerProtocol


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


class LifecycleOperatorProtocol(Protocol):
  """Optional job-scoped lifecycle implemented by stateful operators."""

  def initOperator(self, initContext: dict[str, object]) -> None:
    ...

  def disposeOperator(self) -> None:
    ...


__all__ = [
  "LifecycleOperatorProtocol",
  "OperatorLoggerProtocol",
  "OperatorProtocol",
]
