"""Bind one authenticated native receipt to the existing durable dispatch path.

This library does not schedule itself or grant transport permission. The protected
broker must supply API metadata and bytes from the same bounded artifact download.
"""
from hashlib import sha256
from io import BytesIO
import json
from zipfile import ZipFile

from dispatch_once import ApprovedRequest, DispatchClaims, reconcile_run, send_once
from trusted_route import PRODUCT, bind, exact, ident, need

MAX_ZIP = 32 * 1024
MAX_RECEIPT = 4096


def unique_pairs(pairs):
    value = {}
    for name, item in pairs:
        need(name not in value, "duplicate receipt key")
        value[name] = item
    return value


def receipt_json(artifact, archive, run):
    rid, attempt, aid = ident(run["id"]), ident(run["run_attempt"]), ident(artifact["id"])
    need(type(archive) is bytes and 0 < len(archive) <= MAX_ZIP, "receipt ZIP exceeds bound")
    digest = "sha256:" + sha256(archive).hexdigest()
    need(exact(artifact, {"name": f"ci-request-{rid}-{attempt}", "digest": digest,
                          "expired": False, "size_in_bytes": len(archive)})
         and exact(artifact.get("workflow_run"), {"id": rid, "repository_id": PRODUCT,
                   "head_repository_id": PRODUCT, "head_sha": run["head_sha"]}),
         "unbound receipt artifact")
    with ZipFile(BytesIO(archive)) as z:
        members = z.infolist()
        need(len(members) == 1, "ambiguous receipt members")
        m = members[0]
        mode = (m.external_attr >> 16) & 0o170000
        need(m.filename == "ci-request.json" and 0 < m.file_size <= MAX_RECEIPT
             and not m.flag_bits & 1 and mode in (0, 0o100000), "invalid receipt member")
        with z.open(m) as stream:
            raw = stream.read(MAX_RECEIPT + 1)
        need(len(raw) == m.file_size, "receipt byte mismatch")
    return json.loads(raw.decode("ascii"), object_pairs_hook=unique_pairs), aid, digest


def prepare(lane, run, artifact, archive, *, worker_path, actor, **route_checks):
    """Return a fully bound, canonical non-secret dispatch request. No writes."""
    record, artifact_id, digest = receipt_json(artifact, archive, run)
    bound = bind(lane, record, run, **route_checks)
    body = dict(schema=1, source_run=bound.run, source_attempt=bound.attempt,
                artifact_id=artifact_id, artifact_digest=digest, head=bound.head,
                base=bound.base, execution=bound.execute, pr=bound.pr, route=bound.route)
    body.update(bound.inputs)
    req = ApprovedRequest(bound.worker_workflow, worker_path,
                          bound.worker_ref.removeprefix("refs/tags/"),
                          bound.worker_sha, actor,
                          json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    req.identifier()
    return bound, req


def dispatch_or_read_back(gh, store, bound, req):
    """Reconcile a held identity without sending again; fail closed on uncertainty."""
    body = json.loads(req.wire)
    need((body["source_run"], body["source_attempt"], body["route"], body["head"],
          body["base"], body["execution"], body["pr"], req.workflow, req.worker_sha,
          "refs/tags/" + req.tag)
         == (bound.run, bound.attempt, bound.route, bound.head, bound.base, bound.execute,
             bound.pr, bound.worker_workflow, bound.worker_sha, bound.worker_ref),
         "dispatch differs from bound native event")
    claims = DispatchClaims(store, bound.check, bound.execute)
    key = req.identifier()
    if claims.held(key):
        return reconcile_run(gh, claims, req)
    return send_once(gh, claims, req)
