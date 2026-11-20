# 双工位工程页面 V2：第一版设计与验收

## 对象、版本与边界

- 用户要求按当前页面设计能力重做既有双工位工程页面；不是重建工程或运行旧迁移脚本。
- 工程：`C:\Users\dd109\my_work\emo-master-project\two_station_screw_vision\two_station_screw_vision.emoproj`。
- 源码0.6.6，固定HEAD `31afa5ecab8b7ddb53f0d6bdedc620f0a243e340`；开始时工作区有两份未跟踪的11月17日依赖/压缩报告。本轮增加此报告和外部工程/说明/截图；平台源码未改。这不是该SHA干净版、冻结安装包或生产验收。
- 工程2.9 / 页面1.2 / 设备1.0 / revision13，30流程321→333节点、2旧页→4新版页。没有增加子流程。
- 改版前SHA256 `07d2d959e1ea3fa6acade14e0226702b0453c416be7de0003e9265413ad6279e`；安装后 `a22bf2036b5af5c2a946e6443cc0bb88c2d899c63d8a29c894855ece23a1fead`。
- 改前主工程/当前状态/README在 `build/two_station_pages_v2_20261117/before`。原工程旁`.bak`未覆盖。

## 设计

每工位独立监看、独立本拍详情，保持原工位页面ID和结果作用域。响应式横向双栏/窄屏上下排列，状态映射显示目标检出、首/次拍、机型和本拍启用标记。详情给出已计算的原图测点X/Y，未把检测框中心当成孔心，未给传统轮廓点虚构置信度。

使用已有`navigate(displayed_result)`锁定GUI已提交的结果，恢复实时及返回监看正常释放；不改变检测/设备运行。结果身份、任务状态和连接使用原生外壳。没有历史库就不虚构历史；不把客户端结果连接冒充相机/PLC/机器人在线。保持desktop目标，未发布Web。

14个捕获源、2作用域、2个图像源。每工位监看/详情复用同一显示图片，每详情2表，原生懒加载2页面不越过4已创建表限额。

## 显示链的最小变更

1. 既有业务测点从`a__reframe`、`b__reframe`/`b__offset`只读转发到详情，原始节点参数和测量输入连线保持。新增6个机型分支选择、1个主点选择、1个已校验机型标签比较。
2. 二工位0.75匹首拍原裁剪测量集合不再直接接原图`firstB/measurements`。以同一个`bboxTopLeft`取点，`reframe_points`显式回原图，单点通过`coordinate_calculator`的`centroid`作为Point2D标记输入原有annotate的ROI。新增3个节点；不是补造一个框，不改变业务B点。
3. 原图完成叠加后增加1个`vision.preprocess.resize@1.0.0`，fit1000×1000、area。2448×2048样本得到1000×837 BGR，2,511,000原始字节，真实样本PNG也小于4 MiB。测量原图不缩小。显示线宽6px，B标记橙色。
4. 共12个纯显示节点；只新增resize精确依赖，其余已有依赖与ROI1.2.0保留。

7个ONNX、11处模型引用、9份坐标TXT、相机/PLC/TCP设备定义和原有业务参数未改。两个`commissioned=false`、8个`pending-output`保持。未构造真实Runtime服务、未监听、未连接/改址、未发PLC或机器人消息。

## 执行与结果

环境：`C:\Users\dd109\.conda\envs\emo_master\python.exe`、Python3.10/PySide2；`PYTHONPATH=src;.`，`QT_QPA_PLATFORM=offscreen`。所有专项用例禁止socket connect/connect_ex/bind；Designer客户端只返回静态插件/设备类型元数据，禁止Runtime load/start/device命令。

- `pytest .../test_pages.py -q`：**31 passed，63.99秒**。证据 `pytest-final.log/xml`（同`pytest-ui-final.log/xml`）、`verification-details.json`。
  - 模型/依赖、30流程编译、发布级绑定、捕获预算、纯Runtime准备、标准保存重读。
  - 12条真实图片测量链改前改后精确一致，不仅比较点数量。
  - 工位1/2 × D800=2/3/6，各4帧、两轮首/次拍：新链**不替换display**。业务设备顺序、计数和变量写入对照一致。旧二工位0.75基线有已知显示异常，仅在旧基线对照业务轨迹时显式替换旧display，不将其记作旧完整显示通过。
  - 传统B标记与业务B全图坐标/坐标系精确一致，另检查缩图实际橙色像素，避免只验证drawnCount。
  - 两启用保护、八业务输出门禁仍阻断；仅周期测试内存副本临时放行。未写回放行。
  - 原生Qt真实图片回放：1366×768、1920×1080、768×1024、460×820，不重叠、不横向溢出；16张分辨率/拍次截图。
  - 点击已显示拍101时底层已有拍102，详情仍为拍101；恢复后显示102。返回监看、可选B缺省、目标未检全红色和断连不显示旧成功均验证。
  - Designer实际`MainWindow.loadProjectDirectory -> PageCoordinator.showPages`，4页切换、临时保存重开；不是仅运行WorkflowRunner代替打开验收。
- 安装脚本在核对31项无失败/跳过、主文件和资源SHA后，仅将已通过标准保存的候选精确替换主文件，保留原`.bak`。
- `pytest .../test_installed.py -q`：**2 passed，16.82秒**，见`applied-final.log/xml`（同`applied-ui-review.log/xml`）和`applied-verification.json`。实际路径文件与候选一致，编译/绑定/预算/保存重读、已安装纯图片测量入口、Designer实际路径打开4页并临时另存重开通过。
- 第一轮31项通过记录保留在`pytest-first.log/xml`；视觉走查后去掉冗余导航、把机型标签放入已校验的显示调用范围，并将传统B零尺寸框改为真正点标记后重跑，以上最终结果对应最终SHA。
- 实际Designer截图复核发现测试目录桩返回dict，而正式目录接口返回有属性的对象，后台刷新后目录为空，初版UI断言未捕获该提示。已修正为真实manifest对象、等待原生目录就绪、取消绕过目录检查，并增加编辑器清单/发布级绑定断言，再重跑全部31项和实际路径2项。没有因此改平台或主工程。前一轮日志保留为`pytest-before-ui-review.*`、`applied-initial.*`，不将旧截图或旧弱断言当作最终验收。

## 已知边界，不扩大本轮

- Qt使用隔离设置、原生当前源码及内存显示会话替身。不是人工桌面、冻结安装包、实际双屏或设备联调；未执行全仓库测试。
- 第二拍无B可选源，当前结果协议会显示`INCOMPLETE`，B表正常提示缺省，不沿用上拍；目标检出和任务执行状态独立。没有为消除这个正常缺省改平台协议。
- 1366×768下图像及主要状态可见，底部说明可滚动；窄屏纵向滚动。最终现场屏幕/缩放比例尚需人工查看，不额外引入性能验收门槛。
- 检测框为模型候选区域，实际过滤/测量点在详情表；页面未加入质量合格率、机器人ACK或无数据源的设备状态。
- 用户已接受的模型训练/精度待办不变，未重新裁决历史样本。

外部工程说明及6份精选离线预览：`PAGE_DESIGN_V2_2026-11-17.md`、`validation/page_previews_v2/`。所有其余证据与脚本在`build/two_station_pages_v2_20261117/`。未提交、推送、打包或发布。
