"""An automatic view recovery retains its immutable selected release and identity."""
from copy import deepcopy
import unittest

import test_editor as editor_fixture


class SelectedPackageRecoveryTests(unittest.TestCase):
    setUp = editor_fixture.EditorTests.setUp
    tearDown = editor_fixture.EditorTests.tearDown
    pages = editor_fixture.EditorTests.pages
    save = editor_fixture.EditorTests.save
    publish = editor_fixture.EditorTests.publish

    def releases(self):
        old = self.publish(self.save(self.pages(("result_image", "count", "result")))["draftRevision"])
        response = self.client.post("/api/runs/start", headers=self.headers, json={
            "requestId": "recovery-explicit-start", "configurationRevision": "1",
            "runtimeInstanceId": "mock-instance", "projectId": self.project, "workflowId": "demo-workflow"})
        self.assertEqual(response.status_code, 200, response.text)
        frozen = deepcopy(self.app.state.runtime.frozen_output_config)
        new = self.publish(self.save(self.pages())["draftRevision"], "recovery-second-release")
        return old, new, frozen

    def context_params(self, **changes):
        return {"runtimeId": "configured", "projectId": self.project,
                "runtimeInstanceId": "mock-instance", **changes}

    def read(self, package, **changes):
        return self.client.get("/api/panels/" + package["packageId"], params=self.context_params(**changes))

    def view(self, package, **changes):
        return self.client.post("/api/views", headers=self.headers, json={
            **self.context_params(**changes), "panelId": package["packageId"], "runId": "demo-run"})

    def test_recover_old_release_same_job_and_frozen_coverage(self):
        old, new, frozen = self.releases()
        read = self.read(old)
        self.assertEqual(read.status_code, 200, read.text)
        self.assertEqual(read.json()["id"], old["packageId"])
        self.assertEqual(read.json()["runtimeInstanceId"], "mock-instance")
        recovered = self.view(old)
        self.assertEqual(recovered.status_code, 200, recovered.text)
        self.assertEqual(recovered.json()["runIds"], ["demo-run"])
        self.assertEqual(recovered.json()["sourceProblems"], {})
        selected = self.app.state.views.views[recovered.json()["id"]]
        self.assertEqual(selected["panelId"], old["packageId"])
        self.assertEqual(selected["presentation"]["packageRevision"], read.json()["packageRevision"])
        catalog = self.client.get("/api/panels", params=self.context_params()).json()
        self.assertEqual([p["id"] for p in catalog], [new["packageId"]])
        latest = self.view(new)
        self.assertEqual(latest.status_code, 200, latest.text)
        self.assertEqual(set(latest.json()["sourceProblems"]), {"measurements"})
        self.assertIn("SOURCE_NOT_CAPTURED", latest.json()["sourceProblems"]["measurements"])
        self.assertEqual(self.app.state.runtime.frozen_output_config, frozen)
        self.assertEqual(self.app.state.runtime.control_count, 1)

    def test_old_release_requires_original_target_project_and_expected_instance(self):
        old, _, _ = self.releases()
        actor = {"username": "admin", "role": "admin"}
        self.app.state.registry.create(actor, "other", "Other mock", "mock", "")
        for operation in (self.read, self.view):
            with self.subTest(operation=operation.__name__, condition="unassociated-target"):
                self.assertEqual(operation(old, runtimeId="other", runtimeInstanceId="mock-instance:other").status_code, 404)
            with self.subTest(operation=operation.__name__, condition="different-project"):
                self.assertEqual(operation(old, projectId="other-project").status_code, 409)
            with self.subTest(operation=operation.__name__, condition="stale-instance"):
                self.assertEqual(operation(old, runtimeInstanceId="stale-instance").status_code, 409)
        for field in ("projectId", "runtimeInstanceId"):
            params = self.context_params()
            del params[field]
            with self.subTest(missing=field):
                self.assertEqual(self.client.get("/api/panels/" + old["packageId"], params=params).status_code, 404)
                self.assertEqual(self.client.post("/api/views", headers=self.headers,
                    json={**params, "panelId": old["packageId"], "runId": "demo-run"}).status_code, 404)

    def test_instance_change_invalidates_both_old_and_current_selection(self):
        old, new, _ = self.releases()
        self.app.state.runtime.instance_id = "restarted-instance"
        for package in (old, new):
            for operation in (self.read, self.view):
                with self.subTest(package=package["packageId"], operation=operation.__name__):
                    self.assertEqual(operation(package).status_code, 409)
        self.assertEqual(self.app.state.runtime.control_count, 1)

    def test_historical_recovery_rechecks_current_viewer_permission(self):
        old, _, _ = self.releases()
        actor = {"username": "admin", "role": "admin"}
        self.app.state.registry.set_acl(actor, "configured", "viewer", True, False)
        login = self.client.post("/api/session", json={"username": "viewer", "password": "safe-test-password"})
        self.headers = {"X-CSRF-Token": login.json()["csrf"]}
        self.assertEqual(self.read(old).status_code, 200)
        self.assertEqual(self.view(old).status_code, 200)
        self.app.state.registry.set_acl(actor, "configured", "viewer", False, False)
        self.assertEqual(self.read(old).status_code, 403)
        self.assertEqual(self.view(old).status_code, 403)
        self.assertEqual(self.app.state.runtime.control_count, 1)

    def test_unpublished_package_cannot_be_recovered_even_by_admin(self):
        old, _, _ = self.releases()
        # A valid imported package has never been enabled for this Runtime.
        from test_official_packages import official_bundle
        raw = official_bundle(lambda manifest, presentation, files: presentation["pages"]["live"].update(name="Never enabled"))
        imported = self.client.post("/api/packages", headers={**self.headers, "Content-Type": "application/zip"}, content=raw)
        self.assertEqual(imported.status_code, 200, imported.text)
        for operation in (self.read, self.view):
            self.assertEqual(operation(imported.json()).status_code, 404)
        self.assertEqual(self.read(old).status_code, 200)
