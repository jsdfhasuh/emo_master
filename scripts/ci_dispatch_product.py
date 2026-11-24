"""Dispatch the approved worker and record its receipt; no status or release APIs."""
import argparse
import json
import os
from pathlib import Path
import re
import sys

from scripts.ci_control_client import ControlClient, ControlError
from scripts.ci_dispatch_envelope import PRODUCT_ID, id_string, sha


PUBLIC_ID = 1468905732
DIGEST = re.compile(r"(?:sha256:)?[0-9a-f]{64}")


def dispatch_receipt(env, make_client=ControlClient):
    if env["GITHUB_REPOSITORY_ID"] != str(PRODUCT_ID) or env["GITHUB_REF"] != "refs/heads/main":
        raise ControlError("wrong product dispatcher repository or protected ref")
    event = {"push": "push", "pull_request_target": "pull_request", "workflow_dispatch": "workflow_dispatch"}
    if event.get(env["GITHUB_EVENT_NAME"]) != env["SOURCE_EVENT"] or env["SOURCE_EVENT"] not in event.values():
        raise ControlError("wrong source event")
    run, attempt = id_string(env["GITHUB_RUN_ID"]), id_string(env["GITHUB_RUN_ATTEMPT"])
    if env["REQUEST_ID"] != run + "-" + attempt:
        raise ControlError("wrong request generation")
    artifact_id = int(id_string(env["REQUEST_ARTIFACT_ID"]))
    digest = env["REQUEST_ARTIFACT_DIGEST"]
    if not DIGEST.fullmatch(digest):
        raise ControlError("invalid request artifact digest")
    worker_sha = sha(env["EMO_CI_PUBLIC_WORKER_SHA"])
    workflow_id = int(id_string(env["EMO_CI_PUBLIC_WORKER_ID"]))
    control_sha = sha(env["EMO_CI_PRODUCT_DISPATCHER_SHA"])
    workflow_sha = sha(env["GITHUB_WORKFLOW_SHA"])
    check_sha = sha(env["CHECK_SHA"])
    client = make_client(env["EMO_CI_TOKEN"], public_repository_id=PUBLIC_ID,
                         worker_workflow_id=workflow_id, worker_sha=worker_sha,
                         status_contexts=("dot/dispatch-pilot",))
    public_run = client.dispatch(dispatcher_run_id=int(run), artifact_id=artifact_id,
                                 worker_tag=env["EMO_CI_PUBLIC_WORKER_TAG"])
    return {
        "request_id": env["REQUEST_ID"], "product_repository_id": PRODUCT_ID,
        "product_dispatch_run_id": int(run), "product_dispatch_attempt": int(attempt),
        "product_workflow_sha": workflow_sha, "control_commit_sha": control_sha,
        "request_artifact_id": artifact_id, "request_artifact_sha256": digest.removeprefix("sha256:"),
        "check_sha": check_sha, "source_event_name": env["SOURCE_EVENT"],
        "public_repository_id": PUBLIC_ID, "public_workflow_id": workflow_id,
        "public_workflow_sha": worker_sha,
        "public_run_id": public_run,
        "public_run_url": f"https://github.com/jsdfhasuh/emo_master_ci/actions/runs/{public_run}",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        # Prove the destination is writable before attempting the one-shot POST.
        with args.output.open("x", encoding="utf-8") as output:
            receipt = dispatch_receipt(os.environ)
            json.dump(receipt, output, ensure_ascii=True, separators=(",", ":"))
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as summary:
            summary.write(f"[Exact worker run]({receipt['public_run_url']}) (dispatch accepted; not a test result).\n")
        return 0
    except (ControlError, KeyError, OSError, ValueError):
        print("Worker dispatch unconfirmed. Check existing runs before retrying.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
