from enum import Enum


class ExecutionStatus(str, Enum):
  OK = "ok"
  ERROR = "error"
