# 双工位工程当前状态汇总与隔离加载检查

## 范围、基线和本次确认

- 用户对一工位0.75匹第二拍F2-121的新圆心回复“可以保留 然后下一步”。已在该图复核目录新增用户确认JSON，补充上一份报告，不改测量/坐标/格式化结果。
- 本轮汇总实际工程配置及既有离线结论，重新检查格式、依赖、编译、临时保存重读和Designer实际加载入口，不运行业务父流程或现场设备。
- 平台源码0.6.6，Python3.10.21/PySide2 5.15.2.1/Qt5.15.2，HEAD `556acb10760a9463fe832aa4d8e7fc5c0a95a67e`；开始有8份未跟踪测试报告，本轮新增本报告、补充原报告。没有平台源码改动，不把结果标为无未提交文件的干净版或冻结安装包验收。
- 主工程：`C:/Users/dd109/my_work/emo-master-project/two_station_screw_vision/two_station_screw_vision.emoproj`，SHA256 `fd34f7f0bca88fef969cc347a58b06fb59df6ba1c59932ee67c17dc41fbb4f46`，全程未变。
- 工程2.9/页面1.2/设备1.0，46流程、371节点、2页面、5设备、34项算子依赖。旧工程文档及外部交接存在过时的45流程、缺模型、占位地址等记录；以本轮实际清点为准，不回写工程去迎合历史说明。
- FastCtx不在可调用工具清单中，使用有界Python/JSON本地读取作为替代。没有运行旧生成/迁移/一次性应用脚本。

## 原始工程检查：不能写成全通过

`test_current_project.py`首次运行：**1 failed、2 passed，27.55秒**。原始脚本、日志、JUnit、baseline和verification均保存在`first-run`目录，未弱化或移除失败断言。

通过部分：

1. ProjectDocument原始模型/标准载入一致，格式、46流程及371节点核对；46流程编译和页面绑定校验在失败点前均通过。
2. 标准saveProject写临时副本，loadProjectDocument重读全文档相等，并重新编译46流程。
3. 原生Qt隔离Designer实际`MainWindow.loadProjectDirectory`：46工作流逐一切换并比对节点ID；相机/PLC/TCP三类设备绑定表单可打开且绑定ID一致；临时保存并重开后工作流、页面、设备、全局变量、运行设置、资源、生产与依赖等业务字段保留，编译46流程，无Job。

真实失败：精确依赖对比发现`vision.preprocess.roi`工程锁为1.1.0，本机插件为1.2.0。不能因为编译或Designer打开成功就声称Runtime可加载。

## Runtime依赖问题的源码与直接反例

- `C:/Users/dd109/my_work/emo_master/src/emo_master/core/project/dependencies.py:5-25`要求声明的精确版本与安装版本相等。
- `C:/Users/dd109/my_work/emo_master/src/emo_master/core/project/runtime_directory.py:28-34`在纯准备入口先调用该检查；不需要创建Runtime服务或访问设备即可复现。
- 对主工程实际调用纯`prepareRuntimeProject`，返回唯一`E_OPERATOR_DEPENDENCY`：`dependencies.operators.vision.preprocess.roi`，要求1.1.0、当前1.2.0。
- 全34项依赖清点只有这一处版本不一致；7个唯一ONNX（11处引用）及9份坐标TXT全部存在；5设备定义校验通过，页面绑定无错误。

最小改法是同步**工程依赖元数据**，不是改平台或算法。新增`test_dependency_preview.py`的首项测试：**1 passed，3.37秒**，范围为原工程错误确证和单字段临时候选验证：

1. 保留原始文档，深拷贝后只改`dependencies.operators[28].version`，1.1.0→1.2.0；递归差异断言严格要求仅此一项。
2. 候选精确依赖校验、以实际工程目录为资源根的纯Runtime准备、46流程编译、标准保存重读一致均通过。
3. 候选未安装到主工程，不能把这1项通过加到首次2通过后称“当前工程全部通过”。候选SHA256 `0d76952c11ae128dccfceb020e2cd5b0754e9c45b617746b6bbab3672d143df0`。
4. 候选仅为build内证据，引用的模型/坐标仍属实际工程资源目录，不把它当自包含交付工程。

主工程尚待落实这一条依赖修正。无需修改平台代码；本轮没有自动写主工程。

## 页面检查的补充与证据分层

最初页面截图中两次抓图仍呈现工位1文字，因此没有只凭“调用过navigate”就宣称页面切换验收；补充检查workspace.pageId、renderer.currentPageId在导航前后及适应画布后均等于请求页。独立测试**1 passed，5.44秒**。

另一次使用与原Designer检查相同的微软雅黑字体，显示后再统一1600×1000大小，并显式处理重绘的截图补充测试：**1 passed，5.86秒**。已查看最终工位2截图：导航、当前页名称、页面提示和属性均为工位2，未证实平台页面切换缺陷。初始截图和无中文字体的补充截图不删除；最终清晰截图在`native-page-navigation-font`。

这几轮检查不相加为不同功能的通过数；均为源码原生Qt离屏自动化，**不是人工桌面、真图运行页面、冻结安装包或现场设备验收**。页面显示的是明确标识的设计示例数据，不是实际检测图/产品合格结论。

## 既有测量决定的汇总，不新增图像测试

- 一工位0.75匹首拍：阈值120已落地，A/B各133单点；F1-027已获目标位置认可。首拍旧日志无A/B点，不伪造旧点，不用补偿差异擅改标定。
- 一工位0.75匹第二拍：133单圆心，133组独立坐标复算/纯格式化检查已有完成；本次认可F2-121，保留当前方案。最新确认只涉及代表取点/方法，不扩大为133张逐张人工验收或物理精度。
- 二工位0.75匹首拍：152组；B1-137当前圆心保留。B1-104自动选择长槽仍未修复，用户已接受后续补训练、暂不加选孔规则，不重复升级为本轮新整改门槛。
- 二工位0.75匹第二拍：147组已有认可结果，无前置掩码；同孔多轮廓合并后一个最小外接圆/圆心。
- 共565组精确图片/日志的既有覆盖不等于全部目标或物理精度合格；另有1张未精确配对F图未纳入。当前所有模型/参考TXT齐备，不再报“缺ONNX”或“缺0.75第二拍样本”。
- 二工位1匹首拍A当前面积2500～6000；3张复测中220恢复，197/282仍零点，原因是局部小轮廓，不归为已经证实的模型漏检；没有以新范围重跑其余256张。历史1匹模型漏检/换孔维持用户已接受的后续训练安排，不虚报修复，不重新强加验收前置。

## 保护、文件和未执行项

- 两个commissioned=false，8个pending-output调用保留。主工程、7模型、9TXT及原`.bak`哈希全程保持；最新确认JSON已重读核对。
- 无真实Runtime客户端。Designer使用独立QSettings及禁止loadProject/startJob/submitDeviceCommand的客户端替身；测试禁止Python socket connect/connect_ex/bind，未触发这些禁止入口。
- 纯`prepareRuntimeProject`只在内存准备/文件检查，不是启动Runtime，也未启动TCP。实际TCP配置on_project_open，绑定192.168.101.14:10000，两个机器人白名单为192.168.101.99/.100，不擅改或关闭它以绕过检查。
- 未重跑图像/YOLO，没有相机/PLC/机器人SDK操作、现场参数读回、PLC读写、监听/发送、网络改址、完整业务周期、性能专项、打包发布、提交或推送。
- 在工程根新增`CURRENT_STATUS_2026-11-16.md`，旧README/CONFIGURATION/VALIDATION只加当前状态提示；修改前文档已备份至本轮证据`before-docs`，历史正文保留。

## 文件与下一步

- 证据根：`C:/Users/dd109/my_work/emo_master/build/two_station_current_acceptance_20261116/`。
- `first-run/`及原`pytest.log/xml`：保留实际1失败2通过。
- `dependency-preview.json/log/xml`：原工程真实依赖错误和一字段候选通过。
- `native/`：流程及3类设备表单截图；`native-page-navigation*.json/log/xml`和相应目录：补充页面活动ID与最终字体/重绘截图。
- 工程当前总表：`C:/Users/dd109/my_work/emo-master-project/two_station_screw_vision/CURRENT_STATUS_2026-11-16.md`。
- 最新确认：`D:/2026_8_5_0.7匹/一工位0.75匹第二拍_孔心坐标报文复核_2026-11-16/用户确认_2026-11-16.json`。

下一步先落实已预览的一条ROI依赖版本修正并读回验证，再进入适用的Designer人工/离线走查。真实设备及Runtime加载另按明确授权推进，不能夹带启动自动监听或解除输出保护；不再要求逐张重复确认已收口的代表图。
