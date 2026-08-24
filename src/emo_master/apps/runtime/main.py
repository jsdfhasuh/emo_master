from concurrent import futures
from typing import Any

import grpc

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService


def createRuntimeService() -> RuntimeService:
  return RuntimeService()


def createRuntimeServer(
    host: str = "127.0.0.1", port: int = 50051, runtimeService: RuntimeService | None = None
) -> tuple[Any, int, RuntimeService]:
  server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
  service = runtimeService or createRuntimeService()
  runtime_pb2_grpc.add_RuntimeServiceServicer_to_server(service, server)
  boundPort = server.add_insecure_port(f"{host}:{port}")
  if boundPort <= 0:
    service.close()
    raise RuntimeError(f"unable to bind runtime server to {host}:{port}")
  return server, boundPort, service


def runRuntime(host: str = "127.0.0.1", port: int = 50051) -> None:
  server, _boundPort, service = createRuntimeServer(host, port)
  server.start()
  try:
    server.wait_for_termination()
  finally:
    service.close()
    server.stop(0).wait()


if __name__ == "__main__":
  runRuntime()
