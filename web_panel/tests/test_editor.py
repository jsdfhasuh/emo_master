"""Independent authoring HTTP lifecycle; synthetic Runtime remains explicit."""
from copy import deepcopy
from io import BytesIO
import tempfile
import unittest
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from emo_web_panel.app import create_app
from emo_web_panel.config import Config
from emo_web_panel.packages import PackageError, PackageStore


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Config(Path(self.tmp.name))
        self.app = create_app(self.config)
        self.app.state.auth.create_user("admin", "safe-test-password", "admin")
        self.app.state.auth.create_user("viewer", "safe-test-password", "viewer")
        self.client = TestClient(self.app)
        self.client.__enter__()
        response = self.client.post("/api/session", json={"username": "admin", "password": "safe-test-password"})
        self.headers = {"X-CSRF-Token": response.json()["csrf"]}
        self.base = "/api/editor/configured"
        self.context = self.client.get(self.base + "/context").json()
        self.project = self.context["runtime"]["projectId"]

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def pages(self, ports=("result_image", "count", "result", "detections")):
        source = deepcopy(self.context["draft"]["presentation"])
        first = source["defaultPageId"]
        source["pageOrder"].append("details")
        source["pages"]["details"] = {"name": "详情页", "targets": ["web"], "components": [], "resultScopeIds": ["scope-1"]}
        source["pages"][first]["resultScopeIds"] = ["scope-1"]
        for choice in self.context["catalog"]["choices"]:
            value = choice["source"]
            if value["port"] not in ports:
                continue
            sid = choice["sourceId"]
            source["dataSources"][sid] = value
            source["resultScopes"][choice["scopeId"]] = choice["scope"]
            kind = {"image": "image", "integer": "number", "boolean": "indicator", "collection": "table"}[value["expectedType"]]
            page = source["pages"]["details" if kind == "table" else first]
            page["components"].append({"componentId": "component-" + sid, "type": kind,
                                       "bindings": {{"image": "image", "table": "rows"}.get(kind, "value"): sid}})
        source["pages"][first]["components"].append({"componentId": "nav-details", "type": "navigation_button",
            "props": {"text": "查看详情"}, "actions": {"clicked": {"type": "navigate", "pageId": "details"}}})
        return source

    def save(self, source, expected=None):
        body = {"projectId": self.project, "presentation": source}
        if expected is not None:
            body["expectedDraftRevision"] = expected
        response = self.client.put(self.base + "/draft", headers=self.headers, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def publish(self, revision, request="publish-test-0001"):
        response = self.client.post(self.base + "/publish", headers=self.headers,
            json={"projectId": self.project, "expectedDraftRevision": revision, "requestId": request})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_two_pages_full_source_resources_reopen_preview_without_execution(self):
        buffer = BytesIO()
        Image.new("RGB", (8, 8), "blue").save(buffer, "PNG")
        upload = self.client.post(self.base + "/assets", params={"projectId": self.project, "name": "logo.png"},
            content=buffer.getvalue(), headers={**self.headers, "Content-Type": "image/png"})
        self.assertEqual(upload.status_code, 200, upload.text)
        asset = upload.json()
        source = self.pages()
        source["pages"][source["defaultPageId"]]["components"].append({"componentId": "logo", "type": "image", "props": {"imageResourceId": asset["resourceId"]}})
        saved = self.save(source, "")
        preview = self.client.post(self.base + "/preview", headers=self.headers,
            json={"projectId": self.project, "presentation": saved["presentation"], "mode": "fixtures"})
        self.assertEqual(preview.status_code, 200, preview.text)
        panel = preview.json()["panel"]
        self.assertEqual(len(panel["pages"]), 2)
        self.assertEqual(self.client.get("/api/assets/" + panel["id"] + "/" + asset["resourceId"]).content, buffer.getvalue())
        self.assertEqual(self.app.state.runtime.control_count, 0)
        self.assertEqual(self.app.state.runtime.output_config["sources"], {})
        reopened = create_app(self.config).state.editor.draft("configured", self.project)
        self.assertEqual(reopened, saved)
        self.assertIsInstance(reopened["presentation"]["pages"], dict)
        self.assertEqual(reopened["presentation"]["schemaVersion"], "1.2")
        self.assertTrue(all(page["targets"] == ["web"] for page in reopened["presentation"]["pages"].values()))

    def test_highly_compressible_bmp_previews_publishes_and_reimports_without_relaxing_zip_limits(self):
        buffer = BytesIO()
        Image.new("RGB", (1024, 1024), "white").save(buffer, "BMP")
        data = buffer.getvalue()
        compressed = BytesIO()
        with zipfile.ZipFile(compressed, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("assets/white.bmp", data)
        with zipfile.ZipFile(compressed) as archive:
            info = archive.getinfo("assets/white.bmp")
            self.assertGreater(info.file_size, info.compress_size * PackageStore.MAX_RATIO)
        with tempfile.TemporaryDirectory() as destination:
            imported = PackageStore(Path(destination))
            with self.assertRaisesRegex(PackageError, "Unsafe compression ratio"):
                imported.install(compressed.getvalue())

            upload = self.client.post(self.base + "/assets", params={"projectId": self.project, "name": "white.bmp"},
                content=data, headers={**self.headers, "Content-Type": "image/bmp"})
            self.assertEqual(upload.status_code, 200, upload.text)
            asset = upload.json()
            source = deepcopy(self.context["draft"]["presentation"])
            source["pages"][source["defaultPageId"]]["components"] = [
                {"componentId": "logo", "type": "image", "props": {"imageResourceId": asset["resourceId"]}}]
            saved = self.save(source, "")
            preview = self.client.post(self.base + "/preview", headers=self.headers,
                json={"projectId": self.project, "presentation": saved["presentation"], "mode": "fixtures"})
            self.assertEqual(preview.status_code, 200, preview.text)
            published = self.publish(saved["draftRevision"])
            self.assertEqual(published["state"], "PUBLISHED", published)
            self.assertEqual(published["packageId"], preview.json()["panel"]["id"])
            exported = self.client.get(self.base + "/versions/" + published["versionId"] + "/package",
                params={"projectId": self.project})
            self.assertEqual(exported.status_code, 200, exported.text)
            with zipfile.ZipFile(BytesIO(exported.content)) as archive:
                self.assertEqual(archive.getinfo(asset["path"]).compress_type, zipfile.ZIP_STORED)
                self.assertEqual(archive.getinfo("manifest.json").compress_type, zipfile.ZIP_DEFLATED)
            package = imported.install(exported.content)
            self.assertEqual(package["packageId"], published["packageId"])
            self.assertEqual(imported.asset(package["packageId"], asset["resourceId"]), (data, "image/bmp"))
            self.assertEqual(self.app.state.runtime.control_count, 0)
            self.assertEqual(create_app(self.config).state.editor.draft("configured", self.project), saved)

    def test_ordinary_generated_package_keeps_existing_compressed_bytes_and_identity(self):
        saved = self.save(self.pages())
        published = self.publish(saved["draftRevision"])
        response = self.client.get(self.base + "/versions/" + published["versionId"] + "/package",
            params={"projectId": self.project})
        self.assertEqual(response.status_code, 200)
        previous = BytesIO()
        with zipfile.ZipFile(BytesIO(response.content)) as current, zipfile.ZipFile(previous, "w", compression=zipfile.ZIP_DEFLATED) as old:
            for name in sorted(current.namelist()):
                info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                old.writestr(info, current.read(name))
        self.assertEqual(response.content, previous.getvalue())
        with tempfile.TemporaryDirectory() as destination:
            imported = PackageStore(Path(destination))
            installed = imported.install(previous.getvalue())
            self.assertEqual(imported.install(response.content)["packageId"], installed["packageId"])

    def test_publish_freezes_until_explicit_start_layout_only_and_rollback(self):
        initial = self.save(self.pages(("result_image", "count", "result")))
        published = self.publish(initial["draftRevision"])
        self.assertEqual(published["state"], "PUBLISHED", published)
        bypass = self.client.post("/api/packages/" + published["packageId"] + "/activate", headers=self.headers, json={"runtimeIds": ["configured"]})
        self.assertEqual(bypass.status_code, 409)
        self.assertIn("采集请求", bypass.text)
        self.assertEqual(self.app.state.runtime.control_count, 0)
        self.assertEqual(self.app.state.runtime.frozen_output_config["sources"], {})
        body = {"requestId": "explicit-start-0001", "configurationRevision": "1", "runtimeInstanceId": "mock-instance", "projectId": self.project, "workflowId": "demo-workflow"}
        self.assertEqual(self.client.post("/api/runs/start", headers=self.headers, json=body).status_code, 200)
        frozen = deepcopy(self.app.state.runtime.frozen_output_config)
        expanded = self.save(self.pages())
        updated = self.publish(expanded["draftRevision"], "publish-test-0002")
        self.assertEqual(updated["state"], "PUBLISHED", updated)
        self.assertEqual(self.app.state.runtime.frozen_output_config, frozen)
        self.assertEqual(self.app.state.runtime.control_count, 1)
        layout = deepcopy(expanded["presentation"])
        layout["pages"][layout["defaultPageId"]]["name"] = "检测看板"
        saved = self.save(layout)
        count = len(self.app.state.runtime.output_receipts)
        self.assertEqual(self.publish(saved["draftRevision"], "publish-layout-0003")["state"], "PUBLISHED")
        self.assertEqual(len(self.app.state.runtime.output_receipts), count)
        rollback = self.client.post(self.base + "/rollback", headers=self.headers,
            json={"projectId": self.project, "versionId": published["versionId"], "requestId": "rollback-test-0001"})
        self.assertEqual(rollback.status_code, 200, rollback.text)
        self.assertEqual(rollback.json()["state"], "PUBLISHED", rollback.json())
        self.assertNotIn("measurements", self.app.state.runtime.output_config["sources"])
        self.assertEqual(self.app.state.runtime.frozen_output_config, frozen)
        self.assertGreater(len(self.client.get(self.base + "/versions/" + published["versionId"] + "/package", params={"projectId": self.project}).content), 100)

    def test_lost_submit_reply_is_queried_then_explicitly_completed_without_replay(self):
        saved = self.save(self.pages())
        runtime = self.app.state.runtime
        original = runtime.submit_output_requests
        async def lost_reply(body):
            await original(body)
            raise ConnectionError("one disconnect after commit")
        runtime.submit_output_requests = lost_reply
        record = self.publish(saved["draftRevision"])
        self.assertEqual(record["state"], "REQUEST_UNKNOWN")
        self.assertEqual(len(runtime.output_receipts), 1)
        self.assertEqual(self.client.get("/api/panels").json()[0]["id"], "demo-panel")
        query = self.client.get(self.base + "/publications/" + record["versionId"], params={"projectId": self.project})
        self.assertEqual(query.json()["state"], "REQUEST_SAVED", query.text)
        done = self.client.post(self.base + "/publications/" + record["versionId"] + "/complete", headers=self.headers, json={"projectId": self.project})
        self.assertEqual(done.json()["state"], "PUBLISHED", done.text)
        self.assertEqual(len(runtime.output_receipts), 1)

    def test_type_errors_stale_draft_wrong_target_and_missing_real_preview_task(self):
        source = self.pages()
        source["pages"][source["defaultPageId"]]["components"][0]["type"] = "number"
        source["pages"][source["defaultPageId"]]["components"][0]["bindings"] = {"value": "image"}
        response = self.client.put(self.base + "/draft", headers=self.headers, json={"projectId": self.project, "presentation": source})
        self.assertEqual(response.status_code, 422)
        self.assertIn("component-image", response.text)
        saved = self.save(self.pages())
        response = self.client.put(self.base + "/draft", headers=self.headers, json={"projectId": self.project, "presentation": self.pages(), "expectedDraftRevision": "old"})
        self.assertEqual(response.status_code, 409)
        wrong = self.client.put(self.base + "/draft", headers=self.headers, json={"projectId": "other", "presentation": self.pages()})
        self.assertEqual(wrong.status_code, 409)
        preview = self.client.post(self.base + "/preview", headers=self.headers, json={"projectId": self.project, "presentation": saved["presentation"], "mode": "runtime"})
        self.assertEqual(preview.status_code, 422)
        self.client.post("/api/session", json={"username": "viewer", "password": "safe-test-password"})
        self.assertEqual(self.client.get(self.base + "/context").status_code, 403)

    def test_unknown_without_receipt_can_be_ended_without_replay_or_losing_draft(self):
        saved = self.save(self.pages())
        runtime = self.app.state.runtime
        original = runtime.submit_output_requests
        async def disconnected_before_commit(body):
            raise ConnectionError("disconnect with no observed receipt")
        runtime.submit_output_requests = disconnected_before_commit
        record = self.publish(saved["draftRevision"])
        queried = self.client.get(self.base + "/publications/" + record["versionId"], params={"projectId": self.project})
        self.assertEqual(queried.json()["state"], "REQUEST_UNKNOWN")
        response = self.client.post(self.base + "/publications/" + record["versionId"] + "/abandon", headers=self.headers, json={"projectId": self.project})
        self.assertEqual(response.json()["state"], "ABANDONED")
        self.assertEqual(runtime.output_receipts, {})
        self.assertEqual(self.client.get(self.base + "/context").json()["draft"], saved)
        runtime.submit_output_requests = original
        self.assertEqual(self.publish(saved["draftRevision"], "new-explicit-publish-0001")["state"], "PUBLISHED")

    def test_validation_disconnect_retains_candidate_for_explicit_continue(self):
        saved = self.save(self.pages())
        runtime = self.app.state.runtime
        original = runtime.validate_output_requests
        async def validation_disconnected(body):
            raise ConnectionError("read-only validation disconnected")
        runtime.validate_output_requests = validation_disconnected
        record = self.publish(saved["draftRevision"])
        self.assertEqual(record["state"], "PREPARED")
        self.assertEqual(runtime.output_receipts, {})
        runtime.validate_output_requests = original
        done = self.client.post(self.base + "/publications/" + record["versionId"] + "/complete", headers=self.headers, json={"projectId": self.project})
        self.assertEqual(done.json()["state"], "PUBLISHED", done.text)
        self.assertEqual(len(runtime.output_receipts), 1)

    def test_pending_older_reply_does_not_activate_after_actual_config_changed(self):
        saved = self.save(self.pages())
        runtime = self.app.state.runtime
        original = runtime.submit_output_requests
        async def lost_reply(body):
            await original(body)
            raise ConnectionError("saved but reply lost")
        runtime.submit_output_requests = lost_reply
        record = self.publish(saved["draftRevision"])
        runtime.output_config = {"schemaVersion": "1.0", "sources": {}, "scopes": {}}
        from emo_panel_protocol.canonical import revisionOf
        runtime.output_revision = revisionOf(runtime.output_config)
        response = self.client.post(self.base + "/publications/" + record["versionId"] + "/complete", headers=self.headers, json={"projectId": self.project})
        self.assertEqual(response.json()["state"], "REQUEST_SAVED", response.text)
        self.assertEqual(self.client.get("/api/panels").json()[0]["id"], "demo-panel")
        self.assertEqual(len(runtime.output_receipts), 1)

    def test_invalid_rollback_keeps_active_page_and_original_source(self):
        initial = self.save(self.pages())
        first = self.publish(initial["draftRevision"])
        small = self.save(self.pages(("result_image", "result")))
        second = self.publish(small["draftRevision"], "publish-without-count-0001")
        runtime = self.app.state.runtime
        choices = runtime.output_catalog_choices
        async def changed_catalog():
            return [choice for choice in await choices() if choice["sourceId"] != "count"]
        runtime.output_catalog_choices = changed_catalog
        response = self.client.post(self.base + "/rollback", headers=self.headers,
            json={"projectId": self.project, "versionId": first["versionId"], "requestId": "invalid-rollback-0001"})
        self.assertEqual(response.json()["state"], "REJECTED", response.text)
        self.assertIn("count", str(response.json()["diagnostics"]))
        self.assertEqual(self.client.get("/api/panels").json()[0]["id"], second["packageId"])
        source = self.client.get(self.base + "/versions/" + first["versionId"], params={"projectId": self.project}).json()["source"]
        self.assertIn("count", source["dataSources"])

    def test_quota_rejection_links_runtime_sources_to_web_controls_without_saving_requests(self):
        saved = self.save(self.pages())
        runtime = self.app.state.runtime
        async def quota_rejected(body):
            self.assertFalse(any(key in str(body["config"]) for key in ("pageId", "componentId")))
            return {"ok": False, "code": "INVALID_ARGUMENT", "message": "工作流检测：不同图片输出为3，上限2，包含Qt需求",
                    "diagnostics": [{"code": "CAPTURE_IMAGES_LIMIT", "workflowId": "demo-workflow",
                        "budget": "images", "count": 3, "limit": 2, "sourceIds": ["image", "qt-mask"],
                        "scopeIds": ["scope-1", "qt-main"], "message": "Qt 与外部请求合并后超出两路图片额度"}]}
        runtime.validate_output_requests = quota_rejected
        rejected = self.publish(saved["draftRevision"])
        self.assertEqual(rejected["state"], "REJECTED")
        diagnostic = rejected["diagnostics"][0]
        self.assertEqual(diagnostic["count"], 3)
        self.assertEqual(diagnostic["limit"], 2)
        refs = diagnostic["componentRefs"]
        self.assertEqual([ref["componentId"] for ref in refs], ["component-image"])
        self.assertEqual(refs[0]["pageId"], saved["presentation"]["defaultPageId"])
        self.assertEqual(refs[0]["pageName"], saved["presentation"]["pages"][refs[0]["pageId"]]["name"])
        self.assertEqual(runtime.output_config["sources"], {})
        self.assertEqual(runtime.output_receipts, {})
        self.assertEqual(runtime.control_count, 0)
        self.assertEqual(self.client.get("/api/panels").json()[0]["id"], "demo-panel")
        reopened = self.client.get(self.base + "/versions/" + rejected["versionId"], params={"projectId": self.project})
        self.assertEqual(reopened.json()["diagnostics"], rejected["diagnostics"])
        self.assertEqual(self.client.get(self.base + "/context").json()["draft"], saved)


if __name__ == "__main__":
    unittest.main()
