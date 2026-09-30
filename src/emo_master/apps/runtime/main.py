"""Default runtime transport with bounded control, events, and display readers."""
from pathlib import Path
import asyncio
import threading
from typing import Any

from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.presentation.service import PresentationService


def createRuntimeService() -> RuntimeService:
    return RuntimeService()


class RuntimeServer:
    """Keep the existing start/stop/wait API over the shared bounded aio adapter."""
    def __init__(self, service: RuntimeService, presentation: PresentationService,
                 host: str, port: int):
        self.transport = AioRuntimeServer(service, presentation, port=port,
                                          host=host, startImmediately=False)
        self.port = self.transport.port
        self.stopped = threading.Event()

    def start(self):
        if self.stopped.is_set():
            raise RuntimeError("runtime transport has already stopped")
        self.transport.start()

    def wait_for_termination(self, timeout=None):
        if self.stopped.is_set():
            return False
        return asyncio.run_coroutine_threadsafe(
            self.transport.server.wait_for_termination(timeout), self.transport.loop
        ).result(None if timeout is None else timeout + 2)

    def stop(self, grace):
        if not self.stopped.is_set():
            self.transport.close(grace=grace or 0)
            self.stopped.set()
        return self.stopped


def createRuntimeServer(
    host: str = "127.0.0.1", port: int = 50051, runtimeService: RuntimeService | None = None
) -> tuple[Any, int, RuntimeService]:
    service = runtimeService or createRuntimeService()
    presentation = getattr(service, "_presentationOwner", None)
    try:
        if presentation is None:
            presentation = PresentationService(service, Path(service.workspaceRoot).parent / (Path(service.workspaceRoot).name + "-presentation"))
        server = RuntimeServer(service, presentation, host, port)
    except BaseException:
        # No jobs are created by provisioning capabilities or read-only transport.
        service.close()
        raise
    return server, server.port, service


def runRuntime(host: str = "127.0.0.1", port: int = 50051) -> None:
    server, _boundPort, service = createRuntimeServer(host, port)
    server.start()
    try:
        server.wait_for_termination()
    finally:
        server.stop(0).wait()
        service.close()


if __name__ == "__main__":
    runRuntime()
