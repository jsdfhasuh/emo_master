from emo_master.apps.designer.ui.param_form import SchemaParamForm, getFieldDefinitions


def ensureQApp() -> None:
  try:
    from PySide2.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
      _ = QApplication([])
  except Exception:
    pass


def testGetFieldDefinitionsParsesSchema() -> None:
  schema = {
    "type": "object",
    "properties": {
      "enabled": {"type": "boolean", "default": True},
      "threshold": {"type": "integer", "default": 5, "minimum": 0, "maximum": 255}
    },
    "required": ["threshold"]
  }

  fields = getFieldDefinitions(schema)
  assert len(fields) == 2
  threshold = [field for field in fields if field.name == "threshold"][0]
  assert threshold.required is True
  assert threshold.minimum == 0


def testSchemaParamFormReturnsSchemaDefaults() -> None:
  ensureQApp()

  form = SchemaParamForm()
  schema = {
    "type": "object",
    "properties": {
      "enabled": {"type": "boolean", "default": True},
      "mode": {"type": "string", "enum": ["fast", "accurate"], "default": "fast"}
    }
  }

  form.setSchema(schema, {})
  values = form.getValues()
  assert values["enabled"] is True


def testSchemaParamFormSupportsNestedObjectAndArrayDefaults() -> None:
  ensureQApp()
  form = SchemaParamForm()
  schema = {
    "type": "object",
    "properties": {
      "config": {
        "type": "object",
        "properties": {
          "mode": {"type": "string", "default": "fast"}
        }
      },
      "points": {
        "type": "array",
        "items": {"type": "integer"},
        "default": [1, 2, 3]
      }
    }
  }

  form.setSchema(schema, {})
  values = form.getValues()
  assert values["config"]["mode"] == "fast"
  assert values["points"] == [1, 2, 3]
