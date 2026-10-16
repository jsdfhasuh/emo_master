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
  assert "string editor_spec_json = 12;" in protoText
  assert "string editor_issues_json = 13;" in protoText


def testOperatorEditorPreviewRpcsAreDeclared() -> None:
  protoText = Path("proto/runtime.proto").read_text(encoding="utf-8")
  assert "rpc GetOperatorEditorAsset" in protoText
  assert "rpc UploadPreviewImage" in protoText
  assert "rpc RunOperatorPreview" in protoText
  assert "rpc CancelOperatorPreview" in protoText
  assert "rpc OpenOperatorPreviewSession" in protoText
  assert "rpc StreamOperatorPreviewFrames" in protoText
  assert "rpc CloseOperatorPreviewSession" in protoText
  assert "string project_id = 4;" in protoText
  assert "string project_id = 2;" in protoText


def testGlobalCounterRpcsAreDeclared() -> None:
  protoText = Path("proto/runtime.proto").read_text(encoding="utf-8")
  assert "message GlobalCounterInfo" in protoText
  assert "int64 updated_at_ms = 3;" in protoText
  assert "rpc ListGlobalCounters" in protoText
  assert "rpc GetGlobalCounter" in protoText
  assert "rpc SetGlobalCounter" in protoText
  assert "rpc ResetGlobalCounter" in protoText
