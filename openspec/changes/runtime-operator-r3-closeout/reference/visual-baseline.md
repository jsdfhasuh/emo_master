# R3 原生视觉基准

本补充以 R3 提交 `1f35861a22f4e5bd553fe210db989e28c3c4a0d1` 的原生 Qt
截图为基准：

-   [起始/设置页
    1060×800](https://github.com/jsdfhasuh/emo_master/blob/1f35861a22f4e5bd553fe210db989e28c3c4a0d1/openspec/changes/archive/2026-11-27-runtime-platform-overall-v1/verification/native-qt/01-startup-settings.png)
-   [Job 工作台
    1060×800](https://github.com/jsdfhasuh/emo_master/blob/1f35861a22f4e5bd553fe210db989e28c3c4a0d1/openspec/changes/archive/2026-11-27-runtime-platform-overall-v1/verification/native-qt/04-job-log-workbench.png)
-   [设备管理
    920×620](https://github.com/jsdfhasuh/emo_master/blob/1f35861a22f4e5bd553fe210db989e28c3c4a0d1/openspec/changes/archive/2026-11-27-runtime-platform-overall-v1/verification/native-qt/05-device-controls.png)

## 本次只收尾

1.  统一可用字体回退，避免目标机器缺少首选字体时出现不一致字形。
2.  统一复选框、滚动条、来源 tab、焦点和禁用状态。
3.  来源 tab 紧凑左对齐，不均分整行。
4.  保持当前卡片布局、信息内容、生产页和 Designer 样式不变。

本文件不是要求重画整个 Operator，也不要求固定的多尺寸截图矩阵。原生 Qt
截图用于确认实际字体、QSS 和控件状态；随包 HTML
只用于交互讨论，模拟数据不构成产品验收。
