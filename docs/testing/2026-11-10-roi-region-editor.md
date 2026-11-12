# ROI 完整区域编辑窗口实施与验证

日期：2026-11-10。执行者：Codex。

## 基线和边界

- HEAD：`51bff936ee2f2a0e4c3712441f8e622e33312c3c`。验证对象是包含前两轮 ROI 布局/复用改动与本轮实现的未提交工作区，不是该 SHA 的干净版本。
- 保留原有集合筛选、合并轮廓、其他文档及 `test/` 等不相关改动。没有提交、推送、安装发布或重启用户进程，没有访问现场相机/PLC、修改现场工程或凭据。
- 修改前保存了 ROI 目录、预览基类、公共 ImageView 与原 ROI 测试的 ZIP 基线副本，位于本会话的 `roi-implementation-baseline.zip`；已核对目录及 ZIP CRC。
- 延续 [布局验证](2026-11-10-roi-editor-layout.md) 和 [公共控件复用验证](2026-11-10-roi-editor-reuse.md)，不撤销原有预览身份、失效处理与资源退休逻辑。

## 已实现

1. ROI 自有 `.ui` 与控制器负责图源、双图、工具栏、四种结果切换、区域列表、参数、折叠区及导出。平台继续提供窗口操作、参数来源管理、调试、应用与关闭；没有新增 ROI ID 布局特判。
2. 原图支持选择、平移、绘制矩形/多边形、拖动区域和顶点、撤销、删除、启用/禁用、保留/排除用途。已有旋转矩形与角度参数保留。顶点参数使用可编辑 X/Y 数值表，不要求填写 JSON。
3. 有效多边形三点起自动显示闭合边和半透明填充，并进入纯预览。点首点、Enter 或完成按钮提交；不重复添加首点。Backspace 退回末点，Esc/取消退出草稿，双击不提交也不增加第二个点。零面积、自相交和重复顶点沿用算子校验。
4. 绘制草稿不进入 `collectParams()`；`collectPreviewParams()` 在有效时将草稿附加到请求。应用入口要求先完成或取消草稿，包括已有变量绑定的路径。预览与导出记录通过 `editorState.draftIncluded` 标记临时结果。
5. `vision.preprocess.roi` 升为 1.2.0，新增 `roiType="regions"` 和区域数组。保留区并集减排除区，再与 frame 有效内容相交；无启用保留区从整图开始；完全扣除时报错。旧单区域格式、七个输出端口、旋正和回源映射保持兼容。
6. `roi` 几何端口不编码孔洞和离散区域，仅表示裁剪外包几何；精确区域使用两个 Mask 端口。完整数据契约见 [ROI 输出契约](../operator-io-contracts.md#101-roi-输出契约)。组合区域需要绑定 `regions`；旧顶层几何绑定不会被静默解释成列表内绑定。
7. 平台 `ImageView` 仅增加可选 `preserveView=True`。连续结果刷新保留手动缩放和平移，原有默认适应行为不变。两幅图查看、结果切换和透明度调整均不编辑参数、不请求新预览。新增离线 Lucide 图标沿用仓库许可和版本。

## 验证环境

Windows，`C:\Users\dd109\.conda\envs\emo_master\python.exe`，Python 3.10.21 / PySide2 5.15.2.1。仅使用生成图片、临时工程和本地测试 Runtime。

### 定向源码与 Runtime 回归

定稿回归命令：

```text
python -m pytest tests/designer/test_image_view.py tests/designer/test_roi_editor_layout.py tests/designer/test_roi_region_editing.py tests/designer/test_roi_preview_export.py tests/designer/test_roi_preview_retirement.py tests/designer/test_operator_editor_bindings_host.py tests/designer/test_operator_editor_manager.py tests/designer/test_operator_modes.py tests/designer/test_builtin_operator_editor_ui.py tests/designer/test_node_image_viewer.py tests/core/plugin/test_registry.py tests/plugins/test_roi_operator.py tests/plugins/test_roi_regions.py tests/runtime/test_draft_pure_preview.py tests/designer/test_plc_editor.py tests/sqlite_writer/test_editor_layout.py -q --disable-warnings --maxfail=1
```

定稿结果：**408 passed in 167.44s (0:02:47)**，无跳过。

此前扩展回归 407 passed in 179.23s；随后相关 148 项原生 Qt/算法回归通过。追加了区域类型转换隔离回归后重新执行上述定稿命令。

新增验证覆盖：组合并集/扣除、顺序无关、禁用和空区域、单保留区与旧结果逐像素等价、完整遮蔽失败、配置校验、三种完成方式无重复点、Backspace/双击/Esc、缩放平移后的顶点拖动、撤销和参数回读、坐标表、跨区域类型转换隔离、绑定冲突、纯预览与草稿导出身份。

### 原生 Qt 布局

- 通过编辑器管理器和实际 `.ui` 加载入口，不只直接构造单个控件。
- `QT_QPA_PLATFORM=windows`，定稿 100% 缩放：38 passed in 17.58s；此前相关 148 项运行也使用原生 Windows Qt。
- `QT_SCALE_FACTOR=1.5`，定稿 150% 缩放：38 passed in 26.86s。
- 输出并查看 980/1280 宽布局，以及 1100 宽完整绘制窗口截图。图源为生成的测试图片，布局截图的结果使用 ROI 算子生成并注入；独立 Runtime 集成测试另外验证实际请求、结果资产和导出。

### 静态检查

定向 Ruff 与 `git diff --check` 通过。后者仅提示 `.ui` 按仓库配置在下次 Git 处理时转为 CRLF，没有 whitespace 错误。

## 加载及未验证项

本轮没有更改现场安装包，也没有强制关闭运行中的 Designer/Runtime。正常关闭当前会话并重新启动使用本仓库源码的 Designer 和 Runtime 后，才会加载新控制器、UI 和算子声明。

没有执行全仓库测试、打包/冻结产物自测或现场人工 UI/真实设备验收；原生 Qt 自动化和本地 Runtime 集成不等于现场验收。
