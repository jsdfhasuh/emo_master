"""Transport-independent, located authoring failures."""
from __future__ import annotations


class AuthoringError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.status_code = status
        self.details = details

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, **self.details}

    def to_dict(self) -> dict:
        return self.as_dict()
