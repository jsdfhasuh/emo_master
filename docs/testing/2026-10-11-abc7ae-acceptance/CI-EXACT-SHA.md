# 精确 SHA 的 CI 最终核验
核验时间：2026-10-11 09:00 UTC
仓库：jsdfhasuh/emo_master
目标 SHA：abc7ae0541e997be0bbe3a4794ad604c239de79a
结论：该 SHA 关联的 3 个 workflow run 均 completed / success。7 个 job success，1 个有条件 job skipped；没有失败 job。所有 run attempt=1，未进行任何 rerun、workflow 修改、push 或其他仓库写操作。

## 直接目标 SHA 的 Push CI
Run 38124940288，push，08:57:38 最终 success。
https://github.com/jsdfhasuh/emo_master/actions/runs/38124940288
- Ubuntu job 114427252126：实际 checkout abc7ae0541e997be0bbe3a4794ad604c239de79a；protobuf、Ruff、mypy（400 files）均通过；4829 passed, 19 skipped, 29 subtests passed in 935.67s。
- Windows job 114427251976：实际 checkout abc7ae0541e997be0bbe3a4794ad604c239de79a；protobuf、Ruff、mypy（400 files）均通过；4843 passed, 5 skipped, 29 subtests passed in 1630.22s。
- 两 job 的 checkout identity 均由各自日志确认，未把 API head_sha 当作实际 checkout 证明。

## 合并测试 SHA 的 Pull-request CI
Run 38124943996，pull_request，08:59:05 最终 success。
https://github.com/jsdfhasuh/emo_master/actions/runs/38124943996
- 实际 checkout 均为 29154d04f29b1d4ae50c659fcc30531c8f9000fb（merge abc7ae0 into main 7f141ce627da39d9e174e4a79344fc468607ebef）。
- Ubuntu job 114427263773：4829 passed, 19 skipped, 29 subtests passed in 793.90s。
- Windows job 114427263526：4843 passed, 5 skipped, 29 subtests passed in 1717.06s。
- 两 job protobuf、Ruff、mypy（400 files）均通过。
- API head_sha 虽标 abc7ae0，但此 run 是合并 SHA 测试，和上方直接目标 SHA 证据分开记录。

## Frozen Windows package
Run 38124944740，pull_request，08:37:31 最终 success。
https://github.com/jsdfhasuh/emo_master/actions/runs/38124944740
- prepare 114427265628、package/build 114427350274、verify 114428248613 均 success；vision_train 114427351230 skipped。
- prepare/build/verify 日志均确认源码 checkout 为目标 abc7ae0。
- packager checkout 为 d5f3617a7265c545c793366f93e3a114212d8c59；CALLER_SHA 为 PR merge SHA，不是源码版本。
- 合同测试 11 passed, 2 subtests passed；冻结 GUI / Runtime / provenance 验证成功。
- provenance source_commit=abc7ae0541e997be0bbe3a4794ad604c239de79a。
- portable SHA256 afc9d8f2a535977937a21dbea3b558892977aaa4499315974082b649a5d41dc2。
- setup SHA256 937de5e97243e3f64a86dd3cc09b26fb45ffb0e236e11cbbcce08204519baec2。
- 限制：安装器仅核验 hash，未执行安装；未测实体相机、GPU、完整现场验收、Designer 主编辑、OperatorView 图像像素保真。
- 已有两个未过期 artifact：11697460413（frozen-validation，43371 bytes），11696324969（windows-release，164068597 bytes）。这里只读取元数据和日志，未重新运行或发布。

## 状态解释与证据
- PR #1 初始查询（08:54 UTC）head 是目标 SHA，mergeable=true，merge commit 为上述 29154d04；CI 终态查询（09:00 UTC）按目标 head_sha 限定，返回且仅返回以上 3 runs。
- legacy commit status API 的 pending / statuses=[] / total_count=0 表示没有旧式 status；不可据此否认 Actions success。
- 08:55:39 在 Windows job 运行中下载日志返回 BlobNotFound；终态后已成功取得两份完整日志，这不是测试失败。
- 原始 API：runs-initial.json、runs-final.json、pr-initial.json、checks-initial.json、status-initial.json、jobs-*-initial.json、jobs-*-085951.json、artifacts-38124944740.json。
- 完整日志：job-114427252126.log（push Linux）、job-114427251976.log（push Windows）、job-114427263773.log（PR Linux）、job-114427263526.log（PR Windows）、其余 3 份 frozen-package 日志。
- CI 工作流原文 ci.yml 已按目标 SHA 保存。脚本/测试源码本次未改动。
