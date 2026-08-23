from concurrent import futures

import grpc

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService


def createRuntimeService() -> RuntimeService:
  return RuntimeService()


def runRuntime(host: str = "127.0.0.1", port: int = 50051) -> None:
  server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
  runtime_pb2_grpc.add_RuntimeServiceServicer_to_server(createRuntimeService(), server)
  server.add_insecure_port(f"{host}:{port}")
  server.start()
  server.wait_for_termination()


if __name__ == "__main__":
  runRuntime()
