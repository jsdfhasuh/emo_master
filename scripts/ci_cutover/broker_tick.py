"""One private broker tick. Ledger operations must be durable CAS, never TTL leases."""
from terminal_policy import terminal, delivery


def tick(ledger, statuses, key, expected, observation, *, now, deadline):
    """An injected ledger serializes the status key independently of generation.

    acquire returns an exclusive context-managed guard (or None, never steals).
    guard.snapshot() -> object with generation, decision, phase, tries.
    phase: idle, inflight, uncertain, sealed, blocked. claim increments tries and
    sets inflight durably *before* sending. All guard writes durable before return.
    statuses.read(expected, decision) classifies latest exact authenticated status.
    statuses.post returns no proof of application; even a 201 needs readback.
    """
    guard = ledger.acquire(key)
    if guard is None:
        return 'busy'
    with guard:
        s = guard.snapshot()
        if s.phase in ('sealed', 'blocked'):
            return s.phase
        if s.generation != expected:
            return 'superseded'
        decision = s.decision
        if decision is None:
            action, reason = terminal(expected, s.generation, observation, now=now, deadline=deadline)
            if action in ('ignore', 'wait'):
                return action
            decision = (action, reason)
            guard.freeze(decision)
        receipt = statuses.read(expected, decision)
        if receipt == 'exact':
            guard.seal()
            return 'sealed'
        if s.phase in ('inflight', 'uncertain'):
            # A crash or unknown network outcome may still apply later.
            guard.uncertain()
            return 'uncertain'
        plan = delivery(expected, s.generation, receipt=receipt, desired=decision[0], tries=s.tries)
        if plan != 'claim_then_post':
            guard.block()
            return 'blocked'
        guard.claim()
        try:
            statuses.post(expected, decision)
        except DefiniteRejection:
            # Only inject this for trusted definitive non-application.
            guard.release_claim()
            return 'retry'
        except Exception:
            guard.uncertain()
        if statuses.read(expected, decision) == 'exact':
            guard.seal()
            return 'sealed'
        guard.uncertain()
        return 'uncertain'


class DefiniteRejection(Exception):
    """Transport proved request was not and cannot be applied."""
