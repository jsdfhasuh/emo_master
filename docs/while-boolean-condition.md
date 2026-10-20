# While 的布尔继续条件

While 判断的是每轮执行前的布尔值，不是工作流名称。新建 While 默认使用
`conditionMode=boolean`，不需要独立的条件工作流。

## 配置和数据流

```json
{
  "contractVersion": 2,
  "mode": "while",
  "bodyWorkflowId": "body",
  "conditionMode": "boolean",
  "conditionPort": "hasNext",
  "maxIterations": 10000,
  "timeoutMs": 0
}
```

循环体的状态输入和输出必须同名同类型。`conditionPort` 指定其中的一个
`boolean` 端口；整数、字符串和 `any` 不能替代布尔继续条件。

1. While 输入连线提供初始状态，例如初始 `hasNext=true`。
2. 每轮执行前判断当前 `hasNext`：`false` 立即退出，不执行循环体。
3. `true` 时执行循环体，并用它的同名输出更新状态。
4. 下一轮判断循环体回传的新 `hasNext`，不是重新计算循环外的初始输入。
5. 正常退出时，While 输出最终状态。取消、超时和最大迭代次数保护仍有效。

画布显示 `继续条件：hasNext（布尔值）`，输入标为 `初始条件 (hasNext)`；
关系树显示布尔条件及循环体输出到下一轮条件的静态数据映射。
参数窗口切换循环体时会保留失效端口并阻止应用，不会自动换成另一个条件。

## 图片批处理

推荐示例：`examples/image_batch_while_boolean/image-batch-while-boolean.emoproj`。
这个项目只有主工作流和循环体，不包含 `Continue While Images Remain`。

批量导入算子的 `hasNext` 表示当前图片读取后是否还有下一张。最后一张图片
即使输出 `hasNext=false` 也必须处理完成；它控制的是下一轮，而不是当前图片。
初始 `true` 在示例中由 `Number(1) -> Compare Number(eq 1)` 生成。

最大迭代次数的现有保护语义保持不变：需要留出最终一次 `false` 判断，
所以 `maxIterations` 至少是图片数加 1。示例不附带图片，也不保存/覆盖源图片。

## 旧项目兼容

未声明 `conditionMode` 的旧 While 仍按原条件工作流契约执行。参数窗口将它
标为“条件工作流（旧项目兼容）”，画布明确显示读取 `continue（布尔值）`。

旧节点可在参数窗口显式选择“布尔状态端口”，再选择继续条件端口并应用。
此时节点删除不再使用的 `conditionWorkflowId` 配置，但不删除原工作流、
不改端口键，也不迁移用户保存的项目文件。原工作流若没有其他调用，会出现在
入口未调用的工作流分组。重新保存项目是用户的显式操作。

Designer 和 Runtime 均需更新并重启；正在运行的进程不会自动加载源码修改。
这项源码修改不等同于便携包发布或现场验收。
