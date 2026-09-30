"""Qt-free, immutable envelopes for atomic display reads.

Images share immutable byte-backed numpy storage; envelopes never expose session
dictionaries. Consumers must bound the snapshots they retain.
"""
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from emo_master.core.presentation.results import ClosedResult


@dataclass(frozen=True)
class ScopeView:
    result: ClosedResult
    images: Mapping[str, Any]
    failures: Mapping[str, str]
    readyNs: int
    imageStates: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class SessionView:
    revision: int
    generation: int
    runtimeInstanceId: str
    jobId: str
    connection: str
    detail: str
    scopes: Mapping[str, ScopeView]
    loading: Mapping[str, ClosedResult]
    started: Mapping[str, int]
    expiredScopes: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
