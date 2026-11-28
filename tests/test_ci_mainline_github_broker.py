import json
import unittest
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from scripts.ci_mainline_envelope import build
from scripts.ci_mainline_github_broker import PRODUCT_PATH, SourceBroker, WORKER_PATH
from scripts.ci_mainline_source_result import PUBLIC
from tests.test_ci_mainline_envelope import fixture
from tests.test_ci_mainline_source_result import inputs, NAMES, TAG as RESULT_TAG, WORKER as RESULT_SHA

SHA = "b" * 40
TAG = "ci-worker-canary"
FILE = ".github/workflows/product-ci-source.yml"


class Fake:
    def __init__(self):
        self.posts = []
        self.moved = False
        self.bad_artifact = False
        self.conflict = False

    def __call__(self, req):
        assert req.get_header("Authorization") == "Bearer fake_broker"
        path = urlsplit(req.full_url).path
        if req.method == "POST":
            self.posts.append((path, json.loads(req.data)))
            return (502 if self.conflict else 200), b'{"workflow_run_id":51}'
        answers = {
            PRODUCT_PATH + "/actions/artifacts/12": dict(id=12, name="ci-request-40-1" if self.bad_artifact else "ci-request-40-2",
                expired=False, workflow_run={"id": 40}, digest="sha256:"+"a"*64),
            WORKER_PATH: dict(id=PUBLIC, full_name="jsdfhasuh/emo_master_ci"),
            WORKER_PATH + "/git/ref/tags/" + TAG: dict(ref="refs/tags/"+TAG,
                object=dict(type="commit", sha="1"*40 if self.moved else SHA)),
            WORKER_PATH + "/actions/workflows/7": dict(id=7, path=FILE, state="active"),
            WORKER_PATH + "/contents/" + FILE: dict(path=FILE, type="file"),
            WORKER_PATH + "/actions/runs/51/attempts/1/jobs": {"total_count": 1, "jobs": [{"id": 1}]},
        }
        return 200, json.dumps(answers[path]).encode()


def adapter(fake):
    return SourceBroker("fake_broker", workflow_id=7, worker_sha=SHA, worker_tag=TAG,
                        worker_file="product-ci-source.yml", writer_id=8, transport=fake)


class BrokerTest(unittest.TestCase):
    def test_exact_approved_dispatch_sends_two_nonsecret_inputs(self):
        fake = Fake()
        current = build(**fixture("push"))
        receipt = adapter(fake).dispatch(bound=current, read_current=lambda: current, artifact_id=12)
        self.assertEqual(receipt.worker_run_id, 51)
        self.assertEqual(fake.posts, [(WORKER_PATH + "/actions/workflows/7/dispatches", {
            "ref": TAG, "inputs": {"dispatcher_run_id": "40", "request_artifact_id": "12"}})])

    def test_stale_wrong_artifact_or_moved_tag_never_posts(self):
        current = build(**fixture("push"))
        for kind in ("stale", "moved", "artifact"):
            fake = Fake()
            fake.moved = kind == "moved"
            fake.bad_artifact = kind == "artifact"
            observed = {**current, "request_id": "40-3"} if kind == "stale" else current
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                adapter(fake).dispatch(bound=current, read_current=lambda: observed, artifact_id=12)
            self.assertEqual(fake.posts, [])

    def test_uncertain_post_is_never_retried(self):
        fake = Fake()
        fake.conflict = True
        current = build(**fixture("push"))
        with self.assertRaisesRegex(ValueError, "do not blindly repeat"):
            adapter(fake).dispatch(bound=current, read_current=lambda: current, artifact_id=12)
        self.assertEqual(len(fake.posts), 1)

    def test_attempt_jobs_and_required_context_are_never_overwritten(self):
        fake = Fake()
        self.assertEqual(adapter(fake)._jobs(51), [{"id": 1, "run_attempt": 1}])
        with self.assertRaisesRegex(ValueError, "non-required"):
            adapter(fake)._post_shadow(SHA, {"context": "ubuntu-latest / Python 3.10"})
        self.assertEqual(fake.posts, [])

    def test_status_history_pagination_finds_newest_marker_or_fails_closed(self):
        context = "dot/source-v2-shadow/linux"
        first = [{"id": i, "context": "other"} for i in range(200, 100, -1)]
        target = {"id": 100, "context": context.upper()}
        for second, expected in (([target], target), ([], None), ([first[-1], target], ValueError)):
            queries = []
            def simulate(req):
                queries.append(urlsplit(req.full_url).query)
                return 200, json.dumps(first if len(queries) == 1 else second).encode()
            with self.subTest(second=second):
                if expected is ValueError:
                    with self.assertRaisesRegex(ValueError, "changed during pagination"):
                        adapter(simulate)._status(SHA, context)
                else:
                    self.assertEqual(adapter(simulate)._status(SHA, context), expected)
                self.assertEqual(queries, ["per_page=100&page=1", "per_page=100&page=2"])

    def test_status_history_budget_never_claims_absence(self):
        pages = []
        def simulate(req):
            start = len(pages) * 100 + 1
            pages.append(req.full_url)
            return 200, json.dumps([{"id": i, "context": "other"} for i in range(start, start+100)]).encode()
        with self.assertRaisesRegex(ValueError, "complete status history"):
            adapter(simulate)._status(SHA, "dot/source-v2-shadow/linux")
        self.assertEqual(len(pages), 20)

    def test_report_live_api_shapes_via_fake_transport_binds_only_shadow_context(self):
        from dataclasses import replace
        with TemporaryDirectory() as root:
            staged = inputs(root)
            current = staged["current"]
            receipt = replace(staged["receipt"], worker_workflow_id=7)
            records, posts = [], []
            work = {**staged["run"], "path": FILE, "workflow_id": 7}
            def simulate(req):
                assert req.get_header("Authorization") == "Bearer fake_broker"
                path = urlsplit(req.full_url).path
                if path == "/user":
                    value = dict(id=8)
                elif path == WORKER_PATH + "/actions/runs/51":
                    value = work
                elif path == WORKER_PATH + "/actions/runs/51/attempts/1/jobs":
                    value = dict(total_count=len(staged["jobs"]), jobs=staged["jobs"])
                elif path == f'{PRODUCT_PATH}/commits/{current["check_sha"]}/statuses':
                    value = records
                elif path == f'{PRODUCT_PATH}/statuses/{current["check_sha"]}':
                    value = {**json.loads(req.data), "id": len(posts) + 20, "creator": {"id": 8}}
                    posts.append(value)
                    records.insert(0, value)
                    return 201, json.dumps(value).encode()
                else:
                    self.fail(path)
                return 200, json.dumps(value).encode()

            broker = SourceBroker("fake_broker", workflow_id=7, worker_sha=RESULT_SHA,
                                  worker_tag=RESULT_TAG, worker_file="product-ci-source.yml",
                                  writer_id=8, transport=simulate)
            opts = dict(dispatched=current, read_current=lambda: current, receipt=receipt,
                        verified_evidence=lambda _receipt, _jobs: staged["evidence"], expected_names=NAMES,
                        platform="linux")
            self.assertEqual(broker.report_shadow(stage="pending", **opts), "pending")
            self.assertEqual(broker.report_shadow(stage="terminal", **opts), "success")
            self.assertEqual(broker.report_shadow(stage="terminal", **opts), "unchanged")
            self.assertEqual([(row["state"], row["context"]) for row in posts],
                             [("pending", "dot/source-v2-shadow/linux"), ("success", "dot/source-v2-shadow/linux")])
