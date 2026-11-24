"""Bind a product dispatch request to trusted GitHub event metadata."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


PRODUCT_ID = 1343796337
PRODUCT = "jsdfhasuh/emo_master"
MAIN = "refs/heads/main"
MAX_EVENT = 32 * 1024 * 1024
MAX_RESPONSE = 8 * 1024 * 1024
SHA = re.compile(r"[0-9a-f]{40}")
ID = re.compile(r"[1-9][0-9]{0,19}")


class EnvelopeError(ValueError):
    pass


def sha(value, *, zero=False):
    if not isinstance(value, str) or not SHA.fullmatch(value) or (value == "0" * 40 and not zero):
        raise EnvelopeError("invalid commit identity")
    return value


def id_string(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise EnvelopeError("invalid run or attempt")
    return value


def product_repo(value):
    return isinstance(value, dict) and value.get("id") == PRODUCT_ID and value.get("full_name") == PRODUCT


def map_value(value):
    if not isinstance(value, dict):
        raise EnvelopeError("invalid event metadata")
    return value


def positive_number(value):
    if type(value) is not int or value <= 0:
        raise EnvelopeError("invalid pull request number")
    return value


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args):
        return None


def read_api(path, token):
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_]+", token):
        raise EnvelopeError("missing trusted GitHub reader")
    request = Request("https://api.github.com/repos/" + PRODUCT + path, headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
    })
    try:
        with build_opener(NoRedirect()).open(request, timeout=20) as reply:
            data = reply.read(MAX_RESPONSE + 1)
        if len(data) > MAX_RESPONSE:
            raise EnvelopeError("GitHub metadata too large")
        return map_value(json.loads(data))
    except HTTPError as error:
        error.close()
        raise EnvelopeError(f"GitHub metadata unavailable (HTTP {error.code})") from None
    except (URLError, OSError, UnicodeError, json.JSONDecodeError):
        raise EnvelopeError("GitHub metadata unavailable") from None


def _pr_identity(pr, number):
    if pr.get("number") != number or pr.get("state") != "open":
        raise EnvelopeError("pull request is no longer open")
    head, base = map_value(pr.get("head")), map_value(pr.get("base"))
    if not product_repo(head.get("repo")) or not product_repo(base.get("repo")) or base.get("ref") != "main":
        raise EnvelopeError("pull request is not same-repository main")
    return sha(head.get("sha")), sha(base.get("sha"))


def _current_main(reader, expected):
    ref = reader("/git/ref/heads/main")
    target = map_value(ref.get("object"))
    if ref.get("ref") != MAIN or target.get("type") != "commit" or sha(target.get("sha")) != expected:
        raise EnvelopeError("main moved since the triggering event")


def build_envelope(payload, *, repository_id, event, ref, workflow_commit, run, attempt,
                   worker_sha, packager_sha, read, route):
    if repository_id != str(PRODUCT_ID) or not product_repo(payload.get("repository")) or ref != MAIN:
        raise EnvelopeError("not the authorized product main workflow")
    event_sha = sha(workflow_commit)
    request = {
        "schema_version": 1,
        "request_id": id_string(run) + "-" + id_string(attempt),
        "source_repository_id": PRODUCT_ID,
        "source_event_name": event,
        "source_ref": MAIN,
        "source_head_sha": event_sha,
        "source_base_sha": None,
        "source_before_sha": None,
        "source_merge_sha": None,
        "check_sha": event_sha,
        "execution_sha": event_sha,  # Source suite; packager resolves its own original input semantics.
        "source_pr_number": None,
        "source_tag": None,
        "ci_workflow_sha": sha(worker_sha),
        "packager_sha": sha(packager_sha),
        "suites": [],
        "manual_inputs": {},
    }
    requested = None
    if event == "push":
        if (payload.get("ref") != MAIN or payload.get("deleted") is not False
                or sha(payload.get("after")) != event_sha):
            raise EnvelopeError("push identity does not match main")
        before = sha(payload.get("before"), zero=True)
        request["source_before_sha"] = None if before == "0" * 40 else before
        _current_main(read, event_sha)
    elif event == "pull_request_target":
        if payload.get("action") not in {"opened", "synchronize", "reopened", "ready_for_review"}:
            raise EnvelopeError("unsupported pull request action")
        number = positive_number(payload.get("number"))
        original = map_value(payload.get("pull_request"))
        head, base = _pr_identity(original, number)
        if base != event_sha:
            raise EnvelopeError("PR event does not match trusted target checkout")
        fresh = read(f"/pulls/{number}")
        if _pr_identity(fresh, number) != (head, base):
            raise EnvelopeError("pull request changed after the triggering event")
        merge = sha(fresh.get("merge_commit_sha"))
        commit = read("/git/commits/" + merge)
        parents = commit.get("parents")
        if (commit.get("sha") != merge or not isinstance(parents, list) or len(parents) != 2
                or tuple(sha(map_value(p).get("sha")) for p in parents) != (base, head)):
            raise EnvelopeError("unverified synthetic pull request merge")
        request.update(source_event_name="pull_request", source_head_sha=head, source_base_sha=base,
                       source_merge_sha=merge, check_sha=merge, execution_sha=merge, source_pr_number=number)
    elif event == "workflow_dispatch":
        if payload.get("ref") not in ("main", MAIN):
            raise EnvelopeError("manual dispatch must originate from main")
        inputs = map_value(payload.get("inputs"))
        if set(inputs) - {"suite", "source_ref", "packager_ref"}:
            raise EnvelopeError("unsupported manual inputs")
        requested = inputs.get("suite")
        if not isinstance(requested, str) or not re.fullmatch(r"[a-z0-9-]+", requested) or requested == "release-windows":
            raise EnvelopeError("unsupported nonrelease manual suite")
        if inputs.get("source_ref") not in (None, "", "main", MAIN, event_sha):
            raise EnvelopeError("manual source ref does not match the triggering commit")
        if inputs.get("packager_ref") not in (None, "", packager_sha):
            raise EnvelopeError("manual packager ref is not approved")
        _current_main(read, event_sha)
        request["manual_inputs"] = {"suite": requested, "source_ref": event_sha, "packager_ref": packager_sha}
    else:
        raise EnvelopeError("unsupported product event")
    # Absence of a changed-path proof must not omit a matching legacy suite.
    request["suites"] = list(route(event=request["source_event_name"], ref=MAIN, requested=requested))
    return request


def main():
    from scripts.ci_route import load_trigger_table, route_verified_event

    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    env = os.environ
    try:
        source = Path(env["GITHUB_EVENT_PATH"])
        with source.open("rb") as data:
            raw = data.read(MAX_EVENT + 1)
        if len(raw) > MAX_EVENT:
            raise EnvelopeError("event too large")
        table = load_trigger_table(Path(".github/workflows"))
        row = build_envelope(map_value(json.loads(raw)),
            repository_id=env["GITHUB_REPOSITORY_ID"], event=env["GITHUB_EVENT_NAME"],
            ref=env["GITHUB_REF"], workflow_commit=env["GITHUB_SHA"], run=env["GITHUB_RUN_ID"],
            attempt=env["GITHUB_RUN_ATTEMPT"], worker_sha=env["EMO_CI_PUBLIC_WORKER_SHA"],
            packager_sha=env["EMO_CI_PACKAGER_SHA"],
            read=lambda path: read_api(path, env.get("EMO_CI_GITHUB_READER")),
            route=lambda **kwargs: route_verified_event(table, **kwargs))
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(row, output, ensure_ascii=True, separators=(",", ":"))
        if env.get("GITHUB_OUTPUT"):
            with Path(env["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as outputs:
                for key in ("request_id", "check_sha", "source_event_name"):
                    outputs.write(f"{key}={row[key]}\n")
        return 0
    except (KeyError, OSError, TypeError, ValueError):
        print("Product dispatch envelope could not be safely bound.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
