"""Metadata dispatcher tests use no real credentials or GitHub calls."""
import json
from pathlib import Path
import unittest

from scripts.ci_dispatch_envelope import EnvelopeError, build_envelope

PRODUCT = {"id": 1343796337, "full_name": "jsdfhasuh/emo_master"}
HEAD, BASE, MERGE, WORKER, PACKAGER = (ch * 40 for ch in "abcde")
MAIN = "refs/heads/main"


def route(*, event, ref, requested):
    assert ref == MAIN
    return (requested or "ci",)


def reader(path):
    if path == "/git/ref/heads/main":
        return {"ref": MAIN, "object": {"type": "commit", "sha": HEAD}}
    raise AssertionError("unexpected API path")


def envelope(payload, event="push", read=reader, checkout=HEAD):
    return build_envelope({"repository": PRODUCT, **payload},
        repository_id=str(PRODUCT["id"]), event=event, ref=MAIN, workflow_commit=checkout,
        run="42", attempt="1", worker_sha=WORKER, packager_sha=PACKAGER, read=read, route=route)


def pull():
    return {"number": 5, "state": "open", "head": {"repo": PRODUCT, "sha": HEAD},
            "base": {"repo": PRODUCT, "ref": "main", "sha": BASE}, "merge_commit_sha": MERGE}


class EnvelopeTests(unittest.TestCase):
    def test_main_push(self):
        row = envelope(dict(ref=MAIN, after=HEAD, before=BASE, deleted=False))
        self.assertEqual(row["request_id"], "42-1")
        self.assertEqual(row["source_event_name"], "push")
        self.assertEqual(row["source_head_sha"], row["check_sha"])
        self.assertEqual(row["execution_sha"], HEAD)
        self.assertEqual(row["source_before_sha"], BASE)
        self.assertEqual(row["suites"], ["ci"])
        self.assertNotIn("token", json.dumps(row))

    def test_moved_main_does_not_dispatch_stale_push(self):
        with self.assertRaisesRegex(EnvelopeError, "main moved"):
            envelope(dict(ref=MAIN, after=HEAD, before=BASE, deleted=False),
                     read=lambda _path: {"ref": MAIN, "object": {"type": "commit", "sha": BASE}})

    def test_open_same_repository_pr_verified_merge(self):
        def read(path):
            if path == "/pulls/5":
                return pull()
            if path == "/git/commits/" + MERGE:
                return {"sha": MERGE, "parents": [{"sha": BASE}, {"sha": HEAD}]}
            raise AssertionError("unexpected path")
        row = envelope(dict(action="synchronize", number=5, pull_request=pull()),
                       "pull_request_target", read, BASE)
        self.assertEqual(row["source_event_name"], "pull_request")
        self.assertEqual((row["source_head_sha"], row["source_base_sha"], row["source_merge_sha"], row["check_sha"]),
                         (HEAD, BASE, MERGE, MERGE))
        self.assertEqual(row["source_pr_number"], 5)
        self.assertEqual(row["execution_sha"], MERGE)

    def test_fork_rejected_before_read(self):
        pr = pull()
        pr["head"]["repo"] = {"id": 123, "full_name": "someone/emo_master"}
        with self.assertRaisesRegex(EnvelopeError, "not same-repository"):
            envelope(dict(action="opened", number=5, pull_request=pr), "pull_request_target", checkout=BASE)

    def test_unverified_or_wrong_merge_cannot_be_request(self):
        payload = dict(action="opened", number=5, pull_request=pull())
        def wrong(path):
            if path == "/pulls/5":
                return pull()
            return {"sha": MERGE, "parents": [{"sha": HEAD}, {"sha": BASE}]}
        with self.assertRaisesRegex(EnvelopeError, "unverified synthetic"):
            envelope(payload, "pull_request_target", wrong, BASE)

    def test_manual_supports_only_fixed_main_and_approved_packager(self):
        row = envelope(dict(ref="main", inputs={"suite": "ci", "packager_ref": PACKAGER}), "workflow_dispatch")
        self.assertEqual(row["manual_inputs"], {"suite": "ci", "source_ref": HEAD, "packager_ref": PACKAGER})
        for inputs in ({"suite": "release-windows"}, {"suite": "ci", "source_ref": BASE},
                       {"suite": "ci", "packager_ref": BASE}, {"suite": "ci", "unsupported": "x"}):
            with self.subTest(inputs=inputs), self.assertRaises(EnvelopeError):
                envelope(dict(ref="main", inputs=inputs), "workflow_dispatch")

    def test_workflow_has_no_product_checkout_or_secret_for_untrusted_fork(self):
        import yaml
        doc = yaml.load(Path(".github/workflows/public-ci-dispatch.yml").read_text(), Loader=yaml.BaseLoader)
        envelope_job = doc["jobs"]["envelope"]
        self.assertIn("vars.EMO_CI_DISPATCH_ENABLED == 'true'", envelope_job["if"])
        self.assertIn("github.event.pull_request.head.repo.id == github.event.repository.id", envelope_job["if"])
        for job in doc["jobs"].values():
            checkout = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@"))
            self.assertEqual(checkout["with"]["ref"], "${{ vars.EMO_CI_PRODUCT_DISPATCHER_SHA }}")
            self.assertEqual(checkout["with"]["persist-credentials"], "false")
        self.assertNotIn("secrets.", json.dumps(envelope_job))
        self.assertEqual(doc["jobs"]["kickoff"]["needs"], "envelope")


if __name__ == "__main__":
    unittest.main()
