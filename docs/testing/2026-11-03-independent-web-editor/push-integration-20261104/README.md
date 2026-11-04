# 最新日志基线上的独立 Web 编辑器推送前验证

2026-11-04；实现提交 `f4cf67cecce125873692173175064a7a037116a8`，基线 `8b987ce393f83141738091ed35985695beba6f7f`。在隔离工作树执行，保留原未提交工作区。

| 结果 | 证据 |
| --- | --- |
| 前端 105 passed / 0 skipped | frontend-tests.log |
| TypeScript / Vite 通过 | frontend-build.log |
| Runtime/SDK/Qt 190 passed，Qt 启动/预览/日志 59 passed | runtime-checks/summary.json 及对应日志/JUnit |
| Ruff、mypy 614 文件、proto drift 通过 | runtime-checks/ 中对应日志 |
| Web 全套 183 tests OK、无 skip，包含源码软件 Runtime | web-checks/backend-all-tests-final.log |
| Web wheel 构建、独立安装、无 Node CLI 和轻量导入通过 | web-checks/verification-provenance.json、verify-wheel.log、installed-editor.log |
| 暂存内容重新检出两种换行设置各 3 项合同通过 | sdk-index-check.json |

Web 首轮 183 项中出现 1 error、1 failure，保留在 web-checks/backend-all-tests.log：SDK handoff 尚未复制，补齐后合同通过；原超长路径夹具在较短 checkout 只有 249 字符，未达到 >260 的正确前提。仅按实际根路径补齐目录长度，保留全部断言，最终完整后端通过。

提交前另外同步 Web CI 的 SDK 1.5.0 精确版本断言，并修正一处测试文件的末尾空行。未新增产品限制，未执行整仓约 9000 项测试或人工桌面验收。Qt 使用 offscreen；Web 安装检查使用新的环境和 python -I，实际加载 site-packages；实际检测仍由源码 Runtime 完成。

本轮未重跑之前 13/13 编辑器浏览器复验；其原证据及前三轮完整闭环记录继续保留，正式整体操作待 Dot 按[交接说明](../../../handoffs/2026-11-04-independent-web-editor-review.md)复查。不要将本机针对性通过替代远端完整 CI、安装 Runtime 完整运行、异机或设备现场验收。
