# emo_master

emo_master 是一个参考 VisionMaster 思路实现的机器视觉平台骨架项目，当前包含：

> 当前项目处于实验性 MVP 阶段，适合学习、验证和二次开发，尚未面向生产环境。它是独立实现，与 VisionMaster 及其厂商不存在隶属或官方关联。

- Designer（PySide2 / Qt5）
- Runtime（gRPC 服务）
- 插件算子框架（含 Empty 与 Canny 示例）
- 项目目录格式、打包与回滚基础能力

## Conda 环境准备

项目建议使用 Python 3.10。

### 1) 创建并激活环境

```bash
conda create -n emo_master python=3.10 -y
conda activate emo_master
python -m pip install --upgrade pip
```

### 2) 安装依赖

如果你使用单文件依赖：

```bash
pip install -r requirements.txt
```

如果你拆分了开发依赖（可选）：

```bash
pip install -r requirements-dev.txt
```

## 初始化与校验

```bash
python scripts/gen_proto.py
python scripts/ci_check.py
```

## 常用开发命令

```bash
python scripts/dev.py proto
python scripts/dev.py test
python scripts/dev.py run-runtime
python scripts/dev.py run-designer
```

## 运行与图片测试

1) 启动 Designer（当前内嵌 Runtime）

```bash
python scripts/dev.py run-designer
```

如果要连接外部 Runtime（默认地址 127.0.0.1:50051）：

```bash
set EMO_RUNTIME_TARGET=127.0.0.1:50051
python scripts/dev.py run-designer
```

2) 在 Designer 中点击 `Import Image` 选择本地图片（png/jpg/jpeg/bmp/tif）

3) 点击 `Start` 触发最小运行流程（内置 Canny）

4) 在原图同目录查看输出：`<原文件名>.edges.png`

5) 运行日志通过 Designer 中的 `Open Logs` 按钮打开日志窗口查看。

## 测试

```bash
pytest -q
```
