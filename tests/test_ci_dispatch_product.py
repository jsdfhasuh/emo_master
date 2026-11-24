"""Dispatch receipt requires exact event/run evidence; never writes status."""
import unittest

from scripts.ci_control_client import ControlError
from scripts.ci_dispatch_product import dispatch_receipt


class DispatchReceiptTests(unittest.TestCase):
    def runner_env(self):
        return dict(GITHUB_REPOSITORY_ID="1343796337", GITHUB_REF="refs/heads/main",
                    GITHUB_EVENT_NAME="push", SOURCE_EVENT="push", GITHUB_RUN_ID="42",
                    GITHUB_RUN_ATTEMPT="2", REQUEST_ID="42-2", REQUEST_ARTIFACT_ID="45",
                    REQUEST_ARTIFACT_DIGEST="sha256:" + "1" * 64, EMO_CI_PUBLIC_WORKER_SHA="a" * 40,
                    EMO_CI_PUBLIC_WORKER_ID="46", EMO_CI_PRODUCT_DISPATCHER_SHA="2" * 40,
                    GITHUB_WORKFLOW_SHA="b" * 40, CHECK_SHA="c" * 40,
                    EMO_CI_TOKEN="synthetic_test_value", EMO_CI_PUBLIC_WORKER_TAG="ci-worker-test")

    def test_receipt_is_exact_and_only_dispatch_called(self):
        calls = []
        class Client:
            def __init__(self, _unused_test_token, **config):
                calls.append(config)
            def dispatch(self, **inputs):
                calls.append(inputs)
                return 47
        receipt = dispatch_receipt(self.runner_env(), Client)
        self.assertEqual(calls[0]["worker_workflow_id"], 46)
        self.assertEqual(calls[1], {"dispatcher_run_id": 42, "artifact_id": 45, "worker_tag": "ci-worker-test"})
        self.assertEqual(receipt["public_run_id"], 47)
        self.assertEqual(receipt["request_artifact_sha256"], "1" * 64)
        self.assertEqual(receipt["check_sha"], "c" * 40)
        self.assertNotIn("synthetic_test_value", str(receipt))

    def test_wrong_identity_prevents_client_creation(self):
        def not_called(*_args, **_kwargs):
            self.fail("must not create a network client")
        for changes in (dict(REQUEST_ID="42-1"), dict(SOURCE_EVENT="pull_request"),
                        dict(REQUEST_ARTIFACT_DIGEST="no"), dict(GITHUB_REF="refs/heads/other")):
            with self.subTest(changes=changes), self.assertRaises(ControlError):
                dispatch_receipt({**self.runner_env(), **changes}, not_called)
