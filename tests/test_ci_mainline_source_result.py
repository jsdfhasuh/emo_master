from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.ci_mainline_source_result import CONTEXTS, PUBLIC, SLOTS, DispatchReceipt, VerifiedShard, decide
from scripts.ci_mainline_envelope import build
from tests.test_ci_mainline_envelope import fixture

WORKER = "f" * 40
TAG = "ci-worker-fixed"
NAMES = {slot: f"Source / {slot[0]} {slot[1]}" for slot in SLOTS}


def inputs(root, event="push", targetable=True):
    envelope = build(**fixture(event))
    sha = envelope["execution_sha"]
    receipt = DispatchReceipt(envelope["request_id"], sha, sha, 51, 9, WORKER, TAG)
    run = dict(id=51, workflow_id=9, head_sha=WORKER, head_branch=TAG, event="workflow_dispatch",
               run_attempt=1, status="completed", conclusion="success", repository={"id": PUBLIC},
               head_repository={"id": PUBLIC})
    jobs, evidence = [], []
    for index, slot in enumerate(SLOTS, 1):
        path = Path(root, "result-" + str(index) + ".json")
        selected = [slot[1]]
        if not targetable and index == 2:
            selected = ["runtime"]
        path.write_text(json.dumps(dict(shard=slot[1], exitstatus=0, collected=["runtime", "other"],
                         selected=selected, executed=selected)))
        jobs.append(dict(id=index, run_id=51, run_attempt=1, status="completed",
                         name=NAMES[slot], conclusion="success"))
        evidence.append(VerifiedShard(51, 1, index, index + 10, sha, WORKER, *slot, path))
    return dict(dispatched=envelope, current=dict(envelope), receipt=receipt, run=run, jobs=jobs,
                evidence=evidence, approved_workflow_id=9, approved_worker_sha=WORKER,
                approved_worker_tag=TAG, expected_names=NAMES)


class SourceVerdict(unittest.TestCase):
    def test_current_push_and_same_repository_pr_preserve_exact_original_contexts(self):
        for event in ("push", "pull_request", "workflow_dispatch"):
            with self.subTest(event=event), TemporaryDirectory() as root:
                args = inputs(root, event)
                check, outcomes = decide(**args)
                self.assertEqual(check, args["current"]["execution_sha"])
                self.assertEqual(outcomes, {name: "success" for name in CONTEXTS.values()})

    def test_valid_linux_verified_without_misreporting_windows_failure(self):
        with TemporaryDirectory() as root:
            args = inputs(root)
            args["jobs"][2]["conclusion"] = "failure"
            args["run"]["conclusion"] = "failure"
            _, outcomes = decide(**args)
            self.assertEqual(outcomes, {CONTEXTS["linux"]: "success", CONTEXTS["windows"]: "failure"})

    def test_pending_cannot_claim_success(self):
        with TemporaryDirectory() as root:
            args = inputs(root)
            args["run"].update(status="in_progress", conclusion=None)
            self.assertEqual(set(decide(**args)[1].values()), {"pending"})

    def test_stale_replayed_wrong_sha_or_missing_shard_never_returns_verdict(self):
        changes = (("current", "source_base_sha", "1" * 40),
                   ("run", "run_attempt", 2), ("run", "head_sha", "1" * 40),
                   ("run", "head_branch", "ci-worker-moved"))
        for name, key, value in changes:
            with TemporaryDirectory() as root:
                args = inputs(root)
                args[name][key] = value
                with self.assertRaises(ValueError):
                    decide(**args)
        for change in ("missing", "receipt", "duplicate"):
            with TemporaryDirectory() as root:
                args = inputs(root)
                if change == "missing":
                    args["evidence"].pop()
                if change == "receipt":
                    args["receipt"] = replace(args["receipt"], request_id="40-1")
                if change == "duplicate":
                    args["evidence"][1] = replace(args["evidence"][1], archive_id=args["evidence"][0].archive_id)
                with self.assertRaises(ValueError):
                    decide(**args)

    def test_invalid_original_partition_or_green_run_with_red_shard_is_rejected(self):
        with TemporaryDirectory() as root:
            args = inputs(root, targetable=False)
            with self.assertRaisesRegex(ValueError, "selected by both"):
                decide(**args)
        with TemporaryDirectory() as root:
            args = inputs(root)
            args["jobs"][-1]["conclusion"] = "skipped"
            with self.assertRaisesRegex(ValueError, "contradicts"):
                decide(**args)


class ShadowWrite(unittest.TestCase):
    def test_fresh_pending_to_terminal_and_exact_readback_are_idempotent(self):
        from scripts.ci_mainline_source_result import write_shadow
        with TemporaryDirectory() as root:
            args = inputs(root)
            posts = []
            def post(target, payload):
                posts.append((target, {**payload, "id": len(posts) + 1, "creator": {"id": 9}}))
                return posts[-1][1]
            def read(_target, _context):
                return posts[-1][1] if posts else None
            opts = dict(read_decision=lambda: decide(**args), read_status=read, post_status=post,
                        receipt=args["receipt"], platform="linux", writer_id=9)
            self.assertEqual(write_shadow(stage="pending", **opts), "pending")
            self.assertEqual(write_shadow(stage="terminal", **opts), "success")
            self.assertEqual(write_shadow(stage="terminal", **opts), "unchanged")
            self.assertEqual([item[1]["context"] for item in posts], ["dot/source-v2-shadow/linux"] * 2)

    def test_stale_decision_race_refuses_before_post(self):
        from scripts.ci_mainline_source_result import write_shadow
        with TemporaryDirectory() as root:
            args = inputs(root)
            outcomes = iter([decide(**args), ("e" * 40, decide(**args)[1])])
            with self.assertRaisesRegex(ValueError, "changed"):
                write_shadow(read_decision=lambda: next(outcomes), read_status=lambda *_: None,
                             post_status=lambda *_: self.fail("unexpected status write"), receipt=args["receipt"],
                             platform="linux", writer_id=9, stage="pending")
