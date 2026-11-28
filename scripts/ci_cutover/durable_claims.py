"""Lease-free claim interface over a protected compare-and-swap private state store."""
from dataclasses import dataclass, replace
from secrets import token_hex
from terminal_policy import Generation


class Conflict(ValueError):
    """Refuse without I/O when durable ownership cannot be proven."""


@dataclass(frozen=True)
class State:
    generation: Generation
    phase: str="idle"
    decision: tuple | None=None
    tries: int=0
    owner: str | None=None


def bind_key(g):
    if not g.valid():
        raise Conflict("invalid generation")
    return (g.check_sha,g.context)


class Ledger:
    """CAS is a genuine durable exact-revision adoption, never force update.

    Store.read(key) -> (opaque revision, State or None).
    Store.cas(key, expected revision, new State, unique candidate nonce)
       -> True exactly when that state was durably established and read back.
    A false result or transport exception never grants ownership.
    """
    def __init__(self,store):
        self.store=store

    def install(self,generation):
        key=bind_key(generation)
        rev,state=self.store.read(key)
        if state is not None:
            if state.owner is not None or state.phase in ("inflight","uncertain"):
                raise Conflict("unresolved active status writer")
            if state.generation.revision>=generation.revision:
                raise Conflict("generation does not advance")
        if not self.store.cas(key,rev,State(generation),token_hex(24)):
            raise Conflict("could not install new generation")
        return key

    def acquire(self,key):
        revision,current=self.store.read(key)
        if current is None or current.owner is not None:
            return None
        owner=token_hex(24)
        if not self.store.cas(key,revision,replace(current,owner=owner),token_hex(24)):
            return None
        return Guard(self.store,key,owner)


class Guard:
    def __init__(self,store,key,owner):
        self.store,self.key,self.owner=store,key,owner
    def __enter__(self):
        return self
    def _read(self):
        rev,s=self.store.read(self.key)
        if s is None or s.owner!=self.owner:
            raise Conflict("writer owner changed")
        return rev,s
    def snapshot(self):
        return self._read()[1]
    def _write(self,predicate,**values):
        rev,s=self._read()
        if not predicate(s) or not self.store.cas(self.key,rev,replace(s,**values),token_hex(24)):
            raise Conflict("state transition not confirmed")
    def freeze(self,value):
        self._write(lambda s:s.phase=="idle" and s.decision is None,decision=value)
    def claim(self):
        rev,s=self._read()
        self._write(lambda x:x==s and x.phase=="idle" and x.decision is not None,
                    phase="inflight",tries=s.tries+1)
    def release_claim(self):
        self._write(lambda s:s.phase=="inflight",phase="idle")
    def uncertain(self):
        self._write(lambda s:s.phase in ("inflight","uncertain"),phase="uncertain")
    def seal(self):
        self._write(lambda s:s.decision is not None and s.phase in ("idle","inflight","uncertain"),phase="sealed")
    def block(self):
        self._write(lambda s:s.phase=="idle" and s.decision is not None,phase="blocked")
    def __exit__(self,kind,exc,tb):
        rev,s=self._read()
        if not self.store.cas(self.key,rev,replace(s,owner=None,phase="uncertain" if s.phase=="inflight" else s.phase),token_hex(24)):
            raise Conflict("could not confirm safe writer release")
