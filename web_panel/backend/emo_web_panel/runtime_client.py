"""Transport port. No invented production RPC schema or silent mock fallback."""
from typing import Protocol

class ContractUnavailable(RuntimeError):
    pass

class RuntimePort(Protocol):
    async def info(self) -> dict: ...
    async def runs(self) -> list: ...
    async def snapshot(self, run_id: str) -> dict: ...

class GrpcRuntimeClient:
    def __init__(self, address):
        self.address = address
        self.channel = None
    async def start(self):
        # Must be invoked within the ASGI lifespan's running event loop.
        import grpc
        self.channel = grpc.aio.insecure_channel(self.address)
    async def close(self):
        if self.channel: await self.channel.close()
    async def info(self):
        raise ContractUnavailable("Official emo_panel_protocol SDK/manifest not supplied. Real RPC adapter is blocked; no mock fallback.")
    async def runs(self): return await self.info()
    async def workflows(self): return await self.info()
    async def snapshot(self, run_id): return await self.info()
    async def control(self, action, target, body): return await self.info()
    async def history(self, component_id, page, page_size, **query): return await self.info()
