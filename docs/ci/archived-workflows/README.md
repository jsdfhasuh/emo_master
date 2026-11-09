# 已归档的一次性 CI 工作流

这四个工作流用于历史 Windows 内核追踪或预检，归档后不再由 GitHub Actions 自动执行。YAML 内容按原样保留，包含原路径、历史触发条件和固定提交，供追溯诊断过程。采集与诊断逻辑保持不变；离线夹具同步改从归档位置读取这四份 YAML。

四者原先都只响应 `agent/runtime-workflow-architecture-v1` 分支上自身原路径的 `push`，且要求 `github.run_attempt == 1`、精确的 `github.event.before` 和提交消息同时匹配。它们不是日常测试或验收入口。

## 保留的历史条件

### 固定基线内核追踪

- 文件：[runtime-kernel-pinned-baseline.yml](runtime-kernel-pinned-baseline.yml)；原路径：`.github/workflows/runtime-kernel-pinned-baseline.yml`。
- 用途：对原始执行基线作一次 Windows 内核追踪；执行源固定为 `ecf8bcfcf193e05807556340d670f05c0f3d9e15`，离线夹具源固定为 `0c1ac2fddf453913a29e03b2cb84043ca7d92afe`。
- `before`：`0c1ac2fddf453913a29e03b2cb84043ca7d92afe`；提交消息：`diagnostic: one pinned baseline Windows trace 655eeac 19d4e683`。

### 不采集的内核预检

- 文件：[runtime-kernel-preflight-only.yml](runtime-kernel-preflight-only.yml)；原路径：`.github/workflows/runtime-kernel-preflight-only.yml`。
- 用途：验证离线夹具和现有原生编译器，不执行追踪采集。
- `before`：`ecf8bcfcf193e05807556340d670f05c0f3d9e15`；提交消息：`fix: notify idle display streams; preflight-only 20261002`。

### 单次内核诊断

- 文件：[runtime-kernel-trace-diagnostics.yml](runtime-kernel-trace-diagnostics.yml)；原路径：`.github/workflows/runtime-kernel-trace-diagnostics.yml`。
- 用途：预检通过后，围绕原始完整 pytest 顺序执行一次 Windows 内核追踪；不作为验收。
- `before`：`655eeac84d0602aa670c76af67679509782bbd7f`；提交消息：`diagnostic: one Windows kernel trace 655eeac f2b07937`。

### 只读 WPR 元数据预检

- 文件：[runtime-wpr-metadata-preflight.yml](runtime-wpr-metadata-preflight.yml)；原路径：`.github/workflows/runtime-wpr-metadata-preflight.yml`。
- 用途：检查现有 WPR 元数据和会话状态，不创建采集会话。
- `before`：`425f04d63c453d96f217b43308d7903569761515`；提交消息：`diagnostic: classify scoped WPR status; read-only metadata 9eb237ac`。

## 后续使用

不要直接将这些文件复制回 `.github/workflows/` 来触发运行。需要再次诊断时，应根据当次目标重新确认执行范围、来源提交和运行条件，建立明确的新入口；保留这里的历史文件原文。
