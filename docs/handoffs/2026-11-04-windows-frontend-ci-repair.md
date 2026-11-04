# Windows 前端测试装载修复记录

- 执行与备注：**dot**，2026-11-04。此修改由 dot 完成，不归于其他贡献者。
- 对应 PR：[#11](https://github.com/jsdfhasuh/emo_master/pull/11)。
- 原始失败：[Web panel Windows job 126571390049](https://github.com/jsdfhasuh/emo_master/actions/runs/41829116842/job/126571390049)。
- 远端源码 head：`6a5e2dae7393a7cbc87b89ebefd0cf0d28755426`；本地修复基于同内容树 `0d0c386dd0724e036606cb39ab1e9aeb8c0eb434` 的 `9ce29742611a3ba7151a33406851f5b107bedc7c`。

## 原因与修改逻辑

CI 的 `Build frontend` 组合步骤先运行 `npm test`。原日志显示 123 项中
114 项通过、9 项失败；失败全部来自 `editor_draft_recovery.test.mjs`，
主要错误是 React 的组件类型为 `undefined`，并非 TypeScript 编译错误。

测试装载器用 `path.endsWith('/main.tsx')` 识别入口，在 Windows 的
`D:\\...\\main.tsx` 原生路径上不匹配，因而没有移除入口自动挂载代码，
也没有为测试导出 `App`。另一个相关问题是缓存清理使用 URL 的
`pathname`，与 Windows 原生路径及含空格路径均不相同。

局部修正仅位于测试装载器：

1. 用 `require.resolve('../src/main.tsx')` 精确识别入口原生路径。
2. 用 `fileURLToPath` 获取源目录，供夹具初始化和完整重载清理模块缓存。
3. 新增回归，检查 `App` 的导出、导入时未挂载、源模块缓存实际被清除，
   以及重新装载产生新的组件函数。原有 9 项草稿业务测试及断言不变。

没有改动生产代码、CI 检查范围、失败阈值或跳过条件。

## 本地验证

环境：Linux，Node `v24.22.1`，沿用仓库锁定的前端依赖。

- 在含空格目录运行新增回归，修复前可复现
  `source directory matches the native require cache path` 断言失败。
- 同目录修复后：`node --test tests/editor_draft_recovery.test.mjs`，
  **10/10 通过，0 跳过**。
- 仓库内 `npm --prefix web_panel/frontend test`：**124/124 通过，0 跳过**。
- `npm --prefix web_panel/frontend run build`：**TypeScript 和 Vite 构建通过**。
- `git diff --check`：通过。

本地没有 Windows / Node 22 执行环境。上述结果不是 Windows CI 已通过的声明；
远端 Windows 原生复核及后续浏览器步骤仍须在包含此修复的准确提交上执行。
