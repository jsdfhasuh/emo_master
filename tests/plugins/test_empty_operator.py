from emo_master.plugins.builtins.empty_operator.operator import EmptyOperator


def testEmptyOperatorReturnsOk() -> None:
  operatorInstance = EmptyOperator()
  result = operatorInstance.executeNode({"image": object()}, {}, {})
  assert result["status"] == "ok"
