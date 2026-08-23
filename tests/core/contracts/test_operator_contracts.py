from emo_master.core.contracts.error_codes import ErrorCode
from emo_master.core.contracts.types import ExecutionStatus


def testErrorCodeContainsParamInvalid() -> None:
  assert ErrorCode.E_PARAM_INVALID.value == "E_PARAM_INVALID"


def testExecutionStatusValues() -> None:
  assert ExecutionStatus.OK.value == "ok"
