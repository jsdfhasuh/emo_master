# 算子参数显示名称

参数中文名称使用 JSON Schema 的可选 `title` 注解。属性键始终是执行和存储使用的稳定标识；不要把属性键、`required` 中的键或 `enum` 原始值改成中文。

```json
{
  "type": "object",
  "properties": {
    "kernelSize": {
      "title": "卷积核大小",
      "type": "integer",
      "minimum": 3,
      "default": 5
    }
  },
  "required": ["kernelSize"]
}
```

通用表单显示“卷积核大小 *”，标签与输入控件的悬停提示包含“参数键：kernelSize”。已有 `description` 仍会显示在提示中。缺少标题、空白标题或非字符串标题会回退到原始键名；第三方插件无需补齐中文才能注册。

内置算子的 `manifest.json` 与算子类 `meta.paramSchema` 必须同步维护，包括嵌套 `properties` 和数组 `items` 中的属性标题。注册时继续进行完整 schema 一致性检查。相机、ROI、直方图、PLC 和 SQLite 专用参数控件通过 `applyParameterLabel` 读取同一份标题，按显式字段映射设置标签与提示。

打开旧工程时，Designer 按 `operatorId` 查询已加载的算子目录，将目录中同路径属性的有效标题合并至节点 schema 的深拷贝。只合并 `title`，不替换默认值、约束或参数集合；不修改原节点，也不会因打开编辑器将工程标为未保存。目录没有有效标题时保留工程已有标题。

嵌套对象标签遵循相同规则。数组和自由对象的 JSON 编辑区保留真实键名；标题不会替换 JSON 文本。算子名称、端口名称、枚举选项和校验错误不属于本次中文化范围。

## 验证

- `tests/core/plugin/test_builtin_parameter_titles.py` 检查全部内置参数标题及实际注册结果。
- `tests/designer/test_parameter_titles.py` 检查标题回退、原值回写、嵌套结构、旧工程、撤销重做和窄窗口布局。
- 各专用编辑器测试检查字段标题、英文键提示与现有行为。
- `scripts/validate_parameter_titles.py --output <新输出目录>` 使用 Windows 原生 Qt 和不执行设备操作的上下文生成实际编辑窗口截图；通过 `QT_SCALE_FACTOR=1` 或 `1.5` 分别检查缩放。

参数仍经现有 `param_schema_json` 接口传输，不要求 protobuf 或工程版本迁移。
