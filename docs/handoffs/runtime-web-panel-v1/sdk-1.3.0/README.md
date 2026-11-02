# SDK 1.3.0：持久向上扩展画布开发交接

本次为 **Panel Contract 1.0 / SDK 1.3.0** 的未提交开发交接。
Designer HEAD 为 `8df8599fc026c424a49e87cc53fb45f6b371f16f`，生成时 `sourceDirty=true`；
不能把该 HEAD 单独当作包含本次实现的提交。Web HEAD 为
`b89826b0841c6b4ef7f9c5f11ac938c6008934c7` 加本次消费端修改。
没有合并分支、推送或部署；旧 SDK 交接目录保留。

新增页面 `canvasTop` 和根组件 `responsive.canvasOffsetY`，默认0省略。
两字段为 -32768..0 逻辑像素整数，组件偏移不得小于页面上边界；只用于响应式根页面。
宽度、换行和流占位继续由原响应式规则决定。新包声明 `layout.expanded-canvas-v1`，
Web 检查并按根部留出的扩展空间与组件相对偏移显示。工程仍为2.9/页面1.2。

| 产物 | SHA-256 |
| --- | --- |
| [完整ZIP](panel-contract-v1.zip)（92,030字节） | `4cf0bcb59fcd427d5e48a74c3f2082f834682a321364a6cc42234792f5577aca` |
| [SDK wheel](emo_panel_protocol-1.3.0-py3-none-any.whl)（38,789字节） | `cd86472b5faa74e8fb1048a95fcaf67cf59e5a285e4d5e69763cd9a71bb55295` |

ZIP含由 `scripts/build_panel_contract.py` 同次生成的Schema、TypeScript、wheel、示例工程/
页面包与fixtures；[完整manifest](contract-manifest.json)逐文件记录摘要。
示例标题上移96px，保留原有空白块和历史数据演示。生成器通过模型、保存重读、编译与
normalCapture检查；生成器本身未启动Designer，所以manifest中的designerOpen仍为NOT_RUN。
后续使用模拟客户端、临时设置，经正常MainWindow打开入口独立验证PASS，见
[本次验证记录](../../../testing/2026-11-02-designer-canvas-expansion/README.md)。

Web使用完整ZIP中的所有文件同步`contracts/panel-contract-v1`，同时同步后端内嵌fixtures，
不要只替换示例页面包。新版wheel在独立target安装后运行Web完整后端测试，不改现有运行环境。
当前文件摘要只适用于这批开发产物；正式提交后的干净交接应重新生成并更新摘要。
