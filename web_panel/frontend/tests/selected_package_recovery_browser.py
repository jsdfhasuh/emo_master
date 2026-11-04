"""Installed Edge/actual built React, controlled HTTP+WS, immutable page selection.

The HTTP fixture accepts historical reads only with target/project/instance.
Backend authorization and immutable source coverage are exercised separately by
test_selected_package_recovery.py; this is not device or LAN acceptance.
"""
import hashlib
import json
import os
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import threading
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = Path(os.environ.get("EMO_WEB_RECONNECT_EVIDENCE", ROOT / "test-tmp" / "selected-package-recovery"))
EVIDENCE.mkdir(parents=True, exist_ok=True)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "dist"), **kwargs)

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
reports = []


def exercise(browser, role):
    identity = {"runtimeInstanceId": "instance-A", "projectId": "project-A", "configurationRevision": "1"}
    state = {"active": "old", "requests": [], "views": [], "sockets": [], "errors": [], "actions": []}

    def panel(revision):
        components = [{"id": "version", "type": "text", "props": {"text": "VISIBLE-PAGE-" + revision.upper()}}]
        if revision == "new":
            components.append({"id": "added-count", "type": "number", "label": "New source", "binding": "added"})
        return {"id": "package-" + revision, "name": "Package " + revision, "packageRevision": revision,
                "runtimeId": "A", **identity, "pages": [{"id": "home", "title": "Page " + revision, "components": components}]}

    def mock(route):
        request = route.request
        parsed = urlsplit(request.url)
        path, query = parsed.path, parse_qs(parsed.query)
        record = {"method": request.method, "path": path, "query": query}
        state["requests"].append(record)
        if path == "/api/me":
            data = {"username": role, "role": role, "csrf": "fixture-token", "mode": "grpc"}
        elif path == "/api/runtimes":
            data = [{"id": "A", "alias": "Runtime A", "mode": "grpc", "permissions": ["view"]}]
        elif path == "/api/runtimes/A/info":
            data = dict(identity)
        elif path == "/api/runtimes/A/runs":
            data = [{"id": "job-existing", "state": "running", "name": "Existing job", **identity}]
        elif path == "/api/runtimes/A/workflows":
            data = []
        elif path == "/api/runtimes/A/status":
            data = {"runtime": identity, "counters": {"source": "fixture", "counters": []},
                    "events": {"events": [], "nextSequence": "0", "resetRequired": False, "source": "fixture"}}
        elif path == "/api/runtimes/A/events":
            data = {"events": [], "nextSequence": "0", "resetRequired": False, "source": "fixture", **identity}
        elif path == "/api/panels" or path.startswith("/api/panels/"):
            assert query["runtimeId"] == ["A"] and query["projectId"] == [identity["projectId"]]
            assert query["runtimeInstanceId"] == [identity["runtimeInstanceId"]]
            if path == "/api/panels":
                data = [panel(state["active"])]
            else:
                revision = path.rsplit("-", 1)[1]
                assert revision in ("old", "new")
                data = panel(revision)
        elif path == "/api/views":
            body = request.post_data_json
            record["body"] = body
            assert body["runtimeId"] == "A" and body["projectId"] == identity["projectId"]
            assert body["runtimeInstanceId"] == identity["runtimeInstanceId"]
            state["views"].append(dict(body))
            data = {"id": "view-" + str(len(state["views"])), "runIds": [body["runId"]] if body.get("runId") else [],
                    "sourceProblems": {"added": "SOURCE_NOT_CAPTURED"} if body["panelId"] == "package-new" else {}}
        elif path.startswith("/api/views/") and request.method == "DELETE":
            data = {}
        elif path == "/api/packages":
            data = {"packageId": "imported-not-enabled"} if request.method == "POST" else []
        elif path == "/api/admin/users" or path == "/api/admin/runtimes":
            data = []
        else:
            route.fulfill(status=404, json={"detail": path})
            return
        route.fulfill(json=data)

    page = browser.new_page()
    page.on("pageerror", lambda error: state["errors"].append(str(error)))
    page.route("**/api/**", mock)
    page.route_web_socket("**/ws/**", lambda socket: state["sockets"].append(socket))

    def wait_socket(after):
        for _ in range(150):
            if len(state["sockets"]) > after:
                return state["sockets"][-1]
            page.wait_for_timeout(30)
        raise AssertionError("Expected a recovered subscription")

    def snapshot():
        return {"footer": page.locator("footer").inner_text(), "panelId": page.get_by_label("页面包", exact=True).input_value(),
                "jobId": page.get_by_label("查看运行", exact=True).input_value(), "views": list(state["views"]),
                "userActions": list(state["actions"])}

    try:
        page.goto(f"http://127.0.0.1:{server.server_port}")
        expect(page.get_by_text("VISIBLE-PAGE-OLD", exact=True)).to_be_visible()
        refresh = page.get_by_role("button", name="刷新页面包和状态", exact=True)
        expect(refresh).to_be_enabled()
        state["actions"].append("explicitly select job-existing")
        page.get_by_label("查看运行", exact=True).select_option("job-existing")
        old_socket = wait_socket(0)
        state["active"] = "new"
        quiet = None
        if role == "admin":
            # Import triggers the other quiet refresh path without user intent
            # to adopt the newly active package or issue a production command.
            state["actions"].append("import package (quiet catalog sync; no activation)")
            page.locator("details.admin").filter(has=page.get_by_text("页面包管理", exact=True)).locator("summary").click()
            page.get_by_label("导入页面包", exact=True).set_input_files({"name": "fixture.emoweb", "mimeType": "application/zip", "buffer": b"fixture"})
            expect(page.get_by_text("页面包已导入，尚未启用", exact=True)).to_be_visible()
            expect(page.get_by_text("VISIBLE-PAGE-OLD", exact=True)).to_be_visible()
            quiet = snapshot()
            assert quiet["panelId"] == "package-old" and quiet["jobId"] == "job-existing"
        old_socket.send(json.dumps({"type": "package_updated", "packageId": "package-new", "packageRevision": "new", "explicitRefreshRequired": True}))
        expect(page.get_by_text("页面包已有更新，请点击刷新页面包", exact=True)).to_be_visible()
        expect(page.get_by_text("VISIBLE-PAGE-OLD", exact=True)).to_be_visible()
        notice = snapshot()
        page.screenshot(path=str(EVIDENCE / f"{role}-01-notice-old.png"), full_page=True)
        socket_count = len(state["sockets"])
        action_count = len(state["actions"])
        old_socket.close(code=1001, reason="single ordinary disconnect")
        recovered_socket = wait_socket(socket_count)
        expect(refresh).to_be_enabled()
        expect(page.get_by_text("VISIBLE-PAGE-OLD", exact=True)).to_be_visible()
        recovered_socket.send(json.dumps({"type": "result", "result": {"resultKey": "same-existing-job-result", "jobId": "job-existing",
            "resultScopeId": "scope-1", "sources": {"existing": {"state": "available", "value": 2}}}}))
        expect(page.locator("footer")).to_contain_text("same-existing-job-result")
        recovered = snapshot()
        assert len(state["actions"]) == action_count
        assert recovered["panelId"] == "package-old" and recovered["jobId"] == "job-existing"
        assert recovered["views"][-1]["panelId"] == "package-old" and recovered["views"][-1]["runId"] == "job-existing"
        expect(page.get_by_text("SOURCE_NOT_CAPTURED", exact=False)).to_have_count(0)
        page.screenshot(path=str(EVIDENCE / f"{role}-02-reconnected-old.png"), full_page=True)
        adoption = "explicitly click refresh page package and status" if role == "viewer" else "explicitly switch package dropdown to package-new"
        state["actions"].append(adoption)
        socket_count = len(state["sockets"])
        if role == "viewer":
            refresh.click()
        else:
            page.get_by_label("页面包", exact=True).select_option("package-new")
        expect(page.get_by_text("VISIBLE-PAGE-NEW", exact=True)).to_be_visible()
        latest_socket = wait_socket(socket_count)
        latest_socket.send(json.dumps({"type": "result", "result": {"resultKey": "same-existing-job-result", "jobId": "job-existing",
            "resultScopeId": "scope-1", "sources": {"existing": {"state": "available", "value": 2}}}}))
        expect(page.get_by_text("SOURCE_NOT_CAPTURED", exact=False)).to_be_visible()
        explicit = snapshot()
        assert explicit["panelId"] == "package-new" and explicit["jobId"] == "job-existing"
        page.screenshot(path=str(EVIDENCE / f"{role}-03-explicit-new-uncovered.png"), full_page=True)
        # Ordinary reconnect must still invalidate selection on actual identity
        # replacement, even if the new Runtime happens to reuse the Job ID.
        identity.update(runtimeInstanceId="instance-A-restarted", projectId="project-A-reopened")
        socket_count = len(state["sockets"])
        latest_socket.close(code=1001, reason="Runtime instance replaced fixture")
        expect(page.get_by_label("当前 Runtime 身份", exact=True)).to_contain_text("instance-A-restarted / project-A-reopened", timeout=8000)
        expect(page.get_by_label("查看运行", exact=True)).to_have_value("")
        expect(refresh).to_be_enabled()
        identity_reset = snapshot()
        production = [item for item in state["requests"] if item["path"].startswith("/api/runs/")]
        assert not production and not state["errors"], state["errors"]
        return {"role": role, "status": "PASS", "layer": "installed Edge headless / actual current Vite build / synthetic HTTP and WS",
                "notice": notice, "quietImportSync": quiet, "automaticRecovery": recovered,
                "humanActionDuringAutomaticRecovery": False, "explicitAdoption": explicit, "adoptionAction": adoption, "identityReplacement": identity_reset,
                "productionCommands": production, "pageErrors": state["errors"], "requests": state["requests"]}
    except Exception:
        page.screenshot(path=str(EVIDENCE / f"{role}-failure.png"), full_page=True)
        (EVIDENCE / f"{role}-failure.json").write_text(json.dumps({key: value for key, value in state.items() if key != "sockets"}, ensure_ascii=False, indent=2), encoding="utf-8")
        raise
    finally:
        page.close()


try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=os.environ.get("CHROMIUM_PATH", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"))
        try:
            for role in ("viewer", "admin"):
                reports.append(exercise(browser, role))
        finally:
            browser.close()
    hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted((ROOT / "dist").rglob("*")) if path.is_file()}
    (EVIDENCE / "browser-report.json").write_text(json.dumps({"status": "PASS", "cases": reports, "builtArtifactSha256": hashes}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "PASS", "report": str(EVIDENCE / "browser-report.json")}, ensure_ascii=False))
finally:
    server.shutdown()
