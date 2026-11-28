from dataclasses import replace
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
import json

from candidate_callback import settle
from dispatch_once import ApprovedRequest, DispatchClaims
from durable_claims import State, bind_key
from github_broker_transport import IDS, PUBLIC
from terminal_policy import Generation


class CallbackTest(TestCase):
    def setUp(self):
        raw = dict(schema=1, source_run=11, source_attempt=1, artifact_id=23,
                   artifact_digest="sha256:"+("f"*64), head="a"*40, base=None,
                   execution="a"*40, pr=None, route="windows-package")
        self.context = "emo-ci-shadow/designer-validation"
        self.req = ApprovedRequest(31, ".github/workflows/dispatch-windows-package-candidate.yml",
                                   "ci-worker-fixture", "b"*40, 41,
                                   json.dumps(raw, sort_keys=True, separators=(",", ":")))
        self.bound = SimpleNamespace(worker_workflow=31, worker_sha="b"*40,
                                     worker_ref="refs/tags/ci-worker-fixture",
                                     contexts=(self.context,), route=raw["route"],
                                     run=11, attempt=1, head=raw["head"], base=None,
                                     pr=None, execute=raw["execution"], check=raw["head"])
        key = self.req.identifier()
        slot = DispatchClaims(None, self.bound.check, self.bound.execute).slot(key)
        self.gen = Generation(key, self.bound.check, self.bound.execute, self.context, 1, 51, 1)
        self.store = Mock()
        self.rows = {slot: State(replace(self.gen, context=slot[1]), owner=key[:48]),
                     bind_key(self.gen): State(self.gen)}
        self.store.read.side_effect = lambda key: ("d"*40, self.rows.get(key))
        self.run = dict(id=51, repository={"id": IDS[PUBLIC]}, actor={"id": 41},
                        head_sha=self.req.worker_sha, head_branch=self.req.tag,
                        event="workflow_dispatch", workflow_id=31, run_attempt=1,
                        display_title="emo-ci-candidate-"+key, status="completed",
                        conclusion="success")
        self.gh = Mock()
        self.gh.read_run.return_value = self.run
        self.verify = Mock(return_value="success")
        self.origin = Mock(return_value=True)

    def go(self, *, now=1):
        return settle(self.gh, self.store, self.bound, self.req, self.context,
                      current_origin=self.origin, verify_v2=self.verify,
                      clock=lambda:now, deadline=5)

    @patch("candidate_callback.tick", return_value="sealed")
    def test_current_green_is_verified_and_bracketed(self, finish):
        self.assertEqual(self.go(), "sealed")
        self.verify.assert_called_once_with(self.bound, self.run)
        self.assertEqual(self.gh.read_run.call_count, 2)
        self.assertEqual(finish.call_args.args[-1].proof, "success")
        self.assertEqual(finish.call_args.args[-2], self.gen)

    @patch("candidate_callback.tick", return_value="wait")
    def test_cancellation_cannot_inherit_success_proof(self, finish):
        self.run["conclusion"] = "cancelled"
        self.go()
        self.verify.assert_not_called()
        self.assertEqual(finish.call_args.args[-1].conclusion, "cancelled")
        self.assertEqual(finish.call_args.args[-1].proof, "missing")

    @patch("candidate_callback.tick")
    def test_old_attempt_or_changed_origin_refuse_before_status_io(self, finish):
        self.run["run_attempt"] = 2
        with self.assertRaisesRegex(ValueError, "identity changed"):
            self.go()
        self.run["run_attempt"] = 1
        self.origin.side_effect = [True, False]
        with self.assertRaisesRegex(ValueError, "origin changed"):
            self.go()
        finish.assert_not_called()

    @patch("candidate_callback.tick")
    def test_success_to_other_result_during_proof_defers(self, finish):
        self.gh.read_run.side_effect = [self.run, dict(self.run, conclusion="failure")]
        self.assertEqual(self.go(), "worker_changed")
        finish.assert_not_called()
