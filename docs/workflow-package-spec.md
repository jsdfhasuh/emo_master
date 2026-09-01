# EmoMaster 工作流包 v1.0

工作流包用于在项目之间复用一个工作流及其依赖。文件扩展名为
`.emowf.json`，它与完整项目包 `.vxpkg` 相互独立。

## 文件结构

```json
{
  "schemaVersion": "1.0",
  "packageType": "emo-master.workflow",
  "rootWorkflowId": "main",
  "workflowOrder": ["main", "body"],
  "workflows": {
    "main": {
      "name": "Main",
      "inputs": {},
      "outputs": {},
      "nodes": [],
      "edges": [],
      "layout": {"nodePositions": {}}
    }
  },
  "requiredOperators": []
}
```

`workflows` 直接复用 `project.json v2.1` 的工作流定义。导出会从所选根工作流
开始，递归包含 Subflow、Repeat、ForEach 和 While 的 body/condition 依赖；不相关
工作流不会进入包。

## 导入规则

- 导入不会改变当前项目入口。
- 与当前项目冲突的工作流 ID 自动增加 `-2`、`-3` 后缀。
- Subflow 和 Loop 中的工作流引用会按新 ID 自动重写。
- 工作流边界节点 ID、边端点和布局坐标会同步重写。
- 同一个包可以重复导入，每次都会创建独立副本。
- 导入前会预览根工作流、依赖、ID 重命名、缺失算子和外部文件路径。
- 可选择仅导入并打开包的根工作流，或在当前工作流自动插入指向导入根的
  Subflow 调用节点；后者为默认选项。
- 预览取消、文件无效或导入失败不会修改当前项目。
- 导入完成后需要保存项目才会写入磁盘。

工作流包只携带图、接口、参数和布局。参数中的外部文件路径会原样保留；v1.0
不会复制图片或其他资源文件。需要连同资源迁移时，应使用完整项目包或手动复制资源。

## Designer 操作

在顶部工作流标签上点击右键：

1. 选择“导出工作流包”保存当前标签及依赖。
2. 选择“导入为子工作流”，在预览中核对重命名、算子和外部路径警告。
3. 保持默认勾选可在当前画布自动插入 Subflow；取消勾选则只导入工作流。
4. 检查导入后的工作流与依赖树，然后保存项目。
