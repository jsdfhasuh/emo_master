from pathlib import Path


def testRuntimeProtoExists() -> None:
  assert Path("proto/runtime.proto").exists()


def testOperatorInfoIncludesPortAndSchemaFields() -> None:
  protoText = Path("proto/runtime.proto").read_text(encoding="utf-8")
  assert "map<string, string> input_ports = 4;" in protoText
  assert "map<string, string> output_ports = 5;" in protoText
  assert "string param_schema_json = 6;" in protoText
  assert "string category = 7;" in protoText
  assert "string icon_key = 8;" in protoText
  assert "string summary = 9;" in protoText
