from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as generatedRuntimePb2


for symbolName in dir(generatedRuntimePb2):
  if symbolName.startswith("_"):
    continue
  globals()[symbolName] = getattr(generatedRuntimePb2, symbolName)
