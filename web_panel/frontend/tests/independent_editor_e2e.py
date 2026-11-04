"""Real Edge/Chromium -> standalone Web -> software Runtime editor acceptance.

Requires the built frontend, supplied SDK wheel in the Web interpreter, and a
separate Python 3.10 Runtime interpreter in EMO_WEB_PRODUCT_PYTHON. All state and
images are synthetic. Each attempt preserves a separate evidence directory.
Set EMO_WEB_USE_INSTALLED=1 to run the gateway with Python isolation against the
installed Web wheel instead of the backend source directory.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import suppress
from datetime import datetime
from io import BytesIO
import json
import os
import re
from pathlib import Path
import socket
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
PANEL = ROOT / "web_panel"
BASE_PATH = "/api/editor/configured"
USER, PASSWORD = "editor-test", "Synthetic-editor-test-only!"


def wait(condition, message, timeout=35):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = condition()
        if value:
            return value
        time.sleep(.1)
    raise AssertionError("Timed out: " + message)


def gateway(directory, address, port):
    import uvicorn
    import emo_web_panel
    from emo_web_panel.auth import AuthStore
    from emo_web_panel.app import create_app
    from emo_web_panel.config import Config
    first_start = not (directory / "web.sqlite").exists()
    auth = AuthStore(directory / "web.sqlite")
    if first_start:
        auth.create_user(USER, PASSWORD, "admin")
    print(json.dumps({"event": "web-package-origin", "path": str(Path(emo_web_panel.__file__).resolve()),
                      "isolated": bool(sys.flags.isolated)}), flush=True)
    uvicorn.run(create_app(Config(directory, mode="grpc", runtime_address=address, port=port)),
                host="127.0.0.1", port=port, access_log=False)


def main():
    import httpx
    from PIL import Image
    from playwright.sync_api import sync_playwright, expect
    from emo_panel_protocol.generated import runtime_pb2 as pb, runtime_pb2_grpc as rpc
    from emo_panel_protocol.decoding import decodeResult
    import grpc

    attempt = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
    evidence = PANEL / "test-results/independent-editor" / attempt
    evidence.mkdir(parents=True)
    runtime_dir, web_dir = evidence / "runtime", evidence / "web"
    runtime_dir.mkdir(); web_dir.mkdir()
    report = {"status": "FAILED", "verification": "Edge/Chromium -> independent Web -> software Runtime",
              "syntheticOnly": True, "physicalDevices": False, "steps": []}
    runtime_log = (evidence / "runtime.log").open("w", encoding="utf-8")
    web_log = (evidence / "web.log").open("w", encoding="utf-8")
    runtime = web = connection = browser = None
    try:
        runtime_env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONUTF8="1", QT_QPA_PLATFORM="offscreen")
        runtime = subprocess.Popen([os.environ["EMO_WEB_PRODUCT_PYTHON"], str(ROOT / "scripts/independent_web_editor_demo.py"),
            "--directory", str(runtime_dir), "--port", "0"], cwd=ROOT, env=runtime_env,
            stdin=subprocess.DEVNULL, stdout=runtime_log, stderr=runtime_log)
        def ready():
            if runtime.poll() is not None:
                raise AssertionError("Runtime startup failed; inspect runtime.log")
            for line in (evidence / "runtime.log").read_text(encoding="utf-8").splitlines():
                if line.startswith('{"event": "ready"'):
                    return json.loads(line)
        target = wait(ready, "software Runtime startup")
        report["runtime"] = target
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0)); port = probe.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        use_installed = os.environ.get("EMO_WEB_USE_INSTALLED") == "1"
        web_env = dict(os.environ, PYTHONUTF8="1")
        if use_installed:
            web_env.pop("PYTHONPATH", None)
        else:
            web_env["PYTHONPATH"] = str(PANEL / "backend")
        web_command = [sys.executable, *(["-I"] if use_installed else []),
                       str(Path(__file__).resolve()), "--gateway", str(web_dir),
                       target["address"], str(port)]
        web = subprocess.Popen(web_command, cwd=PANEL, env=web_env,
                                stdin=subprocess.DEVNULL, stdout=web_log, stderr=web_log)
        with httpx.Client(base_url=base, timeout=20) as client:
            def web_ready():
                if web.poll() is not None:
                    raise AssertionError("Gateway startup failed; inspect web.log")
                try:
                    return client.get("/api/health").status_code == 200
                except httpx.ConnectError:
                    return False
            wait(web_ready, "Web startup")
            origin = next(json.loads(line) for line in (evidence / "web.log").read_text(encoding="utf-8").splitlines()
                          if line.startswith('{"event": "web-package-origin"'))
            report["webPackage"] = {"mode": "installed-wheel" if use_installed else "source", **origin}
            if use_installed:
                assert origin["isolated"] and "site-packages" in Path(origin["path"]).parts, origin
            def request(method, path, **kwargs):
                response = client.request(method, path, **kwargs)
                assert response.status_code == 200, f"{method} {path}: {response.status_code} {response.text}"
                return response.json()
            me = request("POST", "/api/session", json={"username": USER, "password": PASSWORD})
            client.headers["X-CSRF-Token"] = me["csrf"]
            ctx = request("GET", BASE_PATH + "/context")
            project = ctx["runtime"]["projectId"]
            assert ctx["requests"]["config"]["sources"] == {}
            assert request("GET", "/api/runtimes/configured/runs") == []
            report["steps"].append("W01: standalone target/catalog with no Designer and no Job")
            connection = grpc.insecure_channel(target["address"])
            display = rpc.DisplayServiceStub(connection)
            with sync_playwright() as pw:
                executable = os.environ.get("CHROMIUM_PATH", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
                browser = pw.chromium.launch(executable_path=executable, headless=True)
                context = browser.new_context(base_url=base, viewport={"width": 1500, "height": 1050})
                context.add_cookies([{"name": k, "value": v, "url": base} for k, v in client.cookies.items()])
                page = context.new_page()
                page.set_default_timeout(20000)
                errors, messages = [], []
                asset_responses = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("response", lambda response: asset_responses.append({"url": response.url, "status": response.status})
                        if "/api/assets/" in response.url or "/api/editor/" in response.url and "/assets/" in response.url else None)
                def frame(payload):
                    try:
                        messages.append(json.loads(payload))
                        if len(messages) > 300:
                            del messages[0]
                    except (ValueError, TypeError):
                        pass
                page.on("websocket", lambda ws: ws.on("framereceived", frame))
                page.goto(base + "/editor")
                expect(page.get_by_role("heading", name="Web 页面编辑器", exact=True)).to_be_visible()
                page.get_by_role("button", name="创建首期两页结构", exact=True).click()
                choices = ctx["catalog"]["choices"]
                needed = [("图像 图像", "blob", "overlay"), ("数值 数值", "count", "count"),
                          ("判定指示 判定指示", "judge", "result")]
                def bind(label, node, port):
                    page.get_by_label(label, exact=True).click()
                    index = next(i for i, c in enumerate(choices) if c["source"].get("nodeId") == node
                                 and c["source"].get("port") == port and not c["source"].get("fieldPath"))
                    page.get_by_label("组件输出绑定", exact=True).select_option(str(index))
                    return choices[index]["sourceId"]
                ids = {port: bind(label, node, port) for label, node, port in needed}
                page.get_by_role("navigation", name="编辑页面标签").get_by_role("button", name="详情页", exact=True).click()
                ids["blobs"] = bind("集合表格 集合表格", "blob", "blobs")
                if not page.get_by_label("表格列1名称", exact=True).count():
                    page.get_by_role("button", name="添加列", exact=True).click()
                page.get_by_label("表格列1名称", exact=True).fill("面积")
                page.get_by_label("表格列1字段", exact=True).fill("area")
                # Browser upload retains a resource ID in the editable source.
                page.locator(".editor-palette").get_by_role("button", name=re.compile("图像$")).click()
                buffer = BytesIO()
                Image.new("RGB", (24, 24), "#318db8").save(buffer, format="PNG")
                page.get_by_label("上传静态图片", exact=True).set_input_files(
                    {"name": "static-demo.png", "mimeType": "image/png", "buffer": buffer.getvalue()})
                expect(page.get_by_alt_text("静态图片", exact=True)).to_be_visible()
                page.get_by_role("button", name="保存草稿", exact=True).click()
                expect(page.get_by_text("草稿已保存；正式页面和 Runtime 采集请求未改变。", exact=True)).to_be_visible()
                saved = request("GET", BASE_PATH + "/context")["draft"]
                assert saved["presentation"]["schemaVersion"] == "1.2"
                assert len(saved["presentation"]["pages"]) == 2
                assert all(p["targets"] == ["web"] for p in saved["presentation"]["pages"].values())
                assert len(saved["resources"]) == 1
                assert request("GET", BASE_PATH + "/context")["requests"]["config"]["sources"] == {}
                page.reload()
                expect(page.get_by_role("button", name="详情页", exact=True)).to_be_visible()
                page.get_by_role("button", name="示例预览", exact=True).click()
                expect(page.get_by_role("heading", name="示例数据预览 · 非真实检测结果", exact=True)).to_be_visible()
                page.get_by_role("button", name="关闭预览", exact=True).click()
                assert request("GET", "/api/runtimes/configured/runs") == []
                report["steps"].append("W02/W03/W04: browser creates/binds/saves/reopens two pages and example preview never executes")
                page.get_by_role("button", name="发布页面", exact=True).click()
                expect(page.get_by_text("发布完成", exact=True).first).to_be_visible()
                published = request("GET", BASE_PATH + "/context")
                first_version = published["versions"][0]
                assert first_version["state"] == "PUBLISHED", first_version
                assert request("GET", "/api/runtimes/configured/runs") == []
                info = published["runtime"]
                page.goto(base + "/")
                expect(page.get_by_role("heading", name="Emo Web 操作员面板", exact=True)).to_be_visible()
                page.get_by_label("启动工作流目标", exact=True).select_option("main")
                page.get_by_role("button", name="启动工作流", exact=True).click()
                started_runs = wait(lambda: request("GET", "/api/runtimes/configured/runs"), "explicit browser Start")
                assert len(started_runs) == 1, started_runs
                run = started_runs[0]["id"]
                before = display.GetJob(pb.DisplayJobRequest(job_id=run,
                    runtime_instance_id=info["runtimeInstanceId"], project_id=project), timeout=5)
                def result_ready():
                    snapshot = display.Snapshot(pb.DisplayRequest(job_id=run), timeout=5)
                    if snapshot.results:
                        return decodeResult(snapshot.results[-1])
                result = wait(result_ready, "same-execution image/count/verdict")
                frozen_revision = result.identity.capturePlanRevision
                assert frozen_revision, "The actual result must identify the frozen capture plan"
                values = {s.sourceId: s for s in result.sources}
                assert values[ids["count"]].valueJson == "2"
                assert values[ids["result"]].valueJson == "true"
                assert values[ids["overlay"]].image.ownerResultKey == result.identity.resultKey
                report["sameResult"] = result.model_dump(mode="json")
                report["steps"].append("W05: explicit Web Start -> real installed operators -> DisplayService same result image/count=2/verdict=true")
                page.get_by_label("查看运行", exact=True).select_option(run)
                expect(page.locator(".panel-card img").first).to_have_js_property("naturalWidth", 320)
                expect(page.get_by_text("OK", exact=True)).to_be_visible()
                page.locator("nav.page-nav").get_by_role("button", name="详情页", exact=True).click()
                expect(page.locator(".panel-card tbody tr")).to_have_count(2)
                expect(page.locator(".panel-card img").first).to_have_js_property("naturalWidth", 24)
                page.screenshot(path=str(evidence / "formal-running-details.png"))
                report["steps"].append("W05: formal operator running page shows the published two-page package and same-task results")
                page.goto(base + "/editor")
                expect(page.get_by_role("heading", name="Web 页面编辑器", exact=True)).to_be_visible()
                page.get_by_role("button", name="刷新目录和任务", exact=True).click()
                page.get_by_label("实时预览任务", exact=True).select_option(run)
                page.get_by_label("终端尺寸", exact=True).select_option("1366x768")
                with page.expect_response(lambda response: response.url.endswith(BASE_PATH + "/preview") and response.request.method == "POST") as preview_response:
                    page.get_by_role("button", name="实时预览", exact=True).click()
                metadata = preview_response.value.json()["jobMetadata"]
                assert metadata["jobId"] == run
                assert metadata["executionRevision"] == result.identity.executionRevision
                assert metadata["capturePlanRevision"] == frozen_revision
                assert metadata["projectRevision"].isdecimal()
                report["selectedJobMetadata"] = metadata
                expect(page.get_by_role("dialog", name="实时任务预览", exact=True)).to_be_visible()
                expect(page.get_by_role("dialog").locator(".editor-preview-terminal")).to_have_css("width", "1366px")
                expect(page.get_by_role("dialog").locator(".editor-preview-terminal")).to_have_css("min-height", "768px")
                page.get_by_role("dialog").get_by_role("button", name="检测页", exact=True).click()
                expect(page.get_by_role("dialog").locator("img").first).to_be_visible(timeout=30000)
                expect(page.get_by_role("dialog").locator("img").first).to_have_js_property("naturalWidth", 320)
                page.get_by_role("dialog").screenshot(path=str(evidence / "live-detection.png"))
                page.get_by_role("dialog").get_by_role("button", name="详情页", exact=True).click()
                expect(page.get_by_role("dialog").locator("thead th").filter(has_text="面积")).to_be_visible()
                expect(page.get_by_role("dialog").locator("tbody td").filter(has_text="1600")).to_be_visible()
                expect(page.get_by_role("dialog").locator("tbody tr")).to_have_count(2)
                expect(page.get_by_role("dialog").get_by_text("连接已断开", exact=False)).to_have_count(0)
                expect(page.get_by_role("dialog").locator("img").first).to_have_js_property("naturalWidth", 24)
                page.get_by_role("dialog").screenshot(path=str(evidence / "live-details.png"))
                page.get_by_role("dialog").get_by_role("button", name="检测页", exact=True).click()
                expect(page.get_by_role("dialog").locator("img").first).to_be_visible()
                expect(page.get_by_role("dialog").get_by_text("OK", exact=True)).to_be_visible()
                page.get_by_role("button", name="关闭预览", exact=True).click()
                assert any(m.get("event") == "result" or m.get("type") == "result" for m in messages), messages[-5:]
                report["steps"].append("W05: actual browser DisplayService WebSocket, image rendering and detail navigation in a 1366x768 live preview terminal")
                # A separate operator keeps its selected release across one
                # ordinary socket loss; only the explicit refresh adopts updates.
                operator = context.new_page()
                operator.add_init_script("""window.__round3Sockets=[];window.__round3Frames=[];const NativeSocket=window.WebSocket;
                    window.WebSocket=class extends NativeSocket {constructor(...args){super(...args);window.__round3Sockets.push(this);
                    this.addEventListener('message',e=>{try{window.__round3Frames.push(JSON.parse(e.data))}catch{}})}};""")
                operator.on("pageerror", lambda error: errors.append(str(error)))
                operator_controls = []
                operator.on("request", lambda req: operator_controls.append({"method": req.method, "url": req.url})
                    if req.method == "POST" and "/api/" in req.url and "/runs/" in req.url else None)
                operator.goto(base + "/")
                expect(operator.get_by_label("页面包", exact=True)).to_have_value(first_version["packageId"])
                operator.get_by_label("查看运行", exact=True).select_option(run)
                expect(operator.locator(".panel-card img").first).to_have_js_property("naturalWidth", 320)
                # Releasing a new layout while a continuous Job runs must preserve it.
                source = deepcopy(saved["presentation"])
                source["pages"][source["defaultPageId"]]["components"][0]["props"]["title"] = "更新后的检测页"
                changed = request("PUT", BASE_PATH + "/draft", json={"projectId": project, "presentation": source,
                    "expectedDraftRevision": saved["draftRevision"]})
                release = request("POST", BASE_PATH + "/publish", json={"projectId": project,
                    "expectedDraftRevision": changed["draftRevision"], "requestId": uuid4().hex})
                assert release["state"] == "PUBLISHED", release
                expect(operator.get_by_text("页面包已有更新，请点击刷新页面包", exact=True)).to_be_visible()
                expect(operator.get_by_label("页面包", exact=True)).to_have_value(first_version["packageId"])
                frames_before_reconnect = operator.evaluate("window.__round3Frames.length")
                operator.evaluate("window.__round3Sockets.filter(s=>s.readyState===1).forEach(s=>s.close())")
                wait(lambda: operator.evaluate("() => window.__round3Sockets.length>=2 && window.__round3Sockets.at(-1).readyState===1"),
                     "operator automatic reconnect without adopting a release")
                wait(lambda: operator.evaluate("start => window.__round3Frames.slice(start).some(m=>m.type==='result'||m.event==='result')", frames_before_reconnect),
                     "new real result frame after operator reconnect")
                expect(operator.locator(".panel-card img").first).to_have_js_property("naturalWidth", 320)
                expect(operator.get_by_label("页面包", exact=True)).to_have_value(first_version["packageId"])
                expect(operator.get_by_label("查看运行", exact=True)).to_have_value(run)
                operator.screenshot(path=str(evidence / "operator-auto-reconnect-pinned.png"))
                operator.get_by_role("button", name="刷新页面包和状态", exact=True).click()
                expect(operator.get_by_label("页面包", exact=True)).to_have_value(release["packageId"])
                expect(operator.get_by_label("查看运行", exact=True)).to_have_value(run)
                expect(operator.locator(".panel-card img").first).to_have_js_property("naturalWidth", 320)
                operator.screenshot(path=str(evidence / "operator-explicit-refresh-new.png"))
                assert not operator_controls, operator_controls
                report["operatorReconnect"] = {"oldPackageId": first_version["packageId"],
                    "newPackageId": release["packageId"], "automaticRecoveryPackageId": first_version["packageId"],
                    "explicitRefreshPackageId": release["packageId"], "jobId": run, "implicitControlRequests": operator_controls,
                    "actions": ["select existing Job", "receive publication notice", "one socket close", "automatic reconnect", "explicit refresh"]}
                report["steps"].append("W09/W11: actual software Runtime operator retains old package/Job after one reconnect; explicit refresh adopts new package without Start/Stop")
                after = display.GetJob(pb.DisplayJobRequest(job_id=run,
                    runtime_instance_id=info["runtimeInstanceId"], project_id=project), timeout=5)
                assert result_ready().identity.capturePlanRevision == frozen_revision
                runs = request("GET", "/api/runtimes/configured/runs")
                assert len(runs) == 1 and runs[0]["id"] == run and runs[0]["active"], runs
                reverted = request("POST", BASE_PATH + "/rollback", json={"projectId": project,
                    "versionId": first_version["versionId"], "requestId": uuid4().hex})
                assert reverted["state"] == "PUBLISHED", reverted
                assert result_ready().identity.capturePlanRevision == frozen_revision
                report["steps"].append("W06/W10: active Job survives layout publication and rollback with frozen capture revision")
                assert not errors, errors
                report["browserErrors"] = errors
                report["browserMessages"] = len(messages)
                report["assetResponses"] = asset_responses
                browser.close(); browser = None
            # Closing the browser and the independent Web process owns no Runtime Job.
            ordinal = result_ready().identity.resultOrdinal
            web.terminate(); web.wait(timeout=15)
            wait(lambda: result_ready().identity.resultOrdinal > ordinal,
                 "Runtime continues detecting while the entire Web service is closed")
            assert result_ready().identity.capturePlanRevision == frozen_revision
            web = subprocess.Popen(web_command, cwd=PANEL, env=web_env,
                                   stdin=subprocess.DEVNULL, stdout=web_log, stderr=web_log)
            wait(web_ready, "independent Web service restart")
            restored = request("GET", BASE_PATH + "/context")
            assert restored["draft"]["presentation"] == source
            assert restored["versions"][0]["state"] == "PUBLISHED"
            assert restored["requests"]["config"]["sources"]
            runs = request("GET", "/api/runtimes/configured/runs")
            assert len(runs) == 1 and runs[0]["id"] == run and runs[0]["active"], runs
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=executable, headless=True)
                context = browser.new_context(base_url=base, viewport={"width": 1500, "height": 1050})
                context.add_cookies([{"name": k, "value": v, "url": base} for k, v in client.cookies.items()])
                page = context.new_page()
                page.goto(base + "/editor")
                expect(page.get_by_role("heading", name="Web 页面编辑器", exact=True)).to_be_visible()
                page.get_by_label("实时预览任务", exact=True).select_option(run)
                page.get_by_role("button", name="实时预览", exact=True).click()
                expect(page.get_by_role("dialog").locator("img").first).to_be_visible(timeout=30000)
                expect(page.get_by_role("dialog").locator("img").first).to_have_js_property("naturalWidth", 320)
                expect(page.get_by_role("dialog").get_by_text("OK", exact=True)).to_be_visible()
                page.get_by_role("dialog").get_by_role("button", name="详情页", exact=True).click()
                expect(page.get_by_role("dialog").locator("tbody tr")).to_have_count(2)
                expect(page.get_by_role("dialog").locator("img").first).to_have_js_property("naturalWidth", 24)
                page.get_by_role("dialog").screenshot(path=str(evidence / "reconnected-details.png"))
                browser.close(); browser = None
            report["steps"].append("W02/W11: actual browser and Web process close/restart retain draft/assets and resubscribe the original continuously running Job")
            request("POST", f"/api/runs/{run}/stop", json={"runtimeId": "configured", "requestId": uuid4().hex,
                "runtimeInstanceId": info["runtimeInstanceId"], "projectId": project, "runGroupId": ""})
            wait(lambda: not request("GET", f"/api/runs/{run}")["active"], "explicit normal Stop")
        report["status"] = "PASS"
    except BaseException as error:
        report["error"] = str(error)
        report["assetResponses"] = locals().get("asset_responses", [])
        raise
    finally:
        if browser:
            with suppress(Exception):
                browser.close()
        if connection:
            connection.close()
        if web and web.poll() is None:
            web.terminate(); web.wait(timeout=15)
        if runtime and runtime.poll() is None:
            (runtime_dir / "stop-requested").touch()
            try:
                runtime.wait(timeout=25)
            except subprocess.TimeoutExpired:
                runtime.kill(); runtime.wait(timeout=10)
        runtime_log.close(); web_log.close()
        (evidence / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": report["status"], "evidence": str(evidence)}, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--gateway":
        gateway(Path(sys.argv[2]), sys.argv[3], int(sys.argv[4]))
    else:
        main()
