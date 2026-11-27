"""Create a nonsecret receipt from a native GitHub Actions product event."""
from pathlib import Path
import json
import os
import re

REPO=1343796337
SHA=re.compile(r"[0-9a-f]{40}")
HEAD=re.compile(r"refs/heads/[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")
TAG=re.compile(r"refs/tags/v[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
OPTION=re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")
OPTIONS={"source_ref","packager_ref"}
MAX=8*1024*1024


def need(value):
    if not value:
        raise ValueError("unsafe or inconsistent product request")


def commit(value):
    need(isinstance(value,str) and SHA.fullmatch(value) and value!="0"*40)
    return value


def repo_id(obj):
    return isinstance(obj,dict) and type(obj.get("id")) is int and obj["id"]==REPO


def number(string):
    need(isinstance(string,str) and re.fullmatch(r"[1-9][0-9]{0,17}",string))
    return int(string)


def build(event,ref,revision,run,attempt,payload):
    revision=commit(revision)
    need(repo_id(payload.get("repository")))
    base=merge=number_pr=None
    options={}
    if event=="pull_request":
        need(payload.get("action") in ("opened","reopened","synchronize"))
        number_pr=payload.get("number")
        need(type(number_pr) is int and 0<number_pr<2**63 and ref==f"refs/pull/{number_pr}/merge")
        pr=payload["pull_request"]
        need(repo_id(pr["head"].get("repo")) and repo_id(pr["base"].get("repo")))
        head,base,merge=commit(pr["head"]["sha"]),commit(pr["base"]["sha"]),revision
    elif event=="push":
        need((HEAD.fullmatch(ref) or TAG.fullmatch(ref)) and payload.get("ref")==ref and
             payload.get("deleted") is False and commit(payload.get("after"))==revision)
        head=revision
    elif event=="workflow_dispatch":
        need(HEAD.fullmatch(ref) and payload.get("ref") in (ref,ref.removeprefix("refs/heads/")))
        raw=payload.get("inputs") or {}
        need(type(raw) is dict and (not raw or set(raw)==OPTIONS))
        need(all(type(v) is str and OPTION.fullmatch(v) and ".." not in v and "//" not in v
                 and not v.endswith(("/",".",".lock")) for v in raw.values()))
        options=dict(raw)
        head=revision
    else:
        raise ValueError("unsupported event")
    return dict(event=event,ref=ref,head=head,base=base,merge=merge,pr=number_pr,
                run=number(run),attempt=number(attempt),repo=REPO,inputs=options)


def main():
    try:
        env=os.environ
        need(env["GITHUB_REPOSITORY_ID"]==str(REPO))
        with Path(env["GITHUB_EVENT_PATH"]).open("rb") as handle:
            body=handle.read(MAX+1)
        need(len(body)<=MAX)
        msg=build(env["GITHUB_EVENT_NAME"],env["GITHUB_REF"],env["GITHUB_SHA"],
                  env["GITHUB_RUN_ID"],env["GITHUB_RUN_ATTEMPT"],json.loads(body))
        wire=json.dumps(msg,ensure_ascii=True,separators=(",",":"),sort_keys=True)
        need(len(wire)<=4096)
        with (Path(env["RUNNER_TEMP"])/"ci-request.json").open("x",encoding="ascii") as handle:
            handle.write(wire+"\n")
    except Exception:
        raise SystemExit("ci_cutover_request=refused") from None


if __name__=="__main__":
    main()
