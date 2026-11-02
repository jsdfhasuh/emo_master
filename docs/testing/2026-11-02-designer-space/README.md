# 页面空间、终端预览与空白块：实施与验证

日期2026-11-02。Designer功能提交 `32c2b4efb7ba932636e2145fd423e9b2b484b902`，基线 `46af63de`；Web功能提交 `06ebe51e97e48d355949eeaf503be4a7c21ff18f`，基线 `b1ca6d38`。两端使用同一个SDK1.2.0，无开发分支互相合并或主分支合并，无部署。

## 交付行为

- 默认页面四周8逻辑像素居中适配，顶部64改12；面板折叠入口移到内容外。手动缩放/平移保留，适应画布/Home恢复自动模式，活动手势延后适配。
- 固定终端四档预设及自定义宽高，实际页面视口精确使用逻辑尺寸。高DPI/窗口变化只调整画布显示，不改工程、历史或固定排版尺寸。
- 页面浮动滚动条由原生范围驱动，不占布局宽高；候选与最终布局共用视口，空/短/长页面按真实溢出显示。
- spacer默认24×24，固定/弹性、主轴权重与换行内填充；编辑虚线提示不参与测量，桌面预览和Web透明占位。旧grid固定占位，弹性属性显示响应式适用条件。
- 共享DTO/发布模型/SDK及Web校验同步；固定与弹性都进入正式示例包，无额外数据来源或采集配置。生成物、摘要和干净源码来源见[SDK交接](../../handoffs/runtime-web-panel-v1/sdk-1.2.0/README.md)。

## 实际验证

| 层次 | 结果 |
| --- | --- |
| 页面编辑完整套件 | **313 passed / 1 failed**；唯一失败在旧源码独立复现，见下文 |
| 共享页面模型与Qt渲染完整套件 | **529 passed / 5 failed**；五项在旧源码独立复现 |
| 新功能定向复测 | **21 passed**；包含真实Designer打开生成工程、滚动条、空白块及组件库 |
| 共享契约、Schema、正式示例与归档 | **55 passed** |
| 头盔工程临时副本 | **1 passed**，正常入口打开、固定视口、切页不改payload，原文件字节未变化；禁用本地数据读取 |
| Web完整后端 | **146 passed / 2 opt-in skipped / 242 subtests passed**，包含最终完整契约摘要与预览上下文 |
| Web前端 | 构建通过，**63 tests passed** |
| 生产前端真实浏览器 | 系统Chrome headless，**10个几何场景通过**，原browser_smoke通过 |
| 静态检查 | 全src/tests Ruff通过，mypy **607 source files**通过，proto drift通过 |
| SDK安装隔离验证 | 本地独立target安装wheel，以Python -I阻断emo_master/Qt/设备与推理模块导入，模型及发布语义通过；wheel等于工作区源码，Git源码按LF规范化后相等 |
| 启动入口 | start_designer.cmd --check通过；未启动窗口或Job |

同一测试可能出现在定向组和完整组，不将上述计数相加冒充独立覆盖。XML、浏览器几何记录及SHA-256见[证据索引](./validation-summary.json)。Qt测试使用offscreen平台，Chrome为实际浏览器布局验收；它们不等于真实桌面鼠标验收。

## 已复现的基线失败与非本轮问题

六项扩展失败均在旧工作区 `0e58440a` 上独立复现；已核对其相关模型、renderer、coordinator及测试与本轮基线46af63de的差异边界，相关失败路径未因本次修改改变。

1. `test_user_path::testNativePageEditingAndLegacyPreviewBackend`：显式2.1旧工程无presentation，saved()仍读取snapshot而断言失败。该问题已有暂停集成分支中的独立修复，本轮没有重复实现或引入其合并。
2. `test_components::testEvictHiddenPageBeforeAllocatingTableQuota`：历史1.0 presentation夹具后来赋值默认响应式Page，重新校验拒绝版本/布局混用。
3. `test_live_refresh::testImageEmptyTextDoesNotHideFailures`四档：测试仍断言tooltip恰好等于reason，而基线已经提供包含reason的丰富诊断。保留原断言和失败证据。

这六项不记为通过。它们不阻断本轮适用范围的人工验收；本报告不宣称整仓pytest或CI全绿。

## 本轮验证中关闭的问题

- 固定终端下只调整窗口时，漏掉显示重新适配；增加视口变化判断及逻辑尺寸不变/显示比例变化回归。
- 全局鼠标按钮状态影响自动适配；改为只判断当前画布拥有的手势。
- 空白块显式12px高度被原生24px提示撑大；测量由模型尺寸决定，增加12×12回归。
- 发布属性必须继承共享VisualProps，保证flex能进入正式包。
- Qt/Web普通fill与弹性块分配不同；统一横向最小基准、等宽基准及零高度，浏览器明确校验228/172与245.33/245.33/109.33。
- 页面可视区缩短后旧测试点击已被裁剪的卡片中心；改点可见交集或先滚入视野，保留原有待应用输入/滚动位置/保存标记断言。
- 临时只同步示例包导致Web预览上下文revision不一致；完整重生成并同步SDK后，两项失败与最终manifest检查全部通过。

先前中断的宽范围检查不能计为通过；随后分组完整执行完成。旧失败记录留在本机build日志与本目录baseline XML中，没有删除断言或将跳过计为通过。

## 人工验收与启动

运行 `D:/jsdfhasuh/documents/my_project/emo_master_designer/start_designer.cmd`。生成临时工程：

```powershell
& C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe scripts/create_designer_space_acceptance.py D:/tmp/emo-designer-space-acceptance/acceptance.emoproj
```

生成器从WorkflowStore最新默认结构创建，检查2.9/1.2、保存重读及编译；本轮另经MainWindow实际打开入口验证。Dot完整步骤见[验收清单](../2026-11-02-designer-space-dot-acceptance.md)。

真实Windows100/150%显示缩放与真实鼠标拖动、冻结包验收尚未执行。当前头盔文件本轮读取已是2.9/1.2响应式页面，因此临时副本结果不冒充旧grid验收；旧网格使用原有15项几何/嵌套尺寸测试验证。没有修改原工程、访问相机/PLC或部署。
