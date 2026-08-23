import os

from emo_master.apps.designer.main import resolveRuntimeTarget


def testResolveRuntimeTargetUsesEnvVar() -> None:
  os.environ["EMO_RUNTIME_TARGET"] = "127.0.0.1:50051"
  assert resolveRuntimeTarget() == "127.0.0.1:50051"
