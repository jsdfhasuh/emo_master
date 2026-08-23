from emo_master.apps.designer.state.schema_utils import applySchemaDefaults, collectSchemaErrors


def testApplySchemaDefaultsForNestedObjectAndArray() -> None:
  schema = {
    "type": "object",
    "properties": {
      "config": {
        "type": "object",
        "properties": {
          "enabled": {"type": "boolean", "default": True}
        }
      },
      "points": {
        "type": "array",
        "items": {"type": "integer"},
        "default": [1, 2]
      }
    }
  }

  values = applySchemaDefaults(schema, {})
  assert isinstance(values, dict)
  assert values["config"]["enabled"] is True
  assert values["points"] == [1, 2]


def testCollectSchemaErrorsForInvalidNestedValues() -> None:
  schema = {
    "type": "object",
    "properties": {
      "items": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "score": {"type": "number", "minimum": 0}
          },
          "required": ["score"]
        }
      }
    },
    "required": ["items"]
  }

  errors = collectSchemaErrors(schema, {"items": [{}, {"score": -1}]}, "params")
  assert any("params.items[0].score is required" in error for error in errors)
  assert any("params.items[1].score must be >= 0" in error for error in errors)
