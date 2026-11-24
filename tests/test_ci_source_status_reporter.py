import copy
from email.message import Message
import hashlib
import io
import json
import unittest
from urllib.error import HTTPError
from zipfile import ZipFile, ZipInfo

from scripts.ci_shadow_verdict import EvidenceProof
from scripts.ci_source_status_reporter import (
    MAIN, PRODUCT, PRODUCT_ID, PRODUCT_WORKFLOW, PUBLIC, PUBLIC_ID, WORKER_WORKFLOW, Pins,
    Selection, Withheld, download_dispatcher_artifact, report,
)

SRC, DEFINITION, CONTROL, WORKER = (c * 40 for c in "abcd")
NAMES = ("linux runtime", "linux other", "windows runtime", "windows other")
PINS = Pins(13, DEFINITION, CONTROL, 17, WORKER, "ci-worker-pinned",
            "dot/source-shadow", 42, NAMES)
SELECTION = Selection(SRC, 11, 1, 31, 32)


class Fake:
    def __init__(self):
        self.posts = []
        self.status_rows = []
        self.main_reads = 0
        self.ref_tip = SRC
        self.ref_race = False
        self.status_reads = 0
        self.status_race = False
        self.public_attempt = 1
        self.worker_outcome = None
        self.worker_status = "in_progress"
        self.partition = True
        self.missing_proof = False
        self.ambiguous_dispatch = False
        self.replayed_receipt = False
        self.blob = DEFINITION
        self.worker_sha = WORKER
        self.digest_race = False
        self.archive_cache = {}
        self.job_rows = [dict(id=i, run_id=19, run_attempt=1, name=name, status="completed", conclusion="success")
                         for i, name in enumerate(NAMES, 70)]

    def archive(self, aid):
        if aid == 31:
            member = "request.json"
            fields = dict(schema_version=1, request_id="11-1", source_repository_id=PRODUCT_ID,
                          source_event_name="push", source_ref=MAIN, source_head_sha=SRC,
                          check_sha=SRC, execution_sha=SRC, ci_workflow_sha=WORKER, suites=["ci"])
        else:
            member = "dispatch-receipt.json"
            fields = dict(request_id="11-0" if self.replayed_receipt else "11-1",
                          product_repository_id=PRODUCT_ID, product_dispatch_run_id=11,
                          product_dispatch_attempt=1, product_workflow_sha=SRC,
                          control_commit_sha=CONTROL, request_artifact_id=31,
                          request_artifact_sha256=hashlib.sha256(self.archive(31)).hexdigest(),
                          check_sha=SRC, source_event_name="push", public_repository_id=PUBLIC_ID,
                          public_workflow_id=17, public_workflow_sha=WORKER, public_run_id=19,
                          public_run_url="https://github.com/jsdfhasuh/emo_master_ci/actions/runs/19")
        buf = io.BytesIO()
        with ZipFile(buf, "w") as arc:
            arc.writestr(ZipInfo(member), json.dumps(fields))
        return buf.getvalue()

    def get(self, path):
        dispatcher = dict(id=11, workflow_id=13, path=PRODUCT_WORKFLOW, event="push", head_branch="main",
                          head_sha=SRC, run_attempt=1, status="completed", conclusion="success",
                          repository=dict(id=PRODUCT_ID), head_repository=dict(id=PRODUCT_ID))
        if path == PRODUCT + "/git/ref/heads/main":
            self.main_reads += 1
            return dict(ref=MAIN, object=dict(type="commit",
                        sha="d" * 40 if self.ref_race and self.main_reads > 1 else self.ref_tip))
        if path == PRODUCT + "/actions/runs/11":
            return dispatcher
        if path.startswith(PRODUCT + "/actions/workflows/13/runs?"):
            return dict(total_count=2 if self.ambiguous_dispatch else 1, workflow_runs=[dispatcher])
        if path == f"{PRODUCT}/contents/{PRODUCT_WORKFLOW}?ref={SRC}":
            return dict(path=PRODUCT_WORKFLOW, type="file", sha=self.blob)
        if path.startswith(PRODUCT + "/actions/artifacts/"):
            aid = int(path.rsplit("/", 1)[1])
            data = self.archive(aid)
            self.archive_cache[aid] = data
            return dict(id=aid, name=("ci-request" if aid == 31 else "ci-dispatch-receipt") + "-11-1",
                        size_in_bytes=len(data), digest="sha256:" + hashlib.sha256(data).hexdigest(),
                        expired=False, workflow_run=dict(id=11))
        if path == PUBLIC + "/actions/runs/19":
            return dict(id=19, workflow_id=17, head_sha=self.worker_sha, run_attempt=self.public_attempt,
                        event="workflow_dispatch", path=WORKER_WORKFLOW, head_branch=PINS.worker_tag,
                        repository=dict(id=PUBLIC_ID), head_repository=dict(id=PUBLIC_ID),
                        status=self.worker_status, conclusion=self.worker_outcome)
        if path == f"{PRODUCT}/commits/{SRC}/status?per_page=100":
            self.status_reads += 1
            records = copy.deepcopy(self.status_rows)
            if self.status_race and self.status_reads == 2:
                records.append(dict(context=PINS.context, state="success", id=777))
            return dict(sha=SRC, total_count=len(records), statuses=records)
        raise AssertionError(path)

    def download(self, aid, size):
        data = self.archive_cache[aid]
        return data if not self.digest_race else b"`" * size

    def post(self, path, value):
        self.posts.append((path, dict(value)))
        row = {**value, "id": 800 + len(self.posts), "creator": {"id": 42}}
        self.status_rows[:] = [row]
        return row

    def read_jobs(self, run):
        assert run == 19
        return self.job_rows

    def collect(self, identity, jobs):
        assert identity.check_sha == SRC and identity.run_id == 19
        proofs = [EvidenceProof(19, 1, row["id"], 91 + i, "sha256:" + "a" * 64, 1024, SRC, WORKER)
                  for i, row in enumerate(jobs)]
        return proofs[:-1] if self.missing_proof else proofs, self.partition

    def report(self, phase="pending"):
        return report(get=self.get, post=self.post, download=self.download, read_jobs=self.read_jobs,
                      collect_verified_source=self.collect, selection=SELECTION, pins=PINS, phase=phase)


class ReporterTests(unittest.TestCase):
    def test_fresh_pending_wait_and_exact_proven_terminal_readback(self):
        fake = Fake()
        self.assertEqual(fake.report(), "pending")
        self.assertEqual(fake.report(), "unchanged")
        self.assertEqual(fake.report("terminal"), "wait")
        fake.worker_status, fake.worker_outcome = "completed", "success"
        self.assertEqual(fake.report("terminal"), "success")
        self.assertEqual(fake.report("terminal"), "unchanged")
        self.assertEqual([t[0] for t in fake.posts], [f"{PRODUCT}/statuses/{SRC}"] * 2)
        self.assertEqual([t[1]["state"] for t in fake.posts], ["pending", "success"])
        self.assertTrue(all(t[1]["description"].startswith("Source CI request 11-1:")
                            for t in fake.posts))

    def test_negative_run_is_failure_and_cannot_be_overwritten(self):
        fake = Fake()
        fake.report()
        fake.worker_status, fake.worker_outcome = "completed", "failure"
        self.assertEqual(fake.report("terminal"), "failure")
        fake.worker_outcome = "success"
        with self.assertRaises(Withheld):
            fake.report("terminal")
        self.assertEqual([t[1]["state"] for t in fake.posts], ["pending", "failure"])

    def test_missing_proof_partition_or_job_never_produces_success(self):
        for problem in (dict(missing_proof=True), dict(partition=False), dict(job_rows=[])):
            with self.subTest(problem=problem):
                fake = Fake()
                fake.report()
                vars(fake).update(problem)
                fake.worker_status, fake.worker_outcome = "completed", "success"
                self.assertEqual(fake.report("terminal"), "error")

    def test_stale_wrong_attempt_replay_or_compare_race_without_post(self):
        for problem in (dict(ref_tip="f" * 40), dict(ref_race=True), dict(public_attempt=2),
                        dict(replayed_receipt=True), dict(status_race=True), dict(worker_sha="f" * 40),
                        dict(ambiguous_dispatch=True), dict(blob="f" * 40), dict(digest_race=True)):
            with self.subTest(problem=problem):
                fake = Fake()
                vars(fake).update(problem)
                with self.assertRaises(Withheld):
                    fake.report()
                self.assertFalse(fake.posts)

    def test_foreign_existing_status_cannot_be_taken_over(self):
        fake = Fake()
        fake.report()
        fake.status_rows[0]["creator"]["id"] = 43
        fake.worker_status, fake.worker_outcome = "completed", "success"
        with self.assertRaisesRegex(Withheld, "another writer"):
            fake.report("terminal")
        self.assertEqual([item[1]["state"] for item in fake.posts], ["pending"])

    def test_terminal_cannot_create_a_fresh_context(self):
        fake = Fake()
        fake.worker_status, fake.worker_outcome = "completed", "success"
        with self.assertRaisesRegex(Withheld, "pending marker"):
            fake.report("terminal")
        self.assertFalse(fake.posts)

    def test_artifact_only_sends_credential_to_api_not_blob(self):
        calls = []
        blob = "https://example.blob.core.windows.net/evidence"
        headers = Message()
        headers["Location"] = blob

        class Reply(io.BytesIO):
            status = 200

        def open_url(request, timeout):
            calls.append((request.full_url, request.get_header("Authorization")))
            if request.full_url.endswith("/zip"):
                raise HTTPError(request.full_url, 302, "redirect", headers, None)
            return Reply(b"evidence")
        self.assertEqual(download_dispatcher_artifact(31, 8, "Bearer fake_test", open_url=open_url),
                         b"evidence")
        self.assertEqual(calls[1], (blob, None))
        headers.replace_header("Location", "https://evil.example/no")
        with self.assertRaises(Withheld):
            download_dispatcher_artifact(31, 8, "Bearer fake_test", open_url=open_url)
        self.assertEqual(len(calls), 3)
