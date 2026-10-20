# emo_master 设备管理与在线配置｜R2.1 含图交接包

2026-10-16 · 完整 R2 方案＋用户设备界面参考图 · 文档交付，未修改源码或执行功能验收

## 阅读和实施

直接打开 [device-management-plan-r2_1.html](device-management-plan-r2_1.html) 可在浏览器阅读完整含图方案。页面和图片已内嵌，不依赖外网；文末的源码参考链接仅在点击时打开外部网站。

交给 Codex 时，使用 [2026-10-16-device-management-online-config-plan-r2_1.md](2026-10-16-device-management-online-config-plan-r2_1.md) 作为唯一实施正文，同时读取 [CODEX_HANDOFF.md](CODEX_HANDOFF.md)。图片已在主计划 §3.1.1 引用，必须连同 `assets/` 文件夹一起保留。

| 文件 | 用途 |
|---|---|
| [2026-10-16-device-management-online-config-plan-r2_1.md](2026-10-16-device-management-online-config-plan-r2_1.md) | 含参考图和布局映射的完整实施计划 |
| [device-management-plan-r2_1.html](device-management-plan-r2_1.html) | 内嵌原图的独立 HTML 阅读版；不是待实施应用的 Web UI |
| [assets/device-management-ui-reference.png](assets/device-management-ui-reference.png) | 用户提供的原始设备界面参考图 |
| [CODEX_HANDOFF.md](CODEX_HANDOFF.md) | 执行契约、顺序和图示落地指引 |
| [REVISION_NOTES.md](REVISION_NOTES.md) | 本次补图记录＋R2 审查项映射 |

本次只增加界面参考，不改变 R2 核心架构、完整功能或 88 项待执行验收。图中弹窗的内部布局用于正式设备管理页；品牌、设备状态和地址属于示例，按钮和真实能力按正文校正。没有新增真实设备驱动或性能专项。

入库时可继续采用正文建议的 canonical 路径 `docs/plans/2026-10-16-device-management-online-config-plan.md`，图片对应存入 `docs/plans/assets/device-management-ui-reference.png`。HTML 仅供离线阅读，不是新的技术栈要求。
