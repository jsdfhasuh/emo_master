"""Private single-writer broker policy. No network or ambient credentials."""
from dataclasses import dataclass
from typing import Optional
import re

@dataclass(frozen=True)
class Generation:
    # binding is the canonical digest of the complete authenticated envelope.
    binding: str
    check_sha: str
    execution_sha: str
    context: str
    revision: int
    worker_run: Optional[int]
    attempt: Optional[int]

    def valid(self):
        return (isinstance(self.binding,str) and re.fullmatch('[0-9a-f]{64}', self.binding)
                and all(isinstance(s,str) and re.fullmatch('[0-9a-f]{40}',s) and s!='0'*40
                        for s in (self.check_sha,self.execution_sha))
                and isinstance(self.context,str) and self.context.strip()
                and type(self.revision) is int and self.revision>0
                and ((self.worker_run is self.attempt is None) or all(type(i) is int and i>0 for i in (self.worker_run,self.attempt))))

@dataclass(frozen=True)
class Observation:
    generation: Generation
    conclusion: str        # independently read from authenticated GitHub run attempt
    proof: str             # trusted recipe verifier: missing, success, failure
    watchdog: bool=False   # private broker confirmed budget exhausted

def terminal(expected, current, observation, *, now, deadline):
    """Return (action,reason). A late observation may not reopen a sealed generation."""
    if not expected.valid() or not current.valid(): raise ValueError("invalid binding")
    if expected != current: return 'ignore','superseded'
    if any(type(i) is not int or i<0 for i in (now,deadline)): raise ValueError('clock')
    late=now>=deadline
    if not observation or expected.worker_run is None:
        return ('error','missing_receipt') if late else ('wait','await_receipt')
    if observation.generation!=expected: return ('error','matching_receipt_missing') if late else ('wait','foreign_receipt')
    c,p=observation.conclusion,observation.proof
    if observation.watchdog is True or c=='timed_out': return 'error','timeout'
    if c=='cancelled': return 'error','cancelled'
    if c=='source_failure': return 'error','source_failure'
    if c=='failure' or p=='failure': return 'failure','verified_failure'
    if c in ('skipped','stale','neutral','action_required','startup_failure'): return 'error',c
    if c=='success' and p=='success':
        return ('error','deadline_without_verified_result') if late else ('success','verified')
    if c not in ('success','in_progress','queued','waiting','pending','requested') or p not in ('missing','success'): return 'error','invalid_result'
    return ('error','deadline_without_verified_result') if late else ('wait','await_result')

def delivery(expected,current,*,receipt,desired,tries,limit=3):
    """receipt: exact / absent / own_pending / foreign / ambiguous (fresh API read).
    'exact' means actor ID, target URL, run, attempt, SHA, context AND state match.
    tries counts persisted send claims BEFORE I/O; never retry a dispatch here.
    """
    if not expected.valid() or not current.valid(): raise ValueError('binding')
    if current!=expected: return 'superseded'
    if type(tries) is not int or tries<0 or type(limit) is not int or not 1<=limit<=5: raise ValueError('tries')
    if desired not in ('error','failure','success'): raise ValueError('not terminal')
    if receipt=='exact': return 'sealed'
    if receipt not in ('absent','own_pending'): return 'escalate_no_write'
    return 'claim_then_post' if tries<limit else 'escalate_no_write'
