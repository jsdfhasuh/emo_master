from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as generatedRuntimePb2Grpc


for symbolName in dir(generatedRuntimePb2Grpc):
  if symbolName.startswith("_"):
    continue
  globals()[symbolName] = getattr(generatedRuntimePb2Grpc, symbolName)
