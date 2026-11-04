"""Bundle already-built Runtime/Web/SDK wheels and the recorded acceptance evidence."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import zipfile


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "build/independent-web-editor"
EVIDENCE = ROOT / "docs/testing/2026-11-03-independent-web-editor"


def digest(path):
    data = path.read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "byteSize": len(data)}


def one_wheel(directory, pattern):
    files = list(directory.glob(pattern))
    if len(files) != 1:
        raise ValueError(f"Expected exactly one current wheel: {directory / pattern}")
    return files[0]


def io_path(path):
    """Synthetic evidence contains nested result hashes beyond MAX_PATH."""
    path = path.resolve()
    if os.name == "nt" and not str(path).startswith("\\\\?\\"):
        value = str(path)
        return Path("\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value)
    return path


def file_tree(directory):
    return {path.relative_to(directory).as_posix(): path
            for path in directory.rglob("*") if path.is_file() and "__pycache__" not in path.parts}


def check_python_wheel(path, package, source_root):
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError(f"Corrupt wheel: {path}")
        actual = {name for name in archive.namelist() if name.startswith(package + "/") and name.endswith(".py")}
        expected = {source.relative_to(source_root).as_posix()
                    for source in (source_root / package).rglob("*.py") if "__pycache__" not in source.parts}
        if actual != expected:
            raise ValueError(f"{package} wheel Python module inventory differs from current source")
        for name in expected:
            if archive.read(name) != (source_root / name).read_bytes():
                raise ValueError(f"Wheel does not match current source: {path.name}: {name}")


def checked_inputs():
    runtime = one_wheel(OUTPUT / "runtime", "emo_master-*.whl")
    web = one_wheel(ROOT / "web_panel/dist", "emo_master_web_panel-*.whl")
    sdk = one_wheel(ROOT / "web_panel/contracts/panel-contract-v1/python", "emo_panel_protocol-*.whl")
    # A successful build can still contain stale setuptools build/lib modules.
    with zipfile.ZipFile(runtime) as archive:
        if archive.testzip() is not None:
            raise ValueError(f"Corrupt Runtime wheel: {runtime}")
        modules = [name for name in archive.namelist() if name.endswith(".py")]
        expected_modules = {path.relative_to(ROOT / "src").as_posix()
                            for path in (ROOT / "src").rglob("*.py") if "__pycache__" not in path.parts}
        if set(modules) != expected_modules:
            raise ValueError("Runtime wheel Python module inventory differs from current source")
        for name in modules:
            source = ROOT / "src" / name
            if not source.is_file() or archive.read(name) != source.read_bytes():
                raise ValueError(f"Runtime wheel does not match current source: {name}")
    check_python_wheel(sdk, "emo_panel_protocol", ROOT / "src")
    contract = json.loads((ROOT / "web_panel/contracts/panel-contract-v1/contract-manifest.json").read_text(encoding="utf-8"))
    if sdk.name not in {Path(name).name for name in contract["sdkWheels"]}:
        raise ValueError("SDK wheel differs from current contract manifest")
    if digest(sdk) != contract["files"]["python/" + sdk.name]:
        raise ValueError("SDK wheel digest differs from current contract manifest")
    if f'"emo-panel-protocol=={contract["sdkVersion"]}"' not in (ROOT / "web_panel/pyproject.toml").read_text(encoding="utf-8"):
        raise ValueError("Web SDK dependency differs from current contract manifest")
    built = file_tree(ROOT / "web_panel/frontend/dist")
    static = file_tree(ROOT / "web_panel/backend/emo_web_panel/static")
    if "index.html" not in built or set(built) != set(static):
        raise ValueError("Build frontend and run package_frontend.py before packaging: frontend/static inventory differs")
    for name in built:
        if built[name].read_bytes() != static[name].read_bytes():
            raise ValueError(f"Frontend build differs from packaged static source: {name}")
    with zipfile.ZipFile(web) as archive:
        if archive.testzip() is not None:
            raise ValueError(f"Corrupt Web wheel: {web}")
        expected_web = {path.relative_to(ROOT / "web_panel/backend").as_posix()
                        for path in (ROOT / "web_panel/backend/emo_web_panel").rglob("*")
                        if path.is_file() and "__pycache__" not in path.parts
                        and (path.suffix == ".py" or "static" in path.parts)}
        if not expected_web <= set(archive.namelist()):
            raise ValueError("Web wheel is missing current Python modules or static frontend files")
        for name in archive.namelist():
            if name.startswith("emo_web_panel/"):
                source = ROOT / "web_panel/backend" / name
                if not source.is_file() or archive.read(name) != source.read_bytes():
                    raise ValueError(f"Web wheel does not match current backend/static source: {name}")
    return runtime, web, sdk, static


def main(*, check_only=False):
    runtime, web, sdk, static = checked_inputs()
    if check_only:
        print(json.dumps({"mode": "read-only artifact/source identity checks", "checks": "PASS",
            "wheels": {str(path): digest(path) for path in (runtime, web, sdk)},
            "frontendAssets": {name: digest(path) for name, path in static.items()}}, ensure_ascii=False, indent=2))
        return
    OUTPUT.mkdir(parents=True, exist_ok=True)
    start = OUTPUT / "START-HERE.md"
    start.write_text(f"""# 独立 Web 页面编辑器交付

本归档包含构建时源码对应的 Runtime、Web、SDK Python wheel。准确源码提交和工作区修改状态记录在 delivery-manifest.json 的 sourceSha/sourceDirty 字段。Web wheel 内置构建前端；没有冻结解释器或离线依赖 wheelhouse。

在解压目录使用 Python 3.10+ 创建独立 Web 环境：

```powershell
py -3.10 -m venv .venv
& .venv/Scripts/python.exe -m pip install ./web/{sdk.name} ./web/{web.name}
& .venv/Scripts/emo-web-panel.exe --data-dir D:/EmoWeb/panel-data create-user admin
& .venv/Scripts/emo-web-panel.exe --data-dir D:/EmoWeb/panel-data serve --mode grpc --runtime-address 127.0.0.1:50061
```

地址按实际 Runtime 修改。打开 http://127.0.0.1:8780/editor ，密码交互输入。运行 Web 无需 Node、Qt、驱动或推理包。

Runtime wheel 位于 runtime/，要求 Python 3.10 及原有 Runtime 依赖，不能装进轻量 Web 环境。Runtime 的实际检查层级与尚未执行层级见 VERIFICATION.md；不能把归档/导入检查当作安装后检测或冻结 EXE 验收。

README.md 说明两页编辑和运行；DEPLOYMENT.md 说明受控 LAN、备份与恢复；VERIFICATION.md 记录 W01—W13；review-round3/README.md 说明本轮编辑、重连版本保持与计划细节收尾；review-round2/README.md 保留发布锁隔离与固定预览高度修复记录。上述文档保留仓库路径供追溯，解压安装布局以本页为准。scripts/independent_web_editor_demo.py 是模拟工程生成器副本，推荐在完整当前仓库源码环境运行。delivery-manifest.json 记录文件及独立证据归档摘要；web/contract-manifest.json 对应配套 SDK。历史失败、反例脚本和最终证据另见与本归档并列的 verification-evidence.zip。
""", encoding="utf-8")
    entries = {
        f"runtime/{runtime.name}": runtime, f"web/{web.name}": web, f"web/{sdk.name}": sdk,
        "START-HERE.md": start, "README.md": ROOT / "web_panel/README.md",
        "DEPLOYMENT.md": ROOT / "web_panel/docs/DEPLOYMENT.md",
        "VERIFICATION.md": EVIDENCE / "README.md",
        "review-fixes/README.md": EVIDENCE / "review-fixes/README.md",
        "review-fixes/delivery-artifacts.json": EVIDENCE / "review-fixes/delivery-artifacts.json",
        "review-fixes/runtime-wheel-content.json": EVIDENCE / "review-fixes/runtime-wheel-content.json",
        "review-round2/README.md": EVIDENCE / "review-round2/README.md",
        "review-round2/delivery-artifacts.json": EVIDENCE / "review-round2/delivery-artifacts.json",
        "review-round3/README.md": EVIDENCE / "review-round3/README.md",
        "review-round3/delivery-artifacts.json": EVIDENCE / "review-round3/delivery-artifacts.json",
        "review-round3/runtime-wheel-content.json": EVIDENCE / "review-round3/runtime-wheel-content.json",
        "web/contract-manifest.json": ROOT / "web_panel/contracts/panel-contract-v1/contract-manifest.json",
        "scripts/independent_web_editor_demo.py": ROOT / "scripts/independent_web_editor_demo.py",
    }
    evidence_path = OUTPUT / "verification-evidence.zip"
    evidence_root = io_path(EVIDENCE)
    with zipfile.ZipFile(evidence_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(evidence_root.rglob("*")):
            if path.is_file() and path.suffix.lower() in {".md", ".json", ".png", ".log", ".xml", ".txt", ".py", ".patch", ".jsonl"}:
                archive.write(path, path.relative_to(evidence_root).as_posix())
    manifest = {"sourceSha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "sourceDirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True)),
                "distributionKind": "Python wheels; no bundled interpreter or offline dependencies",
                "artifactSourceChecks": ["Runtime Python inventory and bytes", "SDK Python inventory and bytes",
                    "SDK contract/dependency version", "frontend dist/static inventory and bytes", "Web backend/static bytes"],
                "frontendAssets": {name: digest(path) for name, path in static.items()},
                "evidenceArchive": {"path": evidence_path.name, **digest(evidence_path)},
                "files": {name: digest(path) for name, path in entries.items()}}
    metadata = OUTPUT / "delivery-manifest.json"
    metadata.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    entries["delivery-manifest.json"] = metadata
    with zipfile.ZipFile(OUTPUT / "independent-web-editor-delivery.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for name, path in entries.items():
            archive.write(path, name)
    for name in ("independent-web-editor-delivery.zip", "verification-evidence.zip"):
        path = OUTPUT / name
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise ValueError(f"Corrupt delivery archive: {path}")
        print(json.dumps({"path": str(path), **digest(path)}, ensure_ascii=False))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true", help="Read-only wheel/source/static checks; do not create archives")
    main(check_only=parser.parse_args().check_only)
