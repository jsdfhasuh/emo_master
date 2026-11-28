"""Settle one authenticated shadow worker without replaying status writes."""
import json
import re

from broker_tick import tick
from dispatch_once import DispatchClaims, match_run
from durable_claims import Ledger, bind_key
from github_broker_transport import PUBLIC, StatusWriter, need
from terminal_policy import Generation, Observation

LIVE = frozenset(("queued", "waiting", "pending", "requested", "in_progress"))


def settle(gh, store, bound, request, context, *, current_origin, verify_v2,
           clock, deadline):
    """Use the durable reservation for the run ID; never accept an ID from a caller.

    current_origin revalidates the event against the reviewed lane without using
    the ingress freshness window. verify_v2 is private, route-specific and
    returns success, failure, or missing only after checking exact artifacts.
    """
    key = request.identifier()
    need(context in bound.contexts and re.fullmatch(r"emo-ci-shadow/[a-z][a-z0-9-]{0,63}", context),
         "unreviewed shadow status context")
    need(request.workflow == bound.worker_workflow and request.worker_sha == bound.worker_sha
         and "refs/tags/" + request.tag == bound.worker_ref,
         "worker policy differs from authenticated origin")
    body = json.loads(request.wire)
    need((body["source_run"], body["source_attempt"], body["route"], body["head"],
          body["base"], body["execution"], body["pr"])
         == (bound.run, bound.attempt, bound.route, bound.head, bound.base,
             bound.execute, bound.pr), "request differs from authenticated origin")
    claim = DispatchClaims(store, bound.check, bound.execute)
    slot = claim.slot(key)
    _, reserved = store.read(slot)
    need(reserved is not None and reserved.owner == key[:48],
         "no exact durable dispatch reservation")
    number = reserved.generation.worker_run
    need(number is not None and reserved.generation == Generation(
         key, bound.check, bound.execute, slot[1], 1, number, 1),
         "dispatch run not independently bound")
    generation = Generation(key, bound.check, bound.execute, context, 1, number, 1)
    target = bind_key(generation)
    _, state = store.read(target)
    need(state is not None and state.generation == generation,
         "terminal generation was not installed or was superseded")
    writer = StatusWriter(gh, bound.run)

    def live():
        run = gh.read_run(PUBLIC, number)
        need(run.get("id") == number and match_run(run, request, key),
             "worker identity changed")
        return run

    need(current_origin(bound), "origin no longer eligible")
    if state.phase == "sealed":
        need(state.decision is not None and writer.read(generation, state.decision) == "exact",
             "sealed ledger and independent status differ")
        return "sealed"

    run = live()
    status = run.get("status")
    proof = "missing"
    if status == "completed":
        result = run.get("conclusion")
        need(type(result) is str, "completed worker lacks conclusion")
        if result == "success" and state.decision is None and clock() < deadline:
            proof = verify_v2(bound, run)
            need(proof in ("success", "failure", "missing"), "unexpected V2 proof verdict")
    else:
        need(status in LIVE and run.get("conclusion") is None, "invalid live worker state")
        result = status

    # Do not use a result that changed during V2 download or allow a stale
    # origin to write. No successful verification can reopen a frozen decision.
    after = live()
    if (after.get("status"), after.get("conclusion")) != (status, run.get("conclusion")):
        return "worker_changed"
    need(current_origin(bound), "origin changed during verification")
    return tick(Ledger(store), writer, target, generation,
                Observation(generation, result, proof), now=clock(), deadline=deadline)
