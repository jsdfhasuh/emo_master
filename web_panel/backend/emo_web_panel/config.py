from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class Config:
    data_dir: Path
    mode: str = "mock"
    host: str = "127.0.0.1"
    port: int = 8780
    runtime_address: str | None = None
    secure_cookie: bool = False
    session_ttl: int = 3600
    max_views: int = 32
    freeze_ttl: int = 120
    allowed_origins: tuple[str, ...] = ()
    preview: bool = False
    def __post_init__(self):
        if self.mode not in {"mock", "grpc"}:
            raise ValueError("mode must be explicitly mock or grpc")
        if self.mode == "grpc" and not self.runtime_address:
            raise ValueError("grpc mode needs configured runtime address")
        if self.host not in {"127.0.0.1", "localhost", "::1"} and not self.allowed_origins:
            raise ValueError("non-loopback requires explicit allowed origins")
