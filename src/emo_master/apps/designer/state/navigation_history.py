"""Session-local editor locations, independent of graph edits and execution."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class NavigationLocation:
    session: str
    workflowId: str
    nodeId: str | None = None
    view: tuple[float, float, float] | None = None

    @property
    def key(self):
        return self.session, self.workflowId, self.nodeId


class NavigationHistory:
    def __init__(self, limit: int = 200):
        self.limit = limit
        self.entries: list[NavigationLocation] = []
        self.index = -1

    @property
    def current(self) -> NavigationLocation | None:
        return self.entries[self.index] if self.index >= 0 else None

    def reset(self, location: NavigationLocation):
        self.entries = [location]
        self.index = 0

    def capture(self, location: NavigationLocation):
        """Selection/pan/zoom update the current location, never add a step."""
        current = self.current
        if current and (current.session, current.workflowId) == (location.session, location.workflowId):
            self.entries[self.index] = location
        else:
            self.visit(location)

    def visit(self, location: NavigationLocation):
        if self.current and self.current.key == location.key:
            self.entries[self.index] = location
            return
        del self.entries[self.index + 1:]
        self.entries.append(location)
        self.entries = self.entries[-self.limit:]
        self.index = len(self.entries) - 1

    def destination(self, direction: int, valid: Callable[[NavigationLocation], bool]) -> int | None:
        if direction not in (-1, 1):
            raise ValueError('Navigation direction must be -1 or 1')
        index = self.index + direction
        while 0 <= index < len(self.entries):
            if valid(self.entries[index]):
                return index
            index += direction
        return None
